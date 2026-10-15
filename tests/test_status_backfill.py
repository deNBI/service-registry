"""Backfill of SubmissionStatusEvent from SubmissionChangeLog."""

import importlib
from datetime import timedelta

import pytest
from django.apps import apps as django_apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

from apps.submissions.models import (
    ServiceSubmission,
    SubmissionChangeLog,
    SubmissionStatusEvent,
)
from tests.factories import ServiceSubmissionFactory

pytestmark = [pytest.mark.django_db, pytest.mark.allow_status_drift]
MIGRATION = importlib.import_module(
    "apps.submissions.migrations.0021_backfill_status_events"
)


def _legacy(status, **kwargs):
    """A submission as it existed before status history: no events."""
    sub = ServiceSubmissionFactory(status=status, biotools_url="", **kwargs)
    SubmissionStatusEvent.objects.filter(submission=sub).delete()
    return sub


def _log(sub, old, new, minutes, by="admin:jane", seconds=0):
    return SubmissionChangeLog.objects.create(
        submission=sub,
        changed_by=by,
        changed_at=sub.submitted_at + timedelta(minutes=minutes, seconds=seconds),
        changes=[{"field": "status", "label": "Status", "old": old, "new": new}],
    )


def _rows(sub):
    return [
        (e.from_status, e.to_status, e.changed_by, e.is_synthetic)
        for e in SubmissionStatusEvent.objects.filter(submission=sub)
    ]


def _run():
    MIGRATION.backfill(django_apps, None)


def test_no_changelog_submitted():
    sub = _legacy("submitted")
    _run()
    assert _rows(sub) == [(None, "submitted", "system", False)]
    event = SubmissionStatusEvent.objects.get(submission=sub)
    assert event.changed_at == sub.submitted_at
    assert event.source == "backfill"


def test_full_history_is_replayed_with_original_actors_and_times():
    sub = _legacy("approved")
    first = _log(sub, "Submitted", "Under Review", 5, by="admin:jane")
    second = _log(sub, "Under Review", "Approved", 10, by="api:Key one")
    _run()
    assert _rows(sub) == [
        (None, "submitted", "system", False),
        ("submitted", "under_review", "admin:jane", False),
        ("under_review", "approved", "api:Key one", False),
    ]
    events = list(SubmissionStatusEvent.objects.filter(submission=sub))
    assert [e.changed_at for e in events[1:]] == [first.changed_at, second.changed_at]
    assert {e.source for e in events} == {"backfill"}


def test_approval_before_logging_is_synthetic():
    sub = _legacy("approved")
    _run()
    assert _rows(sub) == [
        (None, "submitted", "system", False),
        ("submitted", "approved", "system", True),
    ]


def test_gap_before_first_log_entry_is_filled():
    sub = _legacy("submitted")
    _log(sub, "Approved", "Submitted", 30)
    _run()
    assert _rows(sub) == [
        (None, "submitted", "system", False),
        ("submitted", "approved", "system", True),
        ("approved", "submitted", "admin:jane", False),
    ]
    events = list(SubmissionStatusEvent.objects.filter(submission=sub))
    assert events[1].changed_at == events[2].changed_at - timedelta(seconds=1)


def test_gap_event_never_sorts_before_the_event_it_follows():
    sub = _legacy("submitted")
    _log(sub, "Submitted", "Under Review", 5)
    # Next entry 0.5 s later claims it started from Approved: a gap.
    SubmissionChangeLog.objects.create(
        submission=sub,
        changed_by="admin:jane",
        changed_at=sub.submitted_at + timedelta(minutes=5, milliseconds=500),
        changes=[
            {
                "field": "status",
                "label": "Status",
                "old": "Approved",
                "new": "Submitted",
            }
        ],
    )
    _run()
    assert [r[1] for r in _rows(sub)] == [
        "submitted",
        "under_review",
        "approved",
        "submitted",
    ]


def test_unknown_label_is_skipped_and_history_still_closes():
    sub = _legacy("approved")
    _log(sub, "Submitted", "Mystery", 5)
    _run()
    rows = _rows(sub)
    assert rows[-1][1] == "approved"
    assert all(r[1] != "mystery" for r in rows)


