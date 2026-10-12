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


def test_description_rendered_placeholder_without_saved_object():
    from django.contrib.admin.sites import site

    from apps.submissions.admin import ServiceSubmissionAdmin
    from apps.submissions.models import ServiceSubmission

    model_admin = ServiceSubmissionAdmin(ServiceSubmission, site)
    assert model_admin.description_rendered(None) == "—"


# ---------------------------------------------------------------------------
# Admin behaviour in BOTH flag states: the flag only swaps the widget; saving,
# validation, the changelist and legacy rows must behave identically.
# ---------------------------------------------------------------------------


@pytest.fixture(params=[False, True], ids=["flag_off", "flag_on"])
def md_flag(request, settings):
    _set_flag(settings, request.param)
    return request.param


SPECIAL = (
    'Uses <b>tags</b>, x < 5 & y > 2, "quotes", Bob\'s **bold** _it_ and '
    "[a link](https://example.org)\r\n- item &amp; entity text"
)
# Stored exactly as typed except for LF line endings (browsers submit CRLF).
SPECIAL_STORED = SPECIAL.replace("\r\n", "\n")


def _textarea_value(content: bytes) -> str:
    """The textarea's value as a browser would submit it (HTML-unescaped)."""
    import html

    m = re.search(
        r'<textarea[^>]*name="service_description"[^>]*>\n(.*?)</textarea>',
        content.decode(),
        re.DOTALL,
    )
    assert m, "service_description textarea not rendered"
    return html.unescape(m.group(1))


def test_admin_change_form_widget_follows_flag(superuser_client, md_flag):
    sub = ServiceSubmissionFactory(status="approved", biotools_url="")
    html = superuser_client.get(_change_url(sub)).content.decode()
    assert ("data-md-editor" in html) is md_flag
    assert ("js/markdown-editor.js" in html) is md_flag
    assert "Description (rendered)" not in html
    assert b'rows="5"' in _textarea(html.encode())


def test_admin_add_form_widget_follows_flag(superuser_client, md_flag):
    resp = superuser_client.get(reverse("admin:submissions_servicesubmission_add"))
    assert resp.status_code == 200
    assert (b"data-md-editor" in resp.content) is md_flag
    assert b"Description (rendered)" not in resp.content


def test_admin_saves_description_exactly_as_typed(superuser_client, md_flag):
    """No HTML escaping on save in either state: raw text is stored (the model
    only NFC-normalises and strips)."""
    sub = ServiceSubmissionFactory(status="approved", biotools_url="")
    payload = edit_form_payload(sub, service_description=SPECIAL)
    resp = superuser_client.post(_change_url(sub), data=payload)
    assert resp.status_code == 302, (
        resp.context["adminform"].form.errors if resp.context else resp
    )
    sub.refresh_from_db()
    assert sub.service_description == SPECIAL_STORED
    # The change form shows it back exactly (escaped once in the HTML).
    assert _textarea_value(superuser_client.get(_change_url(sub)).content) == (
        SPECIAL_STORED
    )


def test_admin_add_form_saves_with_either_widget(superuser_client, md_flag):
    from apps.submissions.models import ServiceSubmission

    template = ServiceSubmissionFactory(status="approved", biotools_url="")
    payload = edit_form_payload(
        template, service_name="Admin Added Tool", service_description=SPECIAL
    )
    payload["status"] = "submitted"
    resp = superuser_client.post(
        reverse("admin:submissions_servicesubmission_add"), data=payload
    )
    assert resp.status_code == 302, (
        resp.context["adminform"].form.errors if resp.context else resp
    )
    added = ServiceSubmission.objects.get(service_name="Admin Added Tool")
    assert added.service_description == SPECIAL_STORED


def test_admin_validation_error_keeps_widget_and_text(superuser_client, md_flag):
    sub = ServiceSubmissionFactory(status="approved", biotools_url="")
    payload = edit_form_payload(sub, service_description="too short")
    resp = superuser_client.post(_change_url(sub), data=payload)
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "at least 50 characters" in html
    assert ("data-md-editor" in html) is md_flag
    assert _textarea_value(resp.content) == "too short"
    sub.refresh_from_db()
    assert sub.service_description != "too short"


def test_admin_untouched_legacy_row_round_trips_unchanged(superuser_client, md_flag):
    """Saving another field must not alter a legacy entity description: the
    textarea shows the stored text, the browser submits it back verbatim, and
    no description change is logged."""
    from apps.submissions.models import SubmissionChangeLog

    legacy = "Legacy row from the old escaping form: x &gt; 5 &amp; y &lt;b&gt;."
    sub = ServiceSubmissionFactory(
        status="approved", biotools_url="", service_description=legacy
    )
    shown = _textarea_value(superuser_client.get(_change_url(sub)).content)
    assert shown == legacy
    payload = edit_form_payload(sub, service_description=shown)
    payload["comments"] = "admin note changed"
    resp = superuser_client.post(_change_url(sub), data=payload)
    assert resp.status_code == 302
    sub.refresh_from_db()
    assert sub.service_description == legacy
    logged = [
        ch["field"]
        for log in SubmissionChangeLog.objects.filter(submission=sub)
        for ch in log.changes
    ]
    assert "comments" in logged
    assert "service_description" not in logged


