"""Storage, web edit status reset, and template guards for Markdown descriptions."""

import html
import pathlib
import re

import pytest
from django.urls import reverse

from tests.factories import APIKeyFactory, ServiceSubmissionFactory
from tests.helpers import base_form_data, edit_form_data

pytestmark = pytest.mark.django_db

# Must exceed DESCRIPTION_MIN_LENGTH (50 chars).
RAW = (
    "Use x > 5 in the alignment step for reliable filtering.\n\n"
    "- first bullet\n- second bullet\n\n[link](https://e.com)"
)


def test_cross_channel_identical_storage():
    # Same raw source via the web form stores the raw Markdown unchanged
    # (model/form apply idempotent NFC + strip only; no HTML escaping).
    from apps.submissions.forms import SubmissionForm

    data = base_form_data({"service_description": RAW})
    form = SubmissionForm(data=data)
    assert form.is_valid(), form.errors
    obj = form.save()
    obj.refresh_from_db()
    assert obj.service_description == RAW.strip()


# ---------------------------------------------------------------------------
# Web edit flow (submissions:edit): real POSTs through EditView.
# ---------------------------------------------------------------------------


def _grant_edit(client, sub):
    key_obj, _ = APIKeyFactory.create_with_plaintext(submission=sub)
    session = client.session
    session["edit_grants"] = {str(sub.pk): str(key_obj.pk)}
    session.save()


def _approved(description):
    return ServiceSubmissionFactory(
        status="approved", biotools_url="", service_description=description
    )


def test_web_edit_description_change_resets_to_submitted(client):
    sub = _approved("Old description text that is comfortably over fifty chars.")
    _grant_edit(client, sub)
    data = edit_form_data(
        sub,
        service_description="New **markdown** description, also well over fifty chars.",
    )
    resp = client.post(reverse("submissions:edit", args=[sub.pk]), data=data)
    assert resp.status_code == 302, resp.content[:2000]
    sub.refresh_from_db()
    assert sub.status == "submitted"
    assert (
        sub.service_description
        == "New **markdown** description, also well over fifty chars."
    )


def test_web_edit_unchanged_description_stays_approved(client):
    desc = "Stable **markdown** description that is well over fifty chars."
    sub = _approved(desc)
    _grant_edit(client, sub)
    resp = client.post(
        reverse("submissions:edit", args=[sub.pk]), data=edit_form_data(sub)
    )
    assert resp.status_code == 302, resp.content[:2000]
    sub.refresh_from_db()
    assert sub.status == "approved"
    assert sub.service_description == desc


def test_web_edit_resubmit_legacy_entity_stays_approved(client):
    # Legacy rows may hold a literal "&gt;". The edit form must round-trip it:
    # take the textarea value exactly as a browser would submit it (HTML-decoded
    # once) and re-post; nothing changed, so no status reset.
    desc = "Legacy row with x &gt; 5 stored escaped, padded past fifty chars."
    sub = _approved(desc)
    _grant_edit(client, sub)

    page = client.get(reverse("submissions:edit", args=[sub.pk])).content.decode()
    m = re.search(
        r'<textarea[^>]*name="service_description"[^>]*>(.*?)</textarea>',
        page,
        re.DOTALL,
    )
    assert m, "service_description textarea not found"
    # Django emits a leading newline after <textarea>; browsers drop it.
    submitted_value = html.unescape(m.group(1)).removeprefix("\n")
    assert submitted_value == desc

    data = edit_form_data(sub, service_description=submitted_value)
    resp = client.post(reverse("submissions:edit", args=[sub.pk]), data=data)
    assert resp.status_code == 302, resp.content[:2000]
    sub.refresh_from_db()
    assert sub.status == "approved"
    assert sub.service_description == desc


# ---------------------------------------------------------------------------
# Line endings: browsers submit every textarea with CRLF line breaks, while
# rows created through the API (JSON) or seed data hold LF. An untouched
# description must never count as a change because of that.
# ---------------------------------------------------------------------------

MULTILINE_LF = "First line of a description.\nSecond line, long enough overall.\n- a"


def _as_browser(text):
    return text.replace("\r\n", "\n").replace("\n", "\r\n")


