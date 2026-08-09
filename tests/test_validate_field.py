"""Tests for the HTMX inline field-validation endpoint (/register/validate/).

Bakes in the behaviour surfaced by the CSRF/rate-limit investigation:
  - the endpoint is CSRF-protected (a POST without a token is rejected), and
  - it is per-IP rate-limited (RATE_LIMIT_VALIDATE, block=True).

So a 403 from this endpoint under heavy use is *rate-limiting*, i.e. correct
behaviour — not a CSRF bug. (django-ratelimit is disabled by default in the test
settings; the rate-limit test opts back in.)
"""

import pytest
from django.conf import settings
from django.core.cache import cache
from django.test import Client, override_settings
from django.urls import reverse

URL = reverse("submissions:validate_field")


@pytest.mark.django_db
def test_valid_value_returns_200_without_error_fragment(client):
    resp = client.post(
        URL,
        {"field": "public_contact_email", "public_contact_email": "good@example.com"},
    )
    assert resp.status_code == 200
    assert b"text-danger" not in resp.content  # no error div for a valid value


@pytest.mark.django_db
def test_invalid_value_returns_200_with_error_fragment(client):
    resp = client.post(
        URL, {"field": "public_contact_email", "public_contact_email": "not-an-email"}
    )
    assert resp.status_code == 200
    assert b"text-danger" in resp.content  # error div rendered for a bad value


@pytest.mark.django_db
def test_missing_field_name_is_400(client):
    assert client.post(URL, {}).status_code == 400


@pytest.mark.django_db
def test_unknown_field_name_is_400(client):
    assert client.post(URL, {"field": "no_such_field"}).status_code == 400


@pytest.mark.django_db
def test_get_is_405(client):
    assert client.get(URL).status_code == 405


@pytest.mark.django_db
def test_endpoint_is_csrf_protected():
    """A POST without a CSRF token is rejected — proves the observed 403s under a
    stale/garbage token are correct CSRF enforcement, not a missing check."""
    csrf_client = Client(enforce_csrf_checks=True)
    resp = csrf_client.post(
        URL,
        {"field": "public_contact_email", "public_contact_email": "good@example.com"},
    )
    assert resp.status_code == 403


@pytest.mark.django_db
@override_settings(RATELIMIT_ENABLE=True)
def test_rate_limit_is_enforced_and_keyed_on_real_client_ip(client):
    """After RATE_LIMIT_VALIDATE requests the endpoint returns 403 (block=True),
    AND the limit is keyed on the *real client IP* (X-Real-IP), not REMOTE_ADDR.

    This matters behind the reverse proxy: nginx is the TCP peer, so REMOTE_ADDR
    is the proxy's IP for EVERY request — bucketing by it would throttle all
    users as one shared global bucket. Two clients with different X-Real-IP (but
    the same REMOTE_ADDR, as in production) must get independent budgets.
    """
    cache.clear()
    try:
        limit = int(settings.RATE_LIMIT_VALIDATE.split("/")[0])
        payload = {
            "field": "public_contact_email",
            "public_contact_email": "good@example.com",
        }
        client_a, client_b = "203.0.113.7", "203.0.113.8"
        # Client A spends its whole budget → the next request from A is blocked.
        for _ in range(limit):
            assert client.post(URL, payload, HTTP_X_REAL_IP=client_a).status_code == 200
        assert client.post(URL, payload, HTTP_X_REAL_IP=client_a).status_code == 403
        # Client B (different real IP, SAME REMOTE_ADDR) is unaffected — proving
        # the bucket keys on the client IP, not the shared proxy IP.
        assert client.post(URL, payload, HTTP_X_REAL_IP=client_b).status_code == 200
    finally:
        cache.clear()  # don't leak the counter into other tests