@pytest.mark.parametrize(
    "changes",
    [None, "edited by a script", {"field": "status"}, ["status"], [None], []],
    ids=["json_null", "text", "dict_not_list", "list_of_text", "list_of_null", "empty"],
)
def test_an_unreadable_change_log_entry_is_skipped(changes, capsys):
    """Entries written by older tools or scripts must not stop the migration
    (and with it the deploy); the readable entries are still replayed."""
    from django.db.models import JSONField, Value

    sub = _legacy("approved")
    odd = _log(sub, "Submitted", "Under Review", 3)
    SubmissionChangeLog.objects.filter(pk=odd.pk).update(
        changes=Value(changes, JSONField())
    )
    _log(sub, "Under Review", "Approved", 10, by="admin:john")
    _run()
    assert _rows(sub) == [
        (None, "submitted", "system", False),
        ("submitted", "under_review", "system", True),
        ("under_review", "approved", "admin:john", False),
    ]
    out = capsys.readouterr().out
    if changes == []:
        assert "'unreadable_entries': 0" in out
    else:
        assert "'unreadable_entries': 1" in out


@pytest.mark.parametrize(
    "old, new",
    [
        (["Submitted"], "Approved"),
        ("Submitted", ["Approved"]),
        ({"label": "Submitted"}, "Approved"),
        ("Submitted", {"label": "Approved"}),
    ],
    ids=["old_list", "new_list", "old_dict", "new_dict"],
)
def test_a_status_value_that_is_a_list_or_dict_is_an_unknown_label(old, new):
    """The value is not text, so it is skipped like any unknown label; the
    migration neither stops nor reads the entry as a transition."""
    sub = _legacy("approved")
    _log(sub, old, new, 5)
    _run()
    assert _rows(sub) == [
        (None, "submitted", "system", False),
        ("submitted", "approved", "system", True),
    ]


def test_a_status_entry_next_to_an_unreadable_one_is_used():
    sub = _legacy("approved")
    SubmissionChangeLog.objects.create(
        submission=sub,
        changed_by="admin:jane",
        changed_at=sub.submitted_at + timedelta(minutes=5),
        changes=[
            "stray text",
            {
                "field": "status",
                "label": "Status",
                "old": "Submitted",
                "new": "Approved",
            },
        ],
    )
    _run()
    assert _rows(sub) == [
        (None, "submitted", "system", False),
        ("submitted", "approved", "admin:jane", False),
    ]


def test_entries_that_carry_status_codes_are_understood():
    sub = _legacy("approved")
    _log(sub, "submitted", "approved", 5)
    _run()
    assert _rows(sub) == [
        (None, "submitted", "system", False),
        ("submitted", "approved", "admin:jane", False),
    ]


def test_changelog_entries_without_status_changes_are_ignored():
    sub = _legacy("submitted")
    SubmissionChangeLog.objects.create(
        submission=sub,
        changed_by="submitter",
        changed_at=sub.submitted_at + timedelta(minutes=1),
        changes=[
            {"field": "service_name", "label": "Name", "old": "a", "new": "b"},
            {
                "field": "toolbox_name",
                "label": "Toolbox",
                "old": "Submitted",
                "new": "Approved",
            },
        ],
    )
    _run()
    assert _rows(sub) == [(None, "submitted", "system", False)]


def test_status_change_next_to_other_fields_in_one_entry():
    sub = _legacy("approved")
    SubmissionChangeLog.objects.create(
        submission=sub,
        changed_by="admin:jane",
        changed_at=sub.submitted_at + timedelta(minutes=1),
        changes=[
            {"field": "service_name", "label": "Name", "old": "a", "new": "b"},
            {
                "field": "status",
                "label": "Status",
                "old": "Submitted",
                "new": "Approved",
            },
        ],
    )
    _run()
    assert _rows(sub)[-1] == ("submitted", "approved", "admin:jane", False)


def test_deprecated_service():
    sub = _legacy("deprecated")
    _log(sub, "Submitted", "Approved", 5)
    _log(sub, "Approved", "Deprecated", 9)
    _run()
    assert [r[1] for r in _rows(sub)] == ["submitted", "approved", "deprecated"]


def test_status_ping_pong_keeps_every_step():
    sub = _legacy("approved")
    _log(sub, "Submitted", "Approved", 1)
    _log(sub, "Approved", "Submitted", 2)
    _log(sub, "Submitted", "Approved", 3)
    _run()
    assert [r[1] for r in _rows(sub)] == [
        "submitted",
        "approved",
        "submitted",
        "approved",
    ]
    assert not any(r[3] for r in _rows(sub))


def test_close_event_uses_the_later_of_last_event_and_updated_at():
    sub = _legacy("approved")
    _log(sub, "Submitted", "Under Review", 5)
    later = timezone.now() + timedelta(days=1)
    ServiceSubmission.objects.filter(pk=sub.pk).update(updated_at=later)
    _run()
    synthetic = SubmissionStatusEvent.objects.get(submission=sub, is_synthetic=True)
    assert synthetic.changed_at == later


