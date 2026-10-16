"""Values that must not reach public pages and endpoints.

The registration form suggests institutes and the public bio.tools endpoint
returns a stored record; neither may show private submitter data or internal
error text.
"""

import hashlib
import secrets

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.api.models import AdminAPIKey
from apps.submissions.forms import SubmissionForm
from tests.factories import (
    APIKeyFactory,
    BioToolsRecordFactory,
    PIFactory,
    ServiceSubmissionFactory,
)

pytestmark = pytest.mark.django_db
CANARY_AFFILIATION = "CANARY-AFFILIATION-7f3e"
CANARY_ERROR = "CANARY-SYNC-ERROR-91ab"
ALL_STATUSES = [
    "draft",
    "submitted",
    "under_review",
    "approved",
    "rejected",
    "deprecated",
]


def _choices(form, field):
    return [v for v, _ in form.fields[field].widget.choices]


class TestInstituteSuggestions:
    @pytest.mark.parametrize("status", ALL_STATUSES)
    def test_form_does_not_suggest_submitter_affiliations(self, status):
        ServiceSubmissionFactory(
            status=status, biotools_url="", submitter_affiliation=CANARY_AFFILIATION
        )
        form = SubmissionForm()
        assert CANARY_AFFILIATION not in _choices(form, "submitter_affiliation")
        assert CANARY_AFFILIATION not in _choices(form, "host_institute")

    def test_approved_service_does_not_expose_its_submitter_affiliation(self):
        ServiceSubmissionFactory(
            status="approved",
            biotools_url="",
            submitter_affiliation=CANARY_AFFILIATION,
        )
        form = SubmissionForm()
        assert CANARY_AFFILIATION not in _choices(form, "submitter_affiliation")
        assert CANARY_AFFILIATION not in _choices(form, "host_institute")

    def test_suggests_pi_institutes_and_approved_host_institutes(self):
        PIFactory(institute="Example Institute of Doe")
        ServiceSubmissionFactory(
            status="approved", biotools_url="", host_institute="Example Host Institute"
        )
        choices = _choices(SubmissionForm(), "host_institute")
        assert "Example Institute of Doe" in choices
        assert "Example Host Institute" in choices

    @pytest.mark.parametrize("status", [s for s in ALL_STATUSES if s != "approved"])
    def test_host_institutes_of_services_that_are_not_public_are_not_suggested(
        self, status
    ):
        ServiceSubmissionFactory(
            status=status, biotools_url="", host_institute="Example Private Host"
        )
        assert "Example Private Host" not in _choices(
            SubmissionForm(), "host_institute"
        )

    def test_both_institute_fields_share_the_public_suggestions(self):
        PIFactory(institute="Example Institute of Doe")
        form = SubmissionForm()
        assert _choices(form, "submitter_affiliation") == _choices(
            form, "host_institute"
        )

    def test_inactive_pi_institutes_are_not_suggested(self):
        PIFactory(institute="Example Inactive Institute", is_active=False)
        assert "Example Inactive Institute" not in _choices(
            SubmissionForm(), "host_institute"
        )

    def test_blank_values_are_not_suggested(self):
        PIFactory(institute="")
        ServiceSubmissionFactory(status="approved", biotools_url="", host_institute="")
        assert "" not in _choices(SubmissionForm(), "host_institute")[1:]

    def test_each_value_is_suggested_once(self):
        PIFactory(institute="Example Shared Institute")
        for _ in range(2):
            ServiceSubmissionFactory(
                status="approved",
                biotools_url="",
                host_institute="Example Shared Institute",
            )
        choices = _choices(SubmissionForm(), "host_institute")
        assert choices.count("Example Shared Institute") == 1

    def test_the_owner_still_sees_their_own_value_when_editing(self):
        sub = ServiceSubmissionFactory(
            biotools_url="", submitter_affiliation="Example Own Affiliation"
        )
        form = SubmissionForm(instance=sub)
        assert "Example Own Affiliation" in _choices(form, "submitter_affiliation")

    def test_the_owner_does_not_see_other_services_affiliations_when_editing(self):
        ServiceSubmissionFactory(
            biotools_url="", submitter_affiliation=CANARY_AFFILIATION
        )
        own = ServiceSubmissionFactory(
            biotools_url="", submitter_affiliation="Example Own Affiliation"
        )
        form = SubmissionForm(instance=own)
        assert CANARY_AFFILIATION not in _choices(form, "submitter_affiliation")

    def test_free_text_is_still_accepted(self):
        from tests.helpers import base_form_data

        data = base_form_data({"submitter_affiliation": "Brand New Institute 2026"})
        form = SubmissionForm(data)
        assert form.is_valid(), form.errors

    def test_register_page_html_does_not_contain_private_affiliations(self, client):
        for status in ALL_STATUSES:
            ServiceSubmissionFactory(
                status=status,
                biotools_url="",
                submitter_affiliation=f"{CANARY_AFFILIATION}-{status}",
            )
        resp = client.get(reverse("submissions:register"))
        assert resp.status_code == 200
        assert CANARY_AFFILIATION not in resp.content.decode()

    def test_edit_page_html_does_not_contain_other_affiliations(self, client):
        ServiceSubmissionFactory(
            biotools_url="", submitter_affiliation=CANARY_AFFILIATION
        )
        own = ServiceSubmissionFactory(biotools_url="")
        key_obj, _ = APIKeyFactory.create_with_plaintext(submission=own)
        session = client.session
        session["edit_grants"] = {str(own.pk): str(key_obj.pk)}
        session.save()
        resp = client.get(reverse("submissions:edit", args=[own.pk]))
        assert resp.status_code == 200
        assert CANARY_AFFILIATION not in resp.content.decode()


