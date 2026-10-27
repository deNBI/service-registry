"""Reconstruct status history for submissions created before it was recorded.

Uses historical models only. Labels in SubmissionChangeLog are mapped through
a frozen copy of the status labels, so later label changes cannot alter this
migration. Synthetic rows (is_synthetic=True) close gaps in the reconstructed
history and are never used as published dates. Output is aggregate counts only.
"""

from datetime import timedelta

from django.db import migrations

LABEL_TO_CODE = {
    "Draft": "draft",
    "Submitted": "submitted",
    "Under Review": "under_review",
    "Approved": "approved",
    "Rejected": "rejected",
    "Deprecated": "deprecated",
}
CODES = frozenset(LABEL_TO_CODE.values())
SYSTEM = "system"


def _code(value):
    """Status code for a change-log value (a label, or already a code); None
    for anything else, including values that are not text."""
    if not isinstance(value, str):
        return None
    if value in CODES:
        return value
    return LABEL_TO_CODE.get(value)


def _entries(changes, counts):
    """The readable entries of a change-log row. Rows written by older tools
    or scripts may hold something else; those are counted and skipped, so they
    cannot stop the migration."""
    if not isinstance(changes, list):
        counts["unreadable_entries"] += 1
        return []
    readable = [change for change in changes if isinstance(change, dict)]
    if len(readable) != len(changes):
        counts["unreadable_entries"] += 1
    return readable


def _event(Event, sub, frm, to, at, by, synthetic):
    return Event(
        submission_id=sub.pk,
        from_status=frm,
        to_status=to,
        changed_at=at,
        changed_by=by,
        source="backfill",
        is_synthetic=synthetic,
    )


def _close_history(Event, sub, events):
    """Add a synthetic event when the replay does not end at the current status."""
    last = events[-1]
    if last.to_status != sub.status:
        at = max(last.changed_at, sub.updated_at)
        events.append(_event(Event, sub, last.to_status, sub.status, at, SYSTEM, True))


def backfill(apps, schema_editor):
    Submission = apps.get_model("submissions", "ServiceSubmission")
    ChangeLog = apps.get_model("submissions", "SubmissionChangeLog")
    Event = apps.get_model("submissions", "SubmissionStatusEvent")

    have_history = set(Event.objects.values_list("submission_id", flat=True))
    counts = {
        "submissions": 0,
        "events": 0,
        "synthetic": 0,
        "unknown_labels": 0,
        "unreadable_entries": 0,
    }
    to_create = []
    for sub in Submission.objects.order_by("submitted_at").iterator():
        if sub.pk in have_history:
            continue
        events = [
            _event(Event, sub, None, "submitted", sub.submitted_at, "system", False)
        ]
        logs = ChangeLog.objects.filter(submission_id=sub.pk).order_by(
            "changed_at", "id"
        )
        for log in logs:
            for change in _entries(log.changes, counts):
                if change.get("field") != "status":
                    continue
                old = _code(change.get("old"))
                new = _code(change.get("new"))
                if old is None or new is None:
                    counts["unknown_labels"] += 1
                    continue
                prev = events[-1]
                if old != prev.to_status:
                    # Never earlier than the event it follows, so the order of
                    # the rows stays the order of the transitions.
                    gap_at = max(prev.changed_at, log.changed_at - timedelta(seconds=1))
                    events.append(
                        _event(Event, sub, prev.to_status, old, gap_at, SYSTEM, True)
                    )
                events.append(
                    _event(Event, sub, old, new, log.changed_at, log.changed_by, False)
                )
        _close_history(Event, sub, events)
        if events[-1].to_status != sub.status:
            raise RuntimeError(
                f"status history invariant violated for submission {sub.pk}"
            )
        counts["submissions"] += 1
        counts["events"] += len(events)
        counts["synthetic"] += sum(1 for e in events if e.is_synthetic)
        to_create.extend(events)
    Event.objects.bulk_create(to_create)
    if counts["submissions"]:
        print(f"  status history backfill: {counts}")


def unbackfill(apps, schema_editor):
    """Remove every status event, not only the backfilled ones.

    Events written since the forward step continue the backfilled rows; kept
    alone they would start mid-way and leave a history that is not one chain.
    The forward step rebuilds the history of every service from the change
    log, so reversing and applying again gives a consistent history.
    """
    Event = apps.get_model("submissions", "SubmissionStatusEvent")
    Event.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("submissions", "0020_status_events")]
    operations = [migrations.RunPython(backfill, unbackfill)]
