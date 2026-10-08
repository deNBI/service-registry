"""Description cache: one read per catalogue page, and pages still render
when the cache backend is unreachable."""

from unittest import mock

import pytest
from django.core.cache import cache

from apps.submissions import markdown_render as mr
from tests.factories import ServiceCategoryFactory, ServiceSubmissionFactory

pytestmark = pytest.mark.django_db

# A Redis cache on a port nothing listens on: every call raises
# redis.exceptions.ConnectionError, as in production with Redis down.
UNREACHABLE_REDIS = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": "redis://127.0.0.1:1/0",
    }
}


@pytest.fixture
def md_catalogue(settings):
    settings.SITE_CONFIG = {
        **settings.SITE_CONFIG,
        "features": {
            **settings.SITE_CONFIG.get("features", {}),
            "catalogue": True,
            "markdown_descriptions": True,
        },
    }
    cache.clear()


def _services(n, **kwargs):
    return [
        ServiceSubmissionFactory(
            status="approved",
            biotools_url="",
            service_name=f"Tool {i}",
            service_description=f"**Bold {i}** text",
            **kwargs,
        )
        for i in range(n)
    ]


@pytest.mark.parametrize("query", ["", "?view=list", "?group_by=category&view=list"])
def test_catalogue_renders_with_the_cache_unreachable(
    client, settings, md_catalogue, query
):
    _services(3)
    settings.CACHES = UNREACHABLE_REDIS
    resp = client.get(f"/catalogue/{query}")
    assert resp.status_code == 200
    body = resp.content.decode()
    if "view=list" in query:
        assert "<strong>Bold 0</strong>" in body
    else:
        assert "Bold 0 text" in body


def test_grid_partial_renders_with_the_cache_unreachable(
    client, settings, md_catalogue
):
    _services(2)
    settings.CACHES = UNREACHABLE_REDIS
    resp = client.get("/catalogue/grid/?view=list", HTTP_HX_REQUEST="true")
    assert resp.status_code == 200
    assert "<strong>Bold 1</strong>" in resp.content.decode()


def test_single_renders_with_the_cache_unreachable(settings, md_catalogue):
    (sub,) = _services(1)
    settings.CACHES = UNREACHABLE_REDIS
    assert (
        mr.render_submission_description(sub) == "<p><strong>Bold 0</strong> text</p>"
    )
    assert mr.submission_description_snippet(sub) == "Bold 0 text"


class _CountingCache:
    """Wraps the real cache and counts the calls the renderer makes."""

    def __init__(self, real):
        self.real = real
        self.calls = []

    def __getattr__(self, name):
        attr = getattr(self.real, name)

        def wrapper(*args, **kwargs):
            self.calls.append(name)
            return attr(*args, **kwargs)

        return wrapper


@pytest.mark.parametrize("view", ["grid", "list"])
def test_one_cache_read_per_catalogue_page(client, md_catalogue, view):
    _services(12)
    counting = _CountingCache(mr.cache)
    with mock.patch.object(mr, "cache", counting):
        first = client.get(f"/catalogue/?view={view}")
        cold = list(counting.calls)
        counting.calls.clear()
        second = client.get(f"/catalogue/?view={view}")
        warm = list(counting.calls)
    assert cold == ["get_many", "set_many"]
    assert warm == ["get_many"]
    assert first.content == second.content


def test_grouped_service_in_two_groups_is_rendered_once(client, md_catalogue):
    (sub,) = _services(1)
    sub.service_categories.set([ServiceCategoryFactory(), ServiceCategoryFactory()])
    with mock.patch.object(mr, "_render_kind", wraps=mr._render_kind) as render:
        resp = client.get("/catalogue/?group_by=category&view=list")
    assert resp.content.decode().count("<strong>Bold 0</strong>") == 2
    assert render.call_count == 1


def test_primed_value_is_not_reused_after_an_edit(md_catalogue):
    (sub,) = _services(1)
    mr.prime_descriptions([sub], "html")
    sub.service_description = "*new*"
    sub.save()
    assert mr.render_submission_description(sub) == "<p><em>new</em></p>"


def test_flag_off_does_not_touch_the_cache(client, settings, md_catalogue):
    settings.SITE_CONFIG["features"]["markdown_descriptions"] = False
    _services(2)
    settings.CACHES = UNREACHABLE_REDIS
    assert client.get("/catalogue/?view=list").status_code == 200
