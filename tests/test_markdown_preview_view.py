"""Tests for the HTMX Markdown preview endpoint (/markdown-preview/).

The endpoint renders through the shared render_markdown pipeline, is gated on
the markdown_descriptions feature flag (404 when off) and is per-IP
rate-limited on RATE_LIMIT_VALIDATE like validate_field, but non-blocking: a
throttled request gets a 200 with an inline message because htmx ignores 4xx
responses.
"""

import copy

import pytest
from django.conf import settings as django_settings
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

from apps.submissions.models import DESCRIPTION_MAX_LENGTH

pytestmark = pytest.mark.django_db

URL = reverse("submissions:markdown-preview")


def _set_flag(settings, enabled: bool) -> None:
    cfg = copy.deepcopy(getattr(settings, "SITE_CONFIG", {}) or {})
    cfg.setdefault("features", {})["markdown_descriptions"] = enabled
    settings.SITE_CONFIG = cfg


@pytest.fixture
def md_on(settings):
    _set_flag(settings, True)
    return settings


def test_preview_renders_markdown(client, md_on):
    resp = client.post(URL, {"description": "**bold**"})
    assert resp.status_code == 200
    assert b"<strong>bold</strong>" in resp.content


def test_preview_flags_removed_content(client, md_on):
    resp = client.post(URL, {"description": "[x](javascript:alert(1))"})
    assert resp.status_code == 200
    assert b"javascript:" not in resp.content
    assert b"removed" in resp.content.lower()


def test_preview_renders_heading_scaled_without_notice(client, md_on):
    resp = client.post(URL, {"description": "# Heading"})
    assert resp.status_code == 200
    assert b"<h1" not in resp.content
    assert b"<h4>Heading</h4>" in resp.content
    assert b"removed" not in resp.content.lower()


def test_preview_flags_stripped_image(client, md_on):
    resp = client.post(URL, {"description": "Logo ![i](https://e.org/x.png)"})
    assert resp.status_code == 200
    assert b"<img" not in resp.content
    assert b"removed" in resp.content.lower()


def test_preview_flags_schemeless_link_and_unwraps_it(client, md_on):
    resp = client.post(URL, {"description": "[x](/relative)"})
    assert resp.status_code == 200
    assert b"href=" not in resp.content
    assert b"<a" not in resp.content
    assert b"removed" in resp.content.lower()


def test_preview_plain_text_has_no_removed_notice(client, md_on):
    resp = client.post(URL, {"description": "A plain description of a tool."})
    assert resp.status_code == 200
    assert b"A plain description of a tool." in resp.content
    assert b"removed" not in resp.content.lower()


def test_preview_literal_tag_text_is_preserved_not_flagged(client, md_on):
    resp = client.post(URL, {"description": "Use the <select> element."})
    assert resp.status_code == 200
    assert b"&lt;select&gt;" in resp.content
    assert b"<select>" not in resp.content
    assert b"removed" not in resp.content.lower()


def test_preview_rejects_overlong(client, md_on):
    resp = client.post(URL, {"description": "x" * (DESCRIPTION_MAX_LENGTH + 1)})
    assert resp.status_code == 200
    assert b"too long" in resp.content.lower()


def test_preview_length_boundary(client, md_on):
    at_limit = "x" * DESCRIPTION_MAX_LENGTH
    resp = client.post(URL, {"description": at_limit})
    assert resp.status_code == 200
    assert b"too long" not in resp.content.lower()
    assert f"<p>{at_limit}</p>".encode() in resp.content

    resp = client.post(URL, {"description": at_limit + "x"})
    assert resp.status_code == 200
    assert b"too long" in resp.content.lower()
    assert at_limit.encode() not in resp.content


def test_preview_is_404_when_flag_off(client, settings):
    _set_flag(settings, False)
    resp = client.post(URL, {"description": "**bold**"})
    assert resp.status_code == 404


def test_preview_get_is_405(client, md_on):
    assert client.get(URL).status_code == 405


@override_settings(RATELIMIT_ENABLE=True)
def test_preview_is_rate_limited_with_friendly_message(client, md_on):
    """A throttled preview returns 200 with an inline message (htmx drops 4xx
    silently), and the bucket is keyed on the real client IP (X-Real-IP)."""
    cache.clear()
    try:
        limit = int(django_settings.RATE_LIMIT_VALIDATE.split("/")[0])
        payload = {"description": "**bold**"}
        ip_a, ip_b = "203.0.113.9", "203.0.113.10"
        for _ in range(limit):
            resp = client.post(URL, payload, HTTP_X_REAL_IP=ip_a)
            assert resp.status_code == 200
            assert b"<strong>bold</strong>" in resp.content
        resp = client.post(URL, payload, HTTP_X_REAL_IP=ip_a)
        assert resp.status_code == 200
        assert b"Too many previews" in resp.content
        assert b"<strong>bold</strong>" not in resp.content
        resp = client.post(URL, payload, HTTP_X_REAL_IP=ip_b)
        assert resp.status_code == 200
        assert b"<strong>bold</strong>" in resp.content
    finally:
        cache.clear()  # don't leak the counter into other tests


def test_preview_accepts_service_description_field_name(client, md_on):
    resp = client.post(URL, {"service_description": "**bold**"})
    assert resp.status_code == 200
    assert b"<strong>bold</strong>" in resp.content


def test_preview_removed_notice_lists_unsupported_formatting(client, md_on):
    resp = client.post(URL, {"description": "![i](https://e.org/x.png)"})
    assert (
        b"Some formatting was removed: headings, code, images and unsafe links "
        b"are not supported." in resp.content
    )


@pytest.mark.parametrize("text", ["", "   ", "\n\t \n"])
def test_preview_empty_text_returns_empty_body(client, md_on, text):
    """Empty input yields an empty body so the pane matches :empty and hides."""
    resp = client.post(URL, {"service_description": text})
    assert resp.status_code == 200
    assert resp.content == b""
