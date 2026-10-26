"""Every output surface shows the plain-text fields as literal text.

service_name, submitter_first_name, submitter_last_name and
submitter_affiliation are stored as typed, so each HTML surface must escape
them and each plain-text surface must show them unescaped. Rows saved by the
old web form hold entities (R&amp;D); display surfaces decode those once, while
source views (admin, exports, raw API fields, change history) show what is
stored.
"""

import json

import pytest
from django.core import mail
from django.template import Context, Template
from django.urls import reverse
from rest_framework.test import APIClient

from apps.submissions.models import SubmissionChangeLog
from tests.factories import (
    APIKeyFactory,
    PIFactory,
    ServiceCategoryFactory,
    ServiceSubmissionFactory,
)

pytestmark = pytest.mark.django_db

# Markup-like text with an ampersand, quotes and angle brackets.
TYPED = {
    "service_name": "Tool <b>x</b> & \"q\" 'a' 1 < 2",
    "submitter_first_name": "Jo <b>x</b> & Jo",
    "submitter_last_name": 'Doe <i>y</i> "Jr"',
    "submitter_affiliation": "Institute <b>x</b> & Lab",
}
ESCAPED = {
    "service_name": "Tool &lt;b&gt;x&lt;/b&gt; &amp; &quot;q&quot; &#x27;a&#x27; 1 &lt; 2",
    "submitter_first_name": "Jo &lt;b&gt;x&lt;/b&gt; &amp; Jo",
    "submitter_last_name": "Doe &lt;i&gt;y&lt;/i&gt; &quot;Jr&quot;",
    "submitter_affiliation": "Institute &lt;b&gt;x&lt;/b&gt; &amp; Lab",
}
LEGACY = {
    "service_name": "R&amp;D Tool &lt;Beta&gt;",
    "submitter_first_name": "Jo&amp;Jo",
    "submitter_last_name": "Doe &lt;Jr&gt;",
    "submitter_affiliation": "Institute for R&amp;D",
}
LEGACY_DECODED = {
    "service_name": "R&D Tool <Beta>",
    "submitter_first_name": "Jo&Jo",
    "submitter_last_name": "Doe <Jr>",
    "submitter_affiliation": "Institute for R&D",
}

CATALOGUE_ON = {
    "features": {"catalogue": True},
    "catalogue": {"per_page": 12, "card_fields": ["categories", "service_center"]},
    "site": {"name": "Test Registry", "url": "https://example.com"},
    "contact": {},
    "links": {},
    "email": {},
}


def _assert_literal(html: str, extra_escaped: tuple[str, ...] = ()):
    """No markup from the typed text reached the page unescaped."""
    for raw in ("<b>x</b>", "<i>y</i>", "<Beta>", "<Jr>"):
        assert raw not in html, raw
    for escaped in extra_escaped:
        assert escaped in html, escaped


def _approved(**fields):
    return ServiceSubmissionFactory(status="approved", biotools_url="", **fields)


# ---------------------------------------------------------------------------
# Template filter
# ---------------------------------------------------------------------------


def _render(template: str, **ctx) -> str:
    return Template("{% load submission_text %}" + template).render(Context(ctx))


def test_filter_decodes_legacy_entities_and_template_escapes_once():
    assert _render("{{ v|legacy_decoded }}", v="R&amp;D") == "R&amp;D"
    # R&D in the page source is the single escape of the decoded text.
    assert _render("{{ v|legacy_decoded }}", v="R&D <b>") == "R&amp;D &lt;b&gt;"


def test_filter_leaves_plain_text_and_empty_values_alone():
    assert _render("{{ v|legacy_decoded }}", v="Plain") == "Plain"
    assert _render("{{ v|legacy_decoded }}", v="") == ""
    assert _render("{{ v|legacy_decoded }}", v=None) == ""


def test_filter_output_is_not_marked_safe():
    out = _render("{{ v|legacy_decoded }}", v="&lt;b&gt;x&lt;/b&gt;")
    assert "<b>" not in out


