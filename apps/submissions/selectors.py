"""Read-only queries over submission status history."""

from django.db.models import OuterRef, Subquery

from apps.submissions.models import (
    ServiceSubmission,
    SubmissionStatusEvent,
)


def status_history_drift() -> list[tuple[str, str, str | None]]:
    """(id, status, latest event status) for every submission whose latest
    status event does not match its status (None: no event at all).

    "Latest" is the last row written (highest id): wall-clock times can step
    backwards, row ids cannot."""
    latest = (
        SubmissionStatusEvent.objects.filter(submission=OuterRef("pk"))
        .order_by("-id")
        .values("to_status")[:1]
    )
    rows = ServiceSubmission.objects.annotate(latest=Subquery(latest)).values_list(
        "pk", "status", "latest"
    )
    return [(str(pk), status, last) for pk, status, last in rows if status != last]


def status_history_broken() -> list[str]:
    """Ids of submissions whose status events do not form one chain: the first
    event has no from-status and every later event starts at the status the
    previous one ended in (rows taken in the order they were written).
    Submissions without any event are reported by ``status_history_drift``."""
    broken = {}  # ids in the order found, each once
    previous_id, previous_to = None, None
    rows = SubmissionStatusEvent.objects.order_by("submission_id", "id").values_list(
        "submission_id", "from_status", "to_status"
    )
    for submission_id, from_status, to_status in rows.iterator():
        first = submission_id != previous_id
        expected_from = None if first else previous_to
        if from_status != expected_from:
            broken[str(submission_id)] = None
        previous_id, previous_to = submission_id, to_status
    return list(broken)
