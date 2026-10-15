"""Create missing public copies and list deprecated services that need a decision.

Catalogue migration 0002 publishes existing services at deploy with the same
rules; this command repeats them on demand and prints the ids it leaves out.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.catalogue import publishing
from apps.catalogue.models import PublishedService
from apps.submissions.models import (
    ServiceSubmission,
    SubmissionChangeLog,
    SubmissionStatus,
    SubmissionStatusEvent,
)
from apps.submissions.selectors import was_ever_approved

# Change-log fields written together with a status change (tags are cleared
# when a service leaves the approved state). They are not content edits.
_STATUS_LOG_FIELDS = {"status", "primary_maturity_tag", "secondary_maturity_tags"}


def _may_edit_content(changes) -> bool:
    """True when a change-log entry may hold a content edit. An entry that
    cannot be read counts as one, so the service stays private."""
    if not isinstance(changes, list):
        return True
    return any(
        not isinstance(change, dict) or change.get("field") not in _STATUS_LOG_FIELDS
        for change in changes
    )


def _holds_approved_content(sub) -> bool:
    """True when a deprecated service went straight from approved to
    deprecated and its content was not edited after that approval, so the
    working copy is the content that was approved."""
    events = list(
        SubmissionStatusEvent.objects.filter(submission=sub)
        .order_by("id")
        .values_list("to_status", "changed_at")
    )
    last_deprecation = max(
        (i for i, (to_status, _) in enumerate(events) if to_status == "deprecated"),
        default=None,
    )
    if not last_deprecation:  # never deprecated, or created deprecated
        return False
    to_status, approved_at = events[last_deprecation - 1]
    if to_status != SubmissionStatus.APPROVED:
        return False
    later_entries = SubmissionChangeLog.objects.filter(
        submission=sub, changed_at__gt=approved_at
    ).values_list("changes", flat=True)
    return not any(_may_edit_content(changes) for changes in later_entries)


def _backfill_decision(sub) -> str:
    """What the backfill does with a service in its current status."""
    if sub.status == SubmissionStatus.APPROVED:
        return "publish"
    if sub.status != SubmissionStatus.DEPRECATED:
        return "skip"
    if not was_ever_approved(sub.pk):
        return "never_approved"
    return "publish" if _holds_approved_content(sub) else "unconfirmed"


class Command(BaseCommand):
    help = (
        "Publish approved services that have no public copy yet. Deprecated "
        "services are published as deprecated only if they went straight from "
        "approved to deprecated without later content edits; the rest are "
        "listed by id for an admin decision. Services approved before but now "
        "waiting for review are only counted. Safe to run again."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Count what would be published without writing anything.",
        )

    def _publish_one(self, pk) -> bool:
        """Publish one service; False when it no longer qualifies.

        The row is locked and read again first: the service may have been
        edited, reset, deprecated or published since the candidates were chosen.
        """
        with transaction.atomic():
            sub = ServiceSubmission.objects.select_for_update().filter(pk=pk).first()
            if (
                sub is None
                or PublishedService.objects.filter(pk=pk).exists()
                or _backfill_decision(sub) != "publish"
            ):
                return False
            # The copy is as old as the last change of the service.
            publishing.publish(
                sub,
                reason="backfill",
                actor="system",
                public_modified_at=sub.updated_at,
            )
            if sub.status == SubmissionStatus.DEPRECATED:
                publishing.set_publication_state(
                    pk,
                    state=PublishedService.DEPRECATED,
                    public_modified_at=sub.updated_at,
                )
            return True

    def handle(self, *args, dry_run=False, **options):
        published, listed, unconfirmed = 0, [], []
        unpublished = ServiceSubmission.objects.filter(published__isnull=True)
        candidates = unpublished.filter(
            status__in=[SubmissionStatus.APPROVED, SubmissionStatus.DEPRECATED]
        ).order_by("submitted_at", "pk")
        for sub in candidates.iterator():
            decision = _backfill_decision(sub)
            if decision == "never_approved":
                listed.append(str(sub.pk))
            elif decision == "unconfirmed":
                unconfirmed.append(str(sub.pk))
            elif dry_run or self._publish_one(sub.pk):
                published += 1
        waiting = (
            unpublished.filter(
                status__in=[SubmissionStatus.SUBMITTED, SubmissionStatus.UNDER_REVIEW],
                status_events__to_status=SubmissionStatus.APPROVED,
            )
            .distinct()
            .count()
        )
        prefix = "[dry run] " if dry_run else ""
        self.stdout.write(f"{prefix}published: {published}")
        self.stdout.write(
            f"{prefix}deprecated, no recorded approval (not published): {len(listed)}"
        )
        for pk in listed:
            self.stdout.write(f"  {pk}")
        self.stdout.write(
            f"{prefix}deprecated, content not confirmed as approved "
            f"(not published): {len(unconfirmed)}"
        )
        for pk in unconfirmed:
            self.stdout.write(f"  {pk}")
        self.stdout.write(
            f"{prefix}approved before, now waiting for review (not published): "
            f"{waiting}"
        )