@pytest.mark.parametrize("stored_crlf", [False, True], ids=["stored_lf", "stored_crlf"])
def test_admin_browser_crlf_resave_is_not_a_change(
    superuser_client, md_flag, stored_crlf
):
    """Browsers submit textareas with CRLF; an untouched multi-line
    description must not be logged as changed (whatever the stored line
    endings) and ends up stored with LF."""
    from apps.submissions.models import ServiceSubmission, SubmissionChangeLog

    lf = "Line one of the description.\nLine two, long enough overall.\n- item"
    sub = ServiceSubmissionFactory(status="approved", biotools_url="")
    stored = lf.replace("\n", "\r\n") if stored_crlf else lf
    ServiceSubmission.objects.filter(pk=sub.pk).update(service_description=stored)
    sub.refresh_from_db()
    payload = edit_form_payload(sub, service_description=lf.replace("\n", "\r\n"))
    payload["comments"] = "unrelated admin note"
    resp = superuser_client.post(_change_url(sub), data=payload)
    assert resp.status_code == 302
    sub.refresh_from_db()
    assert sub.service_description == lf
    logged = [
        ch["field"]
        for log in SubmissionChangeLog.objects.filter(submission=sub)
        for ch in log.changes
    ]
    assert logged == ["comments"]


def test_admin_changelist_and_search_load(superuser_client, md_flag):
    ServiceSubmissionFactory(
        status="approved", biotools_url="", service_name="Listed **Tool**"
    )
    url = reverse("admin:submissions_servicesubmission_changelist")
    for query in ("", "?q=Listed"):
        resp = superuser_client.get(url + query)
        assert resp.status_code == 200
        assert b"Listed **Tool**" in resp.content


def test_admin_viewer_page_follows_flag(viewer_client, md_flag):
    sub = ServiceSubmissionFactory(
        status="approved",
        biotools_url="",
        service_description="Viewer sees **bold** and x &gt; 5 in the saved text.",
    )
    html = viewer_client.get(_change_url(sub)).content.decode()
    assert ("Description (rendered)" in html) is md_flag
    assert ("<strong>bold</strong>" in html) is md_flag
    assert "data-md-editor" not in html
    # The read-only source value is shown as stored (escaped once).
    assert "Viewer sees **bold** and x &amp;gt; 5 in the saved text." in html


# ---------------------------------------------------------------------------
# Role x flag matrix: only change_servicesubmission decides editor vs
# read-only; every role that can open the change form without it gets the
# rendered row (flag on); nobody without change permission can save.
# ---------------------------------------------------------------------------

ROLES = {
    "superuser": None,
    "editor": ["view_servicesubmission", "change_servicesubmission"],
    "change_only": ["change_servicesubmission"],
    "viewer": ["view_servicesubmission"],
    "approver": ["view_servicesubmission", "approve_servicesubmission"],
    "key_manager": ["view_servicesubmission", "manage_apikeys"],
    "adder": ["add_servicesubmission"],
    "add_viewer": ["add_servicesubmission", "view_servicesubmission"],
}
ROLE_DESC = "Role check: **bold** text and x &gt; 5, long enough to be valid."


def _role_client(role):
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    from django.test import Client

    User = get_user_model()
    if ROLES[role] is None:
        user = User.objects.create_superuser(
            username=f"role-{role}", password="rolepass123", email="r@example.com"
        )
    else:
        user = User.objects.create_user(
            username=f"role-{role}", password="rolepass123", is_staff=True
        )
        user.user_permissions.set(
            Permission.objects.filter(
                content_type__app_label="submissions", codename__in=ROLES[role]
            )
        )
        assert user.user_permissions.count() == len(ROLES[role])
    c = Client()
    c.force_login(user)
    return c


def _perms(role):
    return (
        set(ROLES["editor"] + ["add_servicesubmission"])
        if ROLES[role] is None
        else set(ROLES[role])
    )


