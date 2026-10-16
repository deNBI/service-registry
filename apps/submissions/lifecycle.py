"""
Submission Lifecycle Helpers
============================
Utilities for determining how edits affect the status lifecycle of a
ServiceSubmission.

The key behaviour controlled here:

  ``get_no_reset_fields()``
      Returns the validated set of field names (from ``SUBMISSION_NO_RESET_FIELDS``
      in settings, sourced from ``config/site.toml [submission] no_reset_fields``)
      whose changes must NOT trigger a status reset when a submitter edits an
      approved service.

  Validation rules
  ----------------
  - Only fields that appear in ``DIFFABLE_FIELDS`` or ``DIFFABLE_M2M`` are
    accepted.
  - The system-controlled fields listed in ``_NON_EDITABLE`` (status,
    primary_maturity_tag, etc.) are rejected even if present in
    ``DIFFABLE_FIELDS``.
  - Any unrecognised or system-controlled entry is logged as a warning and
    silently dropped — the deployment continues; only that entry is ignored.

  Result is cached with ``@lru_cache`` and populated at app startup via
  ``SubmissionsConfig.ready()`` to surface misconfigured field names early.
"""

import logging
from functools import lru_cache

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

logger = logging.getLogger(__name__)

# Fields that are managed by the system / admin and must not be user-exempt.
_NON_EDITABLE: frozenset[str] = frozenset(
    {
        "status",
        "date_of_entry",
        "primary_maturity_tag",
        "secondary_maturity_tags",
        "register_as_elixir",
    }
)


@lru_cache(maxsize=None)
def get_no_reset_fields() -> frozenset:
    """
    Return the validated set of exempt field names from settings.

    Fields in this set will not trigger a status reset when edited on an
    approved submission.  Returns an empty frozenset when
    ``SUBMISSION_NO_RESET_FIELDS`` is empty (→ legacy reset-always behaviour).

    Result is cached on first call; call ``get_no_reset_fields.cache_clear()``
    in tests that override ``settings.SUBMISSION_NO_RESET_FIELDS``.
    """
    from django.conf import settings

    from apps.submissions.diff_utils import DIFFABLE_FIELDS, DIFFABLE_M2M

    configured: list = list(getattr(settings, "SUBMISSION_NO_RESET_FIELDS", []))
    if not configured:
        return frozenset()

    all_diffable: frozenset[str] = frozenset(
        f for f, _ in DIFFABLE_FIELDS + DIFFABLE_M2M
    )
    editable_diffable = all_diffable - _NON_EDITABLE

    validated: set[str] = set()
    for field in configured:
        if field in editable_diffable:
            validated.add(field)
        elif field in _NON_EDITABLE:
            logger.warning(
                "SUBMISSION_NO_RESET_FIELDS: '%s' is a system-controlled field "
                "and cannot be made exempt. "
                "This entry is ignored — check config/site.toml [submission].",
                field,
            )
        else:
            logger.warning(
                "SUBMISSION_NO_RESET_FIELDS: '%s' does not correspond to a "
                "known diffable field. This entry is ignored — check for a "
                "typo in config/site.toml [submission] no_reset_fields.",
                field,
            )

    return frozenset(validated)


def record_unrecorded_status(submission_id, status: str, now) -> bool:
    """
    Record ``status`` when the status history does not end in it.

    A status can be written without an event, for example by an earlier
    release running on this database. That release still writes change-log
    entries, so the status changes it logged after the latest event are
    replayed as real events (time and actor from the entry), one chain from
    the latest recorded status. Rules, the same as the backfill migration
    (0022) except for the first:

    - an entry whose old status does not continue the chain is skipped (0022
      closes the gap with an invented event). The entry this release writes
      for each of its own events starts at that event's from-status, and is
      stamped just after the event (the admin bulk status action stamps it
      just before, so the time rule below already ignores it); read as a gap
      it would invent two wrong events.
    - entries are taken by ``changed_at``, then row id; entries not newer than
      the newest event time are ignored, so an entry is never replayed twice;
      unreadable entries and unknown labels are skipped (see
      ``status_log.status_transitions``).
    - when the chain still does not end in ``status``, one event invented to
      close it (is_synthetic=True, changed_by "system") does.

    Nothing is replayed while the history already ends in ``status``, even if
    an earlier release moved the service away and back: the entries this
    release writes around its own events would be replayed as new ones.

    A service without any event gets one invented event (from none to
    ``status``) and no replay. The caller holds the submission's row lock.
    Returns True when an event was written.
    """
    from apps.submissions.models import SubmissionChangeLog, SubmissionStatusEvent
    from apps.submissions.status_log import status_transitions

    latest = (
        SubmissionStatusEvent.objects.filter(submission_id=submission_id)
        .order_by("-id")
        .values_list("to_status", "changed_at")
        .first()
    )
    last_status = latest[0] if latest else None
    if last_status == status:
        return False
    # Row order says where the chain ends; the newest time bounds the replay.
    last_at = SubmissionStatusEvent.objects.filter(
        submission_id=submission_id
    ).aggregate(newest=Max("changed_at"))["newest"]

    def record(to_status, at, by, synthetic):
        nonlocal last_status, last_at
        SubmissionStatusEvent.objects.create(
            submission_id=submission_id,
            from_status=last_status,
            to_status=to_status,
            changed_at=at,
            changed_by=by,
            is_synthetic=synthetic,
        )
        last_status, last_at = to_status, max(at, last_at) if last_at else at

    if latest is not None:
        entries = SubmissionChangeLog.objects.filter(
            submission_id=submission_id, changed_at__gt=last_at
        ).order_by("changed_at", "id")
        for entry in entries:
            for old, new in status_transitions(entry.changes):
                if old == last_status and new != old:
                    record(new, entry.changed_at, entry.changed_by, False)
    if last_status != status:
        record(status, max(now, last_at) if last_at else now, "system", True)
    return True


