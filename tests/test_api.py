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
  GET    /api/v1/categories/         admin token required, active-only
  GET    /api/v1/service-centers/    admin token required, active-only
  GET    /api/v1/pis/                admin token required, active-only
  GET    /api/schema/                always 200
  GET    /api/docs/                  always 200
  Auth:  ApiKey vs Token, revoked denial, scope enforcement, no-auth denial
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
    from django.contrib.auth import get_user_model
    from rest_framework.authtoken.models import Token

    User = get_user_model()
    user = User.objects.create_user(
        username="admin_test", password="testpass123", is_staff=True, is_active=True
    )
    token = Token.objects.create(user=user)
    return user, token.key


@pytest.fixture
def staff_client(api_client, admin_user):
    _, token = admin_user
    api_client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
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
        _, token = admin_user
        api_client.credentials(HTTP_AUTHORIZATION=f"Token {token}")
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