def test_filter_composes_with_first_and_upper():
    assert _render("{{ v|legacy_decoded|first|upper }}", v="&amp;x") == "&amp;"


# ---------------------------------------------------------------------------
# Catalogue
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("params", ["", "?view=list", "?group_by=category"])
def test_catalogue_shows_typed_name_as_literal_text(client, settings, params):
    settings.SITE_CONFIG = CATALOGUE_ON
    cat = ServiceCategoryFactory(name="Category One")
    _approved(service_name=TYPED["service_name"], service_categories=[cat])
    sep = "&" if params else "?"
    html = client.get(f"/catalogue/grid/{params}{sep}x=1").content.decode()
    _assert_literal(html, (ESCAPED["service_name"],))


@pytest.mark.parametrize("params", ["", "?view=list", "?group_by=category"])
def test_catalogue_decodes_legacy_name_once(client, settings, params):
    settings.SITE_CONFIG = CATALOGUE_ON
    cat = ServiceCategoryFactory(name="Category One")
    _approved(service_name=LEGACY["service_name"], service_categories=[cat])
    sep = "&" if params else "?"
    html = client.get(f"/catalogue/grid/{params}{sep}x=1").content.decode()
    assert "R&amp;D Tool &lt;Beta&gt;" in html
    assert "R&amp;amp;D" not in html
    assert "&amp;lt;Beta" not in html
    assert "<Beta>" not in html


@pytest.mark.parametrize("params", ["", "?view=list"])
def test_catalogue_keeps_entity_like_typed_text(client, settings, params):
    settings.SITE_CONFIG = CATALOGUE_ON
    _approved(service_name="Law&ethics Atlas")
    html = client.get(f"/catalogue/grid/{params}").content.decode()
    assert "Law&amp;ethics Atlas" in html
    assert "\u00f0" not in html


@pytest.mark.parametrize("params", ["", "?view=list"])
def test_catalogue_link_labels_decode_legacy_name_once(client, settings, params):
    settings.SITE_CONFIG = CATALOGUE_ON
    sub = _approved(
        service_name=LEGACY["service_name"],
        github_url="https://github.com/example/tool",
    )
    # Set after creation so the bio.tools sync signal does not run.
    type(sub).objects.filter(pk=sub.pk).update(
        biotools_url="https://bio.tools/example_tool"
    )
    html = client.get(f"/catalogue/grid/{params}").content.decode()
    for target in ("on GitHub", "on bio.tools"):
        assert f'aria-label="View R&amp;D Tool &lt;Beta&gt; {target}"' in html
    assert "R&amp;amp;D" not in html


def test_catalogue_full_page_escapes_and_decodes(client, settings):
    settings.SITE_CONFIG = CATALOGUE_ON
    _approved(service_name=TYPED["service_name"])
    _approved(service_name=LEGACY["service_name"])
    html = client.get("/catalogue/").content.decode()
    _assert_literal(html, (ESCAPED["service_name"], "R&amp;D Tool &lt;Beta&gt;"))
    assert "R&amp;amp;D" not in html


def test_catalogue_avatar_initial_uses_decoded_name(client, settings):
    settings.SITE_CONFIG = CATALOGUE_ON
    _approved(service_name="&lt;Beta&gt; Tool")
    html = client.get("/catalogue/grid/").content.decode()
    assert '<span class="catalogue-avatar-initial">&lt;</span>' in html


