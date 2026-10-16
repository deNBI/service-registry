"""Concurrent writers on a real PostgreSQL server.

SQLite ignores row locks, so the other tests can only check that a lock is
requested. Here one connection keeps a transaction open (its row lock held)
while a second connection, in another thread, runs a real code path on the
same service. Each test checks that both changes are kept, or that the
second one is refused when it was made from an outdated copy. For a
published service it also checks the published copy: nothing unreviewed goes
public, no state or revision is lost, and a deleted service leaves a
tombstone.

Run with ``make test-postgres`` (needs a PostgreSQL server, see
config/settings_test_postgres.py).
"""

import importlib
import threading
import time
from contextlib import contextmanager
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection, transaction
from django.db.migrations.loader import MigrationLoader
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from apps.catalogue import publishing
from apps.catalogue.management.commands.publish_backfill import Command
from apps.catalogue.models import PublishedRevision, PublishedService, WithdrawnService
from apps.submissions.edits import apply_edit, capture
from apps.submissions.lifecycle import set_status
from apps.submissions.models import (
    EditConflict,
    ServiceSubmission,
    SubmissionChangeLog,
)
from apps.submissions.selectors import status_history_broken
from tests.factories import APIKeyFactory, ServiceSubmissionFactory
from tests.helpers import edit_form_data, edit_form_payload
from tests.test_concurrency import _grant, _hidden_value
from tests.test_publishing_admin import _ask, _confirm, _messages

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(transaction=True)]

CHANGELIST = "admin:submissions_servicesubmission_changelist"
CHANGE = "admin:submissions_servicesubmission_change"
WAIT = 10  # seconds; generous, only reached when something hangs
DESCRIPTION = "A new description that is long enough for the fifty character rule."


def _row(sub):
    return ServiceSubmission.objects.get(pk=sub.pk)


def _thread(fn):
    """Run ``fn`` in a new thread (its own database connection)."""
    result = {}

    def run():
        try:
            result["value"] = fn()
        except BaseException as exc:  # handed to the test thread
            result["error"] = exc
        finally:
            connection.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, result


@contextmanager
def holding(fn):
    """Run ``fn`` in another connection and keep its transaction, and the row
    locks it took, open until the block ends; then commit."""
    ready, release = threading.Event(), threading.Event()

    def run():
        with transaction.atomic():
            fn()
            ready.set()
            release.wait(WAIT)

    thread, result = _thread(run)
    try:
        assert ready.wait(WAIT), result.get("error")
        yield
    finally:
        release.set()
        thread.join(WAIT)
    if "error" in result:
        raise result["error"]


def _blocked_on_a_lock(thread):
    """True once another session of this database waits for a lock."""
    deadline = time.monotonic() + WAIT
    while time.monotonic() < deadline and thread.is_alive():
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )
            if cursor.fetchone()[0]:
                return True
        time.sleep(0.02)
    return False


def _second_writer(fn):
    """Start ``fn`` while a ``holding`` block is open; it must wait for the lock."""
    thread, result = _thread(fn)
    assert _blocked_on_a_lock(thread), result.get("error", result)
    return thread, result


def _finish(thread, result):
    thread.join(WAIT)
    assert not thread.is_alive()
    return result


def _comment_by_jane(sub):
    """Another request saves a fresh copy of the service."""
    other = _row(sub)
    other.comments = "Changed by Jane"
    other.save()


def _service(**kw):
    return ServiceSubmissionFactory(biotools_url="", **kw)


def test_two_copies_of_the_same_version_save_only_once():
    sub = _service(status="approved")
    first, second = _row(sub), _row(sub)
    first.comments = "Changed by Jane"
    second.comments = "Changed by John"
    with holding(first.save):
        writer = _second_writer(second.save)
    assert isinstance(_finish(*writer).get("error"), EditConflict)
    assert _row(sub).comments == "Changed by Jane"


def test_stale_edit_page_is_refused_after_waiting(client):
    sub = _service(status="approved")
    _grant(client, sub)
    url = reverse("submissions:edit", args=[sub.pk])
    data = edit_form_data(sub, comments="Changed by John")
    data["loaded_version"] = _hidden_value(client.get(url), "loaded_version")
    with holding(lambda: _comment_by_jane(sub)):
        writer = _second_writer(lambda: client.post(url, data))
    assert _finish(*writer)["value"].status_code == 409
    assert _row(sub).comments == "Changed by Jane"


