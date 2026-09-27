"""Tests for the Markdown Write/Preview editor in the ServiceSubmission admin
change form.

The editor (shared markdown_editor_box.html partial, via MarkdownTextareaWidget)
appears only when the markdown_descriptions feature flag is on. Editors see
the editor only; view-only staff (no change permission) see the raw text
read-only, so they also get the rendered ``description_rendered`` row.
Admin edits of the description must not reset an approved submission's status.
"""

import copy
import re

import pytest
from django.urls import reverse

from tests.factories import ServiceSubmissionFactory
from tests.helpers import edit_form_payload

pytestmark = pytest.mark.django_db


def _set_flag(settings, enabled: bool) -> None:
    cfg = copy.deepcopy(getattr(settings, "SITE_CONFIG", {}) or {})
    cfg.setdefault("features", {})["markdown_descriptions"] = enabled
    settings.SITE_CONFIG = cfg


@pytest.fixture
def md_on(settings):
    _set_flag(settings, True)


@pytest.fixture
def md_off(settings):
    _set_flag(settings, False)


@pytest.fixture
def superuser(db):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create_superuser(
        username="mdadmin", password="adminpass123", email="md@example.com"
    )


@pytest.fixture
def viewer_client(db):
    """Staff client with only view_servicesubmission."""
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    from django.test import Client

    user = get_user_model().objects.create_user(
        username="mdviewer", password="viewpass123", is_staff=True
    )
    user.user_permissions.add(Permission.objects.get(codename="view_servicesubmission"))
    c = Client()
    c.force_login(user)
    return c


def _change_url(sub):
    return reverse("admin:submissions_servicesubmission_change", args=[sub.pk])


def _textarea(content: bytes) -> bytes:
    match = re.search(rb'<textarea[^>]*name="service_description"[^>]*>', content)
    assert match, "service_description textarea not rendered"
    return match.group(0)


def test_admin_change_renders_editor(superuser_client, md_on):
    sub = ServiceSubmissionFactory(
        status="approved", biotools_url="", service_description="# T\n**b**"
    )
    resp = superuser_client.get(_change_url(sub))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "data-md-editor" in html
    assert 'id="id_service_description-tab-preview"' in html
    assert 'id="id_service_description-panel-write"' in html
    assert reverse("submissions:markdown-preview") in html
    assert 'rows="5"' in _textarea(resp.content).decode()
    assert "Description (rendered)" not in html
    assert html.count("js/markdown-editor.js") == 1
    assert "admin/css/markdown_preview.css" in html


def test_admin_change_media(superuser_client, md_on):
    sub = ServiceSubmissionFactory(status="approved", biotools_url="")
    html = superuser_client.get(_change_url(sub)).content.decode()
    # The htmx-based Preview button is gone; the editor uses fetch().
    assert "js/htmx.min.js" not in html
    assert "js/htmx-csrf-refresh.js" not in html
    assert html.count("admin/css/markdown_preview.css") == 1
    # Existing media entries are kept.
    assert "js/admin_submission_change.js" in html
    assert "admin/css/submissions_filter_sidebar.css" in html


def test_admin_flag_off_plain_textarea(superuser_client, md_off):
    sub = ServiceSubmissionFactory(
        status="approved", biotools_url="", service_description="**bold**"
    )
    resp = superuser_client.get(_change_url(sub))
    html = resp.content.decode()
    assert "data-md-editor" not in html and "markdown-editor.js" not in html
    assert "Description (rendered)" not in html
    assert "<strong>bold</strong>" not in html
    assert b'rows="5"' in _textarea(resp.content)


def test_admin_add_view_renders_with_flag_on(superuser_client, md_on):
    resp = superuser_client.get(reverse("admin:submissions_servicesubmission_add"))
    assert resp.status_code == 200
    assert b"data-md-editor" in resp.content
    assert b"Description (rendered)" not in resp.content


def test_add_only_user_add_view_has_no_rendered_row(db, md_on):
    """An add-only staff user lacks change permission, but the ADD form must
    show the editor, not an empty read-only rendered row."""
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    from django.test import Client

    user = get_user_model().objects.create_user(
        username="mdadder", password="addpass123", is_staff=True
    )
    user.user_permissions.add(Permission.objects.get(codename="add_servicesubmission"))
    c = Client()
    c.force_login(user)
    resp = c.get(reverse("admin:submissions_servicesubmission_add"))
    assert resp.status_code == 200
    assert b"data-md-editor" in resp.content
    assert b"Description (rendered)" not in resp.content


def test_admin_fieldsets_have_no_rendered_row(rf, superuser, md_on):
    from django.contrib.admin.sites import site

    from apps.submissions.models import ServiceSubmission

    ma = site._registry[ServiceSubmission]
    req = rf.get("/")
    req.user = superuser
    sub = ServiceSubmissionFactory(status="approved", biotools_url="")
    for obj in (None, sub):
        names = [
            f
            for _, opts in ma.get_fieldsets(req, obj)
            for item in opts["fields"]
            for f in (item if isinstance(item, tuple) else (item,))
        ]
        assert "description_rendered" not in names


def test_viewer_sees_rendered_description(viewer_client, md_on):
    sub = ServiceSubmissionFactory(
        status="approved", biotools_url="", service_description="**bold** text"
    )
    resp = viewer_client.get(_change_url(sub))
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Description (rendered)" in html
    assert '<div class="md-rendered"><p><strong>bold</strong> text</p>' in html
    assert "admin/css/markdown_preview.css" in html
    # Read-only: no editor.
    assert "data-md-editor" not in html


def test_viewer_flag_off_no_rendered_row(viewer_client, md_off):
    sub = ServiceSubmissionFactory(
        status="approved", biotools_url="", service_description="**bold** text"
    )
    html = viewer_client.get(_change_url(sub)).content.decode()
    assert "Description (rendered)" not in html
    assert "<strong>bold</strong>" not in html


def test_get_fieldsets_does_not_mutate_class_fieldsets(viewer_client, md_on):
    from apps.submissions.admin import ServiceSubmissionAdmin

    before = copy.deepcopy(ServiceSubmissionAdmin.fieldsets)
    sub = ServiceSubmissionFactory(status="approved", biotools_url="")
    viewer_client.get(_change_url(sub))
    assert ServiceSubmissionAdmin.fieldsets == before


def test_admin_description_edit_does_not_reset_status(superuser_client, md_on):
    sub = ServiceSubmissionFactory(
        status="approved", biotools_url="", service_description="old text here"
    )
    new_text = "An updated description with **bold** text and a _short_ list."
    payload = edit_form_payload(sub, service_description=new_text)
    resp = superuser_client.post(_change_url(sub), data=payload)
    assert resp.status_code == 302, (
        resp.context["adminform"].form.errors if resp.context else resp
    )
    sub.refresh_from_db()
    assert sub.service_description == new_text
    assert sub.status == "approved"
