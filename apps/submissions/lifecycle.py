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


def set_status(submission, new_status, *, actor: str, extra_fields=()) -> bool:
    """
    Change a submission's status and record the change. The only status writer.

    Locks the row, reads the current status from the database (not from the
    possibly stale instance), saves ``status`` plus ``extra_fields`` and writes
    one SubmissionStatusEvent, all in one transaction, then lets the publishing
    layer react (apps.catalogue.publishing.on_status_change). Maturity tags are
    only valid on approved services, so any other status clears them. ``updated_at``
    is only written when it is listed in ``extra_fields``.

    When ``new_status`` is the stored status, nothing is changed and the result
    is False; otherwise the result is True.
    """
    from django.db import transaction
    from django.utils import timezone

    from apps.catalogue.publishing import on_status_change
    from apps.submissions.models import (
        ServiceSubmission,
        SubmissionStatus,
        SubmissionStatusEvent,
    )

    new_status = str(new_status)
    if new_status not in SubmissionStatus.values:
        raise ValueError(f"Unknown submission status: {new_status!r}")
    with transaction.atomic():
        current = (
            ServiceSubmission.objects.select_for_update()
            .values_list("status", flat=True)
            .get(pk=submission.pk)
        )
        now = timezone.now()
        if current == new_status:
            submission.status = current
            return False
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
        # Inside the same transaction: a failing publish undoes the status change.
        on_status_change(submission, new_status, actor=actor)
    return True