@pytest.mark.parametrize(
    "action,start,end",
    [
        ("action_approve", "submitted", "approved"),
        ("action_deprecate", "approved", "deprecated"),
    ],
)
def test_bulk_status_action_waits_and_keeps_the_other_change(
    superuser_client, action, start, end
):
    sub = _service(status=start)
    with holding(lambda: _comment_by_jane(sub)):
        writer = _second_writer(
            lambda: superuser_client.post(
                reverse(CHANGELIST),
                {"action": action, "_selected_action": [str(sub.pk)]},
            )
        )
    assert _finish(*writer)["value"].status_code == 302
    row = _row(sub)
    assert (row.status, row.comments) == (end, "Changed by Jane")


def test_owner_deprecation_waits_and_keeps_the_other_change(client):
    sub = _service(status="approved")
    _grant(client, sub)
    url = reverse("submissions:edit", args=[sub.pk])
    with holding(lambda: _comment_by_jane(sub)):
        writer = _second_writer(lambda: client.post(url, {"_deprecate": "1"}))
    assert _finish(*writer)["value"].status_code == 302
    row = _row(sub)
    assert (row.status, row.comments) == ("deprecated", "Changed by Jane")


def test_api_patch_waits_and_keeps_the_other_change():
    sub = _service(status="approved")
    _, key = APIKeyFactory.create_with_plaintext(submission=sub, scope="write")
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"ApiKey {key}")
    with holding(lambda: _comment_by_jane(sub)):
        writer = _second_writer(
            lambda: api.patch(
                f"/api/v1/submissions/{sub.pk}/",
                {"service_description": DESCRIPTION},
                format="json",
            )
        )
    response = _finish(*writer)["value"]
    assert response.status_code == 200, response.content
    row = _row(sub)
    assert (row.service_description, row.comments) == (DESCRIPTION, "Changed by Jane")


def test_api_patch_with_if_match_waits_and_is_refused_after_a_change():
    sub = _service(status="approved")
    _, key = APIKeyFactory.create_with_plaintext(submission=sub, scope="write")
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"ApiKey {key}")
    tag = api.get(f"/api/v1/submissions/{sub.pk}/")["ETag"]
    with holding(lambda: _comment_by_jane(sub)):
        writer = _second_writer(
            lambda: api.patch(
                f"/api/v1/submissions/{sub.pk}/",
                {"service_description": DESCRIPTION},
                format="json",
                HTTP_IF_MATCH=tag,
            )
        )
    assert _finish(*writer)["value"].status_code == 412
    row = _row(sub)
    assert row.comments == "Changed by Jane"
    assert row.service_description != DESCRIPTION


def _transitions(sub):
    return [(e.from_status, e.to_status) for e in sub.status_events.order_by("id")]


def test_two_parallel_status_changes_form_one_history():
    sub = _service()
    with holding(lambda: set_status(_row(sub), "under_review", actor="admin:jane")):
        writer = _second_writer(
            lambda: set_status(_row(sub), "approved", actor="admin:john")
        )
    assert _finish(*writer)["value"] is True
    assert _row(sub).status == "approved"
    assert _transitions(sub) == [
        (None, "submitted"),
        ("submitted", "under_review"),
        ("under_review", "approved"),
    ]
    assert status_history_broken() == []


def test_the_same_approval_twice_records_one_event():
    sub = _service()
    with holding(lambda: set_status(_row(sub), "approved", actor="admin:jane")):
        writer = _second_writer(
            lambda: set_status(_row(sub), "approved", actor="admin:john")
        )
    assert _finish(*writer)["value"] is False
    assert _transitions(sub) == [(None, "submitted"), ("submitted", "approved")]
    assert sub.status_events.latest("id").changed_by == "admin:jane"


# ---------------------------------------------------------------------------
# The published copy
#
# Every writer of the published copy first takes the row lock of the service.
# These tests keep one of the writers inside its transaction and check what
# the one that waits leaves behind: no unreviewed content made public, no
# lost state, one revision per change, a tombstone for a deleted service.
# ---------------------------------------------------------------------------


def _published_service(**kw):
    sub = _service(**kw)
    set_status(sub, "approved", actor="admin:jane")
    return sub


def _verify():
    out = StringIO()
    call_command("verify_published", stdout=out)
    return out.getvalue()


def _rename(sub, name):
    """Change the working copy without publishing and without a new version
    (a committed edit whose own publish step has not run yet)."""
    ServiceSubmission.objects.filter(pk=sub.pk).update(service_name=name)


def _admin_edit(sub, name):
    """An admin edit saved through apply_edit: it raises the version and
    publishes only when the service is approved."""
    row = _row(sub)
    before = capture(row)

    def store():
        row.service_name = name
        row.save()

    apply_edit(row, before=before, store=store, actor="admin:jane", by_owner=False)


