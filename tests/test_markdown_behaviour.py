"""Storage, web edit status reset, and template guards for Markdown descriptions."""

import html
import pathlib
import re

import pytest
from django.urls import reverse

from tests.factories import APIKeyFactory, ServiceSubmissionFactory
from tests.test_forms import _base_form_data
from tests.test_views import TestEditView as _EditViewTests

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

    data = _base_form_data({"service_description": RAW})
    form = SubmissionForm(data=data)
    assert form.is_valid(), form.errors
    obj = form.save()
    obj.refresh_from_db()
    assert obj.service_description == RAW.strip()


# ---------------------------------------------------------------------------
# Web edit flow (submissions:edit): real POSTs through EditView.
# ---------------------------------------------------------------------------


def _edit_form_data(sub, **overrides):
    # Reuse the complete edit payload builder from the existing edit-view tests.
    return _EditViewTests._edit_form_data(None, sub, **overrides)


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
    data = _edit_form_data(
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
        reverse("submissions:edit", args=[sub.pk]), data=_edit_form_data(sub)
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

    data = _edit_form_data(sub, service_description=submitted_value)
    resp = client.post(reverse("submissions:edit", args=[sub.pk]), data=data)
    assert resp.status_code == 302, resp.content[:2000]
    sub.refresh_from_db()
    assert sub.status == "approved"
    assert sub.service_description == desc


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
