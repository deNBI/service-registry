"""XSS neutralisation and cross-surface consistency of rendered descriptions."""

import copy
import hashlib
import re
import secrets

import pytest
from django.conf import settings as django_settings
from django.core.cache import cache
from django.urls import reverse
from django.utils.html import escape
from rest_framework.test import APIClient

from apps.submissions.markdown_render import (
    ALLOWED_TAGS,
    markdown_to_text,
    render_markdown,
)
from tests.factories import ServiceSubmissionFactory
from tests.helpers import card_desc, list_desc

pytestmark = pytest.mark.django_db

XSS_VECTORS = [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    "[x](javascript:alert(1))",
    "[x](data:text/html,<script>1</script>)",
    "<a href='http://x' onclick='evil()'>x</a>",
    "<svg/onload=alert(1)>",
    "[x](  javascript:alert(1))",
    "[x](jav&#x61;script:alert(1))",
    "[x][r]\n\n[r]: javascript:alert(1)",
    "<iframe src=//evil></iframe>",
    "**<b onmouseover=alert(1)>x</b>**",
    "![i](javascript:alert(1))",
    "[x](vbscript:msgbox(1))",
    # Headings are now allowed (h4-h6): their content must stay inert too.
    "# <script>alert(1)</script>",
    "## [x](javascript:alert(1))",
    "### <img src=x onerror=alert(1)>",
    "[x](//evil.example)",
    # Handler text inside a correctly quoted title value is inert and must
    # not be flagged.
    '[x](https://e.com "onmouseover=alert(1)")',
]

_TAG_RE = re.compile(r"<\s*([a-zA-Z0-9]+)([^>]*)>")
_QUOTED_RE = re.compile(r"\"[^\"]*\"|'[^']*'")
# "/" is a valid attribute separator in HTML (e.g. <a/onclick=x>).
_HANDLER_RE = re.compile(r"[\s/]on\w+\s*=", re.IGNORECASE)
_HREF_RE = re.compile(r"\bhref\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", re.IGNORECASE)
_SAFE_HREF_RE = re.compile(r"^(https?:|mailto:)", re.IGNORECASE)


def _assert_safe_html(out: str) -> None:
    """Parse opening tags: allowlisted names, no live handlers, safe hrefs."""
    for name, attrs in _TAG_RE.findall(out):
        assert name.lower() in ALLOWED_TAGS, (name, out)
        # Quoted values are inert text; only unquoted attribute syntax is live.
        assert not _HANDLER_RE.search(_QUOTED_RE.sub('""', attrs)), (attrs, out)
        hrefs = _HREF_RE.findall(attrs)
        assert len(hrefs) == len(re.findall(r"\bhref\s*=", attrs, re.I))
        for href in hrefs:
            value = href.strip("\"'").strip()
            assert _SAFE_HREF_RE.match(value), (href, out)


def test_safe_html_checker_flags_live_handlers():
    """Self-test: the checker must catch handlers after whitespace or "/",
    and ignore handler-like text inside a quoted attribute value."""
    for live in ("<a/onclick=x>", "<p onmouseover=x>"):
        with pytest.raises(AssertionError):
            _assert_safe_html(live)
    _assert_safe_html('<a title="x onmouseover=y">')


@pytest.mark.parametrize("vec", XSS_VECTORS)
def test_render_markdown_neutralises(vec):
    _assert_safe_html(str(render_markdown(vec)))


# ---------------------------------------------------------------------------
# Cross-surface consistency: API, catalogue list view, catalogue card.
# ---------------------------------------------------------------------------

RAW = (
    "Tool for x > 5 & Bob's data.\n\n"
    "- **first** item\n- second item\n\n"
    "[docs](https://example.com/docs)"
)


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def md_on(settings):
    cfg = copy.deepcopy(django_settings.SITE_CONFIG)
    cfg.setdefault("features", {})
    cfg["features"]["catalogue"] = True
    cfg["features"]["markdown_descriptions"] = True
    settings.SITE_CONFIG = cfg
    return cfg


@pytest.fixture
def api_admin_client():
    from apps.api.models import AdminAPIKey

    plaintext = secrets.token_urlsafe(48)
    AdminAPIKey.objects.create(
        label="Consistency Key",
        key_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
        scope="full",
        is_active=True,
    )
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"AdminKey {plaintext}")
    return c


def test_all_surfaces_render_identically(client, api_admin_client, md_on):
    sub = ServiceSubmissionFactory(
        status="approved",
        biotools_url="",
        service_name="SameSvc",
        service_description=RAW,
    )
    expected_html = str(render_markdown(sub.service_description))
    # Sanity: the fixture actually exercises markdown and entity escaping.
    assert "<strong>first</strong>" in expected_html
    assert "&gt;" in expected_html and "&amp;" in expected_html

    api = api_admin_client.get(f"/api/v1/submissions/{sub.id}/")
    assert api.status_code == 200, api.content
    assert api.json()["service_description_html"] == expected_html

    list_resp = client.get(reverse("catalogue:index") + "?view=list")
    assert list_resp.status_code == 200
    assert list_desc(list_resp.content) == expected_html

    card_resp = client.get(reverse("catalogue:index"))
    assert card_resp.status_code == 200
    assert card_desc(card_resp.content) == escape(
        markdown_to_text(sub.service_description)
    )
