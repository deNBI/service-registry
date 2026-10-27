"""Reading status changes out of change-log entries."""

import importlib
from collections import Counter

import pytest

from apps.submissions.models import SubmissionStatus
from apps.submissions.status_log import status_code, status_transitions

MIGRATION = importlib.import_module(
    "apps.submissions.migrations.0021_backfill_status_events"
)


def _status(old, new, **extra):
    return {"field": "status", "label": "Status", "old": old, "new": new, **extra}


class TestStatusCode:
    @pytest.mark.parametrize("status", SubmissionStatus.values)
    def test_a_status_value_is_understood(self, status):
        assert status_code(status) == status

    @pytest.mark.parametrize("status", SubmissionStatus)
    def test_a_status_label_is_understood(self, status):
        assert status_code(str(status.label)) == status.value

    @pytest.mark.parametrize("value", ["Published", "", "approved ", None, 3, ["x"]])
    def test_anything_else_is_unknown(self, value):
        assert status_code(value) is None


class TestStatusTransitions:
    def test_a_status_entry_gives_the_old_and_new_status(self):
        assert status_transitions([_status("Submitted", "Approved")]) == [
            ("submitted", "approved")
        ]

    def test_entries_come_in_the_order_they_are_listed(self):
        changes = [
            _status("Submitted", "Approved"),
            _status("Approved", "Deprecated"),
        ]
        assert status_transitions(changes) == [
            ("submitted", "approved"),
            ("approved", "deprecated"),
        ]

    def test_other_fields_are_ignored(self):
        changes = [
            {"field": "service_name", "label": "Name", "old": "A", "new": "B"},
            _status("Approved", "Submitted"),
            {"field": "primary_maturity_tag", "old": "Mature", "new": "—"},
        ]
        assert status_transitions(changes) == [("approved", "submitted")]

    def test_another_field_holding_status_labels_is_not_a_status_change(self):
        changes = [{"field": "service_category", "old": "Submitted", "new": "Approved"}]
        assert status_transitions(changes) == []

    def test_an_entry_with_an_unknown_label_is_skipped(self):
        changes = [_status("Submitted", "Published"), _status("Approved", "Rejected")]
        assert status_transitions(changes) == [("approved", "rejected")]

    def test_an_entry_without_old_or_new_is_skipped(self):
        changes = [
            {"field": "status", "new": "Approved"},
            {"field": "status", "old": "Approved"},
        ]
        assert status_transitions(changes) == []

    @pytest.mark.parametrize("changes", [None, {}, "Approved", 7, {"field": "status"}])
    def test_a_value_that_is_not_a_list_gives_nothing(self, changes):
        assert status_transitions(changes) == []

    def test_items_that_are_not_entries_are_skipped_not_fatal(self):
        changes = ["status", None, 3, _status("Submitted", "Approved"), ["x"]]
        assert status_transitions(changes) == [("submitted", "approved")]


# One entry of every shape the earlier tools and scripts could have written.
# (A status given as a list or object is left out: the frozen reader fails on
# it, status_code treats it as unknown.)
SHAPES = [
    [_status("Submitted", "Approved")],
    [_status("submitted", "approved")],
    [_status("Approved", "Under Review"), _status("Under Review", "Rejected")],
    [_status("Submitted", "Published")],
    [_status("Nowhere", "Approved")],
    [_status(None, "Approved")],
    [_status("Draft", None)],
    [{"field": "status"}],
    [{"field": "service_name", "old": "A", "new": "B"}],
    [_status("Submitted", "Approved"), "text", None, 4],
    ["text"],
    [],
    None,
    {},
    {"field": "status", "old": "Submitted", "new": "Approved"},
    "Submitted -> Approved",
    7,
    [_status("Deprecated", "Approved", label="Anything")],
    [_status(3, "Approved")],
    [_status(["Submitted"], "Approved")],
    [_status("Submitted", {"label": "Approved"})],
    [_status({"label": "Submitted"}, ["Approved"])],
    [{"field": "service_category", "old": "Submitted", "new": "Approved"}],
]


def _migration_transitions(changes):
    """The same reading as the frozen migration, through its own helpers."""
    counts = Counter()
    result = []
    for change in MIGRATION._entries(changes, counts):
        if change.get("field") != "status":
            continue
        old = MIGRATION._code(change.get("old"))
        new = MIGRATION._code(change.get("new"))
        if old is None or new is None:
            continue
        result.append((old, new))
    return result


class TestAgreementWithTheBackfillMigration:
    """Migration 0021 is frozen and carries its own reader. Both must read
    every entry shape the same way."""

    @pytest.mark.parametrize("changes", SHAPES)
    def test_both_read_the_entry_the_same_way(self, changes):
        assert status_transitions(changes) == _migration_transitions(changes)

    def test_both_know_the_same_labels(self):
        for status in SubmissionStatus:
            assert status_code(str(status.label)) == MIGRATION._code(str(status.label))
        assert dict(MIGRATION.LABEL_TO_CODE) == {
            str(status.label): status.value for status in SubmissionStatus
        }