def _rejected_edit(sub, name):
    """The owner edits a published service and review rejects the edit: the
    working copy holds ``name``, the published copy does not."""
    set_status(_row(sub), "submitted", actor="submitter", owner_edit=True)
    row = _row(sub)
    row.service_name = name
    row.save()
    set_status(_row(sub), "rejected", actor="admin:jane")


def _no_rows_left(sub):
    """Nothing refers to the deleted service any more."""
    return not (
        ServiceSubmission.objects.filter(pk=sub.pk).exists()
        or sub.status_events.exists()
        or PublishedRevision.objects.filter(submission=sub).exists()
        or PublishedService.objects.filter(pk=sub.pk).exists()
    )


def _tombstones():
    return list(WithdrawnService.objects.values_list("pk", flat=True))


def test_the_same_approval_twice_publishes_once():
    sub = _service()
    with holding(lambda: set_status(_row(sub), "approved", actor="admin:jane")):
        writer = _second_writer(
            lambda: set_status(_row(sub), "approved", actor="admin:john")
        )
    assert _finish(*writer)["value"] is False
    assert PublishedRevision.objects.filter(submission=sub).count() == 1
    assert "0 problems" in _verify()


def test_a_status_change_makes_a_stale_save_of_a_published_service_fail():
    sub = _published_service()
    loaded = _row(sub)  # an edit page loaded while the service is approved
    loaded.service_name = "Example Stale Edit"
    with holding(lambda: set_status(_row(sub), "under_review", actor="admin:jane")):
        writer = _second_writer(loaded.save)
    assert isinstance(_finish(*writer).get("error"), EditConflict)
    row = _row(sub)
    assert row.status == "under_review"
    assert row.service_name != "Example Stale Edit"
    assert PublishedService.objects.get(pk=sub.pk).service_name != "Example Stale Edit"


def test_a_save_of_a_service_deleted_meanwhile_is_refused():
    sub = _published_service()
    loaded = _row(sub)
    with holding(lambda: ServiceSubmission.objects.filter(pk=sub.pk).delete()):
        writer = _second_writer(loaded.save)
    assert isinstance(_finish(*writer).get("error"), EditConflict)
    assert _no_rows_left(sub)
    assert _tombstones() == [sub.pk]


def test_parallel_changes_after_an_unrecorded_status_record_it_once():
    sub = _service()
    ServiceSubmission.objects.filter(pk=sub.pk).update(status="under_review")
    with holding(lambda: set_status(_row(sub), "approved", actor="admin:jane")):
        writer = _second_writer(
            lambda: set_status(_row(sub), "deprecated", actor="admin:john")
        )
    assert "error" not in _finish(*writer)
    assert _transitions(sub) == [
        (None, "submitted"),
        ("submitted", "under_review"),
        ("under_review", "approved"),
        ("approved", "deprecated"),
    ]
    assert sub.status_events.filter(is_synthetic=True).count() == 1
    assert PublishedService.objects.get(pk=sub.pk).state == "deprecated"
    assert "0 problems" in _verify()


def test_parallel_publishes_of_the_same_content_write_one_revision():
    sub = _published_service()
    _rename(sub, "Example Second Version")

    def publish():
        return publishing.publish(_row(sub), reason="admin_edit", actor="admin:jane")

    with holding(publish):
        writer = _second_writer(publish)
    result = _finish(*writer)
    assert "error" not in result and result["value"] is None
    numbers = PublishedRevision.objects.filter(submission=sub).values_list(
        "number", flat=True
    )
    assert sorted(numbers) == [1, 2]
    assert PublishedService.objects.get(pk=sub.pk).revision.number == 2
    assert "0 problems" in _verify()


def test_an_edit_published_after_review_started_stays_private():
    """The edit committed its content; its publish step runs after an admin
    moved the service to review: nothing unreviewed goes public."""
    sub = _published_service(service_name="Example Reviewed Name")
    _rename(sub, "Example Unreviewed Name")
    with holding(lambda: set_status(_row(sub), "under_review", actor="admin:jane")):
        writer = _second_writer(
            lambda: publishing.republish(
                _row(sub), reason="live_edit", actor="submitter"
            )
        )
    assert _finish(*writer)["value"] is None
    copy = PublishedService.objects.get(pk=sub.pk)
    assert copy.service_name == "Example Reviewed Name"
    assert copy.pending_update is True


