"""
Edits of a service
==================
The one place that stores an edit of a service. The owner's web form, the
API PATCH and the admin change form each validate their input and answer the
request; ``apply_edit`` does the rest, in one transaction:

  1. runs the caller's ``store`` step, which saves the content and the links
     (the caller has put the version its form or request was loaded with on
     the instance, so a stale save is refused with ``EditConflict``);
  2. works out what changed;
  3. for an owner edit, sends the service back to review (see below);
  4. publishes again when the service stays approved;
  5. writes the change log.

An owner edit of an approved service resets the status to submitted when a
field outside ``SUBMISSION_NO_RESET_FIELDS`` changed (any field when that list
is empty). An owner edit of a rejected service resets it for any real change,
as the owner has answered the rejection. An admin save never resets the
status; the status buttons and bulk actions of the admin do not use this
module.

What the callers do with the result (messages, e-mails after the commit,
the response) stays with them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from django.db import transaction
from django.utils.timezone import now

from apps.catalogue.publishing import republish

from .diff_utils import build_diff, snapshot, snapshot_m2m
from .lifecycle import get_no_reset_fields, set_status
from .models import SubmissionChangeLog, SubmissionStatus


@dataclass(frozen=True)
class EditResult:
    """What an edit changed: the logged field changes (the reset included) and
    whether the owner edit sent the service back to review."""

    changes: list[dict]
    status_reset: bool


def capture(instance) -> dict:
    """Snapshot of the diffable fields and links of ``instance``.

    Take it before the edit touches the instance (a form's validation already
    writes its values to it) and hand it to ``apply_edit`` as ``before``.
    """
    return {**snapshot(instance), **snapshot_m2m(instance)}


def _resets_status(status: str, changes: list[dict]) -> bool:
    if not changes:
        return False
    if status == SubmissionStatus.REJECTED:
        return True
    if status != SubmissionStatus.APPROVED:
        return False
    exempt = get_no_reset_fields()
    return bool({c["field"] for c in changes} - exempt) or not exempt


def record_change(instance, actor: str, changes: list[dict]) -> None:
    """Write the change-log entry of an edit and keep the same summary on the
    service. Call it inside the transaction of the edit."""
    changed_at = now()
    instance.last_change_summary = {
        "changed_by": actor,
        "changed_at": changed_at.isoformat(),
        "changes": changes,
    }
    instance.save(update_fields=["last_change_summary"])
    SubmissionChangeLog.objects.create(
        submission=instance,
        changed_by=actor,
        changed_at=changed_at,
        changes=changes,
    )


def apply_edit(
    instance,
    *,
    before: dict,
    store: Callable[[], None],
    actor: str,
    by_owner: bool,
    refine_changes: Callable[[list[dict]], list[dict]] | None = None,
) -> EditResult:
    """Store an edit of ``instance`` and everything that follows from it.

    ``before`` is ``capture(instance)`` from before the edit. ``store`` saves
    the content and the links (``instance.save()`` plus the link rows, or a
    serializer's ``save()``); it runs inside the transaction and its
    ``EditConflict`` undoes the edit. ``actor`` is recorded on the status
    event, the published revision and the change log. ``by_owner`` selects the
    reset rules: an admin save never resets the status.

    ``refine_changes`` may drop entries from the logged changes (the web form
    drops differences that only come from cleaning input the owner did not
    touch). It does not affect whether the status is reset.
    """
    status = instance.status
    with transaction.atomic():
        store()
        changes = build_diff(before, capture(instance))
        status_reset = by_owner and _resets_status(status, changes)
        if status_reset:
            set_status(
                instance,
                SubmissionStatus.SUBMITTED,
                actor=actor,
                extra_fields=["updated_at"],
                owner_edit=True,
            )
            # The status change belongs to the logged edit.
            changes = build_diff(before, capture(instance))
        # No-op unless the service is still approved (a reset leaves it
        # submitted): an edit that keeps it approved goes live at once.
        republish(
            instance,
            reason="live_edit" if by_owner else "admin_edit",
            actor=actor,
        )
        if refine_changes is not None:
            changes = refine_changes(changes)
        if changes:
            record_change(instance, actor, changes)
    return EditResult(changes=changes, status_reset=status_reset)
