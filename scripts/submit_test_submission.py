#!/usr/bin/env python3
"""
submit_test_submission.py — Create a test service submission via the public API
================================================================================

Submits a realistic test record for the MetaProFi tool so developers can
exercise the full review/approve workflow without filling in the web form by
hand on every test run.

Run AFTER import_reference_data.py so that the reference data (PI, service
centre, category) already exists.  The script fetches available reference IDs
via the admin API key and picks the first match for each FK/M2M field.

Usage
─────
  python submit_test_submission.py \\
      --api-url http://localhost:8000 \\
      --api-key  YOUR_ADMIN_TOKEN

  python submit_test_submission.py --help

Options
───────
  --api-url   Base URL of the registry (no trailing slash).  [required]
  --api-key   Admin API key — used ONLY to resolve reference IDs via GET.
              The submission itself is sent unauthenticated (public POST).
  --timeout   Per-request timeout in seconds (default: 30).
  --dry-run   Print the payload without sending the POST request.

Output
──────
  On success the script prints the submission UUID and the one-time API key.
  Save the API key — it is returned exactly once and cannot be retrieved again.
"""

import argparse
import json
import sys
import textwrap
import urllib.parse

try:
    import requests
except ImportError:
    sys.exit(
        "ERROR: 'requests' is not installed.\n"
        "Install it with:  pip install requests"
    )


# ---------------------------------------------------------------------------
# Test submission data — MetaProFi
# ---------------------------------------------------------------------------

# This payload is sent verbatim to POST /api/v1/submissions/.
# FK / M2M fields that require live IDs are filled in at runtime.

