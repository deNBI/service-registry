import pytest
from django.core.cache import cache

from apps.catalogue.templatetags.markdown_tags import md_description, md_snippet
from tests.factories import ServiceSubmissionFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_md_cache():
    # settings_test uses LocMemCache (shared across tests); clear to avoid
    # cross-test bleed on the md:v{N}:{kind}:{pk}:{ts} keys.
    cache.clear()
    yield
    cache.clear()


def test_md_description_renders_when_enabled(settings):
    settings.SITE_CONFIG = {"features": {"markdown_descriptions": True}}
    sub = ServiceSubmissionFactory(
        status="approved", biotools_url="", service_description="**bold**"
    )
    assert "<strong>bold</strong>" in md_description(sub)


def test_md_description_plain_when_disabled(settings):
    settings.SITE_CONFIG = {"features": {"markdown_descriptions": False}}
    sub = ServiceSubmissionFactory(
        status="approved", biotools_url="", service_description="**bold**"
    )
    # Flag off: no HTML rendering, markdown chars survive as literal text
    assert "<strong>" not in md_description(sub)
    assert "**bold**" in md_description(sub)


def test_md_snippet_is_plain_text(settings):
    settings.SITE_CONFIG = {"features": {"markdown_descriptions": True}}
    sub = ServiceSubmissionFactory(
        status="approved", biotools_url="", service_description="- one\n- two"
    )
    out = md_snippet(sub)
    assert "<" not in out
    assert "one" in out and "two" in out


def test_md_snippet_plain_when_disabled(settings):
    settings.SITE_CONFIG = {"features": {"markdown_descriptions": False}}
    sub = ServiceSubmissionFactory(
        status="approved", biotools_url="", service_description="**bold**"
    )
    assert md_snippet(sub) == "**bold**"


def test_md_snippet_uses_cached_snippet(settings):
    settings.SITE_CONFIG = {"features": {"markdown_descriptions": True}}
    sub = ServiceSubmissionFactory(
        status="approved", biotools_url="", service_description="first"
    )
    assert md_snippet(sub) == "first"
    # Same pk/updated_at: the filter must serve the cached snippet, not re-render.
    sub.service_description = "second"
    assert md_snippet(sub) == "first"


def test_card_snippet_is_the_list_view_text(settings):
    """The card snippet is the visible text of the list view's HTML, so a
    card and its list row never disagree."""
    from apps.submissions import markdown_render as mr

    settings.SITE_CONFIG = {"features": {"markdown_descriptions": True}}
    raw = "2024. Launched **fast** tools\n2025. Added [docs](https://e.org) &gt; more"
    sub = ServiceSubmissionFactory(
        status="approved", biotools_url="", service_description=raw
    )
    html = md_description(sub)
    snippet = md_snippet(sub)
    assert "<strong>fast</strong>" in str(html)
    assert snippet == mr._html_to_text(str(html), mr.SNIPPET_LIMIT)
    assert snippet == mr.markdown_to_text(raw)
    assert snippet == "2024. Launched fast tools 2025. Added docs > more"
