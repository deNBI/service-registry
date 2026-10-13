"""
Management Command: sync_biotools
==================================
Fetch/refresh bio.tools data for one or all linked submissions.

Usage:
    # Sync all submissions that have a bio.tools URL and an existing record
    python manage.py sync_biotools

    # Sync a specific submission by UUID
    python manage.py sync_biotools --submission <uuid>

    # Create a new record for a submission that doesn't have one yet
    python manage.py sync_biotools --submission <uuid> --create

    # Dry-run: show what would be synced without making API calls
    python manage.py sync_biotools --dry-run
"""

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Sync bio.tools data for submissions that have a bio.tools URL."

    def add_arguments(self, parser):
        parser.add_argument(
            "--submission",
            metavar="UUID",
            help="Sync only this submission UUID.",
        )
        parser.add_argument(
            "--create",
            action="store_true",
            help="Create a new BioToolsRecord if one doesn't exist (requires --submission).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List what would be synced without making API calls.",
        )

    def handle(self, *args, **options):
        from apps.biotools.models import BioToolsRecord
        from apps.biotools.sync import (
            biotools_id_from_url,
            remove_records_without_url,
            sync_tool,
        )
        from apps.submissions.models import ServiceSubmission

        dry_run = options["dry_run"]
        submission_id = options.get("submission")

        if submission_id:
            # Single submission mode
            try:
                submission = ServiceSubmission.objects.get(pk=submission_id)
            except ServiceSubmission.DoesNotExist:
                raise CommandError(f"Submission {submission_id} not found.")
            except Exception:
                raise CommandError(f"Invalid submission UUID: {submission_id}")

            if not submission.biotools_url:
                raise CommandError(
                    f"Submission {submission_id} has no bio.tools URL set."
                )

            biotools_id = biotools_id_from_url(submission.biotools_url)
            self.stdout.write(
                f"{'[DRY RUN] Would sync' if dry_run else 'Syncing'}: "
                f"{submission.service_name} → bio.tools:{biotools_id}"
            )
            if dry_run:
                return

            result = sync_tool(
                biotools_id=biotools_id,
                submission_id=str(submission.pk),
            )
            if result.ok:
                action = "Created" if result.created else "Updated"
                self.stdout.write(
                    self.style.SUCCESS(f"{action} record for {biotools_id}")
                )
            else:
                self.stderr.write(self.style.ERROR(f"Sync failed: {result.error}"))
            return

        # Bulk mode: refresh all existing records, using the bio.tools ID in
        # each submission's current URL (it may differ from the stored ID).
        # Records of submissions without a URL are removed first (not on a
        # dry run).
        if not dry_run:
            removed = remove_records_without_url()
            if removed:
                self.stdout.write(
                    f"Removed {removed} record(s) of submissions without a bio.tools URL."
                )
        records = BioToolsRecord.objects.select_related("submission").all()
        total = records.count()

        if total == 0:
            self.stdout.write(
                "No BioToolsRecord rows found. Use --submission <uuid> --create to add one."
            )
            return

        if dry_run:
            self.stdout.write(f"[DRY RUN] Would sync {total} record(s):")
            for r in records:
                current = biotools_id_from_url(r.submission.biotools_url)
                action = f"bio.tools:{current}" if current else "remove (no URL)"
                self.stdout.write(f"  {r.submission.service_name} → {action}")
            return

        self.stdout.write(f"Syncing {total} bio.tools record(s)...")
        ok = err = 0
        for record in records:
            biotools_id = biotools_id_from_url(record.submission.biotools_url)
            if not biotools_id:
                continue
            result = sync_tool(
                biotools_id=biotools_id,
                submission_id=str(record.submission_id),
            )
            if result.ok:
                ok += 1
                self.stdout.write(f"  ✓ {biotools_id}")
            else:
                err += 1
                self.stderr.write(f"  ✗ {biotools_id}: {result.error}")

        self.stdout.write(self.style.SUCCESS(f"Done. OK: {ok}/{total}, Errors: {err}"))
