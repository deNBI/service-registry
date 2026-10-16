"""Concurrent writers on a real PostgreSQL server.

SQLite ignores row locks, so the other tests can only check that a lock is
requested. Here one connection keeps a transaction open (its row lock held)
while a second connection, in another thread, runs a real code path on the
same service. Each test checks that both changes are kept, or that the
second one is refused when it was made from an outdated copy.

Run with ``make test-postgres`` (needs a PostgreSQL server, see
config/settings_test_postgres.py).
"""

import threading
import time
from contextlib import contextmanager

import pytest
from django.db import connection, transaction
from django.urls import reverse
from rest_framework.test import APIClient

from apps.submissions.lifecycle import set_status
from apps.submissions.models import EditConflict, ServiceSubmission
from apps.submissions.selectors import status_history_broken
from tests.factories import APIKeyFactory, ServiceSubmissionFactory
from tests.helpers import edit_form_data
from tests.test_concurrency import _grant, _hidden_value

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(transaction=True)]

CHANGELIST = "admin:submissions_servicesubmission_changelist"
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
