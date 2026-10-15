"""The status history inline of the submission admin."""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.submissions.lifecycle import set_status
from apps.submissions.models import ServiceSubmission
from tests.factories import (
    ServiceSubmissionFactory,
)

pytestmark = pytest.mark.django_db


def _published(**kw):
    sub = ServiceSubmissionFactory(biotools_url="", **kw)
    set_status(sub, "approved", actor="admin:jane")
    return sub


class TestStatusHistoryIsReadOnly:
    """The history written by the application cannot be edited in the admin."""

    def _inline(self):
        from django.contrib import admin

        from apps.submissions.admin import SubmissionStatusEventInline

        return SubmissionStatusEventInline(ServiceSubmission, admin.site)

    def test_the_inline_offers_neither_add_nor_change(self, rf):
        request = rf.get("/")
        request.user = get_user_model().objects.create_superuser(
            "root", "root@example.org", "pw"
        )
        inline = self._inline()
        assert inline.has_add_permission(request) is False
        assert inline.has_change_permission(request) is False

    def test_the_inline_has_no_delete_box(self, rf):
        request = rf.get("/")
        request.user = get_user_model().objects.create_superuser(
            "root", "root@example.org", "pw"
        )
        formset = self._inline().get_formset(request)
        assert formset.can_delete is False

    def test_a_posted_delete_flag_removes_nothing(self, superuser_client):
        from tests.helpers import edit_form_payload

        sub = _published()
        events = list(sub.status_events.order_by("id"))
        payload = edit_form_payload(sub)
        payload.update(
            {
                "status_events-TOTAL_FORMS": str(len(events)),
                "status_events-INITIAL_FORMS": str(len(events)),
            }
        )
        for number, event in enumerate(events):
            payload[f"status_events-{number}-id"] = str(event.pk)
            payload[f"status_events-{number}-submission"] = str(sub.pk)
            payload[f"status_events-{number}-DELETE"] = "on"
        superuser_client.post(
            reverse("admin:submissions_servicesubmission_change", args=[sub.pk]),
            payload,
        )
        assert sub.status_events.count() == len(events)
