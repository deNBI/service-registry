"""Status history: creation events, set_status, selectors."""

import pytest
from django.db.models import F
from django.urls import reverse

from apps.submissions.lifecycle import set_status
from apps.submissions.models import (
    EditConflict,
    ServiceSubmission,
    SubmissionStatusEvent,
)
from apps.submissions.selectors import status_history_broken, status_history_drift
from tests.factories import ServiceCenterFactory, ServiceSubmissionFactory

pytestmark = pytest.mark.django_db


class TestCreationEvent:
    @pytest.mark.django_db(transaction=True)
    def test_a_failed_creation_event_also_undoes_the_new_row(self, monkeypatch):
        """Registration and API create run without an outer transaction: the
        row and its first event are stored together or not at all."""

        def boom(*args, **kwargs):
            raise RuntimeError("event write failed")

        monkeypatch.setattr(SubmissionStatusEvent.objects, "create", boom)
        with pytest.raises(RuntimeError):
            ServiceSubmissionFactory(biotools_url="")
        assert ServiceSubmission.objects.count() == 0

    def test_factory_creation_records_one_event(self):
        sub = ServiceSubmissionFactory(status="approved", biotools_url="")
        events = list(sub.status_events.all())
        assert len(events) == 1
        event = events[0]
        assert event.from_status is None
        assert event.to_status == "approved"
        assert event.changed_by == "system"
        assert event.source == SubmissionStatusEvent.SOURCE_LIVE
        assert event.is_synthetic is False

    def test_saving_existing_submission_writes_no_event(self):
        sub = ServiceSubmissionFactory(biotools_url="")
        sub.comments = "Internal note"
        sub.save()
        assert sub.status_events.count() == 1

    @pytest.mark.allow_status_drift
    def test_loaddata_style_raw_save_writes_no_event(self):
        from django.core import serializers

        sub = ServiceSubmissionFactory(biotools_url="")
        payload = serializers.serialize("json", [sub])
        pk = sub.pk
        sub.delete()
        for obj in serializers.deserialize("json", payload):
            obj.save()  # raw=True insert, as loaddata does
        assert ServiceSubmission.objects.filter(pk=pk).exists()
        assert SubmissionStatusEvent.objects.filter(submission_id=pk).count() == 0

    def test_admin_change_page_shows_status_history(self, admin_client):
        sub = ServiceSubmissionFactory(biotools_url="")
        url = reverse("admin:submissions_servicesubmission_change", args=[sub.pk])
        resp = admin_client.get(url)
        assert resp.status_code == 200
        assert b"Status history" in resp.content
        assert b'name="status_events-TOTAL_FORMS"' in resp.content


