"""
API Tests
=========
Tests for the DRF REST API endpoints.

Coverage:
  POST   /api/v1/submissions/        create, one-time key, consent, field validation
  GET    /api/v1/submissions/        admin list, pagination, filtering, full detail
  GET    /api/v1/submissions/{id}/   own submission only, wrong key denied, full detail shape
  PATCH  /api/v1/submissions/{id}/   partial update, status reset on approved
  PUT    /api/v1/submissions/{id}/   forbidden (405)
  GET    /api/v1/categories/         admin API key required, active-only
  GET    /api/v1/service-centers/    admin API key required, active-only
  GET    /api/v1/pis/                admin API key required, active-only
  GET    /api/schema/                always 200
  GET    /api/docs/                  always 200
  Auth:  ApiKey vs Token, revoked denial, scope enforcement, no-auth denial
        AdminAPIKey read/full scope — GET allowed, POST/PATCH blocked for read keys
  Shape: links present, sensitive fields absent, EDAM embedded, bio.tools embedded
"""

import pytest
from rest_framework.test import APIClient

from tests.factories import (
    APIKeyFactory,
    BioToolsFunctionFactory,
    BioToolsRecordFactory,
    PIFactory,
    ServiceCategoryFactory,
    ServiceCenterFactory,
    ServiceSubmissionFactory,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def admin_user(db):
    """Create an admin API key with full scope for testing."""
    from apps.api.models import AdminAPIKey
    import secrets
    import hashlib

    plaintext = secrets.token_urlsafe(48)
    key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
    key = AdminAPIKey.objects.create(
        label="Admin Test Key",
        key_hash=key_hash,
        scope="full",
        is_active=True,
    )
    return key, plaintext


@pytest.fixture
def staff_client(api_client, admin_user):
    """API client authenticated with admin API key."""
    _, plaintext = admin_user
    api_client.credentials(HTTP_AUTHORIZATION=f"AdminKey {plaintext}")
    return api_client


def _valid_payload():
    """Return a complete, valid POST payload for submission creation."""
    cat = ServiceCategoryFactory()
    center = ServiceCenterFactory()
    pi = PIFactory()
    from django.utils import timezone

    return {
        "date_of_entry": timezone.now().date().isoformat(),
        "submitter_first_name": "API Test",
        "submitter_last_name": "User",
        "submitter_affiliation": "API Institute",
        "register_as_elixir": False,
        "service_name": "API Created Service",
        "service_description": (
            "A description created via the API that is long enough to pass "
            "validation checks imposed by the model's minimum length constraint."
        ),
        "year_established": 2021,
        "service_category_ids": [cat.pk],
        "is_toolbox": False,
        "publications_pmids": "12345678",
        "responsible_pi_ids": [str(pi.pk)],
        "host_institute": "API Institute",
        "service_center_id": str(center.pk),
        "public_contact_email": "api@example.com",
        "internal_contact_name": "API Contact",
        "internal_contact_email": "api-internal@example.com",
        "website_url": "https://api.example.com",
        "terms_of_use_url": "https://api.example.com/tos",
        "licenses": [],
        "license_note": "Custom license",
        "kpi_monitoring": "planned",
        "kpi_start_year": "2021",
        "survey_participation": True,
        "data_protection_consent": True,
    }


# ===========================================================================
# POST /api/v1/submissions/ — public, no auth
# ===========================================================================


@pytest.mark.django_db
class TestSubmissionCreate:
    def test_create_returns_201(self, api_client):
        resp = api_client.post("/api/v1/submissions/", _valid_payload(), format="json")
        assert resp.status_code == 201

    def test_create_response_contains_api_key(self, api_client):
        resp = api_client.post("/api/v1/submissions/", _valid_payload(), format="json")
        data = resp.json()
        assert "api_key" in data
        assert len(data["api_key"]) >= 48

    def test_create_response_contains_api_key_warning(self, api_client):
        resp = api_client.post("/api/v1/submissions/", _valid_payload(), format="json")
        assert "api_key_warning" in resp.json()

    def test_create_response_has_links(self, api_client):
        resp = api_client.post("/api/v1/submissions/", _valid_payload(), format="json")
        data = resp.json()
        assert "links" in data
        assert "self" in data["links"]

    def test_create_empty_payload_returns_400(self, api_client):
        resp = api_client.post("/api/v1/submissions/", {}, format="json")
        assert resp.status_code == 400

    def test_create_no_consent_returns_400(self, api_client):
        payload = _valid_payload()
        payload["data_protection_consent"] = False
        resp = api_client.post("/api/v1/submissions/", payload, format="json")
        assert resp.status_code == 400

    def test_create_http_url_returns_400(self, api_client):
        payload = _valid_payload()
        payload["website_url"] = "http://not-https.com"
        resp = api_client.post("/api/v1/submissions/", payload, format="json")
        assert resp.status_code == 400

    def test_create_response_excludes_internal_email(self, api_client):
        resp = api_client.post("/api/v1/submissions/", _valid_payload(), format="json")
        data = resp.json()
        assert "internal_contact_email" not in data
        assert "api-internal@example.com" not in str(data)

    def test_create_response_excludes_submission_ip(self, api_client):
        resp = api_client.post("/api/v1/submissions/", _valid_payload(), format="json")
        assert "submission_ip" not in resp.json()

    def test_create_creates_api_key_in_db(self, api_client):
        from apps.submissions.models import SubmissionAPIKey

        before = SubmissionAPIKey.objects.count()
        api_client.post("/api/v1/submissions/", _valid_payload(), format="json")
        assert SubmissionAPIKey.objects.count() == before + 1

    def test_create_error_envelope_on_invalid(self, api_client):
        resp = api_client.post("/api/v1/submissions/", {}, format="json")
        data = resp.json()
        assert "error" in data
        assert "request_id" in data

    def test_create_without_internal_contact_name_returns_400(self, api_client):
        """internal_contact_name is mandatory on POST — missing it must fail."""
        payload = _valid_payload()
        del payload["internal_contact_name"]
        resp = api_client.post("/api/v1/submissions/", payload, format="json")
        assert resp.status_code == 400

    def test_create_without_internal_contact_email_returns_400(self, api_client):
        """internal_contact_email is mandatory on POST — missing it must fail."""
        payload = _valid_payload()
        del payload["internal_contact_email"]
        resp = api_client.post("/api/v1/submissions/", payload, format="json")
        assert resp.status_code == 400

    def test_create_saves_internal_contact_fields_to_db(self, api_client):
        """Internal contact fields submitted via POST must be persisted in the DB."""
        from apps.submissions.models import ServiceSubmission

        resp = api_client.post("/api/v1/submissions/", _valid_payload(), format="json")
        assert resp.status_code == 201
        sub = ServiceSubmission.objects.get(pk=resp.json()["id"])
        assert sub.internal_contact_name == "API Contact"
        assert sub.internal_contact_email == "api-internal@example.com"

    def test_create_with_maturity_tags_in_payload_ignores_them(self, api_client):
        """Maturity tags are read-only — values in POST payload must be silently discarded."""
        from apps.submissions.models import ServiceSubmission

        payload = _valid_payload()
        payload["primary_maturity_tag"] = "mature"
        payload["secondary_maturity_tags"] = ["unstable"]
        resp = api_client.post("/api/v1/submissions/", payload, format="json")
        assert resp.status_code == 201
        sub = ServiceSubmission.objects.get(pk=resp.json()["id"])
        assert sub.primary_maturity_tag is None or sub.primary_maturity_tag == ""
        assert sub.secondary_maturity_tags == []

    def test_create_with_https_url_public_contact(self, api_client):
        """API accepts an https URL for public_contact_email."""
        payload = {
            **_valid_payload(),
            "public_contact_email": "https://support.example.com",
        }
        resp = api_client.post("/api/v1/submissions/", payload, format="json")
        assert resp.status_code == 201
        assert resp.json()["public_contact_email"] == "https://support.example.com"

    def test_create_with_invalid_public_contact_rejected(self, api_client):
        """API rejects a value that is neither a valid email nor an https URL."""
        payload = {**_valid_payload(), "public_contact_email": "not-valid"}
        resp = api_client.post("/api/v1/submissions/", payload, format="json")
        assert resp.status_code == 400
        data = resp.json()
        # Error envelope: validation errors are nested under "error"
        error_body = data.get("error", data)
        assert "public_contact_email" in error_body


# ===========================================================================
# GET /api/v1/submissions/{id}/ — ApiKey auth, full detail
# ===========================================================================


@pytest.mark.django_db
class TestSubmissionRetrieve:
    def test_retrieve_own_submission_with_valid_key(self, api_client):
        sub = ServiceSubmissionFactory()
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.get(f"/api/v1/submissions/{sub.pk}/")
        assert resp.status_code == 200
        assert resp.json()["id"] == str(sub.pk)

    def test_retrieve_returns_edam_topics(self, api_client):
        sub = ServiceSubmissionFactory()
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.get(f"/api/v1/submissions/{sub.pk}/")
        data = resp.json()
        assert "edam_topics" in data
        assert "edam_operations" in data
        assert isinstance(data["edam_topics"], list)

    def test_retrieve_returns_biotoolsrecord_field(self, api_client):
        sub = ServiceSubmissionFactory()
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.get(f"/api/v1/submissions/{sub.pk}/")
        data = resp.json()
        assert "biotoolsrecord" in data  # null if not synced, present either way

    def test_retrieve_returns_responsible_pis(self, api_client):
        sub = ServiceSubmissionFactory()
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.get(f"/api/v1/submissions/{sub.pk}/")
        data = resp.json()
        assert "responsible_pis" in data
        assert isinstance(data["responsible_pis"], list)

    def test_retrieve_fails_without_auth(self, api_client):
        sub = ServiceSubmissionFactory()
        resp = api_client.get(f"/api/v1/submissions/{sub.pk}/")
        assert resp.status_code in (401, 403)

    def test_retrieve_fails_with_wrong_key(self, api_client):
        sub_a = ServiceSubmissionFactory(service_name="Sub A")
        sub_b = ServiceSubmissionFactory(service_name="Sub B")
        _, key_b = APIKeyFactory.create_with_plaintext(submission=sub_b)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {key_b}")
        resp = api_client.get(f"/api/v1/submissions/{sub_a.pk}/")
        assert resp.status_code in (403, 404)

    def test_retrieve_fails_with_revoked_key(self, api_client):
        sub = ServiceSubmissionFactory()
        key_obj, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        key_obj.revoke()
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.get(f"/api/v1/submissions/{sub.pk}/")
        # AuthenticationFailed raises 401; both 401 and 403 are acceptable rejections
        assert resp.status_code in (401, 403)

    def test_retrieve_response_excludes_sensitive_fields(self, api_client):
        sub = ServiceSubmissionFactory()
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.get(f"/api/v1/submissions/{sub.pk}/")
        data = resp.json()
        for field in (
            "internal_contact_email",
            "internal_contact_name",
            "submission_ip",
            "user_agent_hash",
        ):
            assert field not in data

    def test_retrieve_biotoolsrecord_is_null_when_no_record(self, api_client):
        sub = ServiceSubmissionFactory(biotools_url="")
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.get(f"/api/v1/submissions/{sub.pk}/")
        assert resp.status_code == 200
        assert resp.json()["biotoolsrecord"] is None

    def test_retrieve_biotoolsrecord_contains_functions_when_synced(self, api_client):
        sub = ServiceSubmissionFactory(biotools_url="")
        bt = BioToolsRecordFactory(submission=sub, biotools_id="synced")
        BioToolsFunctionFactory(
            record=bt,
            position=0,
            operations=[
                {"uri": "http://edamontology.org/operation_0004", "term": "Operation"}
            ],
        )
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.get(f"/api/v1/submissions/{sub.pk}/")
        assert resp.status_code == 200
        bt_data = resp.json()["biotoolsrecord"]
        assert bt_data is not None
        assert bt_data["biotools_id"] == "synced"
        functions = bt_data["functions"]
        assert len(functions) == 1
        assert (
            functions[0]["operations"][0]["uri"]
            == "http://edamontology.org/operation_0004"
        )

    def test_get_biotoolsrecord_returns_none_when_no_record(self):
        """get_biotoolsrecord must return None (not crash) when no biotools record exists."""
        from apps.api.serializers import SubmissionDetailSerializer

        sub = ServiceSubmissionFactory()
        s = SubmissionDetailSerializer(sub, context={"request": None})
        assert s.data["biotoolsrecord"] is None

    def test_get_links_omits_biotoolsrecord_key_when_no_record(self):
        from apps.api.serializers import SubmissionDetailSerializer

        sub = ServiceSubmissionFactory()
        s = SubmissionDetailSerializer(sub, context={"request": None})
        assert "biotoolsrecord" not in s.data["links"]


# ===========================================================================
# PATCH /api/v1/submissions/{id}/ — scope enforcement
# ===========================================================================


@pytest.mark.django_db
class TestSubmissionUpdate:
    def test_patch_own_submission(self, api_client):
        sub = ServiceSubmissionFactory()
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.patch(
            f"/api/v1/submissions/{sub.pk}/",
            {"kpi_start_year": "2025"},
            format="json",
        )
        assert resp.status_code == 200
        sub.refresh_from_db()
        assert sub.kpi_start_year == "2025"

    def test_patch_rejected_without_auth(self, api_client):
        sub = ServiceSubmissionFactory()
        resp = api_client.patch(f"/api/v1/submissions/{sub.pk}/", {}, format="json")
        assert resp.status_code in (401, 403)

    def test_patch_rejected_with_read_only_key(self, api_client):
        """Read-scoped ApiKey must not be able to PATCH."""
        from apps.submissions.models import SubmissionAPIKey

        sub = ServiceSubmissionFactory()
        key_obj, plaintext = SubmissionAPIKey.create_for_submission(
            submission=sub, label="RO key", created_by="test", scope="read"
        )
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.patch(
            f"/api/v1/submissions/{sub.pk}/",
            {"comments": "should fail"},
            format="json",
        )
        assert resp.status_code == 403

    def test_read_only_key_can_get(self, api_client):
        """Read-scoped ApiKey must be able to GET."""
        from apps.submissions.models import SubmissionAPIKey

        sub = ServiceSubmissionFactory()
        _, plaintext = SubmissionAPIKey.create_for_submission(
            submission=sub, label="RO key", created_by="test", scope="read"
        )
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.get(f"/api/v1/submissions/{sub.pk}/")
        assert resp.status_code == 200

    def test_put_rejected(self, api_client):
        sub = ServiceSubmissionFactory()
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.put(f"/api/v1/submissions/{sub.pk}/", {}, format="json")
        assert resp.status_code == 405

    def test_patch_approved_submission_resets_status(self, api_client, settings):
        settings.SUBMISSION_NO_RESET_FIELDS = []
        from apps.submissions.lifecycle import get_no_reset_fields

        get_no_reset_fields.cache_clear()
        sub = ServiceSubmissionFactory(status="approved")
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.patch(
            f"/api/v1/submissions/{sub.pk}/",
            {"comments": "Updated after approval"},
            format="json",
        )
        assert resp.status_code == 200
        sub.refresh_from_db()
        assert sub.status == "submitted"
        get_no_reset_fields.cache_clear()

    # ── diff capture ─────────────────────────────────────────────────────────

    def test_patch_writes_last_change_summary(self, api_client, settings):
        settings.CELERY_TASK_ALWAYS_EAGER = True
        sub = ServiceSubmissionFactory(comments="")
        key_obj, plaintext = APIKeyFactory.create_with_plaintext(
            submission=sub, label="CI pipeline key"
        )
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.patch(
            f"/api/v1/submissions/{sub.pk}/",
            {"comments": "Added via API"},
            format="json",
        )
        assert resp.status_code == 200
        sub.refresh_from_db()
        assert sub.last_change_summary is not None
        summary = sub.last_change_summary
        assert summary["changed_by"] == "api:CI pipeline key"
        assert "changed_at" in summary
        fields = {ch["field"] for ch in summary["changes"]}
        assert "comments" in fields

    def test_patch_no_change_does_not_write_summary(self, api_client, settings):
        settings.CELERY_TASK_ALWAYS_EAGER = True
        sub = ServiceSubmissionFactory(comments="same value")
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.patch(
            f"/api/v1/submissions/{sub.pk}/",
            {"comments": "same value"},
            format="json",
        )
        assert resp.status_code == 200
        sub.refresh_from_db()
        assert sub.last_change_summary is None

    def test_patch_with_change_sends_submitter_email(self, api_client, settings):
        from django.core import mail

        settings.CELERY_TASK_ALWAYS_EAGER = True
        settings.CELERY_TASK_EAGER_PROPAGATES = True
        sub = ServiceSubmissionFactory(
            comments="", internal_contact_email="owner@example.com"
        )
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        api_client.patch(
            f"/api/v1/submissions/{sub.pk}/",
            {"comments": "Changed via API"},
            format="json",
        )
        recipients = [addr for m in mail.outbox for addr in m.to]
        assert "owner@example.com" in recipients

    def test_patch_last_change_summary_not_in_api_response(self, api_client):
        """last_change_summary must never be exposed via the API."""
        sub = ServiceSubmissionFactory(
            last_change_summary={
                "changed_by": "submitter",
                "changed_at": "2026-01-01T00:00:00",
                "changes": [],
            }
        )
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.get(f"/api/v1/submissions/{sub.pk}/")
        assert resp.status_code == 200
        assert "last_change_summary" not in resp.data

    def test_patch_can_update_internal_contact_fields(self, api_client):
        """PATCH with internal contact fields must persist them to the DB."""
        sub = ServiceSubmissionFactory(
            internal_contact_name="Old Name",
            internal_contact_email="old@example.com",
        )
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.patch(
            f"/api/v1/submissions/{sub.pk}/",
            {
                "internal_contact_name": "New Name, New Uni",
                "internal_contact_email": "new@example.com",
            },
            format="json",
        )
        assert resp.status_code == 200
        sub.refresh_from_db()
        assert sub.internal_contact_name == "New Name, New Uni"
        assert sub.internal_contact_email == "new@example.com"

    def test_patch_response_excludes_internal_contact_fields(self, api_client):
        """Internal contact fields updated via PATCH must not appear in the response."""
        sub = ServiceSubmissionFactory()
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.patch(
            f"/api/v1/submissions/{sub.pk}/",
            {"internal_contact_name": "Secret Contact"},
            format="json",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "internal_contact_name" not in data
        assert "internal_contact_email" not in data

    def test_patch_with_maturity_tags_ignores_them(self, api_client):
        """Maturity tags are read-only — PATCH payload values must be silently discarded."""
        sub = ServiceSubmissionFactory(
            primary_maturity_tag=None, secondary_maturity_tags=[]
        )
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.patch(
            f"/api/v1/submissions/{sub.pk}/",
            {"primary_maturity_tag": "mature", "secondary_maturity_tags": ["unstable"]},
            format="json",
        )
        assert resp.status_code == 200
        sub.refresh_from_db()
        assert sub.primary_maturity_tag is None or sub.primary_maturity_tag == ""
        assert sub.secondary_maturity_tags == []

    def test_patch_approved_non_exempt_field_clears_maturity_tags(
        self, api_client, settings
    ):
        """PATCH on approved submission with a non-exempt field must reset status and clear tags."""
        settings.SUBMISSION_NO_RESET_FIELDS = ["github_url"]
        from apps.submissions.lifecycle import get_no_reset_fields

        get_no_reset_fields.cache_clear()
        sub = ServiceSubmissionFactory(
            status="approved",
            primary_maturity_tag="mature",
            secondary_maturity_tags=["unstable"],
        )
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.patch(
            f"/api/v1/submissions/{sub.pk}/",
            {"comments": "Updated — non-exempt field"},
            format="json",
        )
        assert resp.status_code == 200
        sub.refresh_from_db()
        assert sub.status == "submitted"
        assert not sub.primary_maturity_tag
        assert sub.secondary_maturity_tags == []
        get_no_reset_fields.cache_clear()

    def test_patch_approved_exempt_field_preserves_status_and_tags(
        self, api_client, settings
    ):
        """PATCH on approved submission with only exempt fields must preserve status and tags."""
        settings.SUBMISSION_NO_RESET_FIELDS = ["github_url", "comments"]
        from apps.submissions.lifecycle import get_no_reset_fields

        get_no_reset_fields.cache_clear()
        sub = ServiceSubmissionFactory(
            status="approved",
            primary_maturity_tag="mature",
            secondary_maturity_tags=["unstable"],
        )
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.patch(
            f"/api/v1/submissions/{sub.pk}/",
            {"comments": "Exempt field update"},
            format="json",
        )
        assert resp.status_code == 200
        sub.refresh_from_db()
        assert sub.status == "approved"
        assert sub.primary_maturity_tag == "mature"
        assert sub.secondary_maturity_tags == ["unstable"]
        get_no_reset_fields.cache_clear()

    def test_patch_approved_mixed_fields_resets_status(self, api_client, settings):
        """PATCH with both exempt and non-exempt fields must still reset status."""
        settings.SUBMISSION_NO_RESET_FIELDS = ["github_url"]
        from apps.submissions.lifecycle import get_no_reset_fields

        get_no_reset_fields.cache_clear()
        sub = ServiceSubmissionFactory(
            status="approved",
            primary_maturity_tag="mature",
            secondary_maturity_tags=[],
        )
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.patch(
            f"/api/v1/submissions/{sub.pk}/",
            {"github_url": "https://github.com/new", "comments": "Also non-exempt"},
            format="json",
        )
        assert resp.status_code == 200
        sub.refresh_from_db()
        assert sub.status == "submitted"
        assert not sub.primary_maturity_tag
        get_no_reset_fields.cache_clear()


# ===========================================================================
# GET /api/v1/submissions/ — admin list, full detail
# ===========================================================================


@pytest.mark.django_db
class TestSubmissionList:
    def test_list_requires_admin_token(self, api_client):
        resp = api_client.get("/api/v1/submissions/")
        assert resp.status_code in (401, 403)

    def test_list_apikey_auth_denied(self, api_client):
        """ApiKey must not grant access to the list endpoint."""
        sub = ServiceSubmissionFactory()
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.get("/api/v1/submissions/")
        assert resp.status_code == 403

    def test_list_with_admin_token_returns_200(self, staff_client):
        ServiceSubmissionFactory.create_batch(3)
        resp = staff_client.get("/api/v1/submissions/")
        assert resp.status_code == 200
        assert "results" in resp.json()

    def test_list_returns_full_detail_fields(self, staff_client):
        """List endpoint returns full detail — not a compact summary."""
        ServiceSubmissionFactory()
        resp = staff_client.get("/api/v1/submissions/")
        item = resp.json()["results"][0]
        for field in (
            "edam_topics",
            "edam_operations",
            "responsible_pis",
            "biotoolsrecord",
            "website_url",
            "licenses",
            "license_note",
            "kpi_monitoring",
        ):
            assert field in item, f"Missing field: {field}"

    def test_list_filtered_by_status(self, staff_client):
        ServiceSubmissionFactory(status="approved")
        ServiceSubmissionFactory(status="submitted")
        resp = staff_client.get("/api/v1/submissions/?status=approved")
        for item in resp.json()["results"]:
            assert item["status"] == "approved"

    def test_list_filtered_by_deprecated_status(self, staff_client):
        ServiceSubmissionFactory(status="deprecated")
        ServiceSubmissionFactory(status="approved")
        resp = staff_client.get("/api/v1/submissions/?status=deprecated")
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert len(results) >= 1
        for item in results:
            assert item["status"] == "deprecated"

    def test_list_excludes_internal_contact_email(self, staff_client):
        ServiceSubmissionFactory(internal_contact_email="secret@example.com")
        resp = staff_client.get("/api/v1/submissions/")
        assert "secret@example.com" not in resp.content.decode()

    def test_list_paginated(self, staff_client):
        ServiceSubmissionFactory.create_batch(5)
        resp = staff_client.get("/api/v1/submissions/?page_size=2")
        data = resp.json()
        assert "count" in data
        assert "next" in data


# ===========================================================================
# Reference data endpoints
# ===========================================================================


@pytest.mark.django_db
class TestReferenceDataEndpoints:
    def test_categories_requires_admin_token(self, api_client):
        resp = api_client.get("/api/v1/categories/")
        assert resp.status_code in (401, 403)

    def test_categories_apikey_auth_denied(self, api_client):
        """Submission owner ApiKey must not access reference data endpoints."""
        sub = ServiceSubmissionFactory()
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        assert api_client.get("/api/v1/categories/").status_code in (401, 403)
        assert api_client.get("/api/v1/service-centers/").status_code in (401, 403)
        assert api_client.get("/api/v1/pis/").status_code in (401, 403)

    def test_categories_active_filter(self, staff_client):
        ServiceCategoryFactory(name="Active Cat", is_active=True)
        ServiceCategoryFactory(name="Inactive Cat", is_active=False)
        resp = staff_client.get("/api/v1/categories/?is_active=true")
        names = [c["name"] for c in resp.json()]
        assert "Active Cat" in names
        assert "Inactive Cat" not in names

    def test_service_centers_requires_admin_token(self, api_client):
        resp = api_client.get("/api/v1/service-centers/")
        assert resp.status_code in (401, 403)

    def test_pis_requires_admin_token(self, api_client):
        resp = api_client.get("/api/v1/pis/")
        assert resp.status_code in (401, 403)

    def test_pis_active_filter(self, staff_client):
        PIFactory(last_name="ActivePI", is_active=True)
        PIFactory(last_name="InactivePI", is_active=False)
        resp = staff_client.get("/api/v1/pis/?is_active=true")
        last_names = [p["last_name"] for p in resp.json()]
        assert "ActivePI" in last_names
        assert "InactivePI" not in last_names

    def test_pi_response_has_display_name(self, staff_client):
        PIFactory(first_name="Ada", last_name="Lovelace", is_active=True)
        resp = staff_client.get("/api/v1/pis/")
        pi = next(p for p in resp.json() if p["last_name"] == "Lovelace")
        assert "display_name" in pi
        assert "Lovelace" in pi["display_name"]


# ===========================================================================
# Reference data CRUD — ServiceCategory
# ===========================================================================


@pytest.mark.django_db
class TestServiceCategoryCRUD:
    # ── list ────────────────────────────────────────────────────────────────

    def test_list_requires_admin_token(self, api_client):
        resp = api_client.get("/api/v1/categories/")
        assert resp.status_code in (401, 403)

    def test_list_returns_all_including_inactive(self, staff_client):
        ServiceCategoryFactory(name="Active Cat", is_active=True)
        ServiceCategoryFactory(name="Inactive Cat", is_active=False)
        resp = staff_client.get("/api/v1/categories/")
        names = [c["name"] for c in resp.json()]
        assert "Active Cat" in names
        assert "Inactive Cat" in names

    def test_list_filter_active_only(self, staff_client):
        ServiceCategoryFactory(name="Active Cat", is_active=True)
        ServiceCategoryFactory(name="Inactive Cat", is_active=False)
        resp = staff_client.get("/api/v1/categories/?is_active=true")
        names = [c["name"] for c in resp.json()]
        assert "Active Cat" in names
        assert "Inactive Cat" not in names

    def test_list_filter_inactive_only(self, staff_client):
        ServiceCategoryFactory(name="Active Cat", is_active=True)
        ServiceCategoryFactory(name="Inactive Cat", is_active=False)
        resp = staff_client.get("/api/v1/categories/?is_active=false")
        names = [c["name"] for c in resp.json()]
        assert "Inactive Cat" in names
        assert "Active Cat" not in names

    # ── create ──────────────────────────────────────────────────────────────

    def test_create_returns_201(self, staff_client):
        resp = staff_client.post(
            "/api/v1/categories/", {"name": "New Category"}, format="json"
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "New Category"
        assert resp.json()["is_active"] is True

    def test_create_requires_admin_token(self, api_client):
        resp = api_client.post(
            "/api/v1/categories/", {"name": "New Category"}, format="json"
        )
        assert resp.status_code in (401, 403)

    def test_create_missing_name_returns_400(self, staff_client):
        resp = staff_client.post("/api/v1/categories/", {}, format="json")
        assert resp.status_code == 400

    def test_create_duplicate_name_returns_400(self, staff_client):
        ServiceCategoryFactory(name="Duplicate")
        resp = staff_client.post(
            "/api/v1/categories/", {"name": "Duplicate"}, format="json"
        )
        assert resp.status_code == 400

    # ── retrieve ────────────────────────────────────────────────────────────

    def test_retrieve_returns_200(self, staff_client):
        cat = ServiceCategoryFactory()
        resp = staff_client.get(f"/api/v1/categories/{cat.pk}/")
        assert resp.status_code == 200
        assert resp.json()["id"] == cat.pk

    def test_retrieve_requires_admin_token(self, api_client):
        cat = ServiceCategoryFactory()
        resp = api_client.get(f"/api/v1/categories/{cat.pk}/")
        assert resp.status_code in (401, 403)

    def test_retrieve_nonexistent_returns_404(self, staff_client):
        resp = staff_client.get("/api/v1/categories/999999/")
        assert resp.status_code == 404

    # ── update ──────────────────────────────────────────────────────────────

    def test_patch_updates_name(self, staff_client):
        cat = ServiceCategoryFactory(name="Old Name")
        resp = staff_client.patch(
            f"/api/v1/categories/{cat.pk}/", {"name": "New Name"}, format="json"
        )
        assert resp.status_code == 200
        cat.refresh_from_db()
        assert cat.name == "New Name"

    def test_patch_requires_admin_token(self, api_client):
        cat = ServiceCategoryFactory()
        resp = api_client.patch(
            f"/api/v1/categories/{cat.pk}/", {"name": "X"}, format="json"
        )
        assert resp.status_code in (401, 403)

    # ── soft-delete ─────────────────────────────────────────────────────────

    def test_delete_returns_204(self, staff_client):
        cat = ServiceCategoryFactory(is_active=True)
        resp = staff_client.delete(f"/api/v1/categories/{cat.pk}/")
        assert resp.status_code == 204

    def test_delete_soft_deletes(self, staff_client):
        from apps.registry.models import ServiceCategory

        cat = ServiceCategoryFactory(is_active=True)
        staff_client.delete(f"/api/v1/categories/{cat.pk}/")
        cat.refresh_from_db()
        assert cat.is_active is False
        assert ServiceCategory.objects.filter(pk=cat.pk).exists()

    def test_delete_requires_admin_token(self, api_client):
        cat = ServiceCategoryFactory()
        resp = api_client.delete(f"/api/v1/categories/{cat.pk}/")
        assert resp.status_code in (401, 403)


# ===========================================================================
# Reference data CRUD — ServiceCenter
# ===========================================================================


@pytest.mark.django_db
class TestServiceCenterCRUD:
    def test_list_returns_all_including_inactive(self, staff_client):
        ServiceCenterFactory(short_name="Active", is_active=True)
        ServiceCenterFactory(short_name="Inactive", is_active=False)
        resp = staff_client.get("/api/v1/service-centers/")
        short_names = [c["short_name"] for c in resp.json()]
        assert "Active" in short_names
        assert "Inactive" in short_names

    def test_list_filter_active_only(self, staff_client):
        ServiceCenterFactory(short_name="Active", is_active=True)
        ServiceCenterFactory(short_name="Inactive", is_active=False)
        resp = staff_client.get("/api/v1/service-centers/?is_active=true")
        short_names = [c["short_name"] for c in resp.json()]
        assert "Active" in short_names
        assert "Inactive" not in short_names

    def test_create_returns_201(self, staff_client):
        resp = staff_client.post(
            "/api/v1/service-centers/",
            {
                "short_name": "NEW",
                "full_name": "New Service Centre",
                "website": "https://new.example.com",
            },
            format="json",
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["short_name"] == "NEW"
        assert data["is_active"] is True

    def test_create_requires_admin_token(self, api_client):
        resp = api_client.post(
            "/api/v1/service-centers/",
            {"short_name": "X", "full_name": "Y"},
            format="json",
        )
        assert resp.status_code in (401, 403)

    def test_create_missing_required_fields_returns_400(self, staff_client):
        resp = staff_client.post("/api/v1/service-centers/", {}, format="json")
        assert resp.status_code == 400

    def test_retrieve_returns_200(self, staff_client):
        center = ServiceCenterFactory()
        resp = staff_client.get(f"/api/v1/service-centers/{center.pk}/")
        assert resp.status_code == 200
        assert str(resp.json()["id"]) == str(center.pk)

    def test_patch_updates_full_name(self, staff_client):
        center = ServiceCenterFactory(full_name="Old Name")
        resp = staff_client.patch(
            f"/api/v1/service-centers/{center.pk}/",
            {"full_name": "Updated Name"},
            format="json",
        )
        assert resp.status_code == 200
        center.refresh_from_db()
        assert center.full_name == "Updated Name"

    def test_delete_soft_deletes(self, staff_client):
        from apps.registry.models import ServiceCenter

        center = ServiceCenterFactory(is_active=True)
        resp = staff_client.delete(f"/api/v1/service-centers/{center.pk}/")
        assert resp.status_code == 204
        center.refresh_from_db()
        assert center.is_active is False
        assert ServiceCenter.objects.filter(pk=center.pk).exists()

    def test_delete_requires_admin_token(self, api_client):
        center = ServiceCenterFactory()
        resp = api_client.delete(f"/api/v1/service-centers/{center.pk}/")
        assert resp.status_code in (401, 403)


# ===========================================================================
# Reference data CRUD — PrincipalInvestigator
# ===========================================================================


@pytest.mark.django_db
class TestPrincipalInvestigatorCRUD:
    def test_list_returns_all_including_inactive(self, staff_client):
        PIFactory(last_name="ActivePI", is_active=True)
        PIFactory(last_name="InactivePI", is_active=False)
        resp = staff_client.get("/api/v1/pis/")
        last_names = [p["last_name"] for p in resp.json()]
        assert "ActivePI" in last_names
        assert "InactivePI" in last_names

    def test_list_filter_active_only(self, staff_client):
        PIFactory(last_name="ActivePI", is_active=True)
        PIFactory(last_name="InactivePI", is_active=False)
        resp = staff_client.get("/api/v1/pis/?is_active=true")
        last_names = [p["last_name"] for p in resp.json()]
        assert "ActivePI" in last_names
        assert "InactivePI" not in last_names

    def test_create_returns_201(self, staff_client):
        resp = staff_client.post(
            "/api/v1/pis/",
            {
                "last_name": "Smith",
                "first_name": "Alice",
                "email": "alice.smith@example.com",
                "institute": "Example University",
            },
            format="json",
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["last_name"] == "Smith"
        assert data["first_name"] == "Alice"
        assert data["email"] == "alice.smith@example.com"
        assert data["is_active"] is True

    def test_create_requires_admin_token(self, api_client):
        resp = api_client.post(
            "/api/v1/pis/",
            {"last_name": "Smith", "first_name": "Alice"},
            format="json",
        )
        assert resp.status_code in (401, 403)

    def test_create_missing_required_fields_returns_400(self, staff_client):
        resp = staff_client.post("/api/v1/pis/", {}, format="json")
        assert resp.status_code == 400

    def test_create_invalid_orcid_returns_400(self, staff_client):
        resp = staff_client.post(
            "/api/v1/pis/",
            {
                "last_name": "Smith",
                "first_name": "Alice",
                "orcid": "not-a-valid-orcid",
            },
            format="json",
        )
        assert resp.status_code == 400

    def test_retrieve_returns_200(self, staff_client):
        pi = PIFactory()
        resp = staff_client.get(f"/api/v1/pis/{pi.pk}/")
        assert resp.status_code == 200
        assert str(resp.json()["id"]) == str(pi.pk)

    def test_retrieve_includes_email(self, staff_client):
        pi = PIFactory(email="private@example.com")
        resp = staff_client.get(f"/api/v1/pis/{pi.pk}/")
        assert resp.json()["email"] == "private@example.com"

    def test_pi_email_not_in_submission_response(self, api_client):
        """PI email must not leak into submission responses."""
        from tests.factories import APIKeyFactory

        pi = PIFactory(email="private@example.com")
        sub = ServiceSubmissionFactory(biotools_url="")
        sub.responsible_pis.set([pi])
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.get(f"/api/v1/submissions/{sub.pk}/")
        assert "private@example.com" not in resp.content.decode()

    def test_patch_updates_institute(self, staff_client):
        pi = PIFactory(institute="Old Uni")
        resp = staff_client.patch(
            f"/api/v1/pis/{pi.pk}/",
            {"institute": "New Uni"},
            format="json",
        )
        assert resp.status_code == 200
        pi.refresh_from_db()
        assert pi.institute == "New Uni"

    def test_patch_deactivate(self, staff_client):
        pi = PIFactory(is_active=True)
        resp = staff_client.patch(
            f"/api/v1/pis/{pi.pk}/", {"is_active": False}, format="json"
        )
        assert resp.status_code == 200
        pi.refresh_from_db()
        assert pi.is_active is False

    def test_delete_soft_deletes(self, staff_client):
        from apps.registry.models import PrincipalInvestigator

        pi = PIFactory(is_active=True)
        resp = staff_client.delete(f"/api/v1/pis/{pi.pk}/")
        assert resp.status_code == 204
        pi.refresh_from_db()
        assert pi.is_active is False
        assert PrincipalInvestigator.objects.filter(pk=pi.pk).exists()

    def test_delete_requires_admin_token(self, api_client):
        pi = PIFactory()
        resp = api_client.delete(f"/api/v1/pis/{pi.pk}/")
        assert resp.status_code in (401, 403)

    def test_response_includes_display_name(self, staff_client):
        pi = PIFactory(first_name="Ada", last_name="Lovelace")
        resp = staff_client.get(f"/api/v1/pis/{pi.pk}/")
        assert "display_name" in resp.json()
        assert "Lovelace" in resp.json()["display_name"]


# ===========================================================================
# EDAM endpoint — public
# ===========================================================================


@pytest.mark.django_db
class TestEdamEndpoint:
    def test_edam_list_is_public(self, api_client):
        resp = api_client.get("/api/v1/edam/")
        assert resp.status_code == 200

    def test_edam_filter_by_branch(self, api_client):
        from apps.edam.models import EdamTerm

        # Only run if EDAM data is loaded; skip otherwise
        if not EdamTerm.objects.exists():
            pytest.skip("EDAM data not loaded")
        resp = api_client.get("/api/v1/edam/?branch=topic")
        for term in resp.json():
            assert term["branch"] == "topic"

    @pytest.mark.django_db
    def test_search_by_synonym_returns_matching_term(self, api_client):
        from apps.edam.models import EdamTerm

        EdamTerm.objects.create(
            uri="http://edamontology.org/topic_9903",
            accession="topic_9903",
            branch="topic",
            label="Unique API Topic ZZZ",
            definition="For API synonym search test.",
            synonyms=["api_unique_synonym_xyz"],
            sort_order=9903,
            edam_version="test",
        )
        resp = api_client.get("/api/v1/edam/?q=api_unique_synonym_xyz")
        assert resp.status_code == 200
        data = resp.json()
        accessions = [t["accession"] for t in data]
        assert "topic_9903" in accessions

    @pytest.mark.django_db
    def test_search_by_synonym_does_not_return_non_matching_terms(self, api_client):
        from apps.edam.models import EdamTerm

        EdamTerm.objects.create(
            uri="http://edamontology.org/topic_9904",
            accession="topic_9904",
            branch="topic",
            label="Non-matching Topic",
            definition="Should not appear in synonym search.",
            synonyms=["other_synonym"],
            sort_order=9904,
            edam_version="test",
        )
        resp = api_client.get("/api/v1/edam/?q=api_unique_synonym_xyz_nomatch")
        assert resp.status_code == 200
        data = resp.json()
        accessions = [t["accession"] for t in data]
        assert "topic_9904" not in accessions


# ===========================================================================
# OpenAPI / docs endpoints
# ===========================================================================


@pytest.mark.django_db
class TestOpenAPIEndpoints:
    """Schema and docs are publicly readable — they document the public API surface."""

    def test_schema_returns_200(self, api_client):
        resp = api_client.get("/api/schema/")
        assert resp.status_code == 200

    def test_swagger_ui_returns_200(self, api_client):
        resp = api_client.get("/api/docs/")
        assert resp.status_code == 200

    def test_redoc_returns_200(self, api_client):
        resp = api_client.get("/api/redoc/")
        assert resp.status_code == 200

    def test_schema_mentions_apikey_auth(self, api_client):
        resp = api_client.get("/api/schema/")
        assert b"ApiKey" in resp.content or b"apiKey" in resp.content

    def test_schema_excludes_server_only_fields(self, api_client):
        """Server-generated fields must never appear in the public schema."""
        resp = api_client.get("/api/schema/")
        content = resp.content
        assert b"submission_ip" not in content
        assert b"user_agent_hash" not in content

    def test_schema_includes_internal_contact_fields_as_write_only(self, api_client):
        """internal_contact_name/email appear in schema as writeOnly input fields."""
        resp = api_client.get("/api/schema/")
        content = resp.content
        assert b"internal_contact_email" in content
        assert b"internal_contact_name" in content


# ===========================================================================
# Error envelope consistency
# ===========================================================================


@pytest.mark.django_db
class TestErrorEnvelope:
    def test_auth_error_has_envelope(self, api_client):
        resp = api_client.get("/api/v1/submissions/")
        data = resp.json()
        assert "error" in data
        assert "request_id" in data

    def test_not_found_has_envelope(self, api_client, admin_user):
        _, plaintext = admin_user
        api_client.credentials(HTTP_AUTHORIZATION=f"AdminKey {plaintext}")
        resp = api_client.get(
            "/api/v1/submissions/00000000-0000-0000-0000-000000000000/"
        )
        data = resp.json()
        assert "error" in data
        assert "request_id" in data


# ---------------------------------------------------------------------------
# BioToolsRecord viewset — access control tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBioToolsRecordAccessControl:
    """Ensure bio.tools retrieve endpoint only exposes approved submissions."""

    def test_retrieve_approved_submission_public(self, api_client):
        """Unauthenticated users can retrieve records for approved submissions."""
        record = BioToolsRecordFactory(submission__status="approved")
        resp = api_client.get(f"/api/v1/biotools/{record.biotools_id}/")
        assert resp.status_code == 200
        assert resp.json()["biotools_id"] == record.biotools_id

    def test_retrieve_submitted_denied(self, api_client):
        """Records for non-approved submissions must not be publicly accessible."""
        record = BioToolsRecordFactory(submission__status="submitted")
        resp = api_client.get(f"/api/v1/biotools/{record.biotools_id}/")
        assert resp.status_code == 404

    def test_retrieve_under_review_denied(self, api_client):
        record = BioToolsRecordFactory(submission__status="under_review")
        resp = api_client.get(f"/api/v1/biotools/{record.biotools_id}/")
        assert resp.status_code == 404

    def test_retrieve_rejected_denied(self, api_client):
        record = BioToolsRecordFactory(submission__status="rejected")
        resp = api_client.get(f"/api/v1/biotools/{record.biotools_id}/")
        assert resp.status_code == 404

    def test_retrieve_draft_denied(self, api_client):
        record = BioToolsRecordFactory(submission__status="draft")
        resp = api_client.get(f"/api/v1/biotools/{record.biotools_id}/")
        assert resp.status_code == 404

    def test_no_pk_bypass_for_non_approved_record(self, api_client):
        """
        The old super().get_object() fallback allowed unauthenticated access to
        non-approved bio.tools records by UUID PK. This must return 404 now.
        """
        record = BioToolsRecordFactory(
            submission__status="submitted", biotools_id="secret-tool"
        )
        resp = api_client.get(f"/api/v1/biotools/{record.pk}/")
        assert resp.status_code == 404

    def test_list_requires_admin_token(self, api_client):
        """List endpoint requires admin authentication."""
        BioToolsRecordFactory(submission__status="approved")
        resp = api_client.get("/api/v1/biotools/")
        assert resp.status_code in (401, 403)

    def test_list_apikey_auth_denied(self, api_client):
        """Submission owner ApiKey must not grant access to the biotools list."""
        sub = ServiceSubmissionFactory()
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        BioToolsRecordFactory(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.get("/api/v1/biotools/")
        assert resp.status_code == 403

    def test_list_with_admin_token(self, staff_client):
        """Admin can list all records regardless of submission status."""
        BioToolsRecordFactory(submission__status="approved")
        BioToolsRecordFactory(submission__status="submitted")
        resp = staff_client.get("/api/v1/biotools/")
        assert resp.status_code == 200
        assert len(resp.json()["results"]) == 2

    def test_retrieve_response_shape(self, api_client):
        """Response includes expected fields including nested functions."""
        record = BioToolsRecordFactory(
            submission__status="approved",
            biotools_id="shapetool",
            edam_topic_uris=["http://edamontology.org/topic_0091"],
        )
        BioToolsFunctionFactory(
            record=record,
            position=0,
            operations=[
                {"uri": "http://edamontology.org/operation_0004", "term": "Operation"}
            ],
        )
        resp = api_client.get(f"/api/v1/biotools/{record.biotools_id}/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["biotools_id"] == "shapetool"
        assert "functions" in data
        assert len(data["functions"]) == 1
        assert (
            data["functions"][0]["operations"][0]["uri"]
            == "http://edamontology.org/operation_0004"
        )
        assert "edam_topic_uris" in data
        assert "edam_topics_resolved" in data
        assert "last_synced_at" in data


# ---------------------------------------------------------------------------
# Logo upload tests
# ---------------------------------------------------------------------------


def _make_png_bytes():
    """Minimal 1×1 white PNG for use as a test logo."""
    from PIL import Image
    import io

    buf = io.BytesIO()
    Image.new("RGB", (1, 1), color=(255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.django_db
class TestLogoUpload:
    def test_logo_url_is_null_when_no_logo(self, api_client):
        sub = ServiceSubmissionFactory(biotools_url="")
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.get(f"/api/v1/submissions/{sub.id}/")
        assert resp.status_code == 200
        assert resp.json()["logo_url"] is None

    def test_logo_url_is_absolute_when_logo_set(self, api_client, tmp_path, settings):
        settings.MEDIA_ROOT = tmp_path
        sub = ServiceSubmissionFactory(biotools_url="")
        # Manually set a fake logo path
        from django.core.files.base import ContentFile

        sub.logo.save("logos/test.png", ContentFile(_make_png_bytes()), save=True)
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.get(f"/api/v1/submissions/{sub.id}/")
        assert resp.status_code == 200
        logo_url = resp.json()["logo_url"]
        assert logo_url is not None
        assert logo_url.startswith("http")
        assert "/media/" in logo_url

    def test_upload_valid_png_via_api(self, api_client, tmp_path, settings):
        settings.MEDIA_ROOT = tmp_path
        from django.core.files.uploadedfile import SimpleUploadedFile

        sub = ServiceSubmissionFactory(biotools_url="")
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        logo = SimpleUploadedFile(
            "logo.png", _make_png_bytes(), content_type="image/png"
        )
        resp = api_client.patch(
            f"/api/v1/submissions/{sub.id}/",
            {"logo": logo},
            format="multipart",
        )
        assert resp.status_code == 200
        assert resp.json()["logo_url"] is not None

    def test_upload_invalid_file_returns_400(self, api_client):
        from django.core.files.uploadedfile import SimpleUploadedFile

        sub = ServiceSubmissionFactory(biotools_url="")
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        bad_file = SimpleUploadedFile(
            "logo.png", b"not an image", content_type="image/png"
        )
        resp = api_client.patch(
            f"/api/v1/submissions/{sub.id}/",
            {"logo": bad_file},
            format="multipart",
        )
        assert resp.status_code == 400

    def test_upload_oversized_file_returns_400(self, api_client, settings):
        from django.core.files.uploadedfile import SimpleUploadedFile

        settings.LOGO_MAX_BYTES = 5  # Tiny limit for this test
        sub = ServiceSubmissionFactory(biotools_url="")
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        big_file = SimpleUploadedFile(
            "logo.png", _make_png_bytes(), content_type="image/png"
        )
        resp = api_client.patch(
            f"/api/v1/submissions/{sub.id}/",
            {"logo": big_file},
            format="multipart",
        )
        assert resp.status_code == 400

    def test_logo_field_not_in_read_response(self, api_client):
        """The write-only 'logo' field must not appear in API responses."""
        sub = ServiceSubmissionFactory(biotools_url="")
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.get(f"/api/v1/submissions/{sub.id}/")
        # 'logo' is write_only; 'logo_url' is the read field
        assert "logo" not in resp.json() or resp.json().get("logo") is None
        assert "logo_url" in resp.json()


# ---------------------------------------------------------------------------
# Serializer validation — mirrors model.clean() for API path
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSerializerValidation:
    """Validate that API enforces the same field rules as model.clean()."""

    def test_year_established_too_early_returns_400(self, api_client):
        payload = _valid_payload()
        payload["year_established"] = 1800
        resp = api_client.post("/api/v1/submissions/", payload, format="json")
        assert resp.status_code == 400
        assert "year_established" in str(resp.json())

    def test_year_established_future_returns_400(self, api_client):
        from django.utils import timezone as tz

        payload = _valid_payload()
        payload["year_established"] = tz.now().year + 1
        resp = api_client.post("/api/v1/submissions/", payload, format="json")
        assert resp.status_code == 400
        assert "year_established" in str(resp.json())

    def test_year_established_current_year_accepted(self, api_client):
        from django.utils import timezone as tz

        payload = _valid_payload()
        payload["year_established"] = tz.now().year
        resp = api_client.post("/api/v1/submissions/", payload, format="json")
        assert resp.status_code == 201

    def test_service_description_too_short_returns_400(self, api_client):
        payload = _valid_payload()
        payload["service_description"] = "Too short"
        resp = api_client.post("/api/v1/submissions/", payload, format="json")
        assert resp.status_code == 400
        assert "service_description" in str(resp.json())

    def test_service_description_too_long_returns_400(self, api_client):
        payload = _valid_payload()
        payload["service_description"] = "x" * 5001
        resp = api_client.post("/api/v1/submissions/", payload, format="json")
        assert resp.status_code == 400
        assert "service_description" in str(resp.json())

    def test_service_description_exactly_5000_chars_accepted(self, api_client):
        payload = _valid_payload()
        payload["service_description"] = "x" * 5000
        resp = api_client.post("/api/v1/submissions/", payload, format="json")
        assert resp.status_code == 201

    def test_kpi_start_year_required_when_monitoring_is_yes(self, api_client):
        payload = _valid_payload()
        payload["kpi_monitoring"] = "yes"
        payload["kpi_start_year"] = ""
        resp = api_client.post("/api/v1/submissions/", payload, format="json")
        assert resp.status_code == 400
        assert "kpi_start_year" in str(resp.json())

    def test_kpi_start_year_not_required_when_monitoring_is_planned(self, api_client):
        payload = _valid_payload()
        payload["kpi_monitoring"] = "planned"
        payload["kpi_start_year"] = ""
        resp = api_client.post("/api/v1/submissions/", payload, format="json")
        assert resp.status_code == 201

    def test_patch_year_established_out_of_range_returns_400(self, api_client):
        """PATCH must also validate year_established."""
        from tests.factories import APIKeyFactory, ServiceSubmissionFactory

        sub = ServiceSubmissionFactory()
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.patch(
            f"/api/v1/submissions/{sub.id}/",
            {"year_established": 1800},
            format="json",
        )
        assert resp.status_code == 400

    def test_patch_service_description_too_short_returns_400(self, api_client):
        """PATCH must also validate service_description length."""
        from tests.factories import APIKeyFactory, ServiceSubmissionFactory

        sub = ServiceSubmissionFactory()
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.patch(
            f"/api/v1/submissions/{sub.id}/",
            {"service_description": "Too short"},
            format="json",
        )
        assert resp.status_code == 400

    def test_patch_kpi_start_year_required_when_monitoring_active(self, api_client):
        """PATCH: setting kpi_monitoring=yes without kpi_start_year must fail."""
        from tests.factories import APIKeyFactory, ServiceSubmissionFactory

        sub = ServiceSubmissionFactory(kpi_monitoring="planned", kpi_start_year="")
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.patch(
            f"/api/v1/submissions/{sub.id}/",
            {"kpi_monitoring": "yes"},
            format="json",
        )
        assert resp.status_code == 400
        assert "kpi_start_year" in str(resp.json())


# ---------------------------------------------------------------------------
# IP extraction and user_agent_hash — consistent across web form and API
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestIPExtraction:
    """API must record the correct IP using the same header priority as the web form."""

    def test_create_stores_x_real_ip_over_x_forwarded_for(self, api_client):
        """X-Real-IP takes priority over X-Forwarded-For (matches nginx setup)."""
        resp = api_client.post(
            "/api/v1/submissions/",
            _valid_payload(),
            format="json",
            HTTP_X_REAL_IP="1.2.3.4",
            HTTP_X_FORWARDED_FOR="9.9.9.9",
        )
        assert resp.status_code == 201
        from apps.submissions.models import ServiceSubmission

        sub = ServiceSubmission.objects.get(id=resp.json()["id"])
        assert str(sub.submission_ip) == "1.2.3.4"

    def test_create_falls_back_to_x_forwarded_for(self, api_client):
        """Without X-Real-IP, leftmost X-Forwarded-For entry is used."""
        resp = api_client.post(
            "/api/v1/submissions/",
            _valid_payload(),
            format="json",
            HTTP_X_FORWARDED_FOR="5.6.7.8, 10.0.0.1",
        )
        assert resp.status_code == 201
        from apps.submissions.models import ServiceSubmission

        sub = ServiceSubmission.objects.get(id=resp.json()["id"])
        assert str(sub.submission_ip) == "5.6.7.8"

    def test_create_stores_user_agent_hash(self, api_client):
        """API create must store a non-empty user_agent_hash."""
        import hashlib

        ua = "TestClient/1.0"
        resp = api_client.post(
            "/api/v1/submissions/",
            _valid_payload(),
            format="json",
            HTTP_USER_AGENT=ua,
        )
        assert resp.status_code == 201
        from apps.submissions.models import ServiceSubmission

        sub = ServiceSubmission.objects.get(id=resp.json()["id"])
        expected = hashlib.sha256(ua.encode()).hexdigest()
        assert sub.user_agent_hash == expected

    def test_create_without_user_agent_stores_empty_hash(self, api_client):
        """Missing User-Agent should still store a deterministic hash (of empty string)."""
        import hashlib

        resp = api_client.post("/api/v1/submissions/", _valid_payload(), format="json")
        assert resp.status_code == 201
        from apps.submissions.models import ServiceSubmission

        sub = ServiceSubmission.objects.get(id=resp.json()["id"])
        expected = hashlib.sha256(b"").hexdigest()
        assert sub.user_agent_hash == expected


# ===========================================================================
# AdminAPIKey — scoped machine-to-machine access
# ===========================================================================


def _admin_key_client(api_client, scope):
    """Return an APIClient pre-authenticated with a fresh AdminAPIKey of the given scope."""
    import hashlib
    import secrets

    from apps.api.models import AdminAPIKey

    plaintext = secrets.token_urlsafe(48)
    AdminAPIKey.objects.create(
        key_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
        label=f"test-{scope}",
        scope=scope,
        is_active=True,
    )
    api_client.credentials(HTTP_AUTHORIZATION=f"AdminKey {plaintext}")
    return api_client


@pytest.mark.django_db
class TestAdminAPIKeyAuthentication:
    """Verify that AdminAPIKey credentials are accepted and rejected correctly."""

    def test_invalid_key_returns_401(self, api_client):
        api_client.credentials(HTTP_AUTHORIZATION="AdminKey notavalidkey")
        resp = api_client.get("/api/v1/submissions/")
        assert resp.status_code == 401

    def test_revoked_key_returns_401(self, api_client, db):
        import hashlib
        import secrets

        from apps.api.models import AdminAPIKey

        plaintext = secrets.token_urlsafe(48)
        AdminAPIKey.objects.create(
            key_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
            label="revoked",
            scope=AdminAPIKey.SCOPE_FULL,
            is_active=False,
        )
        api_client.credentials(HTTP_AUTHORIZATION=f"AdminKey {plaintext}")
        resp = api_client.get("/api/v1/submissions/")
        assert resp.status_code == 401

    def test_unrelated_auth_scheme_is_ignored(self, api_client):
        """An unknown auth scheme is safely ignored without crashing."""
        api_client.credentials(HTTP_AUTHORIZATION="Unknown notatoken")
        resp = api_client.get("/api/v1/submissions/")
        # Returns 401 (no auth) not 500 — auth dispatch doesn't crash
        assert resp.status_code in (401, 403)


@pytest.mark.django_db
class TestAdminAPIKeyReadScope:
    """A read-scope AdminAPIKey can GET but not mutate."""

    def test_read_key_lists_submissions(self, api_client):
        ServiceSubmissionFactory()
        client = _admin_key_client(api_client, "read")
        resp = client.get("/api/v1/submissions/")
        assert resp.status_code == 200

    def test_read_key_retrieves_submission(self, api_client):
        sub = ServiceSubmissionFactory()
        client = _admin_key_client(api_client, "read")
        resp = client.get(f"/api/v1/submissions/{sub.pk}/")
        assert resp.status_code == 200

    def test_read_key_blocks_patch_submission(self, api_client):
        sub = ServiceSubmissionFactory()
        client = _admin_key_client(api_client, "read")
        resp = client.patch(
            f"/api/v1/submissions/{sub.pk}/",
            {"service_name": "hacked"},
            format="json",
        )
        assert resp.status_code == 403

    def test_read_key_lists_categories(self, api_client):
        ServiceCategoryFactory()
        client = _admin_key_client(api_client, "read")
        resp = client.get("/api/v1/categories/")
        assert resp.status_code == 200

    def test_read_key_blocks_post_category(self, api_client):
        client = _admin_key_client(api_client, "read")
        resp = client.post("/api/v1/categories/", {"name": "New Cat"}, format="json")
        assert resp.status_code == 403

    def test_read_key_lists_service_centers(self, api_client):
        ServiceCenterFactory()
        client = _admin_key_client(api_client, "read")
        resp = client.get("/api/v1/service-centers/")
        assert resp.status_code == 200

    def test_read_key_blocks_post_service_center(self, api_client):
        client = _admin_key_client(api_client, "read")
        resp = client.post(
            "/api/v1/service-centers/",
            {"short_name": "X", "full_name": "Xtra"},
            format="json",
        )
        assert resp.status_code == 403

    def test_read_key_lists_pis(self, api_client):
        PIFactory()
        client = _admin_key_client(api_client, "read")
        resp = client.get("/api/v1/pis/")
        assert resp.status_code == 200

    def test_read_key_does_not_expose_sensitive_fields(self, api_client):
        """Serialiser exclusions apply regardless of auth method."""
        ServiceSubmissionFactory()
        client = _admin_key_client(api_client, "read")
        resp = client.get("/api/v1/submissions/")
        assert resp.status_code == 200
        data = resp.json()["results"][0]
        for field in ("internal_contact_email", "submission_ip", "user_agent_hash"):
            assert field not in data


@pytest.mark.django_db
class TestAdminAPIKeyFullScope:
    """A full-scope AdminAPIKey can perform all operations."""

    def test_full_key_lists_submissions(self, api_client):
        ServiceSubmissionFactory()
        client = _admin_key_client(api_client, "full")
        resp = client.get("/api/v1/submissions/")
        assert resp.status_code == 200

    def test_full_key_can_patch_submission(self, api_client):
        sub = ServiceSubmissionFactory()
        client = _admin_key_client(api_client, "full")
        resp = client.patch(
            f"/api/v1/submissions/{sub.pk}/",
            {"service_name": sub.service_name},  # no-op patch, just verify 200
            format="json",
        )
        assert resp.status_code == 200

    def test_full_key_can_post_category(self, api_client):
        client = _admin_key_client(api_client, "full")
        resp = client.post("/api/v1/categories/", {"name": "New Cat"}, format="json")
        assert resp.status_code == 201


@pytest.mark.django_db
class TestAdminAPIKeyThrottling:
    """
    Test that AdminAPIKey works with DRF's throttling system.

    Verifies that the is_authenticated property is properly implemented
    so that throttling middleware can check request.user.is_authenticated
    without AttributeError.
    """

    def test_read_key_with_throttling_enabled(self, api_client, settings):
        """
        A read-scope AdminAPIKey can GET endpoints even with throttling enabled.
        This verifies the is_authenticated property is properly implemented
        so that throttling middleware can check request.user.is_authenticated
        without AttributeError.
        """
        # Enable throttling (normally disabled in test settings)
        settings.REST_FRAMEWORK["DEFAULT_THROTTLE_CLASSES"] = [
            "rest_framework.throttling.AnonRateThrottle",
            "rest_framework.throttling.UserRateThrottle",
        ]
        settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] = {
            "anon": "100/day",
            "user": "1000/day",
        }

        ServiceCenterFactory()
        client = _admin_key_client(api_client, "read")

        # This should NOT raise AttributeError: 'AdminAPIKey' object has no attribute 'is_authenticated'
        resp = client.get("/api/v1/service-centers/")
        assert resp.status_code == 200

    def test_admin_key_is_authenticated_property(self, db):
        """Verify AdminAPIKey.is_authenticated property returns correct value."""
        import hashlib
        import secrets

        from apps.api.models import AdminAPIKey

        plaintext = secrets.token_urlsafe(48)
        key = AdminAPIKey.objects.create(
            key_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
            label="test-active",
            scope=AdminAPIKey.SCOPE_READ,
            is_active=True,
        )

        # Active key should have is_authenticated=True
        assert key.is_authenticated is True

        # Revoke the key
        key.is_active = False
        key.save()
        key.refresh_from_db()

        # Revoked key should have is_authenticated=False
        assert key.is_authenticated is False


# ---------------------------------------------------------------------------
# API Maturity Tags Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSubmissionMaturityTagsAPI:
    """Test API serialization and filtering for maturity tags."""

    def test_submission_response_includes_maturity_tags(self):
        """API response includes primary_maturity_tag and secondary_maturity_tags."""
        from apps.submissions.models import SubmissionAPIKey

        sub = ServiceSubmissionFactory(status="approved", primary_maturity_tag="mature")
        key_obj, plaintext = SubmissionAPIKey.create_for_submission(
            submission=sub, label="test", created_by="test"
        )
        client = APIClient()
        response = client.get(
            f"/api/v1/submissions/{sub.id}/",
            HTTP_AUTHORIZATION=f"ApiKey {plaintext}",
        )
        assert response.status_code == 200
        assert "primary_maturity_tag" in response.data
        assert "secondary_maturity_tags" in response.data
        assert response.data["primary_maturity_tag"] == "mature"

    def test_api_patch_maturity_tags_silently_ignored(self):
        """Maturity tags in PATCH body are silently ignored — they are admin-only."""
        from apps.submissions.models import SubmissionAPIKey

        sub = ServiceSubmissionFactory(status="approved", primary_maturity_tag=None)
        key_obj, plaintext = SubmissionAPIKey.create_for_submission(
            submission=sub, label="test", created_by="test"
        )
        client = APIClient()
        response = client.patch(
            f"/api/v1/submissions/{sub.id}/",
            {"primary_maturity_tag": "emerging"},
            HTTP_AUTHORIZATION=f"ApiKey {plaintext}",
            content_type="application/json",
        )
        assert response.status_code == 200
        sub.refresh_from_db()
        # Tag must NOT have been written — it was silently discarded
        assert sub.primary_maturity_tag is None or sub.primary_maturity_tag == ""

    def test_api_patch_maturity_tags_ignored_on_any_status(self):
        """Maturity tag values in PATCH are ignored regardless of submission status."""
        from apps.submissions.models import SubmissionAPIKey

        sub = ServiceSubmissionFactory(status="draft", primary_maturity_tag=None)
        key_obj, plaintext = SubmissionAPIKey.create_for_submission(
            submission=sub, label="test", created_by="test"
        )
        client = APIClient()
        response = client.patch(
            f"/api/v1/submissions/{sub.id}/",
            {"primary_maturity_tag": "mature"},
            HTTP_AUTHORIZATION=f"ApiKey {plaintext}",
            content_type="application/json",
        )
        assert response.status_code == 200
        sub.refresh_from_db()
        assert sub.primary_maturity_tag is None or sub.primary_maturity_tag == ""

    def test_api_patch_invalid_tag_value_silently_ignored(self):
        """Invalid tag values in PATCH are silently ignored (field is read-only)."""
        from apps.submissions.models import SubmissionAPIKey

        sub = ServiceSubmissionFactory(status="approved", primary_maturity_tag=None)
        key_obj, plaintext = SubmissionAPIKey.create_for_submission(
            submission=sub, label="test", created_by="test"
        )
        client = APIClient()
        response = client.patch(
            f"/api/v1/submissions/{sub.id}/",
            {"primary_maturity_tag": "invalid_tag"},
            HTTP_AUTHORIZATION=f"ApiKey {plaintext}",
            content_type="application/json",
        )
        # Read-only field → no validation → 200, value discarded
        assert response.status_code == 200
        sub.refresh_from_db()
        assert sub.primary_maturity_tag is None or sub.primary_maturity_tag == ""

    def test_api_patch_secondary_tags_silently_ignored(self):
        """Secondary maturity tags in PATCH body are silently ignored."""
        from apps.submissions.models import SubmissionAPIKey

        sub = ServiceSubmissionFactory(status="approved", secondary_maturity_tags=[])
        key_obj, plaintext = SubmissionAPIKey.create_for_submission(
            submission=sub, label="test", created_by="test"
        )
        client = APIClient()
        response = client.patch(
            f"/api/v1/submissions/{sub.id}/",
            {"secondary_maturity_tags": ["unstable"]},
            HTTP_AUTHORIZATION=f"ApiKey {plaintext}",
            content_type="application/json",
        )
        assert response.status_code == 200
        sub.refresh_from_db()
        assert sub.secondary_maturity_tags == []

    def test_api_get_after_status_change_clears_tags(self):
        """GET a submission whose secondary_maturity_tags were cleared to None in the DB
        (simulating legacy data or a pre-fix _change_status write) must return 200,
        not a 500 from the JSONField trying to serialize None."""
        from apps.submissions.models import ServiceSubmission, SubmissionAPIKey

        sub = ServiceSubmissionFactory(
            status="approved",
            primary_maturity_tag="mature",
            secondary_maturity_tags=["unstable"],
        )
        key_obj, plaintext = SubmissionAPIKey.create_for_submission(
            submission=sub, label="test", created_by="test"
        )
        # Write NULL directly — matches what _change_status wrote before the fix,
        # and what could exist in production DBs migrated before the patch.
        ServiceSubmission.objects.filter(pk=sub.pk).update(
            status="rejected",
            primary_maturity_tag=None,
            secondary_maturity_tags=None,
        )
        client = APIClient()
        response = client.get(
            f"/api/v1/submissions/{sub.id}/",
            HTTP_AUTHORIZATION=f"ApiKey {plaintext}",
        )
        assert response.status_code == 200
        assert response.data["primary_maturity_tag"] is None
        # JSONField returns None (null) when the DB column holds NULL
        assert response.data["secondary_maturity_tags"] is None

    def test_create_with_maturity_tag_silently_ignored(self):
        """POST with primary_maturity_tag returns 201 — the tag is silently discarded.
        Maturity tags are admin-only; submitters cannot set them via the API."""
        from apps.submissions.models import ServiceSubmission

        client = APIClient()
        payload = _valid_payload()
        payload["primary_maturity_tag"] = "mature"
        response = client.post("/api/v1/submissions/", payload, format="json")
        assert response.status_code == 201
        sub = ServiceSubmission.objects.get(pk=response.data["id"])
        assert sub.primary_maturity_tag is None or sub.primary_maturity_tag == ""


# ---------------------------------------------------------------------------
# API Maturity Tag Filter Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMaturityTagFiltering:
    """Test API list filtering by primary and secondary maturity tags."""

    def test_list_filter_by_primary_tag(self, staff_client):
        """?primary_maturity_tag=mature returns only mature-tagged submissions."""
        mature = ServiceSubmissionFactory(
            status="approved", primary_maturity_tag="mature"
        )
        emerging = ServiceSubmissionFactory(
            status="approved", primary_maturity_tag="emerging"
        )
        untagged = ServiceSubmissionFactory(
            status="approved", primary_maturity_tag=None
        )

        response = staff_client.get("/api/v1/submissions/?primary_maturity_tag=mature")
        assert response.status_code == 200

        ids = [str(item["id"]) for item in response.data["results"]]
        assert str(mature.id) in ids
        assert str(emerging.id) not in ids
        assert str(untagged.id) not in ids

    def test_list_filter_by_secondary_tag(self, staff_client):
        """?secondary_maturity_tags=unstable returns submissions that include that tag."""
        unstable = ServiceSubmissionFactory(
            status="approved", secondary_maturity_tags=["unstable"]
        )
        clean = ServiceSubmissionFactory(status="approved", secondary_maturity_tags=[])

        response = staff_client.get(
            "/api/v1/submissions/?secondary_maturity_tags=unstable"
        )
        assert response.status_code == 200

        ids = [str(item["id"]) for item in response.data["results"]]
        assert str(unstable.id) in ids
        assert str(clean.id) not in ids

    def test_list_filter_by_multiple_secondary_tags(self, staff_client):
        """Comma-separated secondary tags use AND semantics — all listed tags must be present."""
        unstable = ServiceSubmissionFactory(
            status="approved", secondary_maturity_tags=["unstable"]
        )
        neither = ServiceSubmissionFactory(
            status="approved", secondary_maturity_tags=[]
        )

        # Single tag: submission with "unstable" is returned; one with no tags is not.
        r1 = staff_client.get("/api/v1/submissions/?secondary_maturity_tags=unstable")
        assert r1.status_code == 200
        ids1 = [str(item["id"]) for item in r1.data["results"]]
        assert str(unstable.id) in ids1
        assert str(neither.id) not in ids1

        # Two comma-separated tags (AND): the submission only has "unstable", not a
        # second tag, so it must NOT appear — this proves AND rather than OR semantics.
        r2 = staff_client.get(
            "/api/v1/submissions/?secondary_maturity_tags=unstable,secondtag"
        )
        assert r2.status_code == 200
        ids2 = [str(item["id"]) for item in r2.data["results"]]
        assert str(unstable.id) not in ids2

    def test_list_unfiltered_returns_all(self, staff_client):
        """List with no maturity params returns all submissions regardless of tag."""
        tagged = ServiceSubmissionFactory(
            status="approved", primary_maturity_tag="legacy"
        )
        untagged = ServiceSubmissionFactory(
            status="approved", primary_maturity_tag=None
        )

        response = staff_client.get("/api/v1/submissions/")
        assert response.status_code == 200

        ids = [str(item["id"]) for item in response.data["results"]]
        assert str(tagged.id) in ids
        assert str(untagged.id) in ids


# ===========================================================================
# License field — API validation (YAML-driven)
# ===========================================================================


@pytest.mark.django_db
class TestApiLicenseValidation:
    def test_create_rejects_empty_licenses_and_empty_license_note(self, api_client):
        """POST with neither licenses nor license_note must return 400."""
        from apps.licenses.models import SpdxLicense

        # Create a license so we can test the validation without side effects
        SpdxLicense.objects.create(license_id="MIT", name="MIT License")
        payload = _valid_payload()
        payload["licenses"] = []
        payload["license_note"] = ""
        resp = api_client.post("/api/v1/submissions/", payload, format="json")
        assert resp.status_code == 400
        assert "licenses" in resp.json().get("error", resp.json())

    def test_create_accepts_license_note_only(self, api_client):
        """POST with license_note but no licenses must return 201."""
        payload = _valid_payload()
        payload["licenses"] = []
        payload["license_note"] = "Custom license"
        resp = api_client.post("/api/v1/submissions/", payload, format="json")
        assert resp.status_code == 201

    def test_create_accepts_new_spdx_slug(self, api_client):
        """POST with a new SPDX slug must return 201."""
        from apps.licenses.models import SpdxLicense

        # Create the license first - SlugRelatedField requires it to exist
        SpdxLicense.objects.create(license_id="MIT", name="MIT License")
        payload = _valid_payload()
        payload["licenses"] = ["MIT"]
        resp = api_client.post("/api/v1/submissions/", payload, format="json")
        assert resp.status_code == 201


# ===========================================================================
# PATCH — kpi_start_year partial-update validation edge cases
# ===========================================================================


@pytest.mark.django_db
class TestKpiStartYearValidation:
    def test_patch_kpi_monitoring_yes_with_empty_start_year_fails(self, api_client):
        """
        PATCH that sends kpi_monitoring=yes with kpi_start_year="" must fail
        even when the existing instance already has a non-empty kpi_start_year.
        Previously the validator read the instance value as a fallback and
        silently passed.
        """
        from apps.submissions.models import ServiceSubmission

        sub = ServiceSubmissionFactory(kpi_monitoring="planned", kpi_start_year="")
        # Set a non-empty year directly so the instance has data
        ServiceSubmission.objects.filter(pk=sub.pk).update(
            kpi_monitoring="yes", kpi_start_year="2020"
        )
        sub.refresh_from_db()

        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.patch(
            f"/api/v1/submissions/{sub.pk}/",
            {"kpi_monitoring": "yes", "kpi_start_year": ""},
            format="json",
        )
        assert resp.status_code == 400
        assert "kpi_start_year" in resp.json().get("error", resp.json())

    def test_patch_kpi_monitoring_yes_with_valid_start_year_passes(self, api_client):
        """PATCH that sends kpi_monitoring=yes with a valid year must succeed."""
        sub = ServiceSubmissionFactory(kpi_monitoring="planned", kpi_start_year="")
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.patch(
            f"/api/v1/submissions/{sub.pk}/",
            {"kpi_monitoring": "yes", "kpi_start_year": "2022"},
            format="json",
        )
        assert resp.status_code == 200
        sub.refresh_from_db()
        assert sub.kpi_start_year == "2022"

    def test_patch_kpi_monitoring_planned_clears_start_year(self, api_client):
        """PATCH that switches to kpi_monitoring=planned should not require kpi_start_year."""
        from apps.submissions.models import ServiceSubmission

        sub = ServiceSubmissionFactory()
        ServiceSubmission.objects.filter(pk=sub.pk).update(
            kpi_monitoring="yes", kpi_start_year="2020"
        )
        sub.refresh_from_db()
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        api_client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = api_client.patch(
            f"/api/v1/submissions/{sub.pk}/",
            {"kpi_monitoring": "planned"},
            format="json",
        )
        assert resp.status_code == 200


# ===========================================================================
# GET /api/v1/biotools/{id}/ — access control
# ===========================================================================
# GET /api/v1/submissions/ — secondary_maturity_tags filter
# ===========================================================================


@pytest.mark.django_db
class TestSecondaryMaturityTagsFilter:
    def test_filter_returns_matching_submission(self, staff_client):
        """?secondary_maturity_tags=unstable must return submissions tagged unstable."""
        ServiceSubmissionFactory(
            status="approved",
            primary_maturity_tag="emerging",
            secondary_maturity_tags=["unstable"],
        )
        ServiceSubmissionFactory(
            status="approved",
            primary_maturity_tag="stable",
            secondary_maturity_tags=["stable"],
        )
        resp = staff_client.get("/api/v1/submissions/?secondary_maturity_tags=unstable")
        assert resp.status_code == 200
        results = resp.json()["results"]
        assert len(results) >= 1
        for item in results:
            assert "unstable" in (item.get("secondary_maturity_tags") or [])

    def test_filter_no_false_positive_for_partial_slug(self, staff_client):
        """
        'unstable' must not match 'stable' — the quote-wrapped icontains check
        must prevent the false positive where 'stable' contains 'unstable' as a
        substring without the surrounding quotes.
        """
        ServiceSubmissionFactory(
            status="approved",
            primary_maturity_tag="stable",
            secondary_maturity_tags=["stable"],
        )
        resp = staff_client.get("/api/v1/submissions/?secondary_maturity_tags=unstable")
        assert resp.status_code == 200
        results = resp.json()["results"]
        # None of the results should have only 'stable' as their secondary tags
        for item in results:
            tags = item.get("secondary_maturity_tags") or []
            assert "unstable" in tags, (
                f"Submission with tags {tags} incorrectly matched 'unstable' filter"
            )

    def test_filter_no_match_returns_empty(self, staff_client):
        """Filter with a tag that no submission has must return zero results."""
        ServiceSubmissionFactory(
            status="approved",
            secondary_maturity_tags=["stable"],
        )
        resp = staff_client.get("/api/v1/submissions/?secondary_maturity_tags=emerging")
        assert resp.status_code == 200
        # 'stable' submissions must not appear when filtering for 'emerging'
        for item in resp.json()["results"]:
            assert "emerging" in (item.get("secondary_maturity_tags") or [])


# ===========================================================================
# Reference data API mutation logging
# ===========================================================================


@pytest.mark.django_db
class TestReferenceDataLogging:
    def test_service_category_create_is_logged(self, staff_client):
        from unittest.mock import patch

        with patch("apps.api.views.logger") as mock_logger:
            resp = staff_client.post(
                "/api/v1/categories/",
                {"name": "Test Category"},
                format="json",
            )
        assert resp.status_code == 201
        calls = " ".join(str(c) for c in mock_logger.info.call_args_list)
        assert "ServiceCategory created" in calls

    def test_service_category_soft_delete_is_logged(self, staff_client):
        from unittest.mock import patch

        cat = ServiceCategoryFactory(name="ToDelete")
        with patch("apps.api.views.logger") as mock_logger:
            resp = staff_client.delete(f"/api/v1/categories/{cat.id}/")
        assert resp.status_code == 204
        # Check that info was called with args that produce the expected message
        assert mock_logger.info.called
        args = mock_logger.info.call_args_list[0][0]
        assert "deactivated" in args[0]
        assert "ServiceCategory" in str(args)