@pytest.mark.parametrize(
    "stored",
    [MULTILINE_LF, _as_browser(MULTILINE_LF)],
    ids=["stored_lf", "stored_crlf"],
)
def test_web_edit_crlf_resubmit_stays_approved_and_logs_nothing(client, stored):
    from apps.submissions.models import ServiceSubmission, SubmissionChangeLog

    sub = _approved("placeholder description long enough to be valid here.")
    # Store the exact line endings under test (bypasses save() normalisation,
    # as rows written before it did).
    ServiceSubmission.objects.filter(pk=sub.pk).update(service_description=stored)
    sub.refresh_from_db()
    _grant_edit(client, sub)
    data = edit_form_data(sub, service_description=_as_browser(stored))
    resp = client.post(reverse("submissions:edit", args=[sub.pk]), data=data)
    assert resp.status_code == 302, resp.content[:2000]
    sub.refresh_from_db()
    assert sub.status == "approved"
    assert sub.service_description == MULTILINE_LF
    logged = [
        ch["field"]
        for log in SubmissionChangeLog.objects.filter(submission=sub)
        for ch in log.changes
    ]
    assert "service_description" not in logged


def test_every_input_path_stores_lf_line_endings():
    from apps.submissions.forms import SubmissionForm

    form = SubmissionForm(
        data=base_form_data({"service_description": _as_browser(MULTILINE_LF)})
    )
    assert form.is_valid(), form.errors
    obj = form.save()
    obj.refresh_from_db()
    assert obj.service_description == MULTILINE_LF
    obj.service_description = "Old Mac\rline endings, long enough to be a valid text."
    obj.save()
    obj.refresh_from_db()
    assert obj.service_description == (
        "Old Mac\nline endings, long enough to be a valid text."
    )


def test_length_limit_counts_a_line_break_once_on_every_channel():
    """The web form (CRLF), admin and API (LF) all accept the same text: a
    line break counts as one character everywhere."""
    from apps.submissions.forms import SubmissionForm
    from apps.submissions.models import DESCRIPTION_MAX_LENGTH

    body = "x" * (DESCRIPTION_MAX_LENGTH - 10)
    text = body + "\n" * 10  # exactly the limit with LF...
    text = "a" + "\n" * 10 + body[1:]  # ...and not only as trailing whitespace
    assert len(text) == DESCRIPTION_MAX_LENGTH
    form = SubmissionForm(
        data=base_form_data({"service_description": _as_browser(text)})
    )
    assert form.is_valid(), form.errors
    over = SubmissionForm(
        data=base_form_data({"service_description": _as_browser(text + "y")})
    )
    assert not over.is_valid()
    assert "service_description" in over.errors


# ---------------------------------------------------------------------------
# highlight guard
# ---------------------------------------------------------------------------

# `highlight` escape()s its input, so piping the rendered description through
# it would double-escape (and its mark_safe output would mask that). Guard
# against the two direct forms: `x|md_description|highlight` (possibly with
# other filters in between) inside one template expression. Indirect flows via
# {% with %} are not covered; keep this simple.
_MD_THEN_HIGHLIGHT = [
    re.compile(r"md_description\s*\|\s*highlight"),
    re.compile(r"\|\s*md_description[^}]*\|\s*highlight"),
]


def _pipes_md_into_highlight(text: str) -> bool:
    return any(p.search(text) for p in _MD_THEN_HIGHLIGHT)


def test_highlight_guard_detects_offending_pattern():
    assert _pipes_md_into_highlight("{{ s|md_description|highlight:q }}")
    assert _pipes_md_into_highlight("{{ s|md_description|safe|highlight:q }}")
    assert not _pipes_md_into_highlight("{{ s.service_name|highlight:q }}")
    assert not _pipes_md_into_highlight("{{ s|md_description }}")


def test_rendered_description_not_highlighted():
    root = pathlib.Path(__file__).resolve().parent.parent / "templates"
    templates = list(root.rglob("*.html"))
    assert templates
    offenders = [str(t) for t in templates if _pipes_md_into_highlight(t.read_text())]
    assert offenders == []