class TestSetStatus:
    def test_changes_status_and_records_event(self):
        sub = ServiceSubmissionFactory(biotools_url="")
        changed = set_status(sub, "approved", actor="admin:jane")
        assert changed is True
        sub.refresh_from_db()
        assert sub.status == "approved"
        event = sub.status_events.order_by("id").last()
        assert (event.from_status, event.to_status) == ("submitted", "approved")
        assert event.changed_by == "admin:jane"
        assert event.is_synthetic is False
        assert event.source == SubmissionStatusEvent.SOURCE_LIVE

    def test_same_status_is_a_no_op(self):
        sub = ServiceSubmissionFactory(biotools_url="")
        assert set_status(sub, "submitted", actor="admin:jane") is False
        assert sub.status_events.count() == 1

    def test_unknown_status_is_rejected_and_nothing_is_written(self):
        sub = ServiceSubmissionFactory(biotools_url="")
        with pytest.raises(ValueError):
            set_status(sub, "published", actor="admin:jane")
        sub.refresh_from_db()
        assert sub.status == "submitted"
        assert sub.status_events.count() == 1

    def test_accepts_the_choices_enum(self):
        from apps.submissions.models import SubmissionStatus

        sub = ServiceSubmissionFactory(biotools_url="")
        assert set_status(sub, SubmissionStatus.APPROVED, actor="admin:jane")
        assert sub.status_events.order_by("id").last().to_status == "approved"

    def test_from_status_is_read_from_the_database(self):
        sub = ServiceSubmissionFactory(biotools_url="")
        stale = ServiceSubmission.objects.get(pk=sub.pk)
        set_status(sub, "under_review", actor="admin:jane")
        set_status(stale, "approved", actor="admin:john")
        event = sub.status_events.order_by("id").last()
        assert event.from_status == "under_review"
        assert event.to_status == "approved"

    def test_stale_instance_already_at_target_is_a_no_op(self):
        sub = ServiceSubmissionFactory(biotools_url="")
        stale = ServiceSubmission.objects.get(pk=sub.pk)
        set_status(sub, "approved", actor="admin:jane")
        assert set_status(stale, "approved", actor="admin:john") is False
        assert sub.status_events.count() == 2
        assert stale.status == "approved"

    def test_extra_fields_are_saved_with_the_status(self):
        sub = ServiceSubmissionFactory(
            status="approved", primary_maturity_tag="mature", biotools_url=""
        )
        sub.primary_maturity_tag = None
        set_status(
            sub, "deprecated", actor="submitter", extra_fields=["primary_maturity_tag"]
        )
        sub.refresh_from_db()
        assert sub.status == "deprecated"
        assert sub.primary_maturity_tag is None

    def test_other_extra_fields_are_saved_with_the_status(self):
        sub = ServiceSubmissionFactory(biotools_url="")
        sub.comments = "Saved with the status"
        set_status(sub, "approved", actor="admin:jane", extra_fields=["comments"])
        assert ServiceSubmission.objects.get(pk=sub.pk).comments == (
            "Saved with the status"
        )

    @pytest.mark.parametrize(
        "target", ["draft", "submitted", "under_review", "rejected", "deprecated"]
    )
    def test_tags_are_cleared_whatever_the_instance_holds(self, target):
        """The caller's copy may have been loaded before the tags were set."""
        sub = ServiceSubmissionFactory(
            status="approved",
            primary_maturity_tag="mature",
            secondary_maturity_tags=["unstable"],
            biotools_url="",
        )
        stale = ServiceSubmission.objects.get(pk=sub.pk)
        stale.primary_maturity_tag = None
        stale.secondary_maturity_tags = []
        set_status(stale, target, actor="admin:jane")
        stored = ServiceSubmission.objects.get(pk=sub.pk)
        assert stored.status == target
        assert stored.primary_maturity_tag is None
        assert stored.secondary_maturity_tags == []

    def test_approval_does_not_write_the_tags(self):
        sub = ServiceSubmissionFactory(status="under_review", biotools_url="")
        ServiceSubmission.objects.filter(pk=sub.pk).update(
            primary_maturity_tag="mature"
        )
        set_status(sub, "approved", actor="admin:jane")
        stored = ServiceSubmission.objects.get(pk=sub.pk)
        assert (stored.status, stored.primary_maturity_tag) == ("approved", "mature")

    def test_only_listed_fields_are_written(self):
        sub = ServiceSubmissionFactory(biotools_url="")
        sub.comments = "Unsaved note"
        set_status(sub, "approved", actor="admin:jane")
        sub.refresh_from_db()
        assert sub.comments == ""

    def test_updated_at_is_untouched_unless_requested(self):
        sub = ServiceSubmissionFactory(biotools_url="")
        before = ServiceSubmission.objects.get(pk=sub.pk).updated_at
        set_status(sub, "approved", actor="admin:jane")
        assert ServiceSubmission.objects.get(pk=sub.pk).updated_at == before
        set_status(sub, "submitted", actor="admin:jane", extra_fields=["updated_at"])
        assert ServiceSubmission.objects.get(pk=sub.pk).updated_at != before

    def test_locks_the_row_before_it_reads_the_status(self, locked_models):
        """SQLite ignores row locks; the lock must at least be requested, and
        first, so two status changes cannot both start from the same status."""
        sub = ServiceSubmissionFactory(biotools_url="")
        set_status(sub, "submitted", actor="admin:jane")  # unchanged: no write
        assert locked_models == ["ServiceSubmission"]
        del locked_models[:]
        set_status(sub, "under_review", actor="admin:jane")
        assert locked_models[0] == "ServiceSubmission"

    def test_failed_event_write_rolls_back_the_status(self, monkeypatch):
        sub = ServiceSubmissionFactory(biotools_url="")

        def boom(*args, **kwargs):
            raise RuntimeError("event write failed")

        monkeypatch.setattr(SubmissionStatusEvent.objects, "create", boom)
        with pytest.raises(RuntimeError):
            set_status(sub, "approved", actor="admin:jane")
        assert ServiceSubmission.objects.get(pk=sub.pk).status == "submitted"


# ---------------------------------------------------------------------------
# Every entry path records its status change
# ---------------------------------------------------------------------------

from rest_framework.test import APIClient  # noqa: E402

from apps.submissions.lifecycle import get_no_reset_fields  # noqa: E402
from tests.factories import APIKeyFactory  # noqa: E402
from tests.helpers import (  # noqa: E402
    api_create_payload,
    base_form_data,
    edit_form_data,
    edit_form_payload,
)

NEW_TEXT = "A changed description that is definitely long enough to be valid."
CHANGELIST = "admin:submissions_servicesubmission_changelist"
CHANGE = "admin:submissions_servicesubmission_change"


def _transitions(sub):
    return [(e.from_status, e.to_status) for e in sub.status_events.order_by("id")]


@pytest.fixture
def exempt(settings):
    settings.SUBMISSION_NO_RESET_FIELDS = ["github_url"]
    get_no_reset_fields.cache_clear()
    yield
    get_no_reset_fields.cache_clear()


def _grant(client, sub):
    key_obj, _ = APIKeyFactory.create_with_plaintext(submission=sub)
    session = client.session
    session["edit_grants"] = {str(sub.pk): str(key_obj.pk)}
    session.save()


