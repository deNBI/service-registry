"""Tests for the HTMX Markdown preview endpoint (/markdown-preview/).

The endpoint renders through the shared render_markdown pipeline, is gated on
the markdown_descriptions feature flag (404 when off) and is per-IP
rate-limited exactly like validate_field (RATE_LIMIT_VALIDATE, block=True).
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


def test_preview_flags_stripped_heading(client, md_on):
    resp = client.post(URL, {"description": "# Heading"})
    assert resp.status_code == 200
    assert b"<h1" not in resp.content
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


def test_preview_is_404_when_flag_off(client, settings):
    _set_flag(settings, False)
    resp = client.post(URL, {"description": "**bold**"})
    assert resp.status_code == 404


def test_preview_get_is_405(client, md_on):
    assert client.get(URL).status_code == 405


@override_settings(RATELIMIT_ENABLE=True)
def test_preview_is_rate_limited(client, md_on):
    cache.clear()
    try:
        limit = int(django_settings.RATE_LIMIT_VALIDATE.split("/")[0])
        payload = {"description": "**bold**"}
        ip = "203.0.113.9"
        for _ in range(limit):
            assert client.post(URL, payload, HTTP_X_REAL_IP=ip).status_code == 200
        assert client.post(URL, payload, HTTP_X_REAL_IP=ip).status_code == 403
    finally:
        cache.clear()  # don't leak the counter into other tests
