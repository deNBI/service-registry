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
        "license": "apache2",
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

    def test_patch_approved_submission_resets_status(self, api_client):
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
            "license",
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

    def test_schema_excludes_internal_fields(self, api_client):
        """Sensitive internal fields must never appear in the public schema."""
        resp = api_client.get("/api/schema/")
        content = resp.content
        assert b"submission_ip" not in content
        assert b"user_agent_hash" not in content
        assert b"internal_contact_email" not in content
        assert b"internal_contact_name" not in content


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