def test_idempotent_and_skips_existing_history():
    sub = _legacy("submitted")
    live = ServiceSubmissionFactory(biotools_url="")
    _run()
    _run()
    assert len(_rows(sub)) == 1
    assert SubmissionStatusEvent.objects.filter(submission=live).count() == 1
    assert SubmissionStatusEvent.objects.get(submission=live).source == "live"


def test_second_run_after_new_submissions_only_adds_missing_history():
    first = _legacy("approved")
    _run()
    count = SubmissionStatusEvent.objects.filter(submission=first).count()
    second = _legacy("submitted")
    _run()
    assert SubmissionStatusEvent.objects.filter(submission=first).count() == count
    assert len(_rows(second)) == 1


def test_reverse_removes_backfill_rows_only():
    sub = _legacy("approved")
    live = ServiceSubmissionFactory(biotools_url="")
    _run()
    MIGRATION.unbackfill(django_apps, None)
    assert SubmissionStatusEvent.objects.filter(submission=sub).count() == 0
    assert SubmissionStatusEvent.objects.filter(submission=live).count() == 1


def test_invariant_failure_raises_and_writes_nothing(monkeypatch):
    sub = _legacy("approved")
    monkeypatch.setattr(MIGRATION, "_close_history", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="status history"):
        _run()
    assert ServiceSubmission.objects.filter(pk=sub.pk).exists()
    assert SubmissionStatusEvent.objects.filter(submission=sub).count() == 0


def test_synthetic_close_time_never_uses_date_of_entry():
    sub = _legacy("approved")
    ServiceSubmission.objects.filter(pk=sub.pk).update(
        date_of_entry=(timezone.now() - timedelta(days=4000)).date()
    )
    _run()
    synthetic = SubmissionStatusEvent.objects.get(submission=sub, is_synthetic=True)
    assert synthetic.changed_at >= sub.submitted_at


def test_latest_event_equals_status_for_mixed_data():
    from tests.helpers import status_history_drift

    for status in ("draft", "submitted", "under_review", "approved", "rejected"):
        _legacy(status)
    odd = _legacy("deprecated")
    _log(odd, "Mystery", "Approved", 3)
    _run()
    assert status_history_drift() == []


def test_output_contains_counts_only(capsys):
    sub = _legacy("approved", service_name="Example Printed Name")
    _run()
    out = capsys.readouterr().out
    assert "submissions" in out
    assert sub.service_name not in out
    assert str(sub.pk) not in out


def test_nothing_is_printed_when_there_is_nothing_to_do(capsys):
    _run()
    assert capsys.readouterr().out == ""


@pytest.mark.django_db(transaction=True)
def test_migration_runs_forwards_and_backwards_on_real_state():
    """Run the migration through the executor, with data present."""
    sub = _legacy("approved")
    _log(sub, "Submitted", "Approved", 5)
    executor = MigrationExecutor(connection)
    before = [("submissions", "0020_status_events")]
    after = [("submissions", "0021_backfill_status_events")]
    try:
        executor.migrate(before)
        assert SubmissionStatusEvent.objects.count() == 0
        executor = MigrationExecutor(connection)
        executor.migrate(after)
        assert [r[1] for r in _rows(sub)] == ["submitted", "approved"]
        executor = MigrationExecutor(connection)
        executor.migrate(before)
        assert SubmissionStatusEvent.objects.count() == 0
    finally:
        MigrationExecutor(connection).migrate(
            MigrationExecutor(connection).loader.graph.leaf_nodes()
        )


@pytest.mark.django_db(transaction=True)
def test_rolling_back_past_the_backfill_leaves_a_history_that_verify_reports():
    """Going back past the backfill removes the reconstructed rows but keeps
    the rows written since; applying it again skips services that have rows, so
    their history no longer starts at the creation."""
    from io import StringIO

    from django.core.management import CommandError, call_command

    from apps.submissions.lifecycle import set_status
    from apps.submissions.selectors import status_history_broken

    sub = _legacy("approved")
    _log(sub, "Submitted", "Approved", 5)
    before = [("submissions", "0020_status_events")]
    after = [("submissions", "0021_backfill_status_events")]

    def leaf():
        return MigrationExecutor(connection).loader.graph.leaf_nodes()

    try:
        MigrationExecutor(connection).migrate(before)
        MigrationExecutor(connection).migrate(after)
        MigrationExecutor(connection).migrate(leaf())
        assert status_history_broken() == []
        set_status(
            ServiceSubmission.objects.get(pk=sub.pk), "rejected", actor="admin:jane"
        )
        assert status_history_broken() == []

        MigrationExecutor(connection).migrate(before)
        MigrationExecutor(connection).migrate(leaf())

        assert [
            (e.from_status, e.to_status)
            for e in SubmissionStatusEvent.objects.filter(submission=sub)
        ] == [("approved", "rejected")]
        assert status_history_broken() == [str(sub.pk)]
        out = StringIO()
        with pytest.raises(CommandError, match="status_history_broken=1"):
            call_command("verify_published", stdout=out)
        assert str(sub.pk) in out.getvalue()
    finally:
        MigrationExecutor(connection).migrate(leaf())


