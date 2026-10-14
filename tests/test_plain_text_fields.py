"""Service name, first name, last name and affiliation are stored as typed.

The web form used to HTML-escape these four fields (R&D Tool was saved as
R&amp;D Tool) and to delete anything that looked like a tag, while the REST API
and the admin stored the text unchanged. Now every entry path stores the text
as typed (NFC, LF line endings, stripped) and output surfaces escape it.

Rows saved by the old web form still hold entities. Re-submitting an
untouched edit form or re-sending the same API PATCH for such a row must not
count as a change: no status reset, no change-log entry, no rewrite of the
stored value.
"""

import unicodedata
from html.parser import HTMLParser

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.submissions.forms import SubmissionForm
from apps.submissions.lifecycle import get_no_reset_fields
from apps.submissions.models import ServiceSubmission, SubmissionChangeLog
from tests.factories import APIKeyFactory, ServiceSubmissionFactory
from tests.helpers import base_form_data, edit_form_data

pytestmark = pytest.mark.django_db

FIELDS = (
    "service_name",
    "submitter_first_name",
    "submitter_last_name",
    "submitter_affiliation",
)

TYPED_TEXTS = [
    pytest.param("R&D Tool", id="ampersand"),
    pytest.param("<b>x</b> & \"q\" 'a'", id="tags-and-quotes"),
    pytest.param("a<b and c>d", id="angle-pair"),
    pytest.param("1 < 2", id="less-than"),
    pytest.param("Tool <3", id="heart"),
    pytest.param("AT&T Labs", id="entity-like"),
]

# Text a legacy row holds: what the old form stored for the typed value.
LEGACY = {
    "service_name": ("R&amp;D Tool", "R&D Tool"),
    "submitter_first_name": ("Jo&amp;Jo", "Jo&Jo"),
    "submitter_last_name": ("Doe &lt;Jr&gt;", "Doe <Jr>"),
    "submitter_affiliation": ("Institute for R&amp;D", "Institute for R&D"),
}


# ---------------------------------------------------------------------------
# Web registration form: stored as typed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize("text", TYPED_TEXTS)
def test_form_stores_text_exactly_as_typed(field, text):
    form = SubmissionForm(data=base_form_data({field: text}))
    assert form.is_valid(), form.errors
    obj = form.save()
    obj.refresh_from_db()
    assert getattr(obj, field) == text


@pytest.mark.parametrize("field", FIELDS)
def test_form_keeps_markup_like_text_instead_of_deleting_it(field):
    form = SubmissionForm(data=base_form_data({field: "Left <i>mid</i> right"}))
    assert form.is_valid(), form.errors
    assert form.cleaned_data[field] == "Left <i>mid</i> right"


@pytest.mark.parametrize("field", FIELDS)
def test_form_normalises_whitespace_unicode_and_newlines(field):
    nfd = unicodedata.normalize("NFD", "Zoë Müller")
    form = SubmissionForm(data=base_form_data({field: f"  {nfd}\r\nline two  "}))
    assert form.is_valid(), form.errors
    assert form.cleaned_data[field] == "Zoë Müller\nline two"
    assert unicodedata.is_normalized("NFC", form.cleaned_data[field])


@pytest.mark.parametrize(
    "field,message",
    [
        ("service_name", "at least 3"),
        ("submitter_first_name", "at least 2"),
        ("submitter_last_name", "at least 2"),
        ("submitter_affiliation", "at least 2"),
    ],
)
def test_form_minimum_length_still_applies(field, message):
    form = SubmissionForm(data=base_form_data({field: " a "}))
    assert not form.is_valid()
    assert message in str(form.errors[field])


def test_register_view_stores_as_typed(client, settings):
    settings.ALTCHA_HMAC_KEY = ""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    data = base_form_data(
        {
            "service_name": "R&D Tool <beta>",
            "submitter_affiliation": "Institute of A&B",
        }
    )
    resp = client.post(reverse("submissions:register"), data=data)
    assert resp.status_code == 302, resp.content[:1500]
    sub = ServiceSubmission.objects.get()
    assert sub.service_name == "R&D Tool <beta>"
    assert sub.submitter_affiliation == "Institute of A&B"