@pytest.mark.parametrize("role", ROLES)
def test_change_form_by_role(role, md_flag):
    sub = ServiceSubmissionFactory(
        status="approved", biotools_url="", service_description=ROLE_DESC
    )
    perms = _perms(role)
    can_change = "change_servicesubmission" in perms
    can_open = can_change or "view_servicesubmission" in perms
    resp = _role_client(role).get(_change_url(sub))
    if not can_open:
        assert resp.status_code == 403
        return
    assert resp.status_code == 200
    html = resp.content.decode()
    has_textarea = 'name="service_description"' in html
    assert has_textarea is can_change
    assert ("data-md-editor" in html) is (md_flag and can_change)
    assert ("Description (rendered)" in html) is (md_flag and not can_change)
    rendered = re.search(r'<div class="md-rendered">(.*?)</div>', html, re.DOTALL)
    assert (rendered is not None) is (md_flag and not can_change)
    if rendered:
        assert rendered.group(1) == (
            "<p>Role check: <strong>bold</strong> text and x &gt; 5, long enough "
            "to be valid.</p>"
        )
    if not can_change:
        # The raw source is still shown read-only, exactly as stored.
        assert (
            "Role check: **bold** text and x &amp;gt; 5, long enough to be valid."
            in html
        )


@pytest.mark.parametrize("role", ROLES)
def test_add_form_by_role(role, md_flag):
    resp = _role_client(role).get(reverse("admin:submissions_servicesubmission_add"))
    if "add_servicesubmission" not in _perms(role):
        assert resp.status_code == 403
        return
    assert resp.status_code == 200
    assert (b"data-md-editor" in resp.content) is md_flag
    assert b"Description (rendered)" not in resp.content


@pytest.mark.parametrize("role", ROLES)
def test_only_change_permission_can_save_description(role, md_flag):
    sub = ServiceSubmissionFactory(
        status="approved", biotools_url="", service_description=ROLE_DESC
    )
    new = "Role-edited description with enough characters to pass validation."
    resp = _role_client(role).post(
        _change_url(sub), data=edit_form_payload(sub, service_description=new)
    )
    sub.refresh_from_db()
    if "change_servicesubmission" in _perms(role):
        assert resp.status_code == 302
        assert sub.service_description == new
    else:
        assert resp.status_code == 403
        assert sub.service_description == ROLE_DESC


@pytest.mark.parametrize("role", ["superuser", "viewer", "approver"])
@pytest.mark.parametrize("action", ["action_export_csv", "action_export_json"])
def test_exports_carry_the_stored_description_for_any_viewing_role(
    role, action, md_flag
):
    """Exports are a source view: the stored raw text, identical for every
    role allowed to export and in both flag states."""
    import csv
    import io
    import json

    sub = ServiceSubmissionFactory(
        status="approved", biotools_url="", service_description=ROLE_DESC
    )
    resp = _role_client(role).post(
        reverse("admin:submissions_servicesubmission_changelist"),
        {"action": action, "_selected_action": [str(sub.pk)]},
    )
    assert resp.status_code == 200
    if action == "action_export_csv":
        rows = list(csv.DictReader(io.StringIO(resp.content.decode("utf-8-sig"))))
        value = rows[0]["service_description"]
    else:
        value = json.loads(resp.content)[0]["service_description"]
    assert value == ROLE_DESC


def _specificity(selector: str) -> tuple[int, int, int]:
    """(ids, classes/attributes/pseudo-classes, elements) of a simple CSS
    selector; enough for the flat selectors compared below."""
    sel = re.sub(
        r"::?[\w-]+(\([^)]*\))?",
        lambda m: " .x" if m.group(0)[1] != ":" else "",
        selector,
    )
    ids = len(re.findall(r"#[\w-]+", sel))
    classes = len(re.findall(r"\.[\w-]+|\[[^\]]*\]", sel))
    elements = len(re.findall(r"(?:^|[\s>+~])([a-zA-Z][\w-]*)", sel))
    return ids, classes, elements


def _width_selectors(path: str, target: str) -> list[str]:
    """Selectors of rules in the static file `path` that set `width` and
    whose selector contains `target`."""
    from django.contrib.staticfiles import finders

    with open(finders.find(path), encoding="utf-8") as fh:
        css = re.sub(r"/\*.*?\*/", "", fh.read(), flags=re.DOTALL)
    found = []
    for sel_list, block in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if not re.search(r"(^|;)\s*width\s*:", block):
            continue
        found += [" ".join(s.split()) for s in sel_list.split(",") if target in s]
    return found


def test_specificity_helper():
    assert _specificity(".colM .aligned .vLargeTextField") == (0, 3, 0)
    assert _specificity(".colM fieldset.wide .vLargeTextField") == (0, 3, 1)
    assert _specificity("#content-main .md-editor__panel textarea") == (1, 1, 1)
    assert _specificity("a:hover") == (0, 1, 1)


def test_editor_textarea_fills_the_box():
    """The admin sizes .vLargeTextField to fixed widths (e.g. 610px); inside
    the editor the textarea must fill the bordered box instead, so the
    editor's full-width rule has to out-rank every admin width rule."""
    admin = _width_selectors("admin/css/forms.css", ".vLargeTextField")
    ours = _width_selectors(
        "admin/css/markdown_preview.css", ".md-editor__panel textarea"
    )
    assert admin and ours
    assert max(map(_specificity, ours)) > max(map(_specificity, admin))