def test_a_state_change_is_not_lost_to_a_parallel_publish():
    """A publish writes the whole published row; without the lock of the
    state change it could write back the state it read before it."""
    sub = _published_service()
    _rename(sub, "Example Second Version")
    with holding(
        lambda: publishing.set_publication_state(
            sub.pk, state=PublishedService.DEPRECATED
        )
    ):
        writer = _second_writer(
            lambda: publishing.publish(_row(sub), reason="admin_edit", actor="admin:a")
        )
    assert "error" not in _finish(*writer)
    copy = PublishedService.objects.get(pk=sub.pk)
    assert (copy.state, copy.service_name) == ("deprecated", "Example Second Version")


def test_a_rebuild_uses_the_revision_published_meanwhile():
    sub = _published_service()
    _rename(sub, "Example Second Version")
    with holding(
        lambda: publishing.publish(_row(sub), reason="admin_edit", actor="admin:jane")
    ):
        writer = _second_writer(lambda: publishing.rebuild_from_revision(sub.pk))
    assert "error" not in _finish(*writer)
    copy = PublishedService.objects.get(pk=sub.pk)
    assert (copy.revision.number, copy.service_name) == (2, "Example Second Version")
    assert "0 problems" in _verify()


def test_a_delete_during_the_first_publish_leaves_a_tombstone():
    sub = _service(status="approved")
    with holding(
        lambda: publishing.publish(_row(sub), reason="approval", actor="admin:jane")
    ):
        writer = _second_writer(lambda: _row(sub).delete())
    assert "error" not in _finish(*writer)
    assert _no_rows_left(sub)
    assert _tombstones() == [sub.pk]


def test_a_bulk_delete_during_the_first_publish_leaves_a_tombstone():
    sub = _service(status="approved")
    other = _service(status="approved")
    with holding(
        lambda: publishing.publish(_row(sub), reason="approval", actor="admin:jane")
    ):
        writer = _second_writer(
            lambda: ServiceSubmission.objects.filter(pk__in=[sub.pk, other.pk]).delete()
        )
    assert "error" not in _finish(*writer)
    assert not ServiceSubmission.objects.filter(pk__in=[sub.pk, other.pk]).exists()
    assert _tombstones() == [sub.pk]


def test_a_delete_during_a_status_change_removes_everything():
    sub = _published_service()
    with holding(lambda: set_status(_row(sub), "under_review", actor="admin:jane")):
        writer = _second_writer(lambda: _row(sub).delete())
    assert "error" not in _finish(*writer)
    assert _no_rows_left(sub)
    assert _tombstones() == [sub.pk]


def test_a_bulk_delete_during_a_status_change_removes_everything():
    sub = _published_service()
    with holding(lambda: set_status(_row(sub), "under_review", actor="admin:jane")):
        writer = _second_writer(
            lambda: ServiceSubmission.objects.filter(pk=sub.pk).delete()
        )
    assert "error" not in _finish(*writer)
    assert _no_rows_left(sub)
    assert _tombstones() == [sub.pk]


def test_a_status_change_after_a_delete_writes_nothing():
    sub = _published_service()
    with holding(lambda: _row(sub).delete()):
        writer = _second_writer(
            lambda: set_status(_row(sub), "under_review", actor="admin:jane")
        )
    assert isinstance(_finish(*writer).get("error"), ServiceSubmission.DoesNotExist)
    assert _no_rows_left(sub)


# ---------------------------------------------------------------------------
# publish_backfill and the catalogue migration
# ---------------------------------------------------------------------------


def _publish_existing():
    """Run the data migration that publishes existing services."""
    migration = importlib.import_module(
        "apps.catalogue.migrations.0002_publish_existing_services"
    )
    historical = (
        MigrationLoader(None, ignore_no_migrations=True)
        .project_state(("catalogue", "0002_publish_existing_services"))
        .apps
    )
    with transaction.atomic():
        migration.publish_existing(historical, None)


def _backfill():
    call_command("publish_backfill", stdout=StringIO())


def test_publish_backfill_skips_a_service_reset_meanwhile():
    sub = _service(status="approved")

    def edit_and_reset():
        _rename(sub, "Example Unreviewed Name")
        set_status(_row(sub), "submitted", actor="submitter", owner_edit=True)

    with holding(edit_and_reset):
        writer = _second_writer(_backfill)
    assert "error" not in _finish(*writer)
    assert not PublishedService.objects.filter(pk=sub.pk).exists()
    assert not PublishedRevision.objects.filter(submission=sub).exists()


def test_publish_backfill_of_an_unrecorded_approval_then_a_reset_records_it_once():
    """An earlier release approved the service without an event. The backfill
    holds the row: it records the approval and publishes; the reset that
    waited then finds the history complete."""
    sub = _service()
    ServiceSubmission.objects.filter(pk=sub.pk).update(status="approved")
    with holding(lambda: Command()._publish_one(sub.pk)):
        writer = _second_writer(
            lambda: set_status(
                _row(sub), "submitted", actor="submitter", owner_edit=True
            )
        )
    assert "error" not in _finish(*writer)
    assert _transitions(sub) == [
        (None, "submitted"),
        ("submitted", "approved"),
        ("approved", "submitted"),
    ]
    assert PublishedService.objects.get(pk=sub.pk).pending_update is True
    assert "0 problems" in _verify()