_STATIC_PAYLOAD: dict = {
    # ── Section A: General ────────────────────────────────────────────────
    "date_of_entry": "2026-04-10",
    "submitter_first_name": "Sanjay",
    "submitter_last_name": "Srikakulam",
    "submitter_affiliation": "Forschungszentrum Jülich",
    "register_as_elixir": False,
    # ── Section B: Service master data ───────────────────────────────────
    "service_name": "MetaProFi",
    "service_description": (
        "MetaProFi is a protein-family database tool designed for storing and "
        "searching protein and nucleotide sequences using content-based Bloom "
        "filter indexing. It enables efficient large-scale sequence similarity "
        "search across metagenomic and metatranscriptomic datasets. MetaProFi is "
        "implemented in Python and supports multiple sequence formats. It is "
        "particularly suited for high-throughput environments where fast, "
        "memory-efficient queries over large sequence collections are required."
    ),
    "year_established": 2022,
    # service_category_ids injected at runtime
    "is_toolbox": False,
    "toolbox_name": "",
    "user_knowledge_required": (
        "Basic knowledge of bioinformatics command-line tools and sequence data "
        "formats (FASTA/FASTQ) is recommended."
    ),
    "publications_pmids": "10.1093/bioinformatics/btad101",
    # ── Section C: Responsibilities ───────────────────────────────────────
    # responsible_pi_ids and service_center_id injected at runtime
    "associated_partner_note": "",
    "host_institute": "Forschungszentrum Jülich",
    "internal_contact_email": "s.srikakulam@fz-juelich.de",
    "internal_contact_name": "Sanjay Srikakulam",
    "public_contact_email": "s.srikakulam@fz-juelich.de",
    # ── Section D: Websites & links ───────────────────────────────────────
    "website_url": "https://github.com/kalininalab/metaprofi",
    "terms_of_use_url": "https://github.com/kalininalab/metaprofi/blob/main/LICENSE",
    "licenses": [
        "MIT"
    ],
    "github_url": "https://github.com/kalininalab/metaprofi",
    "biotools_url": "https://bio.tools/metaprofi",
    "fairsharing_url": "",
    "other_registry_url": "",
    # ── Section E: KPIs ──────────────────────────────────────────────────
    "kpi_monitoring": "planned",
    "kpi_start_year": "",
    # ── Section F: Discoverability ────────────────────────────────────────
    "keywords_uncited": "bloom filter, sequence search, metagenomics, protein families",
    "keywords_seo": "metaprofi bloom filter protein sequence search tool bioinformatics",
    "survey_participation": True,
    "comments": "Test submission created by submit_test_submission.py — safe to delete.",
    # ── Section G: Consent ───────────────────────────────────────────────
    "data_protection_consent": True,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_url(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + path.lstrip("/")


def _raise_for_status(resp, method: str, url: str, payload=None) -> None:
    if resp.ok:
        return
    body = resp.text[:600]
    hint = f"\n  Payload: {json.dumps(payload, indent=2)}" if payload else ""
    raise RuntimeError(
        f"API error {resp.status_code} on {method} {url}{hint}\n"
        f"  Response: {body}"
    )


def _get_all(session, base: str, path: str, timeout: int) -> list[dict]:
    url = _build_url(base, path)
    resp = session.get(url, timeout=timeout)
    _raise_for_status(resp, "GET", url)
    data = resp.json()
    return data if isinstance(data, list) else data.get("results", data)


def _pick(records: list[dict], label: str) -> dict:
    """Return the first active record, or exit with a clear message."""
    active = [r for r in records if r.get("is_active", True)]
    if not active:
        sys.exit(
            f"ERROR: No active {label} found in the API.\n"
            "Run import_reference_data.py first to populate reference data."
        )
    return active[0]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="submit_test_submission.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent("""\
            Create a test MetaProFi service submission via the public API.

            Run AFTER import_reference_data.py so reference data exists.
            The admin API key is used only to resolve reference IDs (GET).
            The submission POST itself is unauthenticated (public endpoint).
        """),
        epilog=textwrap.dedent("""\
            Examples:
              python submit_test_submission.py \\
                  --api-url http://localhost:8000 \\
                  --api-key abc123

              python submit_test_submission.py \\
                  --api-url http://localhost:8000 \\
                  --api-key abc123 \\
                  --dry-run
        """),
    )
    parser.add_argument("--api-url", required=True, metavar="URL",
                        help="Base URL of the registry, e.g. http://localhost:8000")
    parser.add_argument("--api-key", required=True, metavar="TOKEN",
                        help="Admin API key for fetching reference data.")
    parser.add_argument("--timeout", type=int, default=30, metavar="SECONDS",
                        help="Per-request timeout in seconds (default: 30).")
    parser.add_argument("--dry-run", action="store_true", default=False,
                        help="Print the payload without sending the POST request.")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    # ── Input validation ───────────────────────────────────────────────────
    if not args.api_url.startswith(("http://", "https://")):
        sys.exit(f"ERROR: --api-url must start with http:// or https://\n  Got: {args.api_url}")

    try:
        parsed = urllib.parse.urlparse(args.api_url)
        if not parsed.netloc:
            raise ValueError("No host found.")
    except ValueError as exc:
        sys.exit(f"ERROR: Invalid --api-url: {exc}")

    if any(c in args.api_key for c in ("\n", "\r")):
        sys.exit("ERROR: --api-key contains illegal characters (newline/CR).")

    if args.timeout < 1 or args.timeout > 300:
        sys.exit("ERROR: --timeout must be between 1 and 300 seconds.")

    # ── Admin session (GET only — reference data resolution) ──────────────
    admin_session = requests.Session()
    admin_session.headers.update({
        "Authorization": f"AdminKey {args.api_key}",
        "Accept": "application/json",
    })

    print(f"Fetching reference data from {args.api_url} …")

    try:
        categories = _get_all(admin_session, args.api_url, "/api/v1/categories/", args.timeout)
        centers    = _get_all(admin_session, args.api_url, "/api/v1/service-centers/", args.timeout)
        pis        = _get_all(admin_session, args.api_url, "/api/v1/pis/", args.timeout)
    except RuntimeError as exc:
        sys.exit(f"\nERROR while fetching reference data:\n  {exc}")
    except requests.exceptions.ConnectionError as exc:
        sys.exit(f"\nERROR: Cannot reach the API at {args.api_url}.\nDetails: {exc}")
    except requests.exceptions.Timeout:
        sys.exit(f"\nERROR: Request timed out after {args.timeout}s.")

    cat    = _pick(categories, "service category")
    center = _pick(centers, "service center")
    pi     = _pick(pis, "principal investigator")

    print(f"  Using category      : [{cat['id']}] {cat['name']}")
    print(f"  Using service center: [{center['id']}] {center.get('short_name', '')} — {center.get('full_name', '')}")
    print(f"  Using PI            : [{pi['id']}] {pi.get('first_name', '')} {pi.get('last_name', '')}")

    # ── Build final payload ────────────────────────────────────────────────
    payload = dict(_STATIC_PAYLOAD)
    payload["service_category_ids"] = [cat["id"]]
    payload["service_center_id"]    = center["id"]
    payload["responsible_pi_ids"]   = [pi["id"]]

    print(f"\nPayload:\n{json.dumps(payload, indent=2)}\n")

    if args.dry_run:
        print("*** DRY-RUN — no request sent ***")
        return 0

    # ── POST submission (public endpoint — no auth) ────────────────────────
    public_session = requests.Session()
    public_session.headers.update({
        "Content-Type": "application/json",
        "Accept": "application/json",
    })

    url = _build_url(args.api_url, "/api/v1/submissions/")
    print(f"POSTing test submission to {url} …")

    try:
        resp = public_session.post(url, json=payload, timeout=args.timeout)
        _raise_for_status(resp, "POST", url, payload)
    except RuntimeError as exc:
        print(f"\n[ERROR] {exc}")
        return 1
    except requests.exceptions.ConnectionError as exc:
        sys.exit(f"\nERROR: Cannot reach the API at {args.api_url}.\nDetails: {exc}")
    except requests.exceptions.Timeout:
        sys.exit(f"\nERROR: Request timed out after {args.timeout}s.")

    data = resp.json()
    sub_id  = data.get("id", "—")
    api_key = data.get("api_key", "—")
    warning = data.get("api_key_warning", "")

    print(f"\n{'='*60}")
    print("TEST SUBMISSION CREATED")
    print(f"{'='*60}")
    print(f"  Submission ID : {sub_id}")
    print(f"  API key       : {api_key}")
    if warning:
        print(f"  !! {warning}")
    print(f"\n  Retrieve / update your submission:")
    print(f"    GET  {_build_url(args.api_url, f'/api/v1/submissions/{sub_id}/')}")
    print(f"    Authorization: ApiKey {api_key}")
    print(f"{'='*60}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
