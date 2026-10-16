"""Status changes as the change log records them.

Every release writes a change-log entry for each status change, with the old
and new status as display labels (older tools and scripts may have written
status values). The status history is rebuilt from these entries when an
earlier release changed statuses without recording events (see
``lifecycle.record_unrecorded_status``). Migration 0022 reads the log the same
way with its own frozen copy of these rules; tests keep the two in agreement.
"""

from apps.submissions.models import SubmissionStatus


def status_code(value):
    """Status value for a change-log value (a label or a status value), or
    None when it is neither."""
    if not isinstance(value, str):
        return None
    if value in SubmissionStatus.values:
        return value
    for status in SubmissionStatus:
        if value == str(status.label):
            return status.value
    return None


def status_transitions(changes) -> list[tuple[str, str]]:
    """(old, new) status values of the status entries in one change-log row,
    in the order listed.

    Rows written by older tools or scripts may hold something other than a
    list of entries; items that are not entries, entries without a readable
    old and new status, and other fields are skipped.
    """
    if not isinstance(changes, list):
        return []
    transitions = []
    for change in changes:
        if not isinstance(change, dict) or change.get("field") != "status":
            continue
        old, new = status_code(change.get("old")), status_code(change.get("new"))
        if old is not None and new is not None:
            transitions.append((old, new))
    return transitions