def test_a_reset_of_an_unrecorded_approval_makes_the_backfill_skip():
    """The reset records the approval first, then moves the service to review:
    it is no longer approved, so the backfill publishes nothing."""
    sub = _service()
    ServiceSubmission.objects.filter(pk=sub.pk).update(status="approved")
    with holding(
        lambda: set_status(_row(sub), "submitted", actor="submitter", owner_edit=True)
    ):
        writer = _second_writer(lambda: Command()._publish_one(sub.pk))
    assert _finish(*writer)["value"] is False
    assert not PublishedService.objects.filter(pk=sub.pk).exists()
    assert _transitions(sub) == [
        (None, "submitted"),
        ("submitted", "approved"),
        ("approved", "submitted"),
    ]


def test_a_move_out_of_an_unrecorded_deprecation_keeps_the_copy_deprecated():
    """A published service deprecated by an earlier release, sent back to
    submitted while the backfill waits for the lock: the copy follows the
    deprecation and the backfill leaves the published service alone."""
    sub = _published_service()
    ServiceSubmission.objects.filter(pk=sub.pk).update(status="deprecated")
    with holding(lambda: set_status(_row(sub), "submitted", actor="submitter")):
        writer = _second_writer(lambda: Command()._publish_one(sub.pk))
    assert _finish(*writer)["value"] is False
    copy = PublishedService.objects.get(pk=sub.pk)
    assert (copy.state, copy.pending_update) == ("deprecated", True)
    assert "0 problems" in _verify()


def test_the_repair_of_an_unrecorded_deprecation_then_a_move_records_it_once():
    """An earlier release deprecated a published service. The repair holds
    the row: it records the deprecation and deprecates the copy; the move to
    submitted that waited finds the history complete."""
    sub = _published_service()
    ServiceSubmission.objects.filter(pk=sub.pk).update(status="deprecated")
    with holding(lambda: Command()._repair_one(sub.pk)):
        writer = _second_writer(
            lambda: set_status(_row(sub), "submitted", actor="submitter")
        )
    assert "error" not in _finish(*writer)
    assert _transitions(sub) == [
        (None, "submitted"),
        ("submitted", "approved"),
        ("approved", "deprecated"),
        ("deprecated", "submitted"),
    ]
    copy = PublishedService.objects.get(pk=sub.pk)
    assert (copy.state, copy.pending_update) == ("deprecated", True)
    assert "0 problems" in _verify()


def test_a_move_out_of_an_unrecorded_deprecation_makes_the_repair_skip():
    """The move records the earlier release's deprecation first; the repair
    that waited finds nothing left to record and leaves the flags it set."""
    sub = _published_service()
    ServiceSubmission.objects.filter(pk=sub.pk).update(status="deprecated")
    with holding(lambda: set_status(_row(sub), "submitted", actor="submitter")):
        writer = _second_writer(lambda: Command()._repair_one(sub.pk))
    assert _finish(*writer)["value"] is False
    assert _transitions(sub) == [
        (None, "submitted"),
        ("submitted", "approved"),
        ("approved", "deprecated"),
        ("deprecated", "submitted"),
    ]
    copy = PublishedService.objects.get(pk=sub.pk)
    assert (copy.state, copy.pending_update) == ("deprecated", True)
    assert "0 problems" in _verify()


def test_republishing_an_approved_service_skips_one_reset_meanwhile():
    """The public copy is behind the approved working copy. The service is
    edited and sent to review while the backfill waits for the lock: the
    unreviewed text must not go public."""
    sub = _published_service(host_institute="Example Original Institute")
    ServiceSubmission.objects.filter(pk=sub.pk).update(
        host_institute="Example Reviewed Institute"
    )

    def edit_and_reset():
        ServiceSubmission.objects.filter(pk=sub.pk).update(
            host_institute="Example Unreviewed Institute"
        )
        set_status(_row(sub), "submitted", actor="submitter", owner_edit=True)

    with holding(edit_and_reset):
        writer = _second_writer(_backfill)
    assert "error" not in _finish(*writer)
    copy = PublishedService.objects.get(pk=sub.pk)
    assert copy.host_institute == "Example Original Institute"
    assert copy.pending_update is True
    assert PublishedRevision.objects.filter(submission=sub).count() == 1
    assert "0 problems" in _verify()


