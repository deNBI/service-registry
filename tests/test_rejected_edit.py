"""An owner edit of a rejected service returns it to submitted.

Any real change counts, also one to a field listed in no_reset_fields (those
only keep an approved service approved). An edit that changes nothing, and an
edit of any other status, leaves the status alone.
"""

from unittest import mock

import pytest
from django.core import mail
from django.urls import reverse
from rest_framework.test import APIClient

from apps.submissions.lifecycle import get_no_reset_fields
from apps.submissions.models import SubmissionChangeLog
from apps.submissions.tasks import send_submission_notification
from tests.factories import APIKeyFactory, ServiceSubmissionFactory
from tests.helpers import edit_form_data

pytestmark = pytest.mark.django_db

NEW_TEXT = "A changed description that is definitely long enough to be valid."
NEW_REPO = "https://github.com/example/new-repo"


@pytest.fixture
def exempt(settings):
    settings.SUBMISSION_NO_RESET_FIELDS = ["github_url"]
    get_no_reset_fields.cache_clear()
    yield
    get_no_reset_fields.cache_clear()


@pytest.fixture
def web_notify():
    with mock.patch("apps.submissions.views.send_update_notification") as task:
        yield task.delay


@pytest.fixture
def api_notify():
    with mock.patch("apps.api.views.send_update_notification") as task:
        yield task.delay


def _transitions(sub):
    return [(e.from_status, e.to_status) for e in sub.status_events.order_by("id")]


def _rejected(status="rejected", **extra):
    return ServiceSubmissionFactory(
        status=status,
        biotools_url="",
        primary_maturity_tag="mature",
        secondary_maturity_tags=["unstable"],
        **extra,
    )


def _web_edit(client, sub, **overrides):
    key_obj, _ = APIKeyFactory.create_with_plaintext(submission=sub)
    session = client.session
    session["edit_grants"] = {str(sub.pk): str(key_obj.pk)}
    session.save()
    resp = client.post(
        reverse("submissions:edit", args=[sub.pk]),
        data=edit_form_data(sub, **overrides),
    )
    assert resp.status_code == 302, resp.content
    sub.refresh_from_db()


def _api_patch(sub, body):
    key_obj, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
    resp = client.patch(f"/api/v1/submissions/{sub.pk}/", body, format="json")
    assert resp.status_code == 200, resp.content
    sub.refresh_from_db()
    return key_obj


class TestWebEdit:
    def test_real_change_returns_a_rejected_service_to_submitted(
        self, client, exempt, web_notify
    ):
        sub = _rejected()
        _web_edit(client, sub, service_description=NEW_TEXT)
        assert sub.status == "submitted"
        assert sub.service_description == NEW_TEXT
        assert _transitions(sub)[-1] == ("rejected", "submitted")
        event = sub.status_events.order_by("id").last()
        assert event.changed_by == "submitter"
        assert web_notify.call_args.kwargs["status_reset"] is True

    def test_the_tags_are_kept(self, client, exempt, web_notify):
        sub = _rejected()
        _web_edit(client, sub, service_description=NEW_TEXT)
        assert sub.primary_maturity_tag == "mature"
        assert sub.secondary_maturity_tags == ["unstable"]

    def test_the_change_log_records_content_and_status(
        self, client, exempt, web_notify
    ):
        sub = _rejected()
        _web_edit(client, sub, service_description=NEW_TEXT)
        log = SubmissionChangeLog.objects.get(submission=sub)
        by_field = {c["field"]: c for c in log.changes}
        assert set(by_field) == {"service_description", "status"}
        assert by_field["status"]["old"] == "Rejected"
        assert by_field["status"]["new"] == "Submitted"
        assert sub.last_change_summary["changes"] == log.changes

    def test_a_change_to_an_exempt_field_also_returns_it_to_submitted(
        self, client, exempt, web_notify
    ):
        sub = _rejected()
        _web_edit(client, sub, github_url=NEW_REPO)
        assert sub.github_url == NEW_REPO
        assert sub.status == "submitted"
        assert web_notify.call_args.kwargs["status_reset"] is True

    def test_an_edit_without_a_real_change_stays_rejected(
        self, client, exempt, web_notify
    ):
        sub = _rejected()
        _web_edit(client, sub)
        assert sub.status == "rejected"
        assert len(_transitions(sub)) == 1
        assert not SubmissionChangeLog.objects.filter(submission=sub).exists()
        assert web_notify.call_args.kwargs["status_reset"] is False

    @pytest.mark.parametrize("status", ["under_review", "deprecated", "submitted"])
    def test_other_statuses_keep_their_status(self, client, exempt, web_notify, status):
        sub = ServiceSubmissionFactory(status=status, biotools_url="")
        _web_edit(client, sub, service_description=NEW_TEXT)
        assert sub.status == status
        assert len(_transitions(sub)) == 1
        assert web_notify.call_args.kwargs["status_reset"] is False


