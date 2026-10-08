"""Tests for the Markdown preview endpoint (/markdown-preview/) used by the
Write/Preview description editor.

The endpoint renders through the shared render_markdown pipeline, is gated on
the markdown_descriptions feature flag (404 when off) and is rate-limited on
RATE_LIMIT_PREVIEW per signed-in user, else per IP, but non-blocking: a
throttled request gets a 200 with an inline message because the editor JS
shows any non-2xx response as a generic "Preview unavailable".
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
    resp = client.post(URL, {"service_description": "**bold**"})
    assert resp.status_code == 200
    assert b"<strong>bold</strong>" in resp.content


def test_preview_flags_removed_content(client, md_on):
    resp = client.post(URL, {"service_description": "[x](javascript:alert(1))"})
    assert resp.status_code == 200
    assert b"javascript:" not in resp.content
    assert b"removed" in resp.content.lower()


def test_preview_renders_heading_scaled_without_notice(client, md_on):
    resp = client.post(URL, {"service_description": "# Heading"})
    assert resp.status_code == 200
    assert b"<h1" not in resp.content
    assert b"<h4>Heading</h4>" in resp.content
    assert b"removed" not in resp.content.lower()


def test_preview_flags_stripped_image(client, md_on):
    resp = client.post(URL, {"service_description": "Logo ![i](https://e.org/x.png)"})
    assert resp.status_code == 200
    assert b"<img" not in resp.content
    assert b"removed" in resp.content.lower()


def test_preview_flags_schemeless_link_and_unwraps_it(client, md_on):
    resp = client.post(URL, {"service_description": "[x](/relative)"})
    assert resp.status_code == 200
    assert b"href=" not in resp.content
    assert b"<a" not in resp.content
    assert b"removed" in resp.content.lower()


def test_preview_plain_text_has_no_removed_notice(client, md_on):
    resp = client.post(URL, {"service_description": "A plain description of a tool."})
    assert resp.status_code == 200
    assert b"A plain description of a tool." in resp.content
    assert b"removed" not in resp.content.lower()


def test_preview_literal_tag_text_is_preserved_not_flagged(client, md_on):
    resp = client.post(URL, {"service_description": "Use the <select> element."})
    assert resp.status_code == 200
    assert b"&lt;select&gt;" in resp.content
    assert b"<select>" not in resp.content
    assert b"removed" not in resp.content.lower()


def test_preview_rejects_overlong(client, md_on):
    resp = client.post(URL, {"service_description": "x" * (DESCRIPTION_MAX_LENGTH + 1)})
    assert resp.status_code == 200
    assert b"too long" in resp.content.lower()


def test_preview_length_boundary(client, md_on):
    at_limit = "x" * DESCRIPTION_MAX_LENGTH
    resp = client.post(URL, {"service_description": at_limit})
    assert resp.status_code == 200
    assert b"too long" not in resp.content.lower()
    assert f"<p>{at_limit}</p>".encode() in resp.content

    resp = client.post(URL, {"service_description": at_limit + "x"})
    assert resp.status_code == 200
    assert b"too long" in resp.content.lower()
    assert at_limit.encode() not in resp.content


def test_preview_trailing_whitespace_not_counted(client, md_on):
    """Measured like clean_service_description (NFC + strip), so trailing
    whitespace cannot make the preview say "too long" while the form accepts."""
    at_limit = "x" * DESCRIPTION_MAX_LENGTH
    resp = client.post(URL, {"service_description": "  " + at_limit + "   \n\n  "})
    assert resp.status_code == 200
    assert b"too long" not in resp.content.lower()
    assert f"<p>{at_limit}</p>".encode() in resp.content


def test_preview_length_measured_after_nfc(client, md_on):
    """'e' + combining acute (2 code points) normalises to one: 2500 decomposed
    pairs + 1 char = 5001 raw code points but 2501 after NFC."""
    text = "e\u0301" * (DESCRIPTION_MAX_LENGTH // 2) + "x"
    resp = client.post(URL, {"service_description": text})
    assert resp.status_code == 200
    assert b"too long" not in resp.content.lower()


def test_preview_ignores_legacy_description_param(client, md_on):
    resp = client.post(URL, {"description": "**bold**"})
    assert resp.status_code == 200
    assert b"Nothing to preview." in resp.content
    assert b"<strong>" not in resp.content


def test_preview_is_404_when_flag_off(client, settings):
    _set_flag(settings, False)
    resp = client.post(URL, {"service_description": "**bold**"})
    assert resp.status_code == 404


def test_preview_get_is_405(client, md_on):
    assert client.get(URL).status_code == 405


@override_settings(RATELIMIT_ENABLE=True)
def test_preview_is_rate_limited_with_friendly_message(
    client, md_on, frozen_ratelimit_clock
):
    """A throttled preview returns 200 with an inline message (the editor JS
    shows non-2xx as a generic error), and the bucket is keyed on the real client IP (X-Real-IP)."""
    cache.clear()
    try:
        limit = int(django_settings.RATE_LIMIT_PREVIEW.split("/")[0])
        payload = {"service_description": "**bold**"}
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


@override_settings(RATELIMIT_ENABLE=True)
def test_signed_in_preview_bucket_is_separate_from_the_ip_bucket(
    client, md_on, frozen_ratelimit_clock, django_user_model
):
    """A signed-in user (an admin using the editor) is counted on their own
    bucket, so public visitors behind the same address cannot use it up, and
    the bucket follows the user, not the address."""
    cache.clear()
    try:
        limit = int(django_settings.RATE_LIMIT_PREVIEW.split("/")[0])
        payload = {"service_description": "**bold**"}
        ip = "203.0.113.9"
        for _ in range(limit + 1):
            client.post(URL, payload, HTTP_X_REAL_IP=ip)
        assert (
            b"Too many previews" in client.post(URL, payload, HTTP_X_REAL_IP=ip).content
        )

        client.force_login(django_user_model.objects.create_user("editor"))
        for _ in range(limit):
            resp = client.post(URL, payload, HTTP_X_REAL_IP=ip)
            assert b"<strong>bold</strong>" in resp.content
        resp = client.post(URL, payload, HTTP_X_REAL_IP="203.0.113.10")
        assert b"Too many previews" in resp.content
    finally:
        cache.clear()


@override_settings(RATELIMIT_ENABLE=True)
def test_preview_and_validation_have_separate_buckets(
    client, md_on, frozen_ratelimit_clock
):
    cache.clear()
    try:
        limit = int(django_settings.RATE_LIMIT_VALIDATE.split("/")[0])
        for _ in range(limit + 1):
            client.post("/register/validate/", {"field": "service_name"})
        resp = client.post(URL, {"service_description": "**bold**"})
        assert b"<strong>bold</strong>" in resp.content
    finally:
        cache.clear()


def test_preview_accepts_service_description_field_name(client, md_on):
    resp = client.post(URL, {"service_description": "**bold**"})
    assert resp.status_code == 200
    assert b"<strong>bold</strong>" in resp.content


@pytest.mark.parametrize("text", ["", "   \n\t "])
def test_preview_empty_input_shows_placeholder(client, md_on, text):
    """Empty input always yields a fragment (never an empty body)."""
    resp = client.post(URL, {"service_description": text})
    assert resp.status_code == 200
    body = resp.content.decode()
    assert "Nothing to preview." in body
    assert "md-rendered" not in body


def test_preview_notice_wording(client, md_on):
    resp = client.post(URL, {"service_description": "![i](https://e.org/x.png) text"})
    assert (
        "Some formatting was removed: images, code, horizontal rules, very deep "
        "nesting and links that do not start with http://, https:// or mailto: "
        "are not supported."
    ) in resp.content.decode()


def test_preview_notice_and_error_use_editor_alert_class(client, md_on):
    resp = client.post(URL, {"service_description": "![i](https://e.org/x.png) text"})
    assert b'class="md-editor__alert" role="status"' in resp.content
    resp = client.post(URL, {"service_description": "x" * (DESCRIPTION_MAX_LENGTH + 1)})
    assert b'class="md-editor__alert" role="alert"' in resp.content
    assert b"alert-warning" not in resp.content


def test_preview_body_wrapper_class(client, md_on):
    resp = client.post(URL, {"service_description": "**b**"})
    assert (
        '<div class="md-rendered"><p><strong>b</strong></p></div>'
        in resp.content.decode()
    )