def test_republishing_an_approved_service_then_a_reset_keeps_it_pending():
    sub = _published_service(host_institute="Example Original Institute")
    ServiceSubmission.objects.filter(pk=sub.pk).update(
        host_institute="Example Reviewed Institute"
    )
    with holding(lambda: Command()._republish_one(sub.pk)):
        writer = _second_writer(
            lambda: set_status(
                _row(sub), "submitted", actor="submitter", owner_edit=True
            )
        )
    assert "error" not in _finish(*writer)
    copy = PublishedService.objects.get(pk=sub.pk)
    assert (copy.host_institute, copy.pending_update) == (
        "Example Reviewed Institute",
        True,
    )
    assert "0 problems" in _verify()


def test_the_migration_publishes_the_content_committed_meanwhile():
    sub = _service(status="approved")
    with holding(lambda: _rename(sub, "Example Final Name")):
        writer = _second_writer(_publish_existing)
    assert "error" not in _finish(*writer)
    assert PublishedService.objects.get(pk=sub.pk).service_name == "Example Final Name"
    assert "0 problems" in _verify()


# ---------------------------------------------------------------------------
# Lock order: a query that locks several services does it in primary-key
# order, so two of them on overlapping rows cannot deadlock.
# ---------------------------------------------------------------------------


def _locking_queries(run):
    with CaptureQueriesContext(connection) as queries:
        run()
    return [q["sql"] for q in queries if "FOR UPDATE" in q["sql"]]


def _assert_ordered_by_pk(statements):
    """ORDER BY the id column, by name or by its position as the first
    selected column."""
    id_column = '"submissions_servicesubmission"."id"'
    assert statements
    for sql in statements:
        by_name = f"ORDER BY {id_column} ASC" in sql
        by_position = sql.startswith(f"SELECT {id_column}") and "ORDER BY 1 ASC" in sql
        assert by_name or by_position, sql


def test_the_tag_action_locks_services_in_primary_key_order(superuser_client):
    subs = [_published_service() for _ in range(3)]
    statements = _locking_queries(
        lambda: superuser_client.post(
            reverse(CHANGELIST) + "?o=-1",
            {
                "action": "action_assign_maturity_tags",
                "_selected_action": [str(s.pk) for s in subs],
                "_assign_tags": "1",
                "primary_maturity_tag": "mature",
            },
        )
    )
    _assert_ordered_by_pk([q for q in statements if " IN (" in q])
    assert all(_row(s).primary_maturity_tag == "mature" for s in subs)


def test_a_bulk_delete_locks_services_in_primary_key_order():
    subs = [_service() for _ in range(3)]
    _assert_ordered_by_pk(
        _locking_queries(
            lambda: ServiceSubmission.objects.filter(
                pk__in=[s.pk for s in subs]
            ).delete()
        )
    )


def test_the_migration_locks_services_in_primary_key_order():
    for _ in range(3):
        _service(status="approved")
    _assert_ordered_by_pk(_locking_queries(_publish_existing))


# ---------------------------------------------------------------------------
# Whole requests (web, API, admin) racing a status change or an edit
# ---------------------------------------------------------------------------


def _api_for(sub, scope="write"):
    _, key = APIKeyFactory.create_with_plaintext(submission=sub, scope=scope)
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"ApiKey {key}")
    return api


def _status_change_of(sub):
    """The status entry of the latest change-log row of ``sub``."""
    log = SubmissionChangeLog.objects.filter(submission=sub).latest("id")
    return next(c for c in log.changes if c["field"] == "status")


def test_an_api_edit_during_a_review_is_stored_but_not_published():
    sub = _published_service(service_name="Example Reviewed Name")
    api = _api_for(sub)
    with holding(lambda: set_status(_row(sub), "under_review", actor="admin:jane")):
        writer = _second_writer(
            lambda: api.patch(
                f"/api/v1/submissions/{sub.pk}/",
                {"service_description": DESCRIPTION},
                format="json",
            )
        )
    assert _finish(*writer)["value"].status_code == 200
    row = _row(sub)
    assert (row.status, row.service_description) == ("under_review", DESCRIPTION)
    copy = PublishedService.objects.get(pk=sub.pk)
    assert copy.service_description != DESCRIPTION
    assert copy.pending_update is True
    assert "0 problems" in _verify()