# Which status changes are possible: current status -> statuses it may move to.
# An approved service is withdrawn by deprecating it, not by rejecting it.
# ``draft`` is not used any more: nothing moves into it, and a legacy row in
# draft may move to any other status.
TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset(
        {"submitted", "under_review", "approved", "rejected", "deprecated"}
    ),
    "submitted": frozenset({"under_review", "approved", "rejected", "deprecated"}),
    "under_review": frozenset({"approved", "rejected", "deprecated"}),
    "approved": frozenset({"under_review", "deprecated"}),
    "rejected": frozenset({"submitted", "under_review", "approved", "deprecated"}),
    "deprecated": frozenset({"submitted", "approved"}),
}

# Moves that exist only as the reset after an owner edit of an approved service
# (web form or API); no button or action offers them.
OWNER_EDIT_TRANSITIONS: dict[str, frozenset[str]] = {
    "approved": frozenset({"submitted"}),
}


class TransitionNotAllowed(ValueError):
    """The requested status change is not in the transition table."""


def allowed_transitions(status: str, *, owner_edit: bool = False) -> frozenset[str]:
    """
    Return the statuses a service in ``status`` may move to.

    ``owner_edit=True`` adds the reset of an approved service after an owner
    edit. The admin and the owner page use this to decide which buttons to
    show, so what they offer and what ``set_status`` accepts come from the
    same table.
    """
    allowed = TRANSITIONS.get(str(status), frozenset())
    if owner_edit:
        allowed = allowed | OWNER_EDIT_TRANSITIONS.get(str(status), frozenset())
    return allowed


def set_status(
    submission,
    new_status,
    *,
    actor: str,
    extra_fields=(),
    owner_edit: bool = False,
) -> bool:
    """
    Change a submission's status and record the change. The only status writer.

    Locks the row, reads the stored status and version (not the possibly stale
    instance), saves ``status`` plus ``extra_fields`` and writes one
    SubmissionStatusEvent, all in one transaction. Maturity tags are only valid
    on approved services, so any other status clears them. ``updated_at`` is
    only written when it is listed in ``extra_fields``.

    The caller has already passed its own conflict check (or works on a row it
    has just read under a lock), so the instance takes the stored version
    before the save and the save is not refused for an older copy.

    ``extra_fields`` are written from the caller's copy without that version
    check, so callers pass only fields they have just computed (such as
    ``updated_at`` or ``last_change_summary``), never content loaded earlier.

    A stored status the history does not end in is recorded first (see
    ``record_unrecorded_status``).

    When ``new_status`` is the stored status, nothing is changed. If the
    history did not end in it, the history is recorded and the result is True;
    with a complete history nothing is written and the result is False. So the
    result is True exactly when something was written.

    Any other change must be in ``TRANSITIONS`` (checked against the status
    read under the lock) or ``TransitionNotAllowed`` is raised and nothing is
    written, not even the history recorded first. ``owner_edit=True`` also
    allows the reset of an approved service after an owner edit.
    """
    from apps.submissions.models import (
        ServiceSubmission,
        SubmissionStatus,
        SubmissionStatusEvent,
    )

    new_status = str(new_status)
    if new_status not in SubmissionStatus.values:
        raise ValueError(f"Unknown submission status: {new_status!r}")
    with transaction.atomic():
        current, version = (
            ServiceSubmission.objects.select_for_update()
            .values_list("status", "version")
            .get(pk=submission.pk)
        )
        now = timezone.now()
        recorded = record_unrecorded_status(submission.pk, current, now)
        if current == new_status:
            submission.status = current
            return recorded
        if new_status not in allowed_transitions(current, owner_edit=owner_edit):
            labels = dict(SubmissionStatus.choices)
            raise TransitionNotAllowed(
                f"A service cannot change from {labels[current]} "
                f"to {labels[new_status]}."
            )
        # The history stays in time order: an event replayed now or earlier
        # can carry a later time than ``now`` (clocks differ between hosts).
        newest = SubmissionStatusEvent.objects.filter(
            submission_id=submission.pk
        ).aggregate(newest=Max("changed_at"))["newest"]
        if newest is not None:
            now = max(now, newest)
        submission.version = version
        submission.status = new_status
        fields = ["status", *extra_fields]
        if new_status != SubmissionStatus.APPROVED:
            submission.primary_maturity_tag = None
            submission.secondary_maturity_tags = []
            fields += ["primary_maturity_tag", "secondary_maturity_tags"]
        submission.save(update_fields=list(dict.fromkeys(fields)))
        SubmissionStatusEvent.objects.create(
            submission=submission,
            from_status=current,
            to_status=new_status,
            changed_at=now,
            changed_by=actor,
        )
    return True