@pytest.mark.parametrize(
    "query,expected_names",
    [
        ("R&D", {"Legacy R&amp;D Suite", "New R&D Suite"}),
        ("r&d suite", {"Legacy R&amp;D Suite", "New R&D Suite"}),
        ("Suite", {"Legacy R&amp;D Suite", "New R&D Suite", "Other Suite"}),
        ("<b>", {"Bold <b>Tool</b>"}),
        ("1 < 2", {"Math 1 < 2"}),
    ],
)
def test_catalogue_search_finds_legacy_and_new_rows(
    client, settings, query, expected_names
):
    settings.SITE_CONFIG = CATALOGUE_ON
    for name in (
        "Legacy R&amp;D Suite",
        "New R&D Suite",
        "Other Suite",
        "Bold <b>Tool</b>",
        "Math 1 < 2",
    ):
        _approved(service_name=name)
    from urllib.parse import urlencode

    from apps.catalogue.selectors import get_approved_services

    found = {s.service_name for s in get_approved_services(search=query)}
    assert found == expected_names
    # And through the HTTP endpoint with a properly encoded query string.
    resp = client.get("/catalogue/grid/?" + urlencode({"q": query}))
    assert resp.status_code == 200
    assert resp.context["page_obj"].paginator.count == len(expected_names)
    shown = {s.service_name for s in resp.context["page_obj"].object_list}
    assert shown == expected_names


def test_catalogue_search_is_unchanged_for_plain_terms(settings):
    from apps.catalogue.selectors import get_approved_services

    _approved(service_name="Galaxy Workflow")
    _approved(service_name="Unrelated Tool")
    names = {s.service_name for s in get_approved_services(search="galaxy")}
    assert names == {"Galaxy Workflow"}


# ---------------------------------------------------------------------------
# E-mails
# ---------------------------------------------------------------------------


@pytest.fixture
def outbox(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True
    settings.SUBMISSION_NOTIFY_OVERRIDE = ""
    return mail.outbox


def _html(msg):
    return msg.alternatives[0][0]


def _notify(sub, event, **kwargs):
    from apps.submissions.tasks import send_submission_notification

    send_submission_notification(str(sub.id), event=event, **kwargs)


@pytest.mark.parametrize("event", ["created", "status_changed"])
def test_emails_show_typed_text_literally_in_text_and_escaped_in_html(outbox, event):
    sub = ServiceSubmissionFactory(
        internal_contact_email="owner@example.com", biotools_url="", **TYPED
    )
    _notify(sub, event)
    assert len(outbox) == 2  # admin and submitter
    for msg in outbox:
        assert TYPED["service_name"] in msg.subject
        assert TYPED["service_name"] in msg.body
        for raw in ("&amp;", "&lt;", "&gt;", "&quot;", "&#x27;"):
            assert raw not in msg.body, (raw, msg.body)
        html = _html(msg)
        _assert_literal(html, (ESCAPED["service_name"],))
    submitter = [m for m in outbox if m.to == ["owner@example.com"]][0]
    assert f"{TYPED['submitter_first_name']} {TYPED['submitter_last_name']}" in (
        submitter.body
    )
    admin = [m for m in outbox if m.to != ["owner@example.com"]][0]
    assert TYPED["submitter_affiliation"] in admin.body
    assert ESCAPED["submitter_affiliation"] in _html(admin)


@pytest.mark.parametrize("event", ["created", "status_changed"])
def test_emails_decode_legacy_text_once(outbox, event):
    sub = ServiceSubmissionFactory(
        internal_contact_email="owner@example.com", biotools_url="", **LEGACY
    )
    _notify(sub, event)
    assert len(outbox) == 2
    for msg in outbox:
        assert LEGACY_DECODED["service_name"] in msg.subject
        assert LEGACY_DECODED["service_name"] in msg.body
        assert "&amp;" not in msg.body and "&lt;" not in msg.body
        html = _html(msg)
        assert "R&amp;D Tool &lt;Beta&gt;" in html
        assert "R&amp;amp;D" not in html
        assert "&amp;lt;" not in html
    admin = [m for m in outbox if m.to != ["owner@example.com"]][0]
    assert LEGACY_DECODED["submitter_affiliation"] in admin.body


def test_update_emails_decode_legacy_values_in_the_change_list(outbox):
    sub = ServiceSubmissionFactory(
        internal_contact_email="owner@example.com", biotools_url="", **LEGACY
    )
    changes = [
        {
            "field": "submitter_affiliation",
            "label": "Submitter Affiliation",
            "old": LEGACY["submitter_affiliation"],
            "new": "New <Lab> & Co",
        },
        {
            "field": "host_institute",
            "label": "Host Institute",
            "old": "Kept &amp; literal",
            "new": "x",
        },
    ]
    from apps.submissions.tasks import send_update_notification

    send_update_notification(str(sub.id), changes=changes)
    assert len(outbox) == 2
    for msg in outbox:
        assert "Institute for R&D" in msg.body
        assert "New <Lab> & Co" in msg.body
        # Fields that never held entities are shown as stored.
        assert "Kept &amp; literal" in msg.body
        html = _html(msg)
        assert "Institute for R&amp;D" in html
        assert "New &lt;Lab&gt; &amp; Co" in html
        assert "<Lab>" not in html
        assert "R&amp;amp;D" not in html


def test_update_email_greeting_decodes_legacy_names(outbox):
    sub = ServiceSubmissionFactory(
        internal_contact_email="owner@example.com", biotools_url="", **LEGACY
    )
    from apps.submissions.tasks import send_update_notification

    change = {
        "field": "host_institute",
        "label": "Host Institute",
        "old": "a",
        "new": "b",
    }
    send_update_notification(str(sub.id), changes=[change])
    submitter = [m for m in outbox if "owner@example.com" in m.to][0]
    assert "Dear Jo&Jo Doe <Jr>," in submitter.body
    assert "Dear Jo&amp;Jo Doe &lt;Jr&gt;," in _html(submitter)


def test_email_subject_keeps_entity_like_typed_text(outbox):
    sub = ServiceSubmissionFactory(
        internal_contact_email="owner@example.com",
        biotools_url="",
        service_name="data&copy-number caller",
    )
    _notify(sub, "created")
    assert len(outbox) == 2
    for msg in outbox:
        assert "data&copy-number caller" in msg.subject
        assert "data&copy-number caller" in msg.body


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Two\nlines", "Two lines"),
        ("Carriage\r\nreturn", "Carriage return"),
        ("Tabs\tand  spaces", "Tabs and spaces"),
        ("R&amp;D\nTool", "R&D Tool"),
    ],
)
def test_email_subject_is_a_single_line(outbox, name, expected):
    sub = ServiceSubmissionFactory(
        internal_contact_email="owner@example.com", biotools_url="", service_name=name
    )
    _notify(sub, "created")
    assert len(outbox) == 2
    for msg in outbox:
        assert "\n" not in msg.subject and "\r" not in msg.subject
        assert expected in msg.subject


