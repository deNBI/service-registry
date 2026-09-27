"""Tests for the Markdown Preview control and the rendered, read-only
description in the ServiceSubmission admin change form.

The Preview control (shared partial) and the ``description_rendered`` readonly
field appear only when the markdown_descriptions feature flag is on. Admin
edits of the description must not reset an approved submission's status.
"""

import copy
import re

import pytest
from django.urls import reverse

from tests.factories import ServiceSubmissionFactory
from tests.test_admin import _edit_form_payload, admin_client  # noqa: F401

pytestmark = pytest.mark.django_db


def _set_flag(settings, enabled: bool) -> None:
    cfg = copy.deepcopy(getattr(settings, "SITE_CONFIG", {}) or {})
    cfg.setdefault("features", {})["markdown_descriptions"] = enabled
    settings.SITE_CONFIG = cfg


def _change_url(sub):
    return reverse("admin:submissions_servicesubmission_change", args=[sub.pk])


def _textarea(content: bytes) -> bytes:
    match = re.search(rb'<textarea[^>]*name="service_description"[^>]*>', content)
    assert match, "service_description textarea not rendered"
    return match.group(0)


def test_admin_change_shows_preview_control_and_rendered(admin_client, settings):  # noqa: F811
    _set_flag(settings, True)
    sub = ServiceSubmissionFactory(
        status="approved", biotools_url="", service_description="**bold**"
    )
    resp = admin_client.get(_change_url(sub))
    assert resp.status_code == 200
    assert b"data-md-preview" in resp.content
    assert b'id="md-preview-id_service_description"' in resp.content
    assert b"#id_service_description" in resp.content
    assert reverse("submissions:markdown-preview").encode() in resp.content
    # Readonly rendered preview.
    assert b"Description (rendered)" in resp.content
    assert b"<strong>bold</strong>" in resp.content
    # The swapped widget keeps the admin's row override.
    assert b'rows="5"' in _textarea(resp.content)


def test_admin_change_loads_htmx_csrf_refresh_and_css(admin_client, settings):  # noqa: F811
    _set_flag(settings, True)
    sub = ServiceSubmissionFactory(status="approved", biotools_url="")
    resp = admin_client.get(_change_url(sub))
    assert b"js/htmx.min.js" in resp.content
    assert b"js/htmx-csrf-refresh.js" in resp.content
    assert b"admin/css/markdown_preview.css" in resp.content
    # Existing media entries are kept.
    assert b"js/admin_submission_change.js" in resp.content
    assert b"admin/css/submissions_filter_sidebar.css" in resp.content


def test_admin_change_flag_off_has_no_preview(admin_client, settings):  # noqa: F811
    _set_flag(settings, False)
    sub = ServiceSubmissionFactory(
        status="approved", biotools_url="", service_description="**bold**"
    )
    resp = admin_client.get(_change_url(sub))
    assert resp.status_code == 200
    assert b"data-md-preview" not in resp.content
    assert b"md-preview-id_service_description" not in resp.content
    assert b"Description (rendered)" not in resp.content
    assert b"<strong>bold</strong>" not in resp.content
    assert b'rows="5"' in _textarea(resp.content)


def test_admin_add_view_renders_with_flag_on(admin_client, settings):  # noqa: F811
    _set_flag(settings, True)
    resp = admin_client.get(reverse("admin:submissions_servicesubmission_add"))
    assert resp.status_code == 200
    assert b"data-md-preview" in resp.content


def test_get_fieldsets_does_not_mutate_class_fieldsets(admin_client, settings):  # noqa: F811
    from apps.submissions.admin import ServiceSubmissionAdmin

    before = copy.deepcopy(ServiceSubmissionAdmin.fieldsets)
    _set_flag(settings, True)
    sub = ServiceSubmissionFactory(status="approved", biotools_url="")
    admin_client.get(_change_url(sub))
    assert ServiceSubmissionAdmin.fieldsets == before


def test_admin_description_edit_does_not_reset_status(admin_client, settings):  # noqa: F811
    _set_flag(settings, True)
    sub = ServiceSubmissionFactory(
        status="approved", biotools_url="", service_description="old text here"
    )
    new_text = "An updated description with **bold** text and a _short_ list."
    payload = _edit_form_payload(sub, service_description=new_text)
    resp = admin_client.post(_change_url(sub), data=payload)
    assert resp.status_code == 302, (
        resp.context["adminform"].form.errors if resp.context else resp
    )
    sub.refresh_from_db()
    assert sub.service_description == new_text
    assert sub.status == "approved"
