"""
EDAM Ontology Sync
==================
Core sync logic shared by:
  - manage.py sync_edam          (management command)
  - edam.sync Celery task        (admin button / beat schedule)
  - post_migrate auto-seed       (first-time deployment)
"""

import os
import re
import urllib.request
import xml.etree.ElementTree as ET  # nosec B405 — Python 3.12 bundles Expat 2.7.1 which fixes XXE/billion-laughs
from collections import defaultdict

RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
OWL = "http://www.w3.org/2002/07/owl#"
OBOINOWL = "http://www.geneontology.org/formats/oboInOwl#"
EDAM_BASE = "http://edamontology.org/"

BRANCH_MAP = {
    "topic": "topic",
    "operation": "operation",
    "data": "data",
    "format": "format",
    "identifier": "identifier",
}


def _default_url() -> str:
    try:
        from django.conf import settings

        return getattr(
            settings, "EDAM_OWL_URL", "https://edamontology.org/EDAM_stable.owl"
        )
    except Exception:
        return os.environ.get(
            "EDAM_OWL_URL", "https://edamontology.org/EDAM_stable.owl"
        )


def _tag(ns, local):
    return f"{{{ns}}}{local}"


def _extract_accession(uri: str) -> str | None:
    match = re.search(r"/([\w]+_\d+)$", uri)
    return match.group(1) if match else None


def _extract_branch(accession: str) -> str | None:
    for prefix, branch in BRANCH_MAP.items():
        if accession.startswith(prefix + "_"):
            return branch
    return None


def _extract_sort_order(accession: str) -> int:
    match = re.search(r"_(\d+)$", accession)
    return int(match.group(1)) if match else 0


def _text(el) -> str:
    if el is None:
        return ""
    return (el.text or "").strip()