def test_an_api_edit_during_a_tag_assignment_does_not_drop_the_tag():
    """The PATCH loads the service before the tag commits and stores it
    after: it must not write the old tags back, or publish them."""
    sub = _published_service()
    api = _api_for(sub)

    def assign_tag():
        row = _row(sub)
        row.primary_maturity_tag = "mature"
        row.save(update_fields=["primary_maturity_tag"])
        publishing.republish(row, reason="tag_change", actor="admin:jane")

    with holding(assign_tag):
        writer = _second_writer(
            lambda: api.patch(
                f"/api/v1/submissions/{sub.pk}/",
                {"service_description": DESCRIPTION},
                format="json",
            )
        )
    assert _finish(*writer)["value"].status_code == 200
    row = _row(sub)
    assert (row.primary_maturity_tag, row.service_description) == (
        "mature",
        DESCRIPTION,
    )
    assert PublishedService.objects.get(pk=sub.pk).primary_maturity_tag == "mature"
    assert "0 problems" in _verify()


def test_a_web_edit_loaded_before_an_approval_is_refused(client):
    """The page was loaded while the service waited for review; it is
    approved before the edit is sent: the approved content stays."""
    sub = _service()
    _grant(client, sub)
    url = reverse("submissions:edit", args=[sub.pk])
    data = edit_form_data(sub, service_description=DESCRIPTION)
    data["loaded_version"] = _hidden_value(client.get(url), "loaded_version")
    with holding(lambda: set_status(_row(sub), "approved", actor="admin:jane")):
        writer = _second_writer(lambda: client.post(url, data))
    assert _finish(*writer)["value"].status_code == 409
    row = _row(sub)
    assert row.status == "approved"
    assert row.service_description != DESCRIPTION
    copy = PublishedService.objects.get(pk=sub.pk)
    assert copy.service_description == row.service_description
    assert "0 problems" in _verify()


def test_bulk_status_action_logs_the_status_found_under_the_lock(superuser_client):
    sub = _service()
    with holding(lambda: set_status(_row(sub), "under_review", actor="admin:jane")):
        writer = _second_writer(
            lambda: superuser_client.post(
                reverse(CHANGELIST),
                {"action": "action_reject", "_selected_action": [str(sub.pk)]},
            )
        )
    assert _finish(*writer)["value"].status_code == 302
    assert _transitions(sub)[-2:] == [
        ("submitted", "under_review"),
        ("under_review", "rejected"),
    ]
    assert _status_change_of(sub)["old"] == "Under Review"


def test_owner_deprecation_logs_the_status_found_under_the_lock(client):
    sub = _published_service()
    _grant(client, sub)
    url = reverse("submissions:edit", args=[sub.pk])
    with holding(lambda: set_status(_row(sub), "under_review", actor="admin:jane")):
        writer = _second_writer(lambda: client.post(url, {"_deprecate": "1"}))
    assert _finish(*writer)["value"].status_code == 302
    assert _transitions(sub)[-1] == ("under_review", "deprecated")
    assert _status_change_of(sub)["old"] == "Under Review"
    assert PublishedService.objects.get(pk=sub.pk).state == "deprecated"
    assert "0 problems" in _verify()


def test_tags_are_not_assigned_to_a_service_leaving_approval(superuser_client):
    sub = _published_service()
    with holding(lambda: set_status(_row(sub), "under_review", actor="admin:jane")):
        writer = _second_writer(
            lambda: superuser_client.post(
                reverse(CHANGELIST),
                {
                    "action": "action_assign_maturity_tags",
                    "_selected_action": [str(sub.pk)],
                    "_assign_tags": "1",
                    "primary_maturity_tag": "mature",
                },
            )
        )
    assert "error" not in _finish(*writer)
    row = _row(sub)
    assert (row.status, row.primary_maturity_tag) == ("under_review", None)
    assert PublishedService.objects.get(pk=sub.pk).primary_maturity_tag is None
    assert "0 problems" in _verify()


def _admin_form(client, sub, **changes):
    """A POST of the admin change form as loaded now."""
    page = client.get(reverse(CHANGE, args=[sub.pk]))
    data = edit_form_payload(sub, **changes)
    data["loaded_version"] = _hidden_value(page, "loaded_version")
    return data


def test_an_admin_form_save_after_a_parallel_status_change_is_refused(superuser_client):
    sub = _published_service()
    data = _admin_form(
        superuser_client, sub, github_url="https://github.com/example/moved"
    )
    with holding(lambda: set_status(_row(sub), "under_review", actor="admin:jane")):
        writer = _second_writer(
            lambda: superuser_client.post(reverse(CHANGE, args=[sub.pk]), data)
        )
    response = _finish(*writer)["value"]
    assert response.status_code == 302
    page = superuser_client.get(response["Location"], follow=True)
    assert EditConflict.message in [str(m) for m in page.context["messages"]]
    row = _row(sub)
    assert row.status == "under_review"
    assert row.github_url != "https://github.com/example/moved"
    copy = PublishedService.objects.get(pk=sub.pk)
    assert (copy.github_url, copy.pending_update) == (row.github_url, True)
    assert "0 problems" in _verify()


