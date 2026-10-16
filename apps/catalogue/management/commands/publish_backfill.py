"""Create missing public copies and list deprecated services that need a decision.

Catalogue migration 0002 publishes existing services at deploy with the same
rules; this command repeats them on demand and prints the ids it leaves out.
A service that already has revisions but lost its public copy gets the copy
back from its own latest revision, never from the working copy; one whose
latest revision names a deleted service centre is listed by id instead.
It also repairs published services whose status history does not end in their
stored status (an earlier release changed it): the history is recorded and the
copy's state and pending flag follow it. Content is never published by that
repair.

Finally it publishes the approved working copy of a published service whose
public copy is behind it (an earlier release edited, reviewed and approved the
service again without recording anything). An approved working copy is always
reviewed content, because any other edit sends the service back to review, so
publishing it follows the product rules.
"""

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import OuterRef, Subquery
from django.utils import timezone

from apps.catalogue import publishing
from apps.catalogue.models import PublishedRevision, PublishedService
from apps.submissions.lifecycle import record_unrecorded_status
from apps.submissions.models import (
    ServiceSubmission,
    SubmissionChangeLog,
    SubmissionStatus,
    SubmissionStatusEvent,
)
from apps.submissions.selectors import (
    latest_public_decisions,
    status_history_drift,
    was_ever_approved,
)

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
        .values_list("to_status", "changed_at", "is_synthetic")
    )
    last_deprecation = max(
        (i for i, event in enumerate(events) if event[0] == "deprecated"),
        default=None,
    )
    if not last_deprecation:  # never deprecated, or created deprecated
        return False
    to_status, approved_at, synthetic = events[last_deprecation - 1]
    if to_status != SubmissionStatus.APPROVED:
        return False
    if synthetic and last_deprecation > 1:
        # The date of a reconstructed approval is not a real one: the service
        # may have been approved, and edited, before it. Edits since the event
        # ahead of it count.
        approved_at = events[last_deprecation - 2][1]
    later_entries = SubmissionChangeLog.objects.filter(
        submission=sub, changed_at__gt=approved_at
    ).values_list("changes", flat=True)
    return not any(_may_edit_content(changes) for changes in later_entries)


def _backfill_decision(sub) -> str:
    """What the backfill does with a service in its current status."""
    latest = (
        PublishedRevision.objects.filter(submission_id=sub.pk)
        .order_by("-number")
        .first()
    )
    if latest is not None:
        # The copy is lost, the approved revision is not.
        if publishing.names_deleted_foreign_key(latest.content):
            return "deleted_centre"
        return "rebuild"
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
        "waiting for review are only counted. Published services whose status "
        "history lags behind their status get the history recorded and their "
        "state and pending flag updated, without publishing content. Approved "
        "services whose public copy is behind their working copy are published "
        "again. Safe to run again."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Count what would be published without writing anything.",
        )

    def _publish_one(self, pk) -> bool:
        """Publish one service, or rebuild the copy of one that was published
        before; False when it no longer qualifies.

        The row is locked and read again first: the service may have been
        edited, reset, deprecated or published since the candidates were chosen.
        """
        with transaction.atomic():
            sub = ServiceSubmission.objects.select_for_update().filter(pk=pk).first()
            if sub is None or PublishedService.objects.filter(pk=pk).exists():
                return False
            decision = _backfill_decision(sub)
            if decision == "rebuild":
                # The service was published before: its copy comes back from
                # its own latest revision, not from the working copy.
                publishing.rebuild_from_revision(pk)
                return True
            if decision != "publish":
                return False
            # A status an earlier release wrote without an event is recorded
            # first, so the published service has its approval in the history.
            record_unrecorded_status(sub.pk, sub.status, timezone.now())
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

    def _repair_one(self, pk) -> bool:
        """Record the unrecorded history of one published service and set the
        copy's state and pending flag; False when there is nothing to repair.

        The row is locked and read again first, like in ``_publish_one``.
        """
        with transaction.atomic():
            sub = ServiceSubmission.objects.select_for_update().filter(pk=pk).first()
            if sub is None or not PublishedService.objects.filter(pk=pk).exists():
                return False
            if not record_unrecorded_status(pk, sub.status, timezone.now()):
                return False
            state, pending = publishing.expected_flags(
                sub.status, latest_public_decisions([pk]).get(pk)
            )
            publishing.set_publication_state(pk, state=state, pending_update=pending)
            return True

    def _repair_published(self, dry_run) -> list[str]:
        """Ids of the published services whose history was (or in a dry run,
        would be) repaired."""
        lagging = [pk for pk, _, _ in status_history_drift()]
        candidates = (
            PublishedService.objects.filter(pk__in=lagging)
            .order_by("submission__submitted_at", "pk")
            .values_list("pk", flat=True)
        )
        return [str(pk) for pk in candidates if dry_run or self._repair_one(pk)]

    def _republish_one(self, pk) -> bool:
        """Publish the working copy of one approved, published service; False
        when it no longer qualifies.

        The row is locked and read again first, like in ``_publish_one``: an
        edit that sent the service back to review since the candidates were
        chosen is not reviewed and stays private. ``publish`` compares the
        working copy with the latest revision once more and writes nothing
        when they match.
        """
        with transaction.atomic():
            sub = ServiceSubmission.objects.select_for_update().filter(pk=pk).first()
            if (
                sub is None
                or sub.status != SubmissionStatus.APPROVED
                or not PublishedService.objects.filter(pk=pk).exists()
            ):
                return False
            published = publishing.publish(
                sub,
                reason="backfill",
                actor="system",
                public_modified_at=sub.updated_at,
            )
            if published is None:
                return False
            publishing.set_publication_state(
                pk,
                state=PublishedService.ACTIVE,
                pending_update=False,
                public_modified_at=sub.updated_at,
            )
            return True

    def _republish_approved(self, dry_run) -> list[str]:
        """Ids of the approved published services whose copy was (or in a dry
        run, would be) brought up to date."""
        latest_hash = (
            PublishedRevision.objects.filter(submission=OuterRef("pk"))
            .order_by("-number")
            .values("content_hash")[:1]
        )
        approved = (
            ServiceSubmission.objects.filter(
                status=SubmissionStatus.APPROVED, published__isnull=False
            )
            .annotate(latest_hash=Subquery(latest_hash))
            .order_by("submitted_at", "pk")
        )
        behind = [
            sub.pk
            for sub in approved.iterator()
            if sub.latest_hash != publishing.content_hash(publishing.build_content(sub))
        ]
        return [str(pk) for pk in behind if dry_run or self._republish_one(pk)]

    def handle(self, *args, dry_run=False, **options):
        published, listed, unconfirmed, deleted_centre = 0, [], [], []
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
            elif decision == "deleted_centre":
                deleted_centre.append(str(sub.pk))
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
            f"{prefix}public copy lost and its service centre was deleted "
            f"(not rebuilt): {len(deleted_centre)}"
        )
        for pk in deleted_centre:
            self.stdout.write(f"  {pk}")
        self.stdout.write(
            f"{prefix}approved before, now waiting for review (not published): "
            f"{waiting}"
        )
        repaired = self._repair_published(dry_run)
        self.stdout.write(
            f"{prefix}history repaired on published services: {len(repaired)}"
        )
        for pk in repaired:
            self.stdout.write(f"  {pk}")
        republished = self._republish_approved(dry_run)
        self.stdout.write(
            f"{prefix}approved content republished on published services: "
            f"{len(republished)}"
        )
        for pk in republished:
            self.stdout.write(f"  {pk}")