class TestBrokenHistories:
    @pytest.mark.allow_status_drift
    def test_a_continuous_history_is_not_reported(self):
        sub = ServiceSubmissionFactory(biotools_url="")
        for status in ("approved", "deprecated", "submitted"):
            set_status(sub, status, actor="admin:jane")
        assert status_history_broken() == []

    @pytest.mark.allow_status_drift
    def test_a_submission_without_events_is_left_to_the_drift_check(self):
        sub = ServiceSubmissionFactory(biotools_url="")
        SubmissionStatusEvent.objects.filter(submission=sub).delete()
        assert status_history_broken() == []

    @pytest.mark.allow_status_drift
    def test_each_kind_of_break_is_reported_for_its_own_submission_only(self):
        no_start = ServiceSubmissionFactory(biotools_url="")
        set_status(no_start, "approved", actor="admin:jane")
        SubmissionStatusEvent.objects.filter(
            submission=no_start, from_status__isnull=True
        ).delete()
        gap = ServiceSubmissionFactory(biotools_url="")
        SubmissionStatusEvent.objects.create(
            submission=gap,
            from_status="draft",
            to_status="approved",
            changed_at=gap.updated_at,
            changed_by="system",
        )
        fine = ServiceSubmissionFactory(biotools_url="")
        set_status(fine, "approved", actor="admin:jane")
        assert sorted(status_history_broken()) == sorted(
            [str(no_start.pk), str(gap.pk)]
        )


class TestWebPaths:
    def test_register_records_submitted(self, client, settings):
        settings.ALTCHA_HMAC_KEY = ""
        resp = client.post(reverse("submissions:register"), data=base_form_data())
        assert resp.status_code == 302
        sub = ServiceSubmission.objects.get()
        assert _transitions(sub) == [(None, "submitted")]

    @pytest.mark.parametrize("status", ["approved", "deprecated", "under_review"])
    def test_register_ignores_a_status_sent_by_the_client(
        self, client, settings, status
    ):
        settings.ALTCHA_HMAC_KEY = ""
        data = {**base_form_data(), "status": status}
        resp = client.post(reverse("submissions:register"), data=data)
        assert resp.status_code == 302
        sub = ServiceSubmission.objects.get()
        assert sub.status == "submitted"
        assert _transitions(sub) == [(None, "submitted")]

    @pytest.mark.parametrize("status", ["approved", "deprecated", "rejected"])
    def test_edit_ignores_a_status_sent_by_the_client(
        self, client, settings, exempt, status
    ):
        settings.ALTCHA_HMAC_KEY = ""
        sub = ServiceSubmissionFactory(status="under_review", biotools_url="")
        _grant(client, sub)
        data = edit_form_data(sub, github_url="https://github.com/example/moved")
        data["status"] = status
        resp = client.post(reverse("submissions:edit", args=[sub.pk]), data)
        assert resp.status_code == 302
        sub.refresh_from_db()
        assert (sub.status, sub.github_url) == (
            "under_review",
            "https://github.com/example/moved",
        )
        assert len(_transitions(sub)) == 1

    def test_self_deprecate_records_event_and_clears_tags(self, client):
        sub = ServiceSubmissionFactory(
            status="approved", primary_maturity_tag="mature", biotools_url=""
        )
        _grant(client, sub)
        client.post(reverse("submissions:edit", args=[sub.pk]), {"_deprecate": "1"})
        sub.refresh_from_db()
        assert sub.status == "deprecated"
        assert sub.primary_maturity_tag is None
        event = sub.status_events.order_by("id").last()
        assert (event.from_status, event.to_status, event.changed_by) == (
            "approved",
            "deprecated",
            "submitter",
        )

    def test_self_deprecate_twice_records_once(self, client):
        sub = ServiceSubmissionFactory(status="approved", biotools_url="")
        _grant(client, sub)
        url = reverse("submissions:edit", args=[sub.pk])
        client.post(url, {"_deprecate": "1"})
        client.post(url, {"_deprecate": "1"})
        assert _transitions(sub) == [(None, "approved"), ("approved", "deprecated")]

    def test_self_deprecate_from_a_stale_page_logs_and_notifies_once(
        self, client, settings, django_capture_on_commit_callbacks
    ):
        """Two tabs: the second request loaded the service before the first
        one deprecated it."""
        from unittest import mock

        from apps.submissions.models import SubmissionChangeLog
        from apps.submissions.views import EditView

        settings.CELERY_TASK_ALWAYS_EAGER = True
        sub = ServiceSubmissionFactory(status="approved", biotools_url="")
        _grant(client, sub)
        url = reverse("submissions:edit", args=[sub.pk])
        with mock.patch(
            "apps.submissions.views.send_submission_notification.delay"
        ) as first_notify:
            with django_capture_on_commit_callbacks(execute=True):
                client.post(url, {"_deprecate": "1"})
        assert first_notify.call_count == 1
        real_get = EditView._get_submission

        def stale_get(view, *args, **kwargs):
            obj = real_get(view, *args, **kwargs)
            obj.status = "approved"
            return obj

        with (
            mock.patch.object(EditView, "_get_submission", stale_get),
            mock.patch(
                "apps.submissions.views.send_submission_notification.delay"
            ) as notify,
            django_capture_on_commit_callbacks(execute=True),
        ):
            resp = client.post(url, {"_deprecate": "1"})
        assert resp.status_code == 302
        notify.assert_not_called()
        assert SubmissionChangeLog.objects.filter(submission=sub).count() == 1
        assert _transitions(sub)[-1] == ("approved", "deprecated")

    def test_edit_reset_records_event_and_saves_content(self, client, exempt):
        sub = ServiceSubmissionFactory(
            status="approved", primary_maturity_tag="mature", biotools_url=""
        )
        _grant(client, sub)
        new_text = NEW_TEXT
        data = edit_form_data(sub, service_description=new_text)
        resp = client.post(reverse("submissions:edit", args=[sub.pk]), data=data)
        assert resp.status_code == 302
        sub.refresh_from_db()
        assert sub.status == "submitted"
        assert sub.service_description == new_text
        assert sub.primary_maturity_tag is None
        event = sub.status_events.order_by("id").last()
        assert (event.from_status, event.to_status, event.changed_by) == (
            "approved",
            "submitted",
            "submitter",
        )

    def test_exempt_edit_records_nothing(self, client, exempt):
        sub = ServiceSubmissionFactory(status="approved", biotools_url="")
        _grant(client, sub)
        data = edit_form_data(sub, github_url="https://github.com/example/new-repo")
        client.post(reverse("submissions:edit", args=[sub.pk]), data=data)
        sub.refresh_from_db()
        assert sub.github_url == "https://github.com/example/new-repo"
        assert len(_transitions(sub)) == 1

    def test_edit_of_unapproved_submission_records_nothing(self, client, exempt):
        sub = ServiceSubmissionFactory(biotools_url="")
        _grant(client, sub)
        data = edit_form_data(sub, service_description="Another long enough text. " * 3)
        client.post(reverse("submissions:edit", args=[sub.pk]), data=data)
        assert len(_transitions(sub)) == 1

    def test_unchanged_resend_of_approved_service_records_nothing(self, client, exempt):
        sub = ServiceSubmissionFactory(status="approved", biotools_url="")
        _grant(client, sub)
        client.post(
            reverse("submissions:edit", args=[sub.pk]), data=edit_form_data(sub)
        )
        sub.refresh_from_db()
        assert sub.status == "approved"
        assert len(_transitions(sub)) == 1