@pytest.mark.django_db(transaction=True)
def test_rolling_back_the_publishing_tables_loses_the_revision_history():
    """Going back past catalogue 0001 drops every revision. Applying the
    migrations again publishes approved services once more from their working
    copy, as a single backfill revision."""
    from io import StringIO

    from django.core.management import call_command
    from django.db import connection as conn

    from apps.catalogue import publishing
    from apps.catalogue.models import PublishedRevision, PublishedService
    from apps.submissions.lifecycle import set_status

    sub = ServiceSubmissionFactory(biotools_url="")
    set_status(sub, "approved", actor="admin:jane")
    ServiceSubmission.objects.filter(pk=sub.pk).update(service_name="Example Edited")
    publishing.republish(sub, reason="admin_edit", actor="admin:jane")
    assert PublishedRevision.objects.filter(submission=sub).count() == 2
    table = PublishedService._meta.db_table

    def leaf():
        return MigrationExecutor(connection).loader.graph.leaf_nodes()

    try:
        MigrationExecutor(connection).migrate([("catalogue", None)])
        assert table not in conn.introspection.table_names()
        MigrationExecutor(connection).migrate(leaf())
        assert table in conn.introspection.table_names()
        revisions = PublishedRevision.objects.filter(submission=sub)
        assert [(r.number, r.reason) for r in revisions] == [(1, "backfill")]
        assert PublishedService.objects.get(pk=sub.pk).service_name == "Example Edited"
        out = StringIO()
        call_command("verify_published", stdout=out)
        assert "0 problems" in out.getvalue()
    finally:
        MigrationExecutor(connection).migrate(leaf())


@pytest.mark.django_db(transaction=True)
def test_rolling_back_the_release_and_applying_it_again_rebuilds_a_clean_state():
    """Rollback for this release: catalogue back to zero and submissions back
    to 0019, then the previous code runs. That code records status changes in
    the change log only. Applying the migrations again rebuilds the status
    history from the change log and publishes from the current content, and
    the result passes verify_published."""
    from io import StringIO

    from django.core.management import call_command

    from apps.catalogue.models import PublishedService
    from apps.submissions.lifecycle import set_status

    def leaf():
        return MigrationExecutor(connection).loader.graph.leaf_nodes()

    live = ServiceSubmissionFactory(biotools_url="")
    set_status(live, "approved", actor="admin:jane")
    waiting = ServiceSubmissionFactory(biotools_url="")
    try:
        MigrationExecutor(connection).migrate(
            [("catalogue", None), ("submissions", "0019_logo_help_text")]
        )
        # The previous code changes two statuses: row update plus change log.
        later = timezone.now() + timedelta(minutes=5)
        for sub, old, new, code in (
            (waiting, "Submitted", "Approved", "approved"),
            (live, "Approved", "Deprecated", "deprecated"),
        ):
            ServiceSubmission.objects.filter(pk=sub.pk).update(status=code)
            SubmissionChangeLog.objects.create(
                submission_id=sub.pk,
                changed_by="admin:john",
                changed_at=later,
                changes=[
                    {"field": "status", "label": "Status", "old": old, "new": new}
                ],
            )
        MigrationExecutor(connection).migrate(leaf())
        assert _rows(waiting)[-1] == ("submitted", "approved", "admin:john", False)
        assert _rows(live)[-1] == ("approved", "deprecated", "admin:john", False)
        assert PublishedService.objects.get(pk=waiting.pk).state == "active"
        assert PublishedService.objects.get(pk=live.pk).state == "deprecated"
        out = StringIO()
        call_command("verify_published", stdout=out)
        assert "0 problems" in out.getvalue()
    finally:
        MigrationExecutor(connection).migrate(leaf())
