"""
SPDX License Sync
=================
Core sync logic shared by:
  - manage.py sync_spdx_licenses    (management command)
  - licenses.sync Celery task       (admin button / beat schedule)
  - post_migrate auto-seed          (first-time deployment)
"""

import json
import os
import urllib.request

SPDX_DEFAULT_URL = (
    "https://raw.githubusercontent.com/spdx/license-list-data/main/json/licenses.json"
)


def _default_url() -> str:
    try:
        from django.conf import settings

        return getattr(settings, "SPDX_LICENSES_URL", SPDX_DEFAULT_URL)
    except Exception:
        return os.environ.get("SPDX_LICENSES_URL", SPDX_DEFAULT_URL)


def run_sync(url: str | None = None, dry_run: bool = False, log=None) -> dict:
    """
    Download and upsert SPDX licenses.

    Args:
        url:     JSON source — HTTP URL or local file path. Defaults to
                 SPDX_LICENSES_URL setting or the canonical GitHub raw URL.
        dry_run: Parse but do not write to the database.
        log:     Callable for progress messages (e.g. print, logger.info).
                 No-op if None.

    Returns:
        dict with keys: created, updated, total, version, deprecated_swept
    """
    from apps.licenses.models import SpdxLicense

    if log is None:
        log = lambda *a: None  # noqa: E731

    url = url or _default_url()
    log(f"Loading SPDX licenses from: {url}")

    # ------------------------------------------------------------------
    # Step 1: Load JSON
    # ------------------------------------------------------------------
    try:
        if url.startswith("http"):
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "denbi-registry/1.0 sync_spdx_licenses"},
            )
            with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310
                raw_bytes = resp.read()
        else:
            with open(url, "rb") as f:
                raw_bytes = f.read()
    except Exception as exc:
        raise RuntimeError(f"Failed to load SPDX licenses from {url}: {exc}") from exc

    log(f"Downloaded {len(raw_bytes):,} bytes. Parsing JSON...")

    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Failed to parse SPDX licenses JSON: {exc}") from exc

    if not isinstance(payload, dict) or "licenses" not in payload:
        raise RuntimeError(
            "Unexpected SPDX licenses payload shape — "
            "missing top-level 'licenses' array."
        )

    spdx_version = str(payload.get("licenseListVersion") or "")
    entries = payload.get("licenses") or []
    log(
        f"SPDX License List version: {spdx_version or 'unknown'} — {len(entries)} entries"
    )

    # ------------------------------------------------------------------
    # Step 2: Collect parsed rows
    # ------------------------------------------------------------------
    rows: dict[str, dict] = {}
    for entry in entries:
        license_id = str(entry.get("licenseId") or "").strip()
        if not license_id:
            continue
        name = str(entry.get("name") or "").strip() or license_id
        rows[license_id] = {
            "license_id": license_id,
            "name": name,
            "reference_url": str(entry.get("reference") or "").strip(),
            "see_also": list(entry.get("seeAlso") or []),
            "is_osi_approved": bool(entry.get("isOsiApproved") or False),
            "is_fsf_libre": bool(entry.get("isFsfLibre") or False),
            "is_deprecated": bool(entry.get("isDeprecatedLicenseId") or False),
            "spdx_version": spdx_version,
        }

    if dry_run:
        log(f"Dry run — would upsert {len(rows)} licenses. No database writes.")
        return {
            "created": 0,
            "updated": 0,
            "total": 0,
            "version": spdx_version,
            "deprecated_swept": 0,
        }

    # ------------------------------------------------------------------
    # Step 3: Upsert rows
    # ------------------------------------------------------------------
    log("Writing to database...")
    created_count = 0
    updated_count = 0

    for license_id, data in rows.items():
        _, created = SpdxLicense.objects.update_or_create(
            license_id=license_id,
            defaults={
                "name": data["name"],
                "reference_url": data["reference_url"],
                "see_also": data["see_also"],
                "is_osi_approved": data["is_osi_approved"],
                "is_fsf_libre": data["is_fsf_libre"],
                "is_deprecated": data["is_deprecated"],
                "spdx_version": data["spdx_version"],
            },
        )
        if created:
            created_count += 1
        else:
            updated_count += 1

    # ------------------------------------------------------------------
    # Step 4: Sweep — mark rows missing from upstream as deprecated
    # ------------------------------------------------------------------
    known_ids = set(rows.keys())
    swept = (
        SpdxLicense.objects.exclude(license_id__in=known_ids)
        .exclude(is_deprecated=True)
        .update(is_deprecated=True)
    )
    if swept:
        log(f"Marked {swept} licenses removed from upstream as deprecated.")

    total = SpdxLicense.objects.count()
    log(
        f"Done. Created: {created_count}, Updated: {updated_count}, "
        f"Deprecated sweep: {swept}, Total rows: {total}"
    )

    return {
        "created": created_count,
        "updated": updated_count,
        "total": total,
        "version": spdx_version,
        "deprecated_swept": swept,
    }