def run_sync(
    url: str | None = None, branch: str = "all", dry_run: bool = False, log=None
) -> dict:
    """
    Download and upsert EDAM ontology terms.

    Args:
        url:     OWL source — HTTP URL or local file path. Defaults to EDAM_OWL_URL setting.
        branch:  One of 'topic', 'operation', 'data', 'format', 'identifier', or 'all'.
        dry_run: Parse but do not write to the database.
        log:     Callable for progress messages (e.g. print, logger.info). No-op if None.

    Returns:
        dict with keys: created, updated, total, version, terms_by_branch
    """
    from apps.edam.models import EdamTerm

    if log is None:
        log = lambda *a: None  # noqa: E731

    url = url or _default_url()
    log(f"Loading EDAM from: {url}")

    # ------------------------------------------------------------------
    # Step 1: Load OWL/RDF-XML
    # ------------------------------------------------------------------
    try:
        if url.startswith("http"):
            req = urllib.request.Request(
                url, headers={"User-Agent": "denbi-registry/1.0 sync_edam"}
            )
            with urllib.request.urlopen(req, timeout=120) as resp:  # nosec B310
                raw_bytes = resp.read()
        else:
            with open(url, "rb") as f:
                raw_bytes = f.read()
    except Exception as exc:
        raise RuntimeError(f"Failed to load EDAM from {url}: {exc}") from exc

    log(f"Downloaded {len(raw_bytes):,} bytes. Parsing OWL/RDF-XML...")

    # ------------------------------------------------------------------
    # Step 2: Parse RDF/XML
    # ------------------------------------------------------------------
    try:
        root = ET.fromstring(raw_bytes)
    except ET.ParseError as exc:
        raise RuntimeError(f"Failed to parse OWL XML: {exc}") from exc

    edam_version = ""
    ontology_el = root.find(_tag(OWL, "Ontology"))
    if ontology_el is not None:
        version_el = ontology_el.find(_tag(OWL, "versionInfo"))
        if version_el is None:
            version_el = ontology_el.find(f"{{{RDFS}}}comment")
        edam_version = _text(version_el)
        if not edam_version:
            version_iri = ontology_el.get(_tag(RDF, "about"), "")
            m = re.search(r"(\d+\.\d+)", version_iri)
            if m:
                edam_version = m.group(1)

    log(f"EDAM version: {edam_version or 'unknown'}")

    # ------------------------------------------------------------------
    # Step 3: Parse owl:Class entries
    # ------------------------------------------------------------------
    terms: dict[str, dict] = {}

    for cls in root.findall(_tag(OWL, "Class")):
        uri = cls.get(_tag(RDF, "about"), "")
        if not uri or EDAM_BASE not in uri:
            continue

        accession = _extract_accession(uri)
        if not accession:
            continue

        term_branch = _extract_branch(accession)
        if not term_branch:
            continue

        if branch != "all" and term_branch != branch:
            continue

        label_el = cls.find(_tag(RDFS, "label"))
        label = _text(label_el)
        if not label:
            continue

        defn_el = cls.find(_tag(OBOINOWL, "hasDefinition")) or cls.find(
            _tag(RDFS, "comment")
        )
        definition = _text(defn_el)

        synonyms = [
            _text(s) for s in cls.findall(_tag(OBOINOWL, "hasExactSynonym")) if _text(s)
        ]
        for syn_tag in ("hasNarrowSynonym", "hasBroadSynonym", "hasRelatedSynonym"):
            synonyms.extend(
                _text(s) for s in cls.findall(_tag(OBOINOWL, syn_tag)) if _text(s)
            )

        parent_uri = None
        for sc in cls.findall(_tag(RDFS, "subClassOf")):
            candidate = sc.get(_tag(RDF, "resource"), "")
            if candidate and EDAM_BASE in candidate and _extract_accession(candidate):
                parent_uri = candidate
                break

        deprecated_el = cls.find(_tag(OWL, "deprecated"))
        is_obsolete = (
            _text(deprecated_el).lower() in ("true", "1")
            if deprecated_el is not None
            else False
        )

        terms[uri] = {
            "uri": uri,
            "accession": accession,
            "branch": term_branch,
            "label": label,
            "definition": definition,
            "synonyms": synonyms,
            "parent_uri": parent_uri,
            "is_obsolete": is_obsolete,
            "sort_order": _extract_sort_order(accession),
            "edam_version": edam_version,
        }

    log(f"Parsed {len(terms)} terms.")
    counts = defaultdict(int)
    for t in terms.values():
        counts[t["branch"]] += 1
    for b, c in sorted(counts.items()):
        log(f"  {b}: {c}")

    if dry_run:
        log("Dry run — no database writes.")
        return {
            "created": 0,
            "updated": 0,
            "total": 0,
            "version": edam_version,
            "terms_by_branch": dict(counts),
        }

    # ------------------------------------------------------------------
    # Step 4: Upsert (without parent FKs first)
    # ------------------------------------------------------------------
    log("Writing to database...")
    created_count = 0
    updated_count = 0

    for uri, data in terms.items():
        _, created = EdamTerm.objects.update_or_create(
            uri=uri,
            defaults={
                "accession": data["accession"],
                "branch": data["branch"],
                "label": data["label"],
                "definition": data["definition"],
                "synonyms": data["synonyms"],
                "is_obsolete": data["is_obsolete"],
                "sort_order": data["sort_order"],
                "edam_version": data["edam_version"],
            },
        )
        if created:
            created_count += 1
        else:
            updated_count += 1

    # ------------------------------------------------------------------
    # Step 5: Resolve parent FKs
    # ------------------------------------------------------------------
    log("Resolving parent relationships...")
    for uri, data in terms.items():
        parent_uri = data.get("parent_uri")
        if not parent_uri:
            continue
        try:
            parent_obj = EdamTerm.objects.get(uri=parent_uri)
            EdamTerm.objects.filter(uri=uri).update(parent=parent_obj)
        except EdamTerm.DoesNotExist:
            pass

    # ------------------------------------------------------------------
    # Step 6: Mark terms no longer in the OWL file as obsolete
    # ------------------------------------------------------------------
    if branch == "all":
        known_uris = set(terms.keys())
        newly_obsolete = EdamTerm.objects.exclude(uri__in=known_uris).exclude(
            is_obsolete=True
        )
        obsolete_count = newly_obsolete.update(is_obsolete=True)
        if obsolete_count:
            log(f"Marked {obsolete_count} removed terms as obsolete.")

    total = EdamTerm.objects.count()
    log(f"Done. Created: {created_count}, Updated: {updated_count}, Total: {total}")

    return {
        "created": created_count,
        "updated": updated_count,
        "total": total,
        "version": edam_version,
        "terms_by_branch": dict(counts),
    }
