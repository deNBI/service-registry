"""Tests for the Markdown Preview control on the public register/edit forms.

The control (shared partial markdown_preview_controls.html) is rendered under
the service_description field only when the markdown_descriptions feature flag
is on. It is pure HTMX: no inline script is added by this feature.
"""

import copy
import re

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from tests.factories import APIKeyFactory, ServiceSubmissionFactory

pytestmark = pytest.mark.django_db

PANE_ID = b'id="md-preview-id_service_description"'


def _set_flag(settings, enabled: bool) -> None:
    cfg = copy.deepcopy(getattr(settings, "SITE_CONFIG", {}) or {})
    cfg.setdefault("features", {})["markdown_descriptions"] = enabled
    settings.SITE_CONFIG = cfg


def _preview_button(content: bytes) -> bytes:
    match = re.search(rb"<button[^>]*data-md-preview[^>]*>", content)
    assert match, "Preview button not rendered"
    return match.group(0)


def test_register_form_has_preview_control(client, settings):
    _set_flag(settings, True)
    resp = client.get(reverse("submissions:register"))
    assert resp.status_code == 200
    assert b"markdown-preview" in resp.content
    assert b"Preview" in resp.content
    assert PANE_ID in resp.content


def test_preview_button_attributes(client, settings):
    _set_flag(settings, True)
    resp = client.get(reverse("submissions:register"))
    button = _preview_button(resp.content)
    assert b'type="button"' in button
    assert reverse("submissions:markdown-preview").encode() in button
    assert b'hx-target="#md-preview-id_service_description"' in button
    assert b"#id_service_description" in button
    assert b"csrfmiddlewaretoken" in button
    assert b'hx-trigger="click"' in button
    assert b'hx-params="service_description,csrfmiddlewaretoken"' in button


def test_preview_pane_is_live_region(client, settings):
    _set_flag(settings, True)
    resp = client.get(reverse("submissions:register"))
    match = re.search(rb"<div[^>]*" + re.escape(PANE_ID) + rb"[^>]*>", resp.content)
    assert match
    assert b'aria-live="polite"' in match.group(0)
    assert b"markdown-preview" in match.group(0)


def test_controls_partial_renders_no_script():
    html = render_to_string(
        "submissions/partials/markdown_preview_controls.html",
        {"field_id": "id_service_description"},
    )
    assert 'id="md-preview-id_service_description"' in html
    assert "<script" not in html.lower()


def test_no_preview_control_when_flag_off(client, settings):
    _set_flag(settings, False)
    resp = client.get(reverse("submissions:register"))
    assert resp.status_code == 200
    assert b"markdown-preview" not in resp.content
    assert b"data-md-preview" not in resp.content


def test_edit_form_has_preview_control(client, settings):
    _set_flag(settings, True)
    sub = ServiceSubmissionFactory(biotools_url="")
    key_obj, _ = APIKeyFactory.create_with_plaintext(submission=sub)
    session = client.session
    session["edit_grants"] = {str(sub.pk): str(key_obj.pk)}
    session.save()
    resp = client.get(reverse("submissions:edit", args=[sub.pk]))
    assert resp.status_code == 200
    assert PANE_ID in resp.content
    assert b'type="button"' in _preview_button(resp.content)


def test_edit_form_no_preview_control_when_flag_off(client, settings):
    _set_flag(settings, False)
    sub = ServiceSubmissionFactory(biotools_url="")
    key_obj, _ = APIKeyFactory.create_with_plaintext(submission=sub)
    session = client.session
    session["edit_grants"] = {str(sub.pk): str(key_obj.pk)}
    session.save()
    resp = client.get(reverse("submissions:edit", args=[sub.pk]))
    assert resp.status_code == 200
    # Sanity: this is the real edit form, with the description field.
    assert b'name="service_description"' in resp.content
    assert b"markdown-preview" not in resp.content
    assert b"data-md-preview" not in resp.content
    assert PANE_ID not in resp.content