# ---------------------------------------------------------------------------
# Edit page
# ---------------------------------------------------------------------------


def _grant_edit(client, sub):
    key_obj, _ = APIKeyFactory.create_with_plaintext(submission=sub)
    session = client.session
    session["edit_grants"] = {str(sub.pk): str(key_obj.pk)}
    session.save()


def test_edit_page_escapes_typed_text(client):
    sub = ServiceSubmissionFactory(biotools_url="", **TYPED)
    _grant_edit(client, sub)
    html = client.get(reverse("submissions:edit", args=[sub.pk])).content.decode()
    _assert_literal(html, (ESCAPED["service_name"],))


def test_edit_page_decodes_legacy_name_in_headings(client):
    sub = ServiceSubmissionFactory(biotools_url="", **LEGACY)
    _grant_edit(client, sub)
    html = client.get(reverse("submissions:edit", args=[sub.pk])).content.decode()
    assert "<title>Edit: R&amp;D Tool &lt;Beta&gt;" in html
    assert "<strong>Editing:</strong> R&amp;D Tool &lt;Beta&gt;" in html
    assert "R&amp;amp;D" not in html.split("<form")[0]
    assert "mark <strong>R&amp;D Tool &lt;Beta&gt;</strong> as deprecated" in html


# ---------------------------------------------------------------------------
# Admin and exports (source views show what is stored)
# ---------------------------------------------------------------------------


def _admin_pages(sub):
    return [
        reverse("admin:submissions_servicesubmission_changelist"),
        reverse("admin:submissions_servicesubmission_change", args=[sub.pk]),
        reverse("admin:submissions_servicesubmission_history", args=[sub.pk]),
        reverse("admin:submissions_servicesubmission_delete", args=[sub.pk]),
        reverse("admin:submissions_submissionapikey_changelist"),
        reverse("admin:submissions_submissionchangelog_changelist"),
    ]