def test_a_status_change_after_an_admin_form_save_keeps_the_saved_content(
    superuser_client,
):
    sub = _published_service()
    data = _admin_form(
        superuser_client, sub, github_url="https://github.com/example/moved"
    )
    with holding(lambda: superuser_client.post(reverse(CHANGE, args=[sub.pk]), data)):
        writer = _second_writer(
            lambda: set_status(_row(sub), "under_review", actor="admin:jane")
        )
    assert "error" not in _finish(*writer)
    row = _row(sub)
    assert (row.status, row.github_url) == (
        "under_review",
        "https://github.com/example/moved",
    )
    assert _transitions(sub)[-1] == ("approved", "under_review")
    copy = PublishedService.objects.get(pk=sub.pk)
    assert (copy.github_url, copy.pending_update) == (row.github_url, True)
    assert "0 problems" in _verify()


# ---------------------------------------------------------------------------
# The restore action reads the service under its row lock and compares the
# version the confirmation page showed.
# ---------------------------------------------------------------------------


def _restorable(name="Example Rejected Edit"):
    """A published service whose rejected edit the restore would discard."""
    sub = _published_service(service_name="Example First Version")
    _rejected_edit(sub, name)
    return sub


def test_a_restore_after_a_parallel_status_change_restores_nothing(superuser_client):
    sub = _restorable()
    page = _ask(superuser_client, sub)
    with holding(lambda: set_status(_row(sub), "submitted", actor="submitter")):
        writer = _second_writer(lambda: _confirm(superuser_client, page))
    response = _finish(*writer)["value"]
    row = _row(sub)
    assert (row.status, row.service_name) == ("submitted", "Example Rejected Edit")
    assert any("not restored" in m for m in _messages(response))
    assert "0 problems" in _verify()


def test_a_restore_after_a_parallel_edit_restores_nothing(superuser_client):
    sub = _restorable()
    page = _ask(superuser_client, sub)
    with holding(lambda: _admin_edit(sub, "Example Edited Meanwhile")):
        writer = _second_writer(lambda: _confirm(superuser_client, page))
    response = _finish(*writer)["value"]
    row = _row(sub)
    assert row.service_name == "Example Edited Meanwhile"
    assert any("1 submission(s) not restored" in m for m in _messages(response))
    assert not any(
        "Restored the published version of 1" in m for m in _messages(response)
    )
    assert PublishedService.objects.get(pk=sub.pk).service_name == (
        "Example First Version"
    )
    assert "0 problems" in _verify()


def test_a_status_change_after_a_restore_keeps_the_restored_content(superuser_client):
    sub = _restorable()
    page = _ask(superuser_client, sub)
    with holding(lambda: _confirm(superuser_client, page)):
        writer = _second_writer(
            lambda: set_status(_row(sub), "submitted", actor="submitter")
        )
    assert "error" not in _finish(*writer)
    row = _row(sub)
    assert (row.status, row.service_name) == ("submitted", "Example First Version")
    assert (
        PublishedService.objects.get(pk=sub.pk).service_name == "Example First Version"
    )
    assert "0 problems" in _verify()


def test_an_edit_after_a_restore_is_refused_when_it_was_loaded_before(superuser_client):
    sub = _restorable()
    page = _ask(superuser_client, sub)
    stale = _row(sub)  # the edit form of the rejected content
    stale.comments = "Changed by John"
    with holding(lambda: _confirm(superuser_client, page)):
        writer = _second_writer(stale.save)
    assert isinstance(_finish(*writer).get("error"), EditConflict)
    row = _row(sub)
    assert (row.service_name, row.comments) == ("Example First Version", sub.comments)


def test_a_delete_during_a_restore_removes_everything(superuser_client):
    sub = _restorable()
    page = _ask(superuser_client, sub)
    with holding(lambda: _confirm(superuser_client, page)):
        writer = _second_writer(lambda: _row(sub).delete())
    assert "error" not in _finish(*writer)
    assert _no_rows_left(sub)
    assert _tombstones() == [sub.pk]


def test_a_restore_after_a_delete_restores_nothing(superuser_client):
    sub = _restorable()
    page = _ask(superuser_client, sub)
    with holding(lambda: _row(sub).delete()):
        writer = _second_writer(lambda: _confirm(superuser_client, page))
    assert any("not restored" in m for m in _messages(_finish(*writer)["value"]))
    assert _no_rows_left(sub)
