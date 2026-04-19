"""
Management Command: sync_spdx_licenses
=======================================
Downloads the SPDX License List and upserts all entries into the local
SpdxLicense table.

Usage:
    python manage.py sync_spdx_licenses
    python manage.py sync_spdx_licenses --url /path/to/licenses.json
    python manage.py sync_spdx_licenses --url https://...
    python manage.py sync_spdx_licenses --dry-run

Default URL (configurable via SPDX_LICENSES_URL env var or
[licenses] url in site.toml):
    https://raw.githubusercontent.com/spdx/license-list-data/main/json/licenses.json
"""

from django.core.management.base import BaseCommand, CommandError

from apps.licenses.sync import _default_url, run_sync


class Command(BaseCommand):
    help = "Download and upsert SPDX License List entries into the database."

    def add_arguments(self, parser):
        parser.add_argument(
            "--url",
            default=None,
            help=(
                "URL or local file path for the SPDX licenses JSON. "
                f"Defaults to SPDX_LICENSES_URL env var or {_default_url()}"
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Parse and count entries but do not write to the database.",
        )

    def handle(self, *args, **options):
        def log(msg):
            self.stdout.write(msg)

        try:
            result = run_sync(
                url=options["url"],
                dry_run=options["dry_run"],
                log=log,
            )
        except RuntimeError as exc:
            raise CommandError(str(exc)) from exc

        if not options["dry_run"]:
            self.stdout.write(
                self.style.SUCCESS(
                    f"SPDX license sync complete. Created: {result['created']}, "
                    f"Updated: {result['updated']}, Total: {result['total']}, "
                    f"Deprecated sweep: {result['deprecated_swept']}"
                )
            )