def _admin_key():
    plaintext = secrets.token_urlsafe(48)
    AdminAPIKey.objects.create(
        label="Admin Test Key",
        key_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
        scope="full",
        is_active=True,
    )
    return plaintext


def _record():
    return BioToolsRecordFactory(
        submission__status="approved",
        submission__biotools_url="",
        sync_error=CANARY_ERROR,
    )


class TestBioToolsSyncError:
    def test_public_endpoint_hides_sync_error(self):
        record = _record()
        resp = APIClient().get(f"/api/v1/biotools/{record.biotools_id}/")
        assert resp.status_code == 200
        assert CANARY_ERROR not in resp.content.decode()
        assert "sync_error" not in resp.json()

    def test_public_endpoint_keeps_every_other_field(self):
        record = _record()
        public = APIClient().get(f"/api/v1/biotools/{record.biotools_id}/").json()
        admin = APIClient()
        admin.credentials(HTTP_AUTHORIZATION=f"AdminKey {_admin_key()}")
        full = admin.get(f"/api/v1/biotools/{record.biotools_id}/").json()
        assert set(full) - set(public) == {"sync_error"}
        assert {k: v for k, v in full.items() if k != "sync_error"} == public

    def test_admin_still_sees_sync_error_on_retrieve(self):
        record = _record()
        admin = APIClient()
        admin.credentials(HTTP_AUTHORIZATION=f"AdminKey {_admin_key()}")
        resp = admin.get(f"/api/v1/biotools/{record.biotools_id}/")
        assert resp.json()["sync_error"] == CANARY_ERROR

    def test_admin_still_sees_sync_error_in_the_list(self):
        _record()
        admin = APIClient()
        admin.credentials(HTTP_AUTHORIZATION=f"AdminKey {_admin_key()}")
        resp = admin.get("/api/v1/biotools/")
        assert resp.status_code == 200
        assert CANARY_ERROR in resp.content.decode()

    def test_owner_key_gets_the_public_representation(self):
        record = _record()
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=record.submission)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = client.get(f"/api/v1/biotools/{record.biotools_id}/")
        assert resp.status_code == 200
        assert "sync_error" not in resp.json()

    def test_invalid_admin_credentials_get_the_public_representation(self):
        record = _record()
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="AdminKey not-a-real-key")
        resp = client.get(f"/api/v1/biotools/{record.biotools_id}/")
        assert CANARY_ERROR not in resp.content.decode()

    def test_unapproved_record_is_still_not_found(self):
        record = BioToolsRecordFactory(
            submission__biotools_url="", sync_error=CANARY_ERROR
        )
        resp = APIClient().get(f"/api/v1/biotools/{record.biotools_id}/")
        assert resp.status_code == 404
        assert CANARY_ERROR not in resp.content.decode()

    def test_openapi_schema_documents_the_public_shape(self):
        schema = APIClient().get("/api/schema/", {"format": "json"}).json()
        schemas = schema["components"]["schemas"]
        assert "sync_error" not in schemas["BioToolsRecordPublic"]["properties"]
        assert "sync_error" in schemas["BioToolsRecord"]["properties"]
        path = next(
            p
            for p in schema["paths"]
            if p.startswith("/api/v1/biotools/") and p.endswith("}/")
        )
        retrieve = schema["paths"][path]["get"]
        ref = retrieve["responses"]["200"]["content"]["application/json"]["schema"]
        assert ref["$ref"].endswith("/BioToolsRecordPublic")
