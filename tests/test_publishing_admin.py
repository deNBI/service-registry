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


def _approved(**kw):
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

        sub = _approved()
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


CLEANED_TEXT_FIELDS = [
    "service_name",
    "service_description",
    "toolbox_name",
    "user_knowledge_required",
    "host_institute",
    "keywords_seo",
]
# Stored text that a full save rewrites: line ends, edge whitespace, and
# decomposed characters.
UNCLEAN_TEXT = {
    "crlf": "Example first line\r\nExample second line",
    "trailing_space": "Example text with a trailing space ",
    "decomposed": "Example für text",
}


# A change to each kind of public field, written straight to the database
# (as a rejected edit leaves it).
FIELD_EDITS = {
    "service_name": "Example Renamed Service",
    "service_description": "A different example description that is long enough.\r\nWith a second line.",
    "user_knowledge_required": "Some other knowledge.",
    "primary_maturity_tag": "mature",
    "host_institute": "Example Other Institute",
    "year_established": 2001,
    "is_toolbox": True,
    "toolbox_name": "Example Toolbox Name",
    "website_url": "https://example.org/other",
    "terms_of_use_url": "https://example.org/terms-other",
    "license_note": "Other note",
    "public_contact_email": "jane.doe@example.org",
    "github_url": "https://github.com/example/other",
    "biotools_url": "https://bio.tools/example_other",
    "fairsharing_url": "https://fairsharing.org/other",
    "other_registry_url": "https://example.org/registry-other",
    "keywords_seo": "other, keywords",
    "publications_pmids": "12345",
    "logo": "logos/other.png",
}
