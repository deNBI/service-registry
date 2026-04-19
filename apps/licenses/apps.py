import sys

from django.apps import AppConfig


class LicensesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.licenses"
    verbose_name = "SPDX Licenses"

    def ready(self):
        from django.db.models.signals import post_migrate

        post_migrate.connect(_auto_seed_licenses, sender=self)


def _auto_seed_licenses(sender, **kwargs):
    """
    Seed SPDX licenses automatically on first deployment.

    Fires after every `manage.py migrate` run. The check for an empty
    SpdxLicense table ensures the download only happens once — on the very
    first migrate against a fresh database. Subsequent runs are no-ops.

    Note: migration 0016_add_spdx_licenses_m2m has a parallel inline seed
    so the submissions data migration can map legacy slugs during the same
    `migrate` invocation that first creates the licenses table. Both paths
    check `SpdxLicense.objects.exists()` and are therefore idempotent —
    whichever runs first populates the table, the other becomes a no-op.

    Skipped during test runs (pytest sets sys.modules['pytest']).
    """
    if "pytest" in sys.modules or "test" in sys.argv:
        return

    try:
        from apps.licenses.models import SpdxLicense

        if SpdxLicense.objects.exists():
            return

        print(
            "\n[licenses] SpdxLicense table is empty — running initial SPDX sync.\n"
            "[licenses] This downloads ~500 KB from the SPDX license-list-data repo.\n"
            "[licenses] To skip, set SPDX_LICENSES_URL to a local file path in .env.\n"
        )
        from apps.licenses.sync import run_sync

        result = run_sync(log=lambda msg: print(f"[licenses] {msg}"))
        print(
            f"[licenses] Auto-seed complete — {result['total']} licenses loaded "
            f"(SPDX {result['version'] or 'version unknown'}).\n"
        )
    except Exception as exc:
        print(
            f"\n[licenses] Auto-seed failed: {exc}\n"
            "[licenses] Run manually: python manage.py sync_spdx_licenses\n"
        )
