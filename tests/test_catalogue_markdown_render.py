"""Catalogue templates: rendered Markdown in list view, plain snippet on cards."""

import re

import pytest
from django.core.cache import cache
from django.urls import reverse

from tests.factories import ServiceSubmissionFactory
from tests.test_catalogue_views import CATALOGUE_ON

pytestmark = pytest.mark.django_db

MD_ON = {
    **CATALOGUE_ON,
    "features": {**CATALOGUE_ON["features"], "markdown_descriptions": True},
}
MD_OFF = {
    **CATALOGUE_ON,
    "features": {**CATALOGUE_ON["features"], "markdown_descriptions": False},
}


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _list_url():
    return reverse("catalogue:index") + "?view=list"


def _list_desc(content: bytes) -> str:
    m = re.search(
        r'<div class="catalogue-list-desc-text">(.*?)</div>',
        content.decode(),
        re.DOTALL,
    )
    assert m, "list-view description container not found"
    return m.group(1)


def _card_desc(content: bytes) -> str:
    m = re.search(
        r'<p class="text-muted small catalogue-description mb-3">(.*?)</p>',
        content.decode(),
        re.DOTALL,
    )
    assert m, "card description not found"
    return m.group(1)


def test_list_view_renders_markdown(client, settings):
    settings.SITE_CONFIG = MD_ON
    ServiceSubmissionFactory(
        status="approved",
        biotools_url="",
        service_name="MDSvc",
        service_description="**bold**",
    )
    resp = client.get(_list_url())
    assert b"<strong>bold</strong>" in resp.content


def test_list_view_description_container_is_div(client, settings):
    settings.SITE_CONFIG = MD_ON
    ServiceSubmissionFactory(
        status="approved",
        biotools_url="",
        service_name="MDSvc",
        service_description="para one\n\n- item",
    )
    resp = client.get(_list_url())
    assert b'<div class="catalogue-list-desc-text">' in resp.content
    assert b'<p class="catalogue-list-desc-text">' not in resp.content
    inner = _list_desc(resp.content)
    assert "<p>para one</p>" in inner
    assert "<li>item</li>" in inner


def test_card_shows_plain_snippet(client, settings):
    settings.SITE_CONFIG = MD_ON
    ServiceSubmissionFactory(
        status="approved",
        biotools_url="",
        service_name="MDSvc",
        service_description="**bold** text",
    )
    resp = client.get(reverse("catalogue:index"))
    assert b"<strong>bold</strong>" not in resp.content  # card is plain
    assert _card_desc(resp.content) == "bold text"


def test_legacy_escaped_entity_decoded_once_flag_on(client, settings):
    settings.SITE_CONFIG = MD_ON
    ServiceSubmissionFactory(
        status="approved",
        biotools_url="",
        service_name="MDSvc",
        service_description="x &gt; 5",
    )
    list_inner = _list_desc(client.get(_list_url()).content)
    # Browser shows "x > 5": the entity is present once, never double-escaped.
    assert "&amp;gt;" not in list_inner
    assert "x &gt; 5" in list_inner

    card = _card_desc(client.get(reverse("catalogue:index")).content)
    assert "&amp;gt;" not in card
    assert card == "x &gt; 5"


def test_flag_off_list_and_card_show_raw_autoescaped(client, settings):
    settings.SITE_CONFIG = MD_OFF
    ServiceSubmissionFactory(
        status="approved",
        biotools_url="",
        service_name="MDSvc",
        service_description="**bold** <b>x</b>",
    )
    list_inner = _list_desc(client.get(_list_url()).content)
    assert "<strong>" not in list_inner
    assert "**bold** &lt;b&gt;x&lt;/b&gt;" in list_inner

    card = _card_desc(client.get(reverse("catalogue:index")).content)
    assert "<strong>" not in card
    assert card == "**bold** &lt;b&gt;x&lt;/b&gt;"
