import hashlib
import json
import secrets

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from apps.submissions.markdown_render import render_markdown
from tests.factories import ServiceSubmissionFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_cache():
    # The renderer caches on pk+updated_at and LocMemCache is shared across tests.
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api_admin_client():
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


def test_detail_has_html_field(api_admin_client, settings):
    settings.SITE_CONFIG = {"features": {"markdown_descriptions": True}}
    sub = ServiceSubmissionFactory(
        status="approved", biotools_url="", service_description="**bold**"
    )
    resp = api_admin_client.get(f"/api/v1/submissions/{sub.id}/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["service_description"] == "**bold**"  # raw kept
    assert "<strong>bold</strong>" in body["service_description_html"]


def test_list_omits_html_field(api_admin_client, settings):
    settings.SITE_CONFIG = {"features": {"markdown_descriptions": True}}
    ServiceSubmissionFactory(status="approved", biotools_url="")
    resp = api_admin_client.get("/api/v1/submissions/")
    assert resp.status_code == 200, resp.content
    payload = resp.json()
    rows = (
        payload["results"]
        if isinstance(payload, dict) and "results" in payload
        else payload
    )
    assert "service_description_html" not in rows[0]


def test_detail_html_is_escaped_when_flag_off(api_admin_client, settings):
    """A *_html field must always be safe to insert as HTML: with the flag
    off it carries the escaped raw text, never live markup."""
    settings.SITE_CONFIG = {"features": {"markdown_descriptions": False}}
    raw = "<script>x</script> **b**"
    sub = ServiceSubmissionFactory(
        status="approved", biotools_url="", service_description=raw
    )
    resp = api_admin_client.get(f"/api/v1/submissions/{sub.id}/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    html = body["service_description_html"]
    assert "&lt;script&gt;" in html
    assert "<script" not in html
    assert "<strong>" not in html
    assert html == "&lt;script&gt;x&lt;/script&gt; **b**"
    assert body["service_description"] == raw  # raw unchanged


def test_detail_html_decodes_legacy_entities_once_when_flag_off(
    api_admin_client, settings
):
    """Legacy rows hold HTML entities (the old web form escaped input). With
    the flag off the *_html field decodes them once before escaping, so a
    consumer inserting it as HTML sees ``x > 5 & y``, not a literal ``&gt;``."""
    settings.SITE_CONFIG = {"features": {"markdown_descriptions": False}}
    raw = "x &gt; 5 &amp; y"
    sub = ServiceSubmissionFactory(
        status="approved", biotools_url="", service_description=raw
    )
    resp = api_admin_client.get(f"/api/v1/submissions/{sub.id}/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["service_description_html"] == "x &gt; 5 &amp; y"
    assert body["service_description"] == raw  # raw unchanged


def test_detail_html_escapes_encoded_legacy_markup_when_flag_off(
    api_admin_client, settings
):
    """Decoding entities must never turn a stored ``&lt;script&gt;`` into live
    markup: the decoded text is escaped again."""
    settings.SITE_CONFIG = {"features": {"markdown_descriptions": False}}
    sub = ServiceSubmissionFactory(
        status="approved",
        biotools_url="",
        service_description="&lt;script&gt;x&lt;/script&gt;",
    )
    resp = api_admin_client.get(f"/api/v1/submissions/{sub.id}/")
    html = resp.json()["service_description_html"]
    assert "<script" not in html
    assert html == "&lt;script&gt;x&lt;/script&gt;"


def test_detail_html_decodes_legacy_entities_once_when_flag_on(
    api_admin_client, settings
):
    settings.SITE_CONFIG = {"features": {"markdown_descriptions": True}}
    sub = ServiceSubmissionFactory(
        status="approved", biotools_url="", service_description="x &gt; 5"
    )
    resp = api_admin_client.get(f"/api/v1/submissions/{sub.id}/")
    html = resp.json()["service_description_html"]
    assert "x &gt; 5" in html
    assert "&amp;gt;" not in html


def test_html_field_is_read_only_on_write_serializers():
    from apps.api.serializers import (
        SubmissionCreateSerializer,
        SubmissionUpdateResponseSerializer,
    )

    for cls in (SubmissionCreateSerializer, SubmissionUpdateResponseSerializer):
        # read_only fields are dropped from input by DRF, so a client cannot
        # set service_description_html on POST/PATCH.
        assert cls().fields["service_description_html"].read_only is True


def test_schema_documents_html_field():
    resp = APIClient().get("/api/schema/?format=json")
    assert resp.status_code == 200
    schemas = json.loads(resp.content)["components"]["schemas"]
    prop = schemas["SubmissionDetail"]["properties"]["service_description_html"]
    assert prop["type"] == "string"
    assert prop["readOnly"] is True
    # Detail-only: list payloads stay small.
    assert "service_description_html" not in schemas["SubmissionList"]["properties"]


def test_patch_ignores_client_html_and_renders_new_raw(api_admin_client, settings):
    """End to end: a client-supplied service_description_html is dropped; the
    response carries the server rendering of the NEW raw text, which is what
    gets stored."""
    settings.SITE_CONFIG = {"features": {"markdown_descriptions": True}}
    sub = ServiceSubmissionFactory(
        status="approved",
        biotools_url="",
        service_description="Old description text that is comfortably long.",
    )
    # Warm the render cache with the old text so a stale entry would show up.
    before = api_admin_client.get(f"/api/v1/submissions/{sub.id}/")
    assert "Old description" in before.json()["service_description_html"]

    new_raw = "Updated tool that handles **huge** genomes and x > 5 reads quickly."
    resp = api_admin_client.patch(
        f"/api/v1/submissions/{sub.id}/",
        {
            "service_description": new_raw,
            "service_description_html": "<script>evil()</script>",
        },
        format="json",
    )
    assert resp.status_code == 200, resp.content
    body = resp.json()
    html = body["service_description_html"]
    assert "evil()" not in html
    assert "<script" not in html
    assert html == str(render_markdown(new_raw))
    assert "<strong>huge</strong>" in html
    assert body["service_description"] == new_raw
    sub.refresh_from_db()
    assert sub.service_description == new_raw


# ---------------------------------------------------------------------------
# Every API access path, in both flag states: the raw field is the stored
# value and service_description_html is the same, correct rendering whoever
# asks (owner write/read key, admin full/read key, create/update responses).
# ---------------------------------------------------------------------------

API_RAW = (
    "Aligns **short reads** & x &gt; 5 for List<String> users.\n\n"
    "- fast\n- accurate\n\n[docs](https://example.org/docs)"
)
API_HTML_ON = (
    "<p>Aligns <strong>short reads</strong> &amp; x &gt; 5 for "
    "List&lt;String&gt; users.</p>\n<ul>\n<li>fast</li>\n<li>accurate</li>\n</ul>\n"
    '<p><a href="https://example.org/docs" rel="nofollow noopener noreferrer" '
    'target="_blank">docs</a></p>'
)
# Flag off: entities decoded once, then the plain text escaped once.
API_HTML_OFF = (
    "Aligns **short reads** &amp; x &gt; 5 for List&lt;String&gt; users.\n\n"
    "- fast\n- accurate\n\n[docs](https://example.org/docs)"
)


@pytest.fixture(params=[False, True], ids=["flag_off", "flag_on"])
def api_flag(request, settings):
    settings.SITE_CONFIG = {"features": {"markdown_descriptions": request.param}}
    return request.param


def _expected_html(flag_on: bool) -> str:
    return API_HTML_ON if flag_on else API_HTML_OFF


def _admin_key_client(scope: str) -> APIClient:
    from apps.api.models import AdminAPIKey

    plaintext = secrets.token_urlsafe(48)
    AdminAPIKey.objects.create(
        label=f"MD {scope} key",
        key_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
        scope=scope,
        is_active=True,
    )
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"AdminKey {plaintext}")
    return c


def _owner_key_client(sub, scope: str) -> APIClient:
    from tests.factories import APIKeyFactory

    _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub, scope=scope)
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
    return c


def test_expected_html_literals_match_the_renderer():
    """Guards the literals above against drifting from the spec'd pipeline."""
    assert str(render_markdown(API_RAW)) == API_HTML_ON


@pytest.mark.parametrize(
    "who", ["owner_write", "owner_read", "admin_full", "admin_read"]
)
def test_detail_same_for_every_authorised_caller(who, api_flag):
    sub = ServiceSubmissionFactory(
        status="approved", biotools_url="", service_description=API_RAW
    )
    kind, scope = who.split("_")
    client = (
        _owner_key_client(sub, scope) if kind == "owner" else _admin_key_client(scope)
    )
    resp = client.get(f"/api/v1/submissions/{sub.id}/")
    assert resp.status_code == 200, resp.content
    body = resp.json()
    assert body["service_description"] == API_RAW
    assert body["service_description_html"] == _expected_html(api_flag)


@pytest.mark.parametrize("scope", ["full", "read"])
def test_list_raw_only_for_admin_keys(scope, api_flag):
    ServiceSubmissionFactory(
        status="approved", biotools_url="", service_description=API_RAW
    )
    resp = _admin_key_client(scope).get("/api/v1/submissions/")
    assert resp.status_code == 200
    rows = resp.json()["results"]
    assert [r["service_description"] for r in rows] == [API_RAW]
    assert all("service_description_html" not in r for r in rows)


def test_create_stores_raw_and_returns_rendering(api_flag):
    """An unauthenticated create stores the text exactly as sent (no HTML
    escaping on any input path) and the 201 body already carries the html."""
    from apps.submissions.models import ServiceSubmission
    from tests.test_api import _valid_payload

    payload = {**_valid_payload(), "service_description": API_RAW}
    resp = APIClient().post("/api/v1/submissions/", payload, format="json")
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["service_description"] == API_RAW
    assert body["service_description_html"] == _expected_html(api_flag)
    stored = ServiceSubmission.objects.get(pk=body["id"])
    assert stored.service_description == API_RAW


def test_owner_patch_response_and_follow_up_get_agree(api_flag):
    sub = ServiceSubmissionFactory(
        status="approved",
        biotools_url="",
        service_description="Original description that is long enough to be valid.",
    )
    client = _owner_key_client(sub, "write")
    client.get(f"/api/v1/submissions/{sub.id}/")  # warm the render cache
    resp = client.patch(
        f"/api/v1/submissions/{sub.id}/",
        {"service_description": API_RAW},
        format="json",
    )
    assert resp.status_code == 200, resp.content
    assert resp.json()["service_description_html"] == _expected_html(api_flag)
    again = client.get(f"/api/v1/submissions/{sub.id}/").json()
    assert again["service_description"] == API_RAW
    assert again["service_description_html"] == _expected_html(api_flag)


def test_api_resend_of_crlf_row_is_not_a_change(api_flag):
    """A row stored with CRLF (old web-form rows) is re-stored with LF on the
    next save, but that is not a description change: nothing is logged and
    the approved service stays approved (reset only on a real change)."""
    from apps.submissions.models import ServiceSubmission, SubmissionChangeLog

    crlf = "Line one of the description.\r\nLine two, long enough overall."
    sub = ServiceSubmissionFactory(status="approved", biotools_url="")
    ServiceSubmission.objects.filter(pk=sub.pk).update(service_description=crlf)
    resp = _owner_key_client(sub, "write").patch(
        f"/api/v1/submissions/{sub.id}/",
        {"service_description": crlf.replace("\r\n", "\n")},
        format="json",
    )
    assert resp.status_code == 200, resp.content
    sub.refresh_from_db()
    assert sub.service_description == crlf.replace("\r\n", "\n")
    assert sub.status == "approved"
    logged = [
        ch["field"]
        for log in SubmissionChangeLog.objects.filter(submission=sub)
        for ch in log.changes
    ]
    assert logged == []


def test_key_factory_honours_scope_and_rejects_unknown_arguments():
    """The factory once dropped scope= silently, so 'read key' tests really
    used write keys. It must create the requested scope or fail loudly."""
    from tests.factories import APIKeyFactory

    sub = ServiceSubmissionFactory(biotools_url="")
    key, _ = APIKeyFactory.create_with_plaintext(submission=sub, scope="read")
    assert key.scope == "read"
    key, _ = APIKeyFactory.create_with_plaintext(submission=sub)
    assert key.scope == "write"
    with pytest.raises(TypeError, match="is_active"):
        APIKeyFactory.create_with_plaintext(submission=sub, is_active=False)


def test_owner_read_key_cannot_patch_description(api_flag):
    sub = ServiceSubmissionFactory(
        status="approved",
        biotools_url="",
        service_description="Original description that is long enough to be valid.",
    )
    resp = _owner_key_client(sub, "read").patch(
        f"/api/v1/submissions/{sub.id}/",
        {"service_description": API_RAW},
        format="json",
    )
    assert resp.status_code == 403
    sub.refresh_from_db()
    assert sub.service_description.startswith("Original description")
