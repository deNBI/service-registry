import hashlib
import secrets

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from tests.factories import ServiceSubmissionFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_cache():
    # The renderer caches on pk+updated_at and LocMemCache is shared across tests.
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def admin_client():
    from apps.api.models import AdminAPIKey

    plaintext = secrets.token_urlsafe(48)
    AdminAPIKey.objects.create(
        label="MD Test Key",
        key_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
        scope="full",
        is_active=True,
    )
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"AdminKey {plaintext}")
    return c


def test_detail_has_html_field(admin_client, settings):
    settings.SITE_CONFIG = {"features": {"markdown_descriptions": True}}
    sub = ServiceSubmissionFactory(
        status="approved", biotools_url="", service_description="**bold**"
    )
    resp = admin_client.get(f"/api/v1/submissions/{sub.id}/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["service_description"] == "**bold**"  # raw kept
    assert "<strong>bold</strong>" in body["service_description_html"]


def test_list_omits_html_field(admin_client, settings):
    settings.SITE_CONFIG = {"features": {"markdown_descriptions": True}}
    ServiceSubmissionFactory(status="approved", biotools_url="")
    resp = admin_client.get("/api/v1/submissions/")
    assert resp.status_code == 200, resp.content
    payload = resp.json()
    rows = (
        payload["results"]
        if isinstance(payload, dict) and "results" in payload
        else payload
    )
    assert "service_description_html" not in rows[0]


def test_detail_html_is_escaped_when_flag_off(admin_client, settings):
    """A *_html field must always be safe to insert as HTML: with the flag
    off it carries the escaped raw text, never live markup."""
    settings.SITE_CONFIG = {"features": {"markdown_descriptions": False}}
    raw = "<script>x</script> **b**"
    sub = ServiceSubmissionFactory(
        status="approved", biotools_url="", service_description=raw
    )
    resp = admin_client.get(f"/api/v1/submissions/{sub.id}/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    html = body["service_description_html"]
    assert "&lt;script&gt;" in html
    assert "<script" not in html
    assert "<strong>" not in html
    assert html == "&lt;script&gt;x&lt;/script&gt; **b**"
    assert body["service_description"] == raw  # raw unchanged


def test_html_field_is_read_only_on_write_serializers():
    from apps.api.serializers import (
        SubmissionCreateSerializer,
        SubmissionUpdateResponseSerializer,
    )

    for cls in (SubmissionCreateSerializer, SubmissionUpdateResponseSerializer):
        # read_only fields are dropped from input by DRF, so a client cannot
        # set service_description_html on POST/PATCH.
        assert cls().fields["service_description_html"].read_only is True


def test_schema_documents_html_field(admin_client):
    resp = APIClient().get("/api/schema/")
    assert resp.status_code == 200
    assert b"service_description_html" in resp.content