class TestApiPatch:
    def test_real_change_returns_a_rejected_service_to_submitted(
        self, exempt, api_notify
    ):
        sub = _rejected()
        key_obj = _api_patch(sub, {"service_description": NEW_TEXT})
        assert sub.status == "submitted"
        assert sub.service_description == NEW_TEXT
        assert sub.primary_maturity_tag == "mature"
        assert sub.secondary_maturity_tags == ["unstable"]
        event = sub.status_events.order_by("id").last()
        assert (event.from_status, event.to_status) == ("rejected", "submitted")
        assert event.changed_by == f"api:{key_obj.label}"
        assert api_notify.call_args.kwargs["status_reset"] is True

    def test_the_change_log_records_content_and_status(self, exempt, api_notify):
        sub = _rejected()
        _api_patch(sub, {"service_description": NEW_TEXT})
        log = SubmissionChangeLog.objects.get(submission=sub)
        assert {c["field"] for c in log.changes} == {"service_description", "status"}
        assert {c["field"] for c in sub.last_change_summary["changes"]} == {
            "service_description",
            "status",
        }

    def test_a_change_to_an_exempt_field_also_returns_it_to_submitted(
        self, exempt, api_notify
    ):
        sub = _rejected()
        _api_patch(sub, {"github_url": NEW_REPO})
        assert sub.status == "submitted"
        assert api_notify.call_args.kwargs["status_reset"] is True

    def test_resending_stored_values_stays_rejected(self, exempt, api_notify):
        sub = _rejected()
        _api_patch(sub, {"service_description": sub.service_description})
        assert sub.status == "rejected"
        assert len(_transitions(sub)) == 1
        assert not SubmissionChangeLog.objects.filter(submission=sub).exists()
        assert api_notify.call_args.kwargs["status_reset"] is False

    @pytest.mark.parametrize("status", ["under_review", "deprecated", "submitted"])
    def test_other_statuses_keep_their_status(self, exempt, api_notify, status):
        sub = ServiceSubmissionFactory(status=status, biotools_url="")
        _api_patch(sub, {"service_description": NEW_TEXT})
        assert sub.status == status
        assert len(_transitions(sub)) == 1
        assert api_notify.call_args.kwargs["status_reset"] is False


class TestNotice:
    def test_the_submitter_notice_does_not_assume_the_service_was_approved(self):
        sub = _rejected(internal_contact_email="pi@example.com")
        send_submission_notification(
            str(sub.pk),
            event="updated",
            changes=[
                {
                    "field": "status",
                    "label": "Status",
                    "old": "Rejected",
                    "new": "Submitted",
                }
            ],
            status_reset=True,
        )
        email = next(m for m in mail.outbox if sub.internal_contact_email in m.to)
        html = email.alternatives[0][0]
        for body in (email.body, html):
            assert "reset to" in body
            assert "core service fields" not in body