class TestApiPaths:
    def test_create_records_submitted(self):
        resp = APIClient().post(
            "/api/v1/submissions/", api_create_payload(), format="json"
        )
        assert resp.status_code == 201, resp.content
        sub = ServiceSubmission.objects.get(pk=resp.json()["id"])
        assert sub.status == "submitted"
        assert _transitions(sub) == [(None, "submitted")]

    @pytest.mark.parametrize("status", ["approved", "deprecated", "under_review"])
    def test_create_ignores_a_status_sent_by_the_client(self, status):
        payload = {**api_create_payload(), "status": status}
        resp = APIClient().post("/api/v1/submissions/", payload, format="json")
        assert resp.status_code == 201, resp.content
        sub = ServiceSubmission.objects.get(pk=resp.json()["id"])
        assert sub.status == "submitted"
        assert _transitions(sub) == [(None, "submitted")]

    @pytest.mark.parametrize("status", ["approved", "deprecated", "rejected"])
    def test_patch_ignores_a_status_sent_by_the_client(self, exempt, status):
        sub = ServiceSubmissionFactory(status="under_review", biotools_url="")
        client, _ = self._client(sub)
        resp = client.patch(
            f"/api/v1/submissions/{sub.pk}/",
            {"status": status, "github_url": "https://github.com/example/moved"},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        sub.refresh_from_db()
        assert (sub.status, sub.github_url) == (
            "under_review",
            "https://github.com/example/moved",
        )
        assert len(_transitions(sub)) == 1

    def _client(self, sub):
        key_obj, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        return client, key_obj

    def test_patch_reset_records_event(self, exempt):
        sub = ServiceSubmissionFactory(
            status="approved", primary_maturity_tag="mature", biotools_url=""
        )
        client, key_obj = self._client(sub)
        resp = client.patch(
            f"/api/v1/submissions/{sub.pk}/",
            {"service_description": NEW_TEXT},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        sub.refresh_from_db()
        assert sub.status == "submitted"
        assert sub.primary_maturity_tag is None
        event = sub.status_events.order_by("id").last()
        assert (event.from_status, event.to_status) == ("approved", "submitted")
        assert event.changed_by == f"api:{key_obj.label}"

    def test_patch_reset_still_moves_updated_at(self, exempt):
        sub = ServiceSubmissionFactory(status="approved", biotools_url="")
        before = ServiceSubmission.objects.get(pk=sub.pk).updated_at
        client, _ = self._client(sub)
        client.patch(
            f"/api/v1/submissions/{sub.pk}/",
            {"service_description": NEW_TEXT},
            format="json",
        )
        assert ServiceSubmission.objects.get(pk=sub.pk).updated_at != before

    def test_exempt_patch_records_nothing(self, exempt):
        sub = ServiceSubmissionFactory(status="approved", biotools_url="")
        client, _ = self._client(sub)
        resp = client.patch(
            f"/api/v1/submissions/{sub.pk}/",
            {"github_url": "https://github.com/example/new-repo"},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        assert len(_transitions(sub)) == 1

    def test_patch_with_no_real_change_records_nothing(self, exempt):
        sub = ServiceSubmissionFactory(status="approved", biotools_url="")
        client, _ = self._client(sub)
        client.patch(
            f"/api/v1/submissions/{sub.pk}/",
            {"service_description": sub.service_description},
            format="json",
        )
        sub.refresh_from_db()
        assert sub.status == "approved"
        assert len(_transitions(sub)) == 1


class TestAdminPaths:
    @pytest.mark.parametrize(
        "action,start,end",
        [
            ("action_approve", "submitted", "approved"),
            ("action_reject", "submitted", "rejected"),
            ("action_mark_under_review", "submitted", "under_review"),
            ("action_deprecate", "approved", "deprecated"),
            ("action_undeprecate", "deprecated", "submitted"),
        ],
    )
    def test_bulk_action_records_event(self, superuser_client, action, start, end):
        sub = ServiceSubmissionFactory(status=start, biotools_url="")
        superuser_client.post(
            reverse(CHANGELIST),
            {"action": action, "_selected_action": [str(sub.pk)]},
        )
        sub.refresh_from_db()
        assert sub.status == end
        event = sub.status_events.order_by("id").last()
        assert (event.from_status, event.to_status, event.changed_by) == (
            start,
            end,
            "admin:testadmin",
        )

    @pytest.mark.parametrize(
        "button,start,end",
        [
            ("_approve", "submitted", "approved"),
            ("_reject", "submitted", "rejected"),
            ("_under_review", "submitted", "under_review"),
            ("_deprecate", "approved", "deprecated"),
            ("_undeprecate", "deprecated", "submitted"),
        ],
    )
    def test_change_form_button_records_event(
        self, superuser_client, button, start, end
    ):
        sub = ServiceSubmissionFactory(status=start, biotools_url="")
        resp = superuser_client.post(
            reverse(CHANGE, args=[sub.pk]), edit_form_payload(sub, **{button: "1"})
        )
        assert resp.status_code == 302
        sub.refresh_from_db()
        assert sub.status == end
        assert sub.status_events.order_by("id").last().to_status == end

    def test_bulk_action_on_current_status_records_nothing(self, superuser_client):
        sub = ServiceSubmissionFactory(status="approved", biotools_url="")
        superuser_client.post(
            reverse(CHANGELIST),
            {"action": "action_approve", "_selected_action": [str(sub.pk)]},
        )
        assert len(_transitions(sub)) == 1

    def test_deprecate_clears_tags_with_the_status(self, superuser_client):
        sub = ServiceSubmissionFactory(
            status="approved",
            primary_maturity_tag="mature",
            secondary_maturity_tags=["unstable"],
            biotools_url="",
        )
        superuser_client.post(
            reverse(CHANGELIST),
            {"action": "action_deprecate", "_selected_action": [str(sub.pk)]},
        )
        sub.refresh_from_db()
        assert sub.primary_maturity_tag is None
        assert sub.secondary_maturity_tags == []
        assert sub.last_change_summary["changes"][0]["new"] == "Deprecated"

    def test_admin_add_records_submitted(self, superuser_client):
        template = ServiceSubmissionFactory(biotools_url="")
        payload = edit_form_payload(template, service_name="Example Admin Added")
        resp = superuser_client.post(
            reverse("admin:submissions_servicesubmission_add"), data=payload
        )
        assert resp.status_code == 302
        added = ServiceSubmission.objects.get(service_name="Example Admin Added")
        assert _transitions(added) == [(None, "submitted")]

    def test_plain_admin_save_records_nothing(self, superuser_client):
        sub = ServiceSubmissionFactory(status="approved", biotools_url="")
        resp = superuser_client.post(
            reverse(CHANGE, args=[sub.pk]),
            edit_form_payload(sub, service_name="Example Renamed"),
        )
        assert resp.status_code == 302
        assert len(_transitions(sub)) == 1


class TestBulkStatusChange:
    """Another request changes the service after the action was chosen:
    everything recorded comes from the row read under the lock."""

    @pytest.fixture
    def stale_rows(self, monkeypatch, superuser_client):
        """run(action, sub, other): ``other(pk)`` changes the service after the
        action was chosen and before the rows are read for the change."""
        from apps.submissions.admin import ServiceSubmissionAdmin

        real = ServiceSubmissionAdmin._change_status

        def run(action, sub, other):
            def change_status(admin, request, queryset, new_status, label):
                other(sub.pk)
                return real(admin, request, queryset, new_status, label)

            monkeypatch.setattr(ServiceSubmissionAdmin, "_change_status", change_status)
            return superuser_client.post(
                reverse(CHANGELIST),
                {"action": action, "_selected_action": [str(sub.pk)]},
            )

        return run

    @staticmethod
    def _move(status):
        return lambda pk: set_status(
            ServiceSubmission.objects.get(pk=pk), status, actor="admin:john"
        )

    @staticmethod
    def _status_change(sub):
        from apps.submissions.models import SubmissionChangeLog

        log = SubmissionChangeLog.objects.filter(submission=sub).latest("id")
        return next(c for c in log.changes if c["field"] == "status")

    def test_change_log_names_the_status_found_under_the_lock(self, stale_rows):
        sub = ServiceSubmissionFactory(biotools_url="")
        stale_rows("action_approve", sub, self._move("under_review"))
        assert _transitions(sub)[-1] == ("under_review", "approved")
        assert self._status_change(sub)["old"] == "Under Review"
        sub.refresh_from_db()
        old = sub.last_change_summary["changes"][0]["old"]
        assert old == "Under Review"

    def test_a_service_moved_to_the_target_meanwhile_is_left_alone(
        self, stale_rows, django_capture_on_commit_callbacks
    ):
        from unittest import mock

        from apps.submissions.models import SubmissionChangeLog

        sub = ServiceSubmissionFactory(biotools_url="")
        with mock.patch(
            "apps.submissions.admin.send_submission_notification.delay"
        ) as notify:
            with django_capture_on_commit_callbacks(execute=True):
                stale_rows("action_approve", sub, self._move("approved"))
        notify.assert_not_called()
        assert not SubmissionChangeLog.objects.filter(submission=sub).exists()
        assert _transitions(sub)[-1] == ("submitted", "approved")
        assert len(_transitions(sub)) == 2

    def test_tags_given_meanwhile_are_cleared_and_logged(self, stale_rows):
        sub = ServiceSubmissionFactory(status="under_review", biotools_url="")

        def approve_and_tag(pk):
            set_status(
                ServiceSubmission.objects.get(pk=pk), "approved", actor="admin:john"
            )
            ServiceSubmission.objects.filter(pk=pk).update(
                primary_maturity_tag="mature", secondary_maturity_tags=["unstable"]
            )

        stale_rows("action_reject", sub, approve_and_tag)
        sub.refresh_from_db()
        assert sub.status == "rejected"
        assert sub.primary_maturity_tag is None
        assert sub.secondary_maturity_tags == []
        log = sub.change_log.latest("id")
        assert {"status", "primary_maturity_tag", "secondary_maturity_tags"} == {
            c["field"] for c in log.changes
        }

    def test_each_row_is_locked_before_it_is_changed(
        self, superuser_client, locked_models, monkeypatch
    ):
        from apps.submissions import admin as subs_admin

        sub = ServiceSubmissionFactory(biotools_url="")
        real = subs_admin.set_status
        locks_at_call = []

        def spy(*args, **kwargs):
            locks_at_call.append(len(locked_models))
            return real(*args, **kwargs)

        monkeypatch.setattr(subs_admin, "set_status", spy)
        superuser_client.post(
            reverse(CHANGELIST),
            {"action": "action_approve", "_selected_action": [str(sub.pk)]},
        )
        assert locks_at_call and locks_at_call[0] >= 1

    def test_a_failed_audit_entry_undoes_the_status_change(
        self, superuser_client, monkeypatch
    ):
        from apps.submissions.models import SubmissionChangeLog

        sub = ServiceSubmissionFactory(biotools_url="")

        def boom(*args, **kwargs):
            raise RuntimeError("log write failed")

        monkeypatch.setattr(SubmissionChangeLog.objects, "create", boom)
        with pytest.raises(RuntimeError):
            superuser_client.post(
                reverse(CHANGELIST),
                {"action": "action_approve", "_selected_action": [str(sub.pk)]},
            )
        assert ServiceSubmission.objects.get(pk=sub.pk).status == "submitted"
        assert len(_transitions(sub)) == 1

    def test_the_notification_is_sent_once_per_changed_service(
        self, superuser_client, django_capture_on_commit_callbacks
    ):
        from unittest import mock

        subs = [ServiceSubmissionFactory(biotools_url="") for _ in range(3)]
        with mock.patch(
            "apps.submissions.admin.send_submission_notification.delay"
        ) as notify:
            with django_capture_on_commit_callbacks(execute=True):
                superuser_client.post(
                    reverse(CHANGELIST),
                    {
                        "action": "action_approve",
                        "_selected_action": [str(s.pk) for s in subs],
                    },
                )
        assert notify.call_count == 3


class TestOwnerAuditEntries:
    """The audit entry of an owner's change is stored with the change."""

    def _fail_log_writes(self, monkeypatch):
        from apps.submissions.models import SubmissionChangeLog

        def boom(*args, **kwargs):
            raise RuntimeError("log write failed")

        monkeypatch.setattr(SubmissionChangeLog.objects, "create", boom)

    def test_a_failed_audit_entry_undoes_a_web_edit(
        self, client, exempt, monkeypatch, settings
    ):
        settings.ALTCHA_HMAC_KEY = ""
        sub = ServiceSubmissionFactory(
            status="approved", primary_maturity_tag="mature", biotools_url=""
        )
        _grant(client, sub)
        old_description = sub.service_description
        self._fail_log_writes(monkeypatch)
        data = edit_form_data(sub, service_description=NEW_TEXT)
        with pytest.raises(RuntimeError):
            client.post(reverse("submissions:edit", args=[sub.pk]), data)
        stored = ServiceSubmission.objects.get(pk=sub.pk)
        assert stored.service_description == old_description
        assert (stored.status, stored.primary_maturity_tag) == ("approved", "mature")
        assert stored.last_change_summary in (None, {})
        assert len(_transitions(sub)) == 1

    def test_a_failed_audit_entry_undoes_an_api_patch(self, monkeypatch):
        sub = ServiceSubmissionFactory(
            status="approved", primary_maturity_tag="mature", biotools_url=""
        )
        key_obj, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        old_description = sub.service_description
        self._fail_log_writes(monkeypatch)
        with pytest.raises(RuntimeError):
            client.patch(
                f"/api/v1/submissions/{sub.pk}/",
                {"service_description": NEW_TEXT},
                format="json",
            )
        stored = ServiceSubmission.objects.get(pk=sub.pk)
        assert stored.service_description == old_description
        assert (stored.status, stored.primary_maturity_tag) == ("approved", "mature")
        assert len(_transitions(sub)) == 1

    def test_a_failed_audit_entry_undoes_a_self_deprecation(self, client, monkeypatch):
        sub = ServiceSubmissionFactory(
            status="approved", primary_maturity_tag="mature", biotools_url=""
        )
        _grant(client, sub)
        self._fail_log_writes(monkeypatch)
        with pytest.raises(RuntimeError):
            client.post(reverse("submissions:edit", args=[sub.pk]), {"_deprecate": "1"})
        stored = ServiceSubmission.objects.get(pk=sub.pk)
        assert (stored.status, stored.primary_maturity_tag) == ("approved", "mature")
        assert len(_transitions(sub)) == 1

    def test_self_deprecation_logs_the_status_found_under_the_lock(self, client):
        """The owner's page was loaded while the service was approved; an admin
        has since put it under review."""
        from unittest import mock

        from apps.submissions.models import SubmissionChangeLog
        from apps.submissions.views import EditView

        sub = ServiceSubmissionFactory(status="approved", biotools_url="")
        _grant(client, sub)
        set_status(
            ServiceSubmission.objects.get(pk=sub.pk), "under_review", actor="admin:jane"
        )
        real_get = EditView._get_submission

        def stale_get(view, *args, **kwargs):
            obj = real_get(view, *args, **kwargs)
            obj.status = "approved"
            return obj

        with mock.patch.object(EditView, "_get_submission", stale_get):
            client.post(reverse("submissions:edit", args=[sub.pk]), {"_deprecate": "1"})
        assert _transitions(sub)[-1] == ("under_review", "deprecated")
        log = SubmissionChangeLog.objects.get(submission=sub)
        assert log.changes[0]["old"] == "Under Review"

    def test_self_deprecation_locks_the_row_before_changing_it(
        self, client, locked_models, monkeypatch
    ):
        from apps.submissions import views

        sub = ServiceSubmissionFactory(status="approved", biotools_url="")
        _grant(client, sub)
        real = views.set_status
        locks_at_call = []

        def spy(*args, **kwargs):
            locks_at_call.append(len(locked_models))
            return real(*args, **kwargs)

        monkeypatch.setattr(views, "set_status", spy)
        client.post(reverse("submissions:edit", args=[sub.pk]), {"_deprecate": "1"})
        assert locks_at_call and locks_at_call[0] >= 1

    def test_self_deprecation_of_a_deleted_service_is_a_404(self, client):
        from unittest import mock

        from apps.submissions.views import EditView

        sub = ServiceSubmissionFactory(status="approved", biotools_url="")
        _grant(client, sub)
        real_get = EditView._get_submission

        def get_then_delete(view, *args, **kwargs):
            obj = real_get(view, *args, **kwargs)
            ServiceSubmission.objects.filter(pk=sub.pk).delete()
            return obj

        with mock.patch.object(EditView, "_get_submission", get_then_delete):
            resp = client.post(
                reverse("submissions:edit", args=[sub.pk]), {"_deprecate": "1"}
            )
        assert resp.status_code == 404


class TestInvariantHelper:
    @pytest.mark.allow_status_drift
    def test_detects_a_status_written_without_an_event(self):
        sub = ServiceSubmissionFactory(biotools_url="")
        assert status_history_drift() == []
        ServiceSubmission.objects.filter(pk=sub.pk).update(status="approved")
        assert status_history_drift() == [(str(sub.pk), "approved", "submitted")]

    @pytest.mark.allow_status_drift
    def test_detects_a_submission_without_any_event(self):
        sub = ServiceSubmissionFactory(biotools_url="")
        sub.status_events.all().delete()
        assert status_history_drift() == [(str(sub.pk), "submitted", None)]


class TestVersionAfterStatusChange:
    """A status change raises the version like any other save: an edit loaded
    before it is refused and nothing of it is stored."""

    MOVED = "https://github.com/example/moved"

    def _approved(self):
        sub = ServiceSubmissionFactory(biotools_url="")
        set_status(sub, "approved", actor="admin:jane")
        return sub

    @staticmethod
    def _reject_elsewhere(sub):
        set_status(
            ServiceSubmission.objects.get(pk=sub.pk), "rejected", actor="admin:jane"
        )

    def _assert_edit_refused(self, sub):
        stored = ServiceSubmission.objects.get(pk=sub.pk)
        assert stored.status == "rejected"
        assert stored.github_url != self.MOVED
        assert _transitions(sub)[-1] == ("approved", "rejected")

    def test_a_status_change_raises_the_version(self):
        sub = ServiceSubmissionFactory(biotools_url="")
        before = ServiceSubmission.objects.get(pk=sub.pk).version
        set_status(sub, "approved", actor="admin:jane")
        assert ServiceSubmission.objects.get(pk=sub.pk).version == before + 1
        assert sub.version == before + 1

    def test_a_no_op_does_not_raise_the_version(self):
        sub = ServiceSubmissionFactory(biotools_url="")
        before = ServiceSubmission.objects.get(pk=sub.pk).version
        set_status(sub, "submitted", actor="admin:jane")
        assert ServiceSubmission.objects.get(pk=sub.pk).version == before

    def test_creation_event_leaves_the_version_alone(self):
        """A new row starts at version 1; the factory's link changes raise it
        later, the event write does not."""
        sub = ServiceSubmissionFactory.build(
            biotools_url="", service_center=ServiceCenterFactory()
        )
        sub.save()
        assert sub.version == 1
        assert ServiceSubmission.objects.get(pk=sub.pk).version == 1
        assert sub.status_events.count() == 1

    def test_saving_a_stale_copy_after_a_status_change_is_refused(self):
        sub = self._approved()
        loaded = ServiceSubmission.objects.get(pk=sub.pk)
        self._reject_elsewhere(sub)
        loaded.github_url = self.MOVED
        with pytest.raises(EditConflict):
            loaded.save()
        self._assert_edit_refused(sub)

    def test_a_stale_copy_can_still_change_the_status(self):
        """set_status takes the version of the locked row: the caller has
        already passed its own conflict check."""
        sub = self._approved()
        stale = ServiceSubmission.objects.get(pk=sub.pk)
        ServiceSubmission.objects.filter(pk=sub.pk).update(version=F("version") + 5)
        assert set_status(stale, "rejected", actor="admin:jane") is True
        stored = ServiceSubmission.objects.get(pk=sub.pk)
        assert stored.status == "rejected"
        assert stored.version == stale.version

    def test_a_copy_can_be_saved_after_its_own_status_change(self):
        sub = self._approved()
        loaded = ServiceSubmission.objects.get(pk=sub.pk)
        set_status(loaded, "under_review", actor="admin:jane")
        loaded.github_url = self.MOVED
        loaded.save()
        stored = ServiceSubmission.objects.get(pk=sub.pk)
        assert (stored.status, stored.github_url) == ("under_review", self.MOVED)

    def test_saving_a_copy_whose_row_was_deleted_is_refused(self):
        sub = self._approved()
        loaded = ServiceSubmission.objects.get(pk=sub.pk)
        ServiceSubmission.objects.filter(pk=sub.pk).delete()
        loaded.github_url = self.MOVED
        with pytest.raises(EditConflict):
            loaded.save()
        assert not ServiceSubmission.objects.filter(pk=sub.pk).exists()
        assert not SubmissionStatusEvent.objects.filter(submission_id=sub.pk).exists()

    def test_set_status_on_a_deleted_row_fails_cleanly(self):
        sub = self._approved()
        ServiceSubmission.objects.filter(pk=sub.pk).delete()
        with pytest.raises(ServiceSubmission.DoesNotExist):
            set_status(sub, "rejected", actor="admin:jane")

    def test_web_edit_loaded_before_a_status_change_is_refused(self, client, exempt):
        sub = self._approved()
        _grant(client, sub)
        data = edit_form_data(sub, github_url=self.MOVED)
        data["loaded_version"] = str(ServiceSubmission.objects.get(pk=sub.pk).version)
        self._reject_elsewhere(sub)
        resp = client.post(reverse("submissions:edit", args=[sub.pk]), data)
        assert resp.status_code == 409
        assert EditConflict.message in resp.content.decode()
        self._assert_edit_refused(sub)

    def test_api_edit_with_an_outdated_etag_is_refused(self, exempt):
        sub = self._approved()
        key_obj, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        etag = client.get(f"/api/v1/submissions/{sub.pk}/")["ETag"]
        self._reject_elsewhere(sub)
        resp = client.patch(
            f"/api/v1/submissions/{sub.pk}/",
            {"github_url": self.MOVED},
            format="json",
            HTTP_IF_MATCH=etag,
        )
        assert resp.status_code == 412, resp.content
        self._assert_edit_refused(sub)

    def test_admin_change_form_loaded_before_a_status_change_is_refused(
        self, superuser_client
    ):
        sub = self._approved()
        data = edit_form_payload(sub, github_url=self.MOVED)
        data["loaded_version"] = str(ServiceSubmission.objects.get(pk=sub.pk).version)
        self._reject_elsewhere(sub)
        resp = superuser_client.post(reverse(CHANGE, args=[sub.pk]), data)
        assert resp.status_code == 302
        page = superuser_client.get(resp["Location"]).content.decode()
        assert "changed while you were editing" in page
        self._assert_edit_refused(sub)

    def test_admin_change_form_button_after_a_save_keeps_the_status_change(
        self, superuser_client
    ):
        """The form is saved first (raising the version), then the button
        changes the status in the same request."""
        sub = ServiceSubmissionFactory(biotools_url="")
        data = edit_form_payload(sub, comments="Reviewed", _approve="1")
        data["loaded_version"] = str(sub.version)
        resp = superuser_client.post(reverse(CHANGE, args=[sub.pk]), data)
        assert resp.status_code == 302
        stored = ServiceSubmission.objects.get(pk=sub.pk)
        assert (stored.status, stored.comments) == ("approved", "Reviewed")
        assert _transitions(sub)[-1] == ("submitted", "approved")