def test_admin_pages_escape_typed_text(superuser_client):
    sub = ServiceSubmissionFactory(biotools_url="", **TYPED)
    APIKeyFactory.create_with_plaintext(submission=sub)
    SubmissionChangeLog.objects.create(
        submission=sub,
        changed_by="submitter",
        changed_at=sub.submitted_at,
        changes=[
            {
                "field": "submitter_affiliation",
                "label": "Submitter Affiliation",
                "old": "Old <b>x</b>",
                "new": TYPED["submitter_affiliation"],
            }
        ],
    )
    for url in _admin_pages(sub):
        resp = superuser_client.get(url)
        assert resp.status_code == 200, url
        html = resp.content.decode()
        for raw in ("<b>x</b>", "<i>y</i>"):
            assert raw not in html, (url, raw)


def test_admin_edit_banner_and_history_escape_typed_text(superuser_client):
    from tests.helpers import edit_form_payload

    sub = ServiceSubmissionFactory(biotools_url="")
    payload = edit_form_payload(sub, **TYPED)
    resp = superuser_client.post(
        reverse("admin:submissions_servicesubmission_change", args=[sub.pk]),
        data=payload,
        follow=True,
    )
    assert resp.status_code == 200
    page = resp.content.decode()
    assert "field(s) changed" in page  # the diff banner is on this page
    _assert_literal(page, (ESCAPED["submitter_affiliation"],))
    history = superuser_client.get(
        reverse("admin:submissions_servicesubmission_history", args=[sub.pk])
    ).content.decode()
    _assert_literal(history)
    sub.refresh_from_db()
    assert sub.service_name == TYPED["service_name"]


def test_admin_change_form_shows_stored_legacy_text(superuser_client):
    sub = ServiceSubmissionFactory(biotools_url="", **LEGACY)
    html = superuser_client.get(
        reverse("admin:submissions_servicesubmission_change", args=[sub.pk])
    ).content.decode()
    # The input holds the stored value; its escaped form is what the page has.
    assert 'value="R&amp;amp;D Tool &amp;lt;Beta&amp;gt;"' in html


def test_admin_exports_contain_stored_values(superuser_client):
    ServiceSubmissionFactory(biotools_url="", **TYPED)
    legacy = ServiceSubmissionFactory(biotools_url="", **LEGACY)
    url = reverse("admin:submissions_servicesubmission_changelist")
    for action, check in (
        ("action_export_csv", lambda body: all(v in body for v in TYPED.values())),
        ("action_export_json", None),
    ):
        resp = superuser_client.post(
            url,
            {
                "action": action,
                "_selected_action": [
                    str(pk) for pk in type(legacy).objects.values_list("pk", flat=True)
                ],
            },
        )
        assert resp.status_code == 200, action
        body = resp.content.decode()
        if check:
            assert check(body.replace('""', '"')), body
        else:
            data = json.loads(body)
            names = {row["service_name"] for row in data}
            assert TYPED["service_name"] in names
            assert LEGACY["service_name"] in names


# ---------------------------------------------------------------------------
# REST API (raw fields carry the stored text)
# ---------------------------------------------------------------------------


def test_api_returns_typed_text_as_json_strings():
    sub = ServiceSubmissionFactory(biotools_url="", **TYPED)
    _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
    body = client.get(f"/api/v1/submissions/{sub.pk}/").json()
    for field, text in TYPED.items():
        assert body[field] == text


def test_catalogue_page_and_pi_names_are_unaffected(client, settings):
    settings.SITE_CONFIG = CATALOGUE_ON
    pi = PIFactory(first_name="John", last_name="Doe")
    _approved(service_name="Plain Service", responsible_pis=[pi])
    html = client.get("/catalogue/grid/?group_by=pi").content.decode()
    assert "Plain Service" in html and "John" in html
