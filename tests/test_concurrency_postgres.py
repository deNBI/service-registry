"""Concurrent writers on a real PostgreSQL server.

SQLite ignores row locks, so the other tests can only check that a lock is
requested. Here one connection keeps a transaction open (its row locks held)
while a second connection, in another thread, runs a real code path on the
same service. Each test checks the outcome: no lost status change, no
overwritten content, no unreviewed content made public, no gap in the
history, and verify_published stays clean.

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
from django.urls import reverse
from rest_framework.test import APIClient

from apps.catalogue import publishing
from apps.catalogue.management.commands.publish_backfill import Command
from apps.catalogue.models import PublishedRevision, PublishedService, WithdrawnService
from apps.submissions.lifecycle import set_status
from apps.submissions.models import (
    ServiceSubmission,
    StatusChangedError,
    SubmissionChangeLog,
)
from tests.factories import APIKeyFactory, ServiceSubmissionFactory
from tests.helpers import edit_form_data, edit_form_payload
from tests.test_publishing_admin import _ask, _confirm, _messages

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(transaction=True)]

CHANGELIST = "admin:submissions_servicesubmission_changelist"
CHANGE = "admin:submissions_servicesubmission_change"
WAIT = 10  # seconds; generous, only reached when something hangs


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


def _transitions(sub):
    return [(e.from_status, e.to_status) for e in sub.status_events.order_by("id")]


def _published_service(**kw):
    sub = ServiceSubmissionFactory(biotools_url="", **kw)
    set_status(sub, "approved", actor="admin:jane")
    return sub


def _verify():
    out = StringIO()
    call_command("verify_published", stdout=out)
    return out.getvalue()


def _rename(sub, name):
    """Change the working copy without publishing (as another request would
    before its own publish step)."""
    ServiceSubmission.objects.filter(pk=sub.pk).update(service_name=name)


def _rejected_edit(sub, name):
    """The owner edits a published service and review rejects the edit: the
    working copy holds ``name``, the published copy does not."""
    set_status(_row(sub), "submitted", actor="submitter")
    _rename(sub, name)
    set_status(_row(sub), "rejected", actor="admin:jane")


def _restore_page(client, sub):
    """The confirmation page of the restore action, as it is shown now."""
    return _ask(client, sub)


def _no_rows_left(sub):
    """Nothing refers to the deleted service any more."""
    return not (
        ServiceSubmission.objects.filter(pk=sub.pk).exists()
        or sub.status_events.exists()
        or PublishedRevision.objects.filter(submission=sub).exists()
        or PublishedService.objects.filter(pk=sub.pk).exists()
    )


class TestStatusChanges:
    def test_parallel_status_changes_form_one_history(self):
        sub = ServiceSubmissionFactory(biotools_url="")
        with holding(lambda: set_status(_row(sub), "under_review", actor="admin:a")):
            second = _second_writer(
                lambda: set_status(_row(sub), "rejected", actor="admin:b")
            )
        assert "error" not in _finish(*second)
        assert _transitions(sub) == [
            (None, "submitted"),
            ("submitted", "under_review"),
            ("under_review", "rejected"),
        ]
        assert _row(sub).status == "rejected"

    def test_the_same_approval_twice_records_and_publishes_once(self):
        sub = ServiceSubmissionFactory(biotools_url="")
        with holding(lambda: set_status(_row(sub), "approved", actor="admin:a")):
            second = _second_writer(
                lambda: set_status(_row(sub), "approved", actor="admin:b")
            )
        assert _finish(*second)["value"] is False
        assert _transitions(sub) == [(None, "submitted"), ("submitted", "approved")]
        assert PublishedRevision.objects.filter(submission=sub).count() == 1

    def test_parallel_changes_after_an_unrecorded_status_record_it_once(self):
        sub = ServiceSubmissionFactory(biotools_url="")
        ServiceSubmission.objects.filter(pk=sub.pk).update(status="under_review")
        with holding(lambda: set_status(_row(sub), "approved", actor="admin:a")):
            second = _second_writer(
                lambda: set_status(_row(sub), "rejected", actor="admin:b")
            )
        assert "error" not in _finish(*second)
        assert _transitions(sub) == [
            (None, "submitted"),
            ("submitted", "under_review"),
            ("under_review", "approved"),
            ("approved", "rejected"),
        ]
        assert sub.status_events.filter(is_synthetic=True).count() == 1

    def test_a_full_save_after_a_parallel_status_change_is_refused(self):
        sub = _published_service()
        loaded = _row(sub)  # an edit page loaded while the service is approved
        loaded.service_name = "Example Stale Edit"
        with holding(lambda: set_status(_row(sub), "under_review", actor="admin:a")):
            second = _second_writer(loaded.save)
        assert isinstance(_finish(*second).get("error"), StatusChangedError)
        row = _row(sub)
        assert row.status == "under_review"
        assert row.service_name != "Example Stale Edit"

    def test_a_full_save_of_a_service_deleted_meanwhile_is_refused(self):
        sub = _published_service()
        loaded = _row(sub)
        with holding(lambda: ServiceSubmission.objects.filter(pk=sub.pk).delete()):
            second = _second_writer(loaded.save)
        assert isinstance(_finish(*second).get("error"), StatusChangedError)
        assert not ServiceSubmission.objects.filter(pk=sub.pk).exists()


class TestPublishing:
    def test_parallel_publishes_do_not_collide(self):
        sub = _published_service()
        _rename(sub, "Example Second Version")

        def publish():
            return publishing.publish(_row(sub), reason="live_edit", actor="submitter")

        with holding(publish):
            second = _second_writer(publish)
        result = _finish(*second)
        assert "error" not in result and result["value"] is None
        numbers = PublishedRevision.objects.filter(submission=sub).values_list(
            "number", flat=True
        )
        assert sorted(numbers) == [1, 2]
        assert PublishedService.objects.get(pk=sub.pk).revision.number == 2
        assert "0 problems" in _verify()

    def test_a_live_edit_does_not_publish_once_review_started(self):
        """The edit committed its content; its publish step runs after an
        admin moved the service to review: nothing unreviewed goes public."""
        sub = _published_service(service_name="Example Reviewed Name")
        _rename(sub, "Example Unreviewed Name")
        with holding(lambda: set_status(_row(sub), "under_review", actor="admin:a")):
            second = _second_writer(
                lambda: publishing.republish(
                    _row(sub), reason="live_edit", actor="submitter"
                )
            )
        assert _finish(*second)["value"] is None
        ps = PublishedService.objects.get(pk=sub.pk)
        assert ps.service_name == "Example Reviewed Name"
        assert ps.pending_update is True

    def test_a_state_change_is_not_lost_to_a_parallel_publish(self):
        """Every writer of the published copy takes the submission lock, so a
        publish cannot write back the state it read before the change."""
        sub = _published_service()
        _rename(sub, "Example Second Version")
        with holding(
            lambda: publishing.set_publication_state(
                sub.pk, state=PublishedService.DEPRECATED
            )
        ):
            second = _second_writer(
                lambda: publishing.publish(
                    _row(sub), reason="admin_edit", actor="admin:a"
                )
            )
        assert "error" not in _finish(*second)
        ps = PublishedService.objects.get(pk=sub.pk)
        assert (ps.state, ps.service_name) == ("deprecated", "Example Second Version")

    def test_rebuild_uses_the_revision_published_meanwhile(self):
        sub = _published_service()
        _rename(sub, "Example Second Version")
        with holding(
            lambda: publishing.publish(_row(sub), reason="live_edit", actor="submitter")
        ):
            second = _second_writer(lambda: publishing.rebuild_from_revision(sub.pk))
        assert "error" not in _finish(*second)
        ps = PublishedService.objects.get(pk=sub.pk)
        assert (ps.revision.number, ps.service_name) == (2, "Example Second Version")
        assert "0 problems" in _verify()

    def test_a_delete_during_the_first_publish_leaves_a_tombstone(self):
        sub = ServiceSubmissionFactory(status="approved", biotools_url="")
        with holding(
            lambda: publishing.publish(_row(sub), reason="approval", actor="admin:a")
        ):
            second = _second_writer(lambda: _row(sub).delete())
        assert "error" not in _finish(*second)
        assert not ServiceSubmission.objects.filter(pk=sub.pk).exists()
        assert WithdrawnService.objects.filter(pk=sub.pk).exists()

    def test_a_bulk_delete_during_the_first_publish_leaves_a_tombstone(self):
        sub = ServiceSubmissionFactory(status="approved", biotools_url="")
        other = ServiceSubmissionFactory(status="approved", biotools_url="")
        with holding(
            lambda: publishing.publish(_row(sub), reason="approval", actor="admin:a")
        ):
            second = _second_writer(
                lambda: ServiceSubmission.objects.filter(
                    pk__in=[sub.pk, other.pk]
                ).delete()
            )
        assert "error" not in _finish(*second)
        assert not ServiceSubmission.objects.filter(pk__in=[sub.pk, other.pk]).exists()
        assert list(WithdrawnService.objects.values_list("pk", flat=True)) == [sub.pk]


class TestCommandsAndMigration:
    def test_publish_backfill_skips_a_service_reset_meanwhile(self):
        sub = ServiceSubmissionFactory(status="approved", biotools_url="")

        def reset_with_new_text():
            _rename(sub, "Example Unreviewed Name")
            set_status(_row(sub), "submitted", actor="submitter")

        with holding(reset_with_new_text):
            second = _second_writer(
                lambda: call_command("publish_backfill", stdout=StringIO())
            )
        assert "error" not in _finish(*second)
        assert not PublishedService.objects.filter(pk=sub.pk).exists()
        assert not PublishedRevision.objects.filter(submission=sub).exists()

    def test_publish_backfill_holding_a_drifted_approval_then_a_reset_records_it_once(
        self,
    ):
        """An earlier release approved the service without an event. The
        backfill holds the row: it records the approval and publishes; the
        reset that waited then finds the history complete."""
        sub = ServiceSubmissionFactory(biotools_url="")
        ServiceSubmission.objects.filter(pk=sub.pk).update(status="approved")
        with holding(lambda: Command()._publish_one(sub.pk)):
            second = _second_writer(
                lambda: set_status(_row(sub), "submitted", actor="submitter")
            )
        assert "error" not in _finish(*second)
        assert _transitions(sub) == [
            (None, "submitted"),
            ("submitted", "approved"),
            ("approved", "submitted"),
        ]
        assert PublishedService.objects.get(pk=sub.pk).pending_update is True
        assert "0 problems" in _verify()

    def test_a_reset_of_a_drifted_approval_holding_makes_the_backfill_skip(self):
        """The reset records the approval first, then moves the service to
        review: it is no longer approved, so the backfill publishes nothing."""
        sub = ServiceSubmissionFactory(biotools_url="")
        ServiceSubmission.objects.filter(pk=sub.pk).update(status="approved")
        with holding(lambda: set_status(_row(sub), "submitted", actor="submitter")):
            second = _second_writer(lambda: Command()._publish_one(sub.pk))
        assert _finish(*second)["value"] is False
        assert not PublishedService.objects.filter(pk=sub.pk).exists()
        assert _transitions(sub) == [
            (None, "submitted"),
            ("submitted", "approved"),
            ("approved", "submitted"),
        ]

    def test_a_reset_of_a_drifted_deprecation_keeps_the_copy_deprecated_for_the_backfill(
        self,
    ):
        """A published service deprecated by an earlier release, reset while
        the backfill waits for its lock: the copy follows the deprecation and
        the backfill leaves the published service alone."""
        sub = _published_service()
        ServiceSubmission.objects.filter(pk=sub.pk).update(status="deprecated")
        with holding(lambda: set_status(_row(sub), "submitted", actor="submitter")):
            second = _second_writer(lambda: Command()._publish_one(sub.pk))
        assert _finish(*second)["value"] is False
        copy = PublishedService.objects.get(pk=sub.pk)
        assert (copy.state, copy.pending_update) == ("deprecated", True)
        assert "0 problems" in _verify()

    def test_the_repair_of_a_published_service_holding_then_a_reset_records_it_once(
        self,
    ):
        """An earlier release deprecated a published service. The repair holds
        the row: it records the deprecation and deprecates the copy; the reset
        that waited finds the history complete."""
        sub = _published_service()
        ServiceSubmission.objects.filter(pk=sub.pk).update(status="deprecated")
        with holding(lambda: Command()._repair_one(sub.pk)):
            second = _second_writer(
                lambda: set_status(_row(sub), "submitted", actor="submitter")
            )
        assert "error" not in _finish(*second)
        assert _transitions(sub) == [
            (None, "submitted"),
            ("submitted", "approved"),
            ("approved", "deprecated"),
            ("deprecated", "submitted"),
        ]
        copy = PublishedService.objects.get(pk=sub.pk)
        assert (copy.state, copy.pending_update) == ("deprecated", True)
        assert "0 problems" in _verify()

    def test_a_reset_of_a_published_service_holding_makes_the_repair_skip(self):
        """The reset records the earlier release's deprecation first; the repair
        that waited finds nothing left to record and leaves the flags the reset
        set."""
        sub = _published_service()
        ServiceSubmission.objects.filter(pk=sub.pk).update(status="deprecated")
        with holding(lambda: set_status(_row(sub), "submitted", actor="submitter")):
            second = _second_writer(lambda: Command()._repair_one(sub.pk))
        assert _finish(*second)["value"] is False
        assert _transitions(sub) == [
            (None, "submitted"),
            ("submitted", "approved"),
            ("approved", "deprecated"),
            ("deprecated", "submitted"),
        ]
        copy = PublishedService.objects.get(pk=sub.pk)
        assert (copy.state, copy.pending_update) == ("deprecated", True)
        assert "0 problems" in _verify()

    def test_the_migration_publishes_the_content_committed_meanwhile(self):
        sub = ServiceSubmissionFactory(status="approved", biotools_url="")
        migration = importlib.import_module(
            "apps.catalogue.migrations.0002_publish_existing_services"
        )
        historical = (
            MigrationLoader(None, ignore_no_migrations=True)
            .project_state(("catalogue", "0002_publish_existing_services"))
            .apps
        )

        def migrate():
            with transaction.atomic():
                migration.publish_existing(historical, None)

        with holding(lambda: _rename(sub, "Example Final Name")):
            second = _second_writer(migrate)
        assert "error" not in _finish(*second)
        ps = PublishedService.objects.get(pk=sub.pk)
        assert ps.service_name == "Example Final Name"
        assert "0 problems" in _verify()


class TestLockOrder:
    """Every query that locks several services locks them in primary-key
    order, so two such queries on overlapping rows cannot deadlock."""

    @staticmethod
    def _locking_queries(run):
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as queries:
            run()
        return [q["sql"] for q in queries if "FOR UPDATE" in q["sql"]]

    def _assert_ordered_by_pk(self, sql_list):
        """ORDER BY the id column, by name or by its position as the first
        selected column."""
        id_column = '"submissions_servicesubmission"."id"'
        assert sql_list
        for sql in sql_list:
            by_name = f"ORDER BY {id_column} ASC" in sql
            by_position = sql.startswith(f"SELECT {id_column}") and (
                "ORDER BY 1 ASC" in sql
            )
            assert by_name or by_position, sql

    def test_the_tag_action(self, superuser_client):
        subs = [_published_service() for _ in range(3)]
        sql = self._locking_queries(
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
        several = [q for q in sql if " IN (" in q]
        self._assert_ordered_by_pk(several)
        assert all(_row(s).primary_maturity_tag == "mature" for s in subs)

    def test_a_bulk_delete(self):
        subs = [ServiceSubmissionFactory(biotools_url="") for _ in range(3)]
        sql = self._locking_queries(
            lambda: ServiceSubmission.objects.filter(
                pk__in=[s.pk for s in subs]
            ).delete()
        )
        self._assert_ordered_by_pk(sql)

    def test_the_migration(self):
        for _ in range(3):
            ServiceSubmissionFactory(status="approved", biotools_url="")
        migration = importlib.import_module(
            "apps.catalogue.migrations.0002_publish_existing_services"
        )
        historical = (
            MigrationLoader(None, ignore_no_migrations=True)
            .project_state(("catalogue", "0002_publish_existing_services"))
            .apps
        )

        def migrate():
            with transaction.atomic():
                migration.publish_existing(historical, None)

        sql = self._locking_queries(migrate)
        self._assert_ordered_by_pk(sql)


class TestRequests:
    """Whole requests (web, API, admin) racing a status change."""

    def test_an_api_edit_during_a_review_is_refused(self, settings):
        sub = _published_service(service_name="Example Reviewed Name")
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        url = f"/api/v1/submissions/{sub.pk}/"
        payload = {
            "service_description": "An edit that arrives while an admin is reviewing the service."
        }

        with holding(lambda: set_status(_row(sub), "under_review", actor="admin:a")):
            second = _second_writer(lambda: client.patch(url, payload, format="json"))
        response = _finish(*second)["value"]
        assert response.status_code == 409
        row = _row(sub)
        assert row.status == "under_review"
        assert row.service_description != payload["service_description"]
        ps = PublishedService.objects.get(pk=sub.pk)
        assert ps.service_description != payload["service_description"]
        assert "0 problems" in _verify()

    def test_an_api_edit_during_a_tag_assignment_does_not_drop_the_tag(
        self, superuser_client
    ):
        """The PATCH loads the service before the tag commits and stores it
        after: it must not write the old tags back (or publish that)."""
        sub = _published_service()
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        url = f"/api/v1/submissions/{sub.pk}/"
        payload = {"website_url": "https://example.org/moved"}

        def assign_tag():
            row = _row(sub)
            row.primary_maturity_tag = "mature"
            row.save(update_fields=["primary_maturity_tag"])
            publishing.republish(row, reason="tag_change", actor="admin:a")

        with holding(assign_tag):
            second = _second_writer(lambda: client.patch(url, payload, format="json"))
        response = _finish(*second)["value"]
        assert response.status_code == 409
        row = _row(sub)
        assert row.primary_maturity_tag == "mature"
        assert row.website_url != "https://example.org/moved"
        assert PublishedService.objects.get(pk=sub.pk).primary_maturity_tag == "mature"
        assert "0 problems" in _verify()

    def test_a_web_edit_loaded_before_an_approval_is_refused(self, client, settings):
        """The page was loaded while the service waited for review; it is
        approved before the edit is sent: the approved content stays."""
        settings.ALTCHA_HMAC_KEY = ""
        sub = ServiceSubmissionFactory(biotools_url="")
        key, _ = APIKeyFactory.create_with_plaintext(submission=sub)
        session = client.session
        session["edit_grants"] = {str(sub.pk): str(key.pk)}
        session.save()
        data = edit_form_data(_row(sub), service_description="X" * 80)
        url = reverse("submissions:edit", args=[sub.pk])
        # The request reads the service before the approval commits, then waits
        # for the row lock when it stores the edit.
        with holding(lambda: set_status(_row(sub), "approved", actor="admin:a")):
            second = _second_writer(lambda: client.post(url, data))
        response = _finish(*second)["value"]
        assert response.status_code == 409
        row = _row(sub)
        assert row.status == "approved"
        assert row.service_description != "X" * 80
        assert PublishedService.objects.get(pk=sub.pk).service_description == (
            row.service_description
        )

    def test_bulk_status_change_logs_the_status_found_under_the_lock(
        self, superuser_client
    ):
        sub = ServiceSubmissionFactory(biotools_url="")
        with holding(lambda: set_status(_row(sub), "under_review", actor="admin:a")):
            second = _second_writer(
                lambda: superuser_client.post(
                    reverse(CHANGELIST),
                    {"action": "action_reject", "_selected_action": [str(sub.pk)]},
                )
            )
        assert _finish(*second)["value"].status_code == 302
        assert _transitions(sub)[-2:] == [
            ("submitted", "under_review"),
            ("under_review", "rejected"),
        ]
        log = SubmissionChangeLog.objects.filter(submission=sub).latest("id")
        status = next(c for c in log.changes if c["field"] == "status")
        assert status["old"] == "Under Review"

    def test_self_deprecation_logs_the_status_found_under_the_lock(self, client):
        sub = _published_service()
        key, _ = APIKeyFactory.create_with_plaintext(submission=sub)
        session = client.session
        session["edit_grants"] = {str(sub.pk): str(key.pk)}
        session.save()
        url = reverse("submissions:edit", args=[sub.pk])
        with holding(lambda: set_status(_row(sub), "under_review", actor="admin:a")):
            second = _second_writer(lambda: client.post(url, {"_deprecate": "1"}))
        assert "error" not in _finish(*second)
        assert _transitions(sub)[-1] == ("under_review", "deprecated")
        log = SubmissionChangeLog.objects.filter(submission=sub).latest("id")
        status = next(c for c in log.changes if c["field"] == "status")
        assert status["old"] == "Under Review"
        assert PublishedService.objects.get(pk=sub.pk).state == "deprecated"

    def test_tags_are_not_assigned_to_a_service_leaving_approval(
        self, superuser_client
    ):
        sub = _published_service()
        with holding(lambda: set_status(_row(sub), "under_review", actor="admin:a")):
            second = _second_writer(
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
        assert "error" not in _finish(*second)
        row = _row(sub)
        assert row.status == "under_review"
        assert row.primary_maturity_tag is None
        assert "0 problems" in _verify()

    def test_restore_does_not_undo_a_live_edit_published_meanwhile(
        self, superuser_client
    ):
        sub = _published_service(service_name="Example First Version")
        page = _ask(superuser_client, sub)

        def live_edit():
            _rename(sub, "Example Live Edit")
            publishing.republish(_row(sub), reason="live_edit", actor="submitter")

        with holding(live_edit):
            second = _second_writer(lambda: _confirm(superuser_client, page))
        response = _finish(*second)["value"]
        assert _row(sub).service_name == "Example Live Edit"
        assert PublishedService.objects.get(pk=sub.pk).service_name == (
            "Example Live Edit"
        )
        # The published live edit is what the working copy holds: nothing to discard.
        assert any("1 already matched it" in m for m in _messages(response))
        assert "0 problems" in _verify()


class TestAdminChangeFormAndStatusChange:
    """The admin change form saves the whole working copy; a status change
    that commits while it waits must not be undone."""

    @staticmethod
    def _payload(sub):
        return edit_form_payload(sub, github_url="https://github.com/example/moved")

    def test_a_form_save_after_a_parallel_status_change_is_refused(
        self, superuser_client
    ):
        sub = _published_service()
        data = self._payload(sub)
        with holding(lambda: set_status(_row(sub), "under_review", actor="admin:a")):
            second = _second_writer(
                lambda: superuser_client.post(reverse(CHANGE, args=[sub.pk]), data)
            )
        response = _finish(*second)["value"]
        assert response.status_code == 302
        page = superuser_client.get(response["Location"]).content.decode()
        assert "status of this service changed" in page
        row = _row(sub)
        assert row.status == "under_review"
        assert row.github_url != "https://github.com/example/moved"
        ps = PublishedService.objects.get(pk=sub.pk)
        assert (ps.github_url, ps.pending_update) == (row.github_url, True)
        assert "0 problems" in _verify()

    def test_a_status_change_after_a_form_save_keeps_the_saved_content(
        self, superuser_client
    ):
        sub = _published_service()
        data = self._payload(sub)
        with holding(
            lambda: superuser_client.post(reverse(CHANGE, args=[sub.pk]), data)
        ):
            second = _second_writer(
                lambda: set_status(_row(sub), "under_review", actor="admin:a")
            )
        assert "error" not in _finish(*second)
        row = _row(sub)
        assert (row.status, row.github_url) == (
            "under_review",
            "https://github.com/example/moved",
        )
        assert _transitions(sub)[-1] == ("approved", "under_review")
        ps = PublishedService.objects.get(pk=sub.pk)
        assert (ps.github_url, ps.pending_update) == (row.github_url, True)
        assert "0 problems" in _verify()


class TestRestoreAndStatusChange:
    """The restore action reads the service under its row lock and checks the
    page it was confirmed on, so a status change that commits first stops it."""

    def test_a_restore_after_a_parallel_status_change_restores_nothing(
        self, superuser_client
    ):
        sub = _published_service(service_name="Example First Version")
        _rejected_edit(sub, "Example Rejected Edit")
        page = _restore_page(superuser_client, sub)
        with holding(lambda: set_status(_row(sub), "submitted", actor="submitter")):
            second = _second_writer(lambda: _confirm(superuser_client, page))
        response = _finish(*second)["value"]
        row = _row(sub)
        assert (row.status, row.service_name) == (
            "submitted",
            "Example Rejected Edit",
        )
        assert any("not restored" in m for m in _messages(response))
        assert "0 problems" in _verify()

    def test_a_status_change_after_a_restore_keeps_the_restored_content(
        self, superuser_client
    ):
        sub = _published_service(service_name="Example First Version")
        _rejected_edit(sub, "Example Rejected Edit")
        page = _restore_page(superuser_client, sub)
        with holding(lambda: _confirm(superuser_client, page)):
            second = _second_writer(
                lambda: set_status(_row(sub), "submitted", actor="submitter")
            )
        assert "error" not in _finish(*second)
        row = _row(sub)
        assert (row.status, row.service_name) == ("submitted", "Example First Version")
        assert PublishedService.objects.get(pk=sub.pk).service_name == (
            "Example First Version"
        )
        assert "0 problems" in _verify()


class TestDeleteWhileAnotherWriterRuns:
    """A delete waits for the row lock; the service and everything that
    refers to it go together, and a published service leaves a tombstone."""

    def test_a_delete_during_a_status_change_removes_everything(self):
        sub = _published_service()
        with holding(lambda: set_status(_row(sub), "under_review", actor="admin:a")):
            second = _second_writer(lambda: _row(sub).delete())
        assert "error" not in _finish(*second)
        assert _no_rows_left(sub)
        assert list(WithdrawnService.objects.values_list("pk", flat=True)) == [sub.pk]

    def test_a_queryset_delete_during_a_status_change_removes_everything(self):
        sub = _published_service()
        with holding(lambda: set_status(_row(sub), "under_review", actor="admin:a")):
            second = _second_writer(
                lambda: ServiceSubmission.objects.filter(pk=sub.pk).delete()
            )
        assert "error" not in _finish(*second)
        assert _no_rows_left(sub)
        assert list(WithdrawnService.objects.values_list("pk", flat=True)) == [sub.pk]

    def test_a_delete_during_a_restore_removes_everything(self, superuser_client):
        sub = _published_service(service_name="Example First Version")
        _rejected_edit(sub, "Example Rejected Edit")
        page = _restore_page(superuser_client, sub)
        with holding(lambda: _confirm(superuser_client, page)):
            second = _second_writer(lambda: _row(sub).delete())
        assert "error" not in _finish(*second)
        assert _no_rows_left(sub)
        assert list(WithdrawnService.objects.values_list("pk", flat=True)) == [sub.pk]

    def test_a_status_change_after_a_delete_writes_nothing(self):
        sub = _published_service()
        with holding(lambda: _row(sub).delete()):
            second = _second_writer(
                lambda: set_status(_row(sub), "under_review", actor="admin:a")
            )
        assert isinstance(_finish(*second).get("error"), ServiceSubmission.DoesNotExist)
        assert _no_rows_left(sub)

    def test_a_restore_after_a_delete_restores_nothing(self, superuser_client):
        sub = _published_service(service_name="Example First Version")
        _rejected_edit(sub, "Example Rejected Edit")
        page = _restore_page(superuser_client, sub)
        with holding(lambda: _row(sub).delete()):
            second = _second_writer(lambda: _confirm(superuser_client, page))
        response = _finish(*second)["value"]
        assert any("not restored" in m for m in _messages(response))
        assert _no_rows_left(sub)


class TestRepublishApprovedAndReset:
    def test_the_republish_of_an_approved_service_skips_one_reset_meanwhile(self):
        """An earlier release left the public copy behind the approved working
        copy. The service is edited and sent to review while the backfill
        waits for the lock: the unreviewed text must not go public."""
        sub = _published_service(host_institute="Example Original Institute")
        ServiceSubmission.objects.filter(pk=sub.pk).update(
            host_institute="Example Reviewed Institute"
        )

        def edit_and_reset():
            ServiceSubmission.objects.filter(pk=sub.pk).update(
                host_institute="Example Unreviewed Institute"
            )
            set_status(_row(sub), "submitted", actor="submitter")

        with holding(edit_and_reset):
            second = _second_writer(
                lambda: call_command("publish_backfill", stdout=StringIO())
            )
        assert "error" not in _finish(*second)
        ps = PublishedService.objects.get(pk=sub.pk)
        assert ps.host_institute == "Example Original Institute"
        assert ps.pending_update is True
        assert PublishedRevision.objects.filter(submission=sub).count() == 1
        assert "0 problems" in _verify()

    def test_the_republish_of_an_approved_service_holding_then_a_reset_keeps_it_pending(
        self,
    ):
        sub = _published_service(host_institute="Example Original Institute")
        ServiceSubmission.objects.filter(pk=sub.pk).update(
            host_institute="Example Reviewed Institute"
        )
        with holding(lambda: Command()._republish_one(sub.pk)):
            second = _second_writer(
                lambda: set_status(_row(sub), "submitted", actor="submitter")
            )
        assert "error" not in _finish(*second)
        ps = PublishedService.objects.get(pk=sub.pk)
        assert (ps.host_institute, ps.pending_update) == (
            "Example Reviewed Institute",
            True,
        )
        assert "0 problems" in _verify()
