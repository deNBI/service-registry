"""Client IP extraction behind the reverse proxy (apps/submissions/http_utils).

Only syntactically valid IP addresses are ever returned: a malformed header
used to reach django-ratelimit (ValueError -> HTTP 500 on every rate-limited
endpoint) and the submission_ip GenericIPAddressField (a database error on
PostgreSQL). Behind nginx, X-Real-IP is always overwritten with the real
address, so this only matters without the proxy, but it must never crash.
"""

import pytest
from django.test import RequestFactory, override_settings
from django.urls import reverse

from apps.submissions.http_utils import get_client_ip, get_ratelimit_ip

rf = RequestFactory()


def _req(real=None, xff=None, remote="192.0.2.10"):
    meta = {"REMOTE_ADDR": remote}
    if real is not None:
        meta["HTTP_X_REAL_IP"] = real
    if xff is not None:
        meta["HTTP_X_FORWARDED_FOR"] = xff
    return rf.get("/", **meta)


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        # Valid sources in priority order (unchanged behaviour).
        ({"real": "203.0.113.7", "xff": "198.51.100.1"}, "203.0.113.7"),
        ({"xff": "198.51.100.1, 10.0.0.1"}, "198.51.100.1"),
        ({}, "192.0.2.10"),
        ({"real": "  203.0.113.7  "}, "203.0.113.7"),
        ({"real": "2001:db8::1"}, "2001:db8::1"),
        ({"real": "2001:DB8:0::1"}, "2001:db8::1"),  # canonical form
        ({"real": "::ffff:203.0.113.7"}, "::ffff:203.0.113.7"),
        # Invalid X-Real-IP falls through to the next valid source.
        ({"real": "198.51.100.29968", "xff": "198.51.100.1"}, "198.51.100.1"),
        ({"real": "not-an-ip"}, "192.0.2.10"),
        ({"real": "203.0.113.7:8080"}, "192.0.2.10"),
        ({"real": "fe80::1%eth0"}, "192.0.2.10"),  # zone ids are not client IPs
        ({"real": "203.0.113.0/24"}, "192.0.2.10"),
        ({"real": "<script>"}, "192.0.2.10"),
        # Invalid leftmost X-Forwarded-For is skipped too.
        ({"xff": "garbage, 198.51.100.1"}, "192.0.2.10"),
        ({"xff": ""}, "192.0.2.10"),
        # Nothing valid at all.
        ({"real": "x", "xff": "y", "remote": ""}, ""),
        ({"remote": "unix:/run/gunicorn.sock"}, ""),
    ],
)
def test_get_client_ip_returns_only_valid_addresses(kwargs, expected):
    assert get_client_ip(_req(**kwargs)) == expected


def test_ratelimit_key_is_always_a_parseable_ip():
    import ipaddress

    assert get_ratelimit_ip(_req(real="203.0.113.7")) == "203.0.113.7"
    fallback = get_ratelimit_ip(_req(real="x", remote=""))
    ipaddress.ip_address(fallback)  # never raises
    assert fallback == "0.0.0.0"


def test_ratelimit_setting_uses_the_safe_key_function(settings):
    assert settings.RATELIMIT_IP_META_KEY == (
        "apps.submissions.http_utils.get_ratelimit_ip"
    )


@pytest.mark.django_db
@override_settings(RATELIMIT_ENABLE=True)
@pytest.mark.parametrize("header", ["198.51.100.29968", "not-an-ip", "::1%lo"])
def test_malformed_x_real_ip_is_not_a_server_error(client, header):
    """End to end through django-ratelimit: previously HTTP 500."""
    from django.core.cache import cache

    cache.clear()
    try:
        resp = client.post(
            reverse("submissions:validate_field"),
            {"field": "public_contact_email", "public_contact_email": "a@b.org"},
            HTTP_X_REAL_IP=header,
        )
        assert resp.status_code == 200
    finally:
        cache.clear()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "extra,stored",
    [
        ({"HTTP_X_REAL_IP": "203.0.113.7"}, "203.0.113.7"),
        # Malformed header: the next valid source (the TCP peer) is stored.
        ({"HTTP_X_REAL_IP": "198.51.100.29968"}, "127.0.0.1"),
        # Nothing valid anywhere: stored as NULL, never as an invalid value.
        ({"HTTP_X_REAL_IP": "bogus", "REMOTE_ADDR": ""}, None),
    ],
)
def test_api_create_stores_only_a_valid_submission_ip(extra, stored):
    from rest_framework.test import APIClient

    from apps.submissions.models import ServiceSubmission
    from tests.test_api import _valid_payload

    resp = APIClient().post(
        "/api/v1/submissions/", _valid_payload(), format="json", **extra
    )
    assert resp.status_code == 201, resp.content
    sub = ServiceSubmission.objects.get(pk=resp.json()["id"])
    assert sub.submission_ip == stored
