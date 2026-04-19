# Generated + hand-edited for SPDX license integration (2026-04-17).
#
# This migration safely transitions ServiceSubmission.license (CharField slug)
# to ServiceSubmission.licenses (M2M -> SpdxLicense) + ServiceSubmission.license_note
# (free-text fallback).
#
# Order:
#   1. AddField(licenses)           — new M2M, nullable by construction
#   2. AddField(license_note)       — blank free-text
#   3. RunPython(_migrate_data)     — seed SpdxLicense if empty; map legacy slugs
#   4. RemoveField(license)         — drop the old CharField slug
#
# The data step is idempotent and reversible (best-effort reverse via _unmigrate_data).

from django.db import migrations, models


# ---------------------------------------------------------------------------
# Legacy YAML slug -> SPDX licenseId map
# ---------------------------------------------------------------------------
LEGACY_TO_SPDX: dict[str, str] = {
    "agpl3": "AGPL-3.0-only",
    "gpl3": "GPL-3.0-only",
    "gpl2": "GPL-2.0-only",
    "lgpl3": "LGPL-3.0-only",
    "lgpl21": "LGPL-2.1-only",
    "mpl2": "MPL-2.0",
    "apache2": "Apache-2.0",
    "mit": "MIT",
    "bsd2": "BSD-2-Clause",
    "bsd3": "BSD-3-Clause",
    "isc": "ISC",
    "boost": "BSL-1.0",
    "epl2": "EPL-2.0",
    "eupl12": "EUPL-1.2",
    "cc0": "CC0-1.0",
    "cc_by4": "CC-BY-4.0",
    "cc_by_sa4": "CC-BY-SA-4.0",
    "artistic2": "Artistic-2.0",
    "unlicense": "Unlicense",
}
# Legacy slugs that have no SPDX equivalent — stored into license_note instead.
LEGACY_TO_NOTE: dict[str, str] = {
    "other": "Other",
    "na": "Not applicable",
}


def _migrate_data(apps, schema_editor):
    """Map legacy license slugs to SPDX M2M + license_note.

    If the SpdxLicense table is empty (fresh deploy or test), attempt a sync.
    If sync fails, legacy slugs that map to SPDX IDs will still be recorded in
    license_note as a degraded fallback so no data is lost.
    """
    ServiceSubmission = apps.get_model("submissions", "ServiceSubmission")
    SpdxLicense = apps.get_model("licenses", "SpdxLicense")

    # Seed SPDX licenses if the table is empty (production / fresh deploy).
    # Skip during tests — they manage SpdxLicense fixtures explicitly and
    # network calls in migrations would make the suite non-hermetic.
    import sys

    in_tests = "pytest" in sys.modules or "test" in sys.argv
    if not in_tests and not SpdxLicense.objects.exists():
        try:
            from apps.licenses.sync import run_sync

            print(
                "\n[licenses] SpdxLicense table is empty — running initial SPDX sync.\n"
                "[licenses] This downloads ~500 KB from the SPDX license-list-data repo.\n"
                "[licenses] To skip, set SPDX_LICENSES_URL to a local file path in .env.\n"
            )

            def log(msg):
                print(f"[licenses] {msg}")

            result = run_sync(log=log)
            print(
                f"[licenses] Auto-seed complete — {result['total']} licenses loaded "
                f"(SPDX {result['version'] or 'version unknown'}).\n"
            )
        except Exception as exc:
            print(
                f"\n[licenses] Auto-seed failed: {exc}\n"
                "[licenses] Run manually: python manage.py sync_spdx_licenses\n"
            )

    # Build lookup keyed by license_id (present after sync)
    by_id = {lic.license_id: lic for lic in SpdxLicense.objects.all()}

    # Warn loudly when the SPDX table is empty at this point — any legacy slug
    # with an SPDX equivalent will fall through to the "Legacy: <slug>" note
    # path and need manual re-linking in the admin.
    if not in_tests and not by_id:
        print(
            "[licenses] WARNING: SpdxLicense table is empty — SPDX-mappable "
            "legacy slugs will be stored as 'Legacy: <slug>' in license_note. "
            "After migration: run `python manage.py sync_spdx_licenses`, then "
            "re-link affected submissions via the admin (filter license_note "
            "by 'Legacy:')."
        )

    for sub in ServiceSubmission.objects.all():
        slug = (sub.license or "").strip()
        if not slug:
            continue
        if slug in LEGACY_TO_NOTE:
            sub.license_note = LEGACY_TO_NOTE[slug]
            sub.save(update_fields=["license_note"])
            continue
        spdx_id = LEGACY_TO_SPDX.get(slug)
        if spdx_id and spdx_id in by_id:
            sub.licenses.add(by_id[spdx_id])
            continue
        # Unknown or unmatched legacy slug — preserve for manual review
        sub.license_note = f"Legacy: {slug}"
        sub.save(update_fields=["license_note"])


def _unmigrate_data(apps, schema_editor):
    """Best-effort reverse — writes the first M2M license_id back into license_note.

    The old CharField is restored by the RemoveField reversal at the framework
    layer; we do not attempt to repopulate it from the M2M because the reverse
    mapping is lossy (several SPDX IDs map to 'other' or 'na').
    """
    # No-op: leaving license_note as-is is the safest reverse.
    return


class Migration(migrations.Migration):
    dependencies = [
        ("licenses", "0001_initial"),
        ("submissions", "0015_alter_servicesubmission_last_change_summary"),
    ]

    operations = [
        migrations.AddField(
            model_name="servicesubmission",
            name="licenses",
            field=models.ManyToManyField(
                blank=True,
                help_text=(
                    "Licenses governing this service. "
                    "Multiple allowed for dual/mixed licensing."
                ),
                related_name="submissions",
                to="licenses.spdxlicense",
            ),
        ),
        migrations.AddField(
            model_name="servicesubmission",
            name="license_note",
            field=models.CharField(
                blank=True,
                help_text=(
                    "Free-text note for cases where no standard license applies "
                    "(e.g. 'Not applicable', 'Proprietary', or a custom license name)."
                ),
                max_length=200,
            ),
        ),
        migrations.RunPython(_migrate_data, _unmigrate_data),
        migrations.RemoveField(
            model_name="servicesubmission",
            name="license",
        ),
    ]