# ---------------------------------------------------------------------------
# Same stored text on every entry path
# ---------------------------------------------------------------------------


def _api_payload(**overrides):
    from django.utils import timezone

    from tests.factories import PIFactory, ServiceCategoryFactory, ServiceCenterFactory

    payload = {
        "date_of_entry": timezone.now().date().isoformat(),
        "submitter_first_name": "Jane",
        "submitter_last_name": "Doe",
        "submitter_affiliation": "Example Institute",
        "service_name": "Example Service",
        "service_description": "An example description that is long enough to be accepted.",
        "year_established": 2021,
        "service_category_ids": [ServiceCategoryFactory().pk],
        "is_toolbox": False,
        "publications_pmids": "12345678",
        "responsible_pi_ids": [str(PIFactory().pk)],
        "host_institute": "Example Institute",
        "service_center_id": str(ServiceCenterFactory().pk),
        "public_contact_email": "public@example.com",
        "internal_contact_name": "Jane Doe",
        "internal_contact_email": "internal@example.com",
        "website_url": "https://example.com",
        "terms_of_use_url": "https://example.com/tos",
        "licenses": [],
        "license_note": "Custom",
        "kpi_monitoring": "planned",
        "kpi_start_year": "2021",
        "survey_participation": True,
        "data_protection_consent": True,
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("text", TYPED_TEXTS)
def test_web_form_and_api_store_identical_text(text):
    overrides = {
        "service_name": text,
        "submitter_first_name": text,
        "submitter_last_name": text,
        "submitter_affiliation": text,
    }
    form = SubmissionForm(data=base_form_data(overrides))
    assert form.is_valid(), form.errors
    via_form = form.save()

    resp = APIClient().post(
        "/api/v1/submissions/", _api_payload(**overrides), format="json"
    )
    assert resp.status_code == 201, resp.content
    via_api = ServiceSubmission.objects.get(pk=resp.json()["id"])

    for field in FIELDS:
        via_form.refresh_from_db()
        assert getattr(via_form, field) == getattr(via_api, field) == text, field


@pytest.mark.parametrize("field", FIELDS)
def test_admin_save_stores_as_typed(superuser_client, field):
    from tests.helpers import edit_form_payload

    sub = ServiceSubmissionFactory(biotools_url="")
    payload = edit_form_payload(sub, **{field: "R&D <Tool>"})
    resp = superuser_client.post(
        reverse("admin:submissions_servicesubmission_change", args=[sub.pk]),
        data=payload,
    )
    assert resp.status_code == 302, resp.content[:2000]
    sub.refresh_from_db()
    assert getattr(sub, field) == "R&D <Tool>"


# ---------------------------------------------------------------------------
# Legacy rows and the edit form
# ---------------------------------------------------------------------------


class _FormValues(HTMLParser):
    """Values a browser would submit for text inputs and selects."""

    def __init__(self):
        super().__init__()
        self.values = {}
        self._select = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "input" and a.get("name") and a.get("type", "text") == "text":
            self.values[a["name"]] = a.get("value", "")
        elif tag == "select":
            self._select = a.get("name")
        elif tag == "option" and self._select and "selected" in a:
            self.values[self._select] = a.get("value", "")

    def handle_endtag(self, tag):
        if tag == "select":
            self._select = None


def _grant_edit(client, sub):
    key_obj, _ = APIKeyFactory.create_with_plaintext(submission=sub)
    session = client.session
    session["edit_grants"] = {str(sub.pk): str(key_obj.pk)}
    session.save()


def _shown_values(client, sub):
    page = client.get(reverse("submissions:edit", args=[sub.pk])).content.decode()
    parser = _FormValues()
    parser.feed(page)
    return parser.values


@pytest.fixture
def no_reset():
    """Pin the exempt-field list for one test (the lookup is cached)."""

    def _set(settings, fields):
        settings.SUBMISSION_NO_RESET_FIELDS = fields
        get_no_reset_fields.cache_clear()

    yield _set
    get_no_reset_fields.cache_clear()


def _legacy_row(**extra):
    stored = {f: LEGACY[f][0] for f in FIELDS}
    stored.update(extra)
    return ServiceSubmissionFactory(
        status="approved",
        biotools_url="",
        primary_maturity_tag="mature",
        secondary_maturity_tags=["unstable"],
        **stored,
    )


@pytest.mark.parametrize("field", FIELDS)
def test_edit_page_shows_legacy_text_decoded(client, field):
    sub = _legacy_row()
    _grant_edit(client, sub)
    shown = _shown_values(client, sub)
    assert shown[field] == LEGACY[field][1]


def test_affiliation_suggestions_are_decoded(client):
    sub = _legacy_row()
    _grant_edit(client, sub)
    page = client.get(reverse("submissions:edit", args=[sub.pk])).content.decode()
    assert "Institute for R&amp;D" in page  # the escaped form of "R&D"
    assert "Institute for R&amp;amp;D" not in page


@pytest.mark.parametrize("no_reset_list", [["github_url"], []], ids=["exempt", "none"])
def test_untouched_edit_of_legacy_row_is_not_a_change(
    client, settings, no_reset, no_reset_list
):
    settings.ALTCHA_HMAC_KEY = ""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    no_reset(settings, no_reset_list)
    sub = _legacy_row()
    _grant_edit(client, sub)

    # Exactly what a browser submits for the four fields: the shown values.
    shown = _shown_values(client, sub)
    data = edit_form_data(sub, **{f: shown[f] for f in FIELDS})
    resp = client.post(reverse("submissions:edit", args=[sub.pk]), data=data)
    assert resp.status_code == 302, resp.content[:2000]

    sub.refresh_from_db()
    assert sub.status == "approved"
    assert sub.primary_maturity_tag == "mature"
    assert sub.secondary_maturity_tags == ["unstable"]
    assert sub.last_change_summary is None
    assert not SubmissionChangeLog.objects.filter(submission=sub).exists()
    # Untouched legacy text is left as stored.
    for field in FIELDS:
        assert getattr(sub, field) == LEGACY[field][0], field


def test_untouched_edit_sends_no_change_notification(client, settings, monkeypatch):
    settings.ALTCHA_HMAC_KEY = ""
    sent = []
    monkeypatch.setattr(
        "apps.submissions.views.send_update_notification.delay",
        lambda *a, **kw: sent.append(kw),
    )
    sub = _legacy_row()
    _grant_edit(client, sub)
    shown = _shown_values(client, sub)
    data = edit_form_data(sub, **{f: shown[f] for f in FIELDS})
    client.post(reverse("submissions:edit", args=[sub.pk]), data=data)
    assert sent == [{"changes": [], "status_reset": False}]


@pytest.mark.parametrize("field", FIELDS)
def test_posting_the_stored_legacy_text_back_is_not_a_change(
    client, settings, no_reset, field
):
    """A client that still sends the stored (entity) text is unchanged too."""
    settings.ALTCHA_HMAC_KEY = ""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    no_reset(settings, ["github_url"])
    sub = _legacy_row()
    _grant_edit(client, sub)
    resp = client.post(
        reverse("submissions:edit", args=[sub.pk]), data=edit_form_data(sub)
    )
    assert resp.status_code == 302
    sub.refresh_from_db()
    assert sub.status == "approved"
    assert sub.last_change_summary is None
    assert getattr(sub, field) == LEGACY[field][0]


@pytest.mark.parametrize(
    "field", ["submitter_first_name", "submitter_last_name", "submitter_affiliation"]
)
def test_real_edit_of_legacy_field_is_a_change_stored_as_typed(
    client, settings, no_reset, field
):
    settings.ALTCHA_HMAC_KEY = ""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    no_reset(settings, ["github_url"])
    sub = _legacy_row()
    _grant_edit(client, sub)
    shown = _shown_values(client, sub)
    data = edit_form_data(sub, **{f: shown[f] for f in FIELDS})
    data[field] = LEGACY[field][1] + " & <more>"
    resp = client.post(reverse("submissions:edit", args=[sub.pk]), data=data)
    assert resp.status_code == 302, resp.content[:2000]

    sub.refresh_from_db()
    assert getattr(sub, field) == LEGACY[field][1] + " & <more>"
    assert sub.status == "submitted"
    changes = sub.last_change_summary["changes"]
    assert [c["field"] for c in changes if c["field"] in FIELDS] == [field]
    assert SubmissionChangeLog.objects.filter(submission=sub).count() == 1


def test_edit_keeps_entity_like_typed_text(client, settings, no_reset):
    """Typed text such as Law&ethics is shown and saved back unchanged."""
    settings.ALTCHA_HMAC_KEY = ""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    no_reset(settings, [])
    sub = _legacy_row(submitter_affiliation="Centre for Law&ethics")
    _grant_edit(client, sub)
    shown = _shown_values(client, sub)
    assert shown["submitter_affiliation"] == "Centre for Law&ethics"
    data = edit_form_data(sub, **{f: shown[f] for f in FIELDS})
    data["submitter_affiliation"] += ", Bonn"
    resp = client.post(reverse("submissions:edit", args=[sub.pk]), data=data)
    assert resp.status_code == 302, resp.content[:2000]

    sub.refresh_from_db()
    assert sub.submitter_affiliation == "Centre for Law&ethics, Bonn"


@pytest.mark.parametrize(
    "stored", ["Example Institute&nbsp;", "&#32;Example Institute", "Cafe&#x301;"]
)
def test_untouched_edit_is_not_a_change_when_decoding_changes_spacing(
    client, settings, no_reset, stored
):
    """The form normalises what it receives (surrounding space, NFC), so the
    comparison with the stored text must normalise both sides."""
    settings.ALTCHA_HMAC_KEY = ""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    no_reset(settings, [])
    sub = _legacy_row(submitter_affiliation=stored)
    _grant_edit(client, sub)
    shown = _shown_values(client, sub)
    data = edit_form_data(sub, **{f: shown[f] for f in FIELDS})
    resp = client.post(reverse("submissions:edit", args=[sub.pk]), data=data)
    assert resp.status_code == 302, resp.content[:2000]

    sub.refresh_from_db()
    assert sub.status == "approved"
    assert sub.submitter_affiliation == stored
    assert not SubmissionChangeLog.objects.filter(submission=sub).exists()


def test_edit_keeps_the_service_name_locked_for_legacy_rows(client, settings):
    settings.ALTCHA_HMAC_KEY = ""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    sub = _legacy_row()
    _grant_edit(client, sub)
    data = edit_form_data(sub, service_name="Some other name")
    resp = client.post(reverse("submissions:edit", args=[sub.pk]), data=data)
    assert resp.status_code == 302
    sub.refresh_from_db()
    assert sub.service_name == LEGACY["service_name"][0]
    assert sub.status == "approved"


def test_edit_of_plain_row_with_special_characters_is_not_a_change(
    client, settings, no_reset
):
    """Rows created after this change hold the text as typed; re-submitting
    them unchanged must not look like an edit."""
    settings.ALTCHA_HMAC_KEY = ""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    no_reset(settings, ["github_url"])
    sub = ServiceSubmissionFactory(
        status="approved",
        biotools_url="",
        service_name="R&D <Tool>",
        submitter_first_name="Jo&Jo",
        submitter_last_name="Doe <Jr>",
        submitter_affiliation="A & B",
    )
    _grant_edit(client, sub)
    shown = _shown_values(client, sub)
    assert shown["service_name"] == "R&D <Tool>"
    data = edit_form_data(sub, **{f: shown[f] for f in FIELDS})
    resp = client.post(reverse("submissions:edit", args=[sub.pk]), data=data)
    assert resp.status_code == 302
    sub.refresh_from_db()
    assert sub.status == "approved"
    assert sub.last_change_summary is None
    assert sub.service_name == "R&D <Tool>"


# ---------------------------------------------------------------------------
# Legacy rows and the REST API
# ---------------------------------------------------------------------------


def _patch(sub, body):
    _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
    resp = client.patch(f"/api/v1/submissions/{sub.pk}/", body, format="json")
    assert resp.status_code == 200, resp.content
    sub.refresh_from_db()
    return resp


@pytest.fixture
def notify():
    from unittest import mock

    with mock.patch("apps.api.views.send_update_notification") as task:
        yield task.delay


@pytest.mark.parametrize("no_reset_list", [["github_url"], []], ids=["exempt", "none"])
@pytest.mark.parametrize("variant", [0, 1], ids=["stored-text", "decoded-text"])
@pytest.mark.parametrize(
    "field", ["submitter_first_name", "submitter_last_name", "submitter_affiliation"]
)
def test_api_patch_resending_legacy_text_is_not_a_change(
    settings, no_reset, notify, field, variant, no_reset_list
):
    no_reset(settings, no_reset_list)
    sub = _legacy_row()
    _patch(sub, {field: LEGACY[field][variant]})
    assert sub.status == "approved"
    assert sub.primary_maturity_tag == "mature"
    assert sub.last_change_summary is None
    assert not SubmissionChangeLog.objects.filter(submission=sub).exists()
    assert getattr(sub, field) == LEGACY[field][0]
    assert notify.call_args.kwargs == {"changes": [], "status_reset": False}


@pytest.mark.parametrize(
    "field", ["submitter_first_name", "submitter_last_name", "submitter_affiliation"]
)
def test_api_patch_real_change_of_legacy_field_is_stored_as_sent(
    settings, no_reset, notify, field
):
    no_reset(settings, ["github_url"])
    sub = _legacy_row()
    _patch(sub, {field: "Changed & <new>"})
    assert getattr(sub, field) == "Changed & <new>"
    assert sub.status == "submitted"
    logged = [
        c["field"]
        for log in SubmissionChangeLog.objects.filter(submission=sub)
        for c in log.changes
    ]
    assert field in logged


@pytest.mark.parametrize("variant", [0, 1], ids=["stored-text", "decoded-text"])
def test_api_patch_echoing_legacy_service_name_gives_no_warning(
    settings, no_reset, notify, variant
):
    no_reset(settings, ["github_url"])
    sub = _legacy_row()
    resp = _patch(sub, {"service_name": LEGACY["service_name"][variant]})
    assert "warnings" not in resp.json()
    assert sub.status == "approved"


def test_api_patch_different_service_name_still_warns(settings, no_reset, notify):
    no_reset(settings, ["github_url"])
    sub = _legacy_row()
    resp = _patch(sub, {"service_name": "Another Name"})
    assert "warnings" in resp.json()
    assert sub.service_name == LEGACY["service_name"][0]


def test_api_returns_stored_text_unchanged(settings):
    sub = _legacy_row()
    _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
    body = client.get(f"/api/v1/submissions/{sub.pk}/").json()
    for field in FIELDS:
        assert body[field] == LEGACY[field][0]


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def test_keep_stored_text_helper():
    from apps.submissions.models import keep_stored_text

    assert keep_stored_text("R&amp;D", "R&D") == "R&amp;D"
    assert keep_stored_text("R&amp;D", "R&amp;D") == "R&amp;D"
    assert keep_stored_text("R&amp;D", "R&D 2") == "R&D 2"
    assert keep_stored_text("", "R&D") == "R&D"
    assert keep_stored_text(None, "R&D") == "R&D"
    assert keep_stored_text("Plain", "Plain") == "Plain"
    assert keep_stored_text("Plain", "Other") == "Other"
