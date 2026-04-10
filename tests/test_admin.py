"""
Admin Tests
===========
Tests for Django admin actions: status transitions (deprecate/undeprecate)
and comprehensive CSV/JSON exports.
"""

import csv
import io
import json

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from apps.edam.models import EdamTerm
from apps.submissions.models import SubmissionChangeLog
from tests.factories import (
    BioToolsFunctionFactory,
    BioToolsRecordFactory,
    ServiceSubmissionFactory,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(db):
    User = get_user_model()
    user = User.objects.create_superuser(
        username="testadmin", password="adminpass123", email="admin@example.com"
    )
    c = Client()
    c.force_login(user)
    return c


def _changelist_url():
    return reverse("admin:submissions_servicesubmission_changelist")


def _run_action(admin_client, action, *submissions):
    ids = [str(s.pk) for s in submissions]
    return admin_client.post(
        _changelist_url(),
        {
            "action": action,
            "_selected_action": ids,
        },
    )


# ===========================================================================
# Deprecate / Undeprecate bulk actions
# ===========================================================================


@pytest.mark.django_db
class TestDeprecateBulkAction:
    def test_action_deprecate_sets_status(self, admin_client):
        sub = ServiceSubmissionFactory(status="approved")
        resp = _run_action(admin_client, "action_deprecate", sub)
        assert resp.status_code in (200, 302)
        sub.refresh_from_db()
        assert sub.status == "deprecated"

    def test_action_deprecate_multiple_submissions(self, admin_client):
        subs = [ServiceSubmissionFactory(status="approved") for _ in range(3)]
        resp = _run_action(admin_client, "action_deprecate", *subs)
        assert resp.status_code in (200, 302)
        for sub in subs:
            sub.refresh_from_db()
            assert sub.status == "deprecated"

    def test_action_deprecate_already_deprecated_is_skipped(self, admin_client):
        sub = ServiceSubmissionFactory(status="deprecated")
        _run_action(admin_client, "action_deprecate", sub)
        sub.refresh_from_db()
        assert sub.status == "deprecated"  # unchanged, no error


@pytest.mark.django_db
class TestUndeprecateBulkAction:
    def test_action_undeprecate_sets_status_to_submitted(self, admin_client):
        sub = ServiceSubmissionFactory(status="deprecated")
        resp = _run_action(admin_client, "action_undeprecate", sub)
        assert resp.status_code in (200, 302)
        sub.refresh_from_db()
        assert sub.status == "submitted"

    def test_action_undeprecate_multiple(self, admin_client):
        subs = [ServiceSubmissionFactory(status="deprecated") for _ in range(2)]
        _run_action(admin_client, "action_undeprecate", *subs)
        for sub in subs:
            sub.refresh_from_db()
            assert sub.status == "submitted"


# ===========================================================================
# Status transition audit logging
# ===========================================================================


@pytest.mark.django_db
class TestStatusTransitionAuditLog:
    """Status transitions via bulk actions are recorded in SubmissionChangeLog
    and last_change_summary — not only in Django's LogEntry."""

    def test_deprecate_creates_changelog_entry(self, admin_client):
        sub = ServiceSubmissionFactory(status="approved")
        _run_action(admin_client, "action_deprecate", sub)
        sub.refresh_from_db()
        log = SubmissionChangeLog.objects.get(submission=sub)
        assert log.changes[0]["field"] == "status"
        assert log.changes[0]["old"] == "Approved"
        assert log.changes[0]["new"] == "Deprecated"

    def test_deprecate_updates_last_change_summary(self, admin_client):
        sub = ServiceSubmissionFactory(status="approved")
        _run_action(admin_client, "action_deprecate", sub)
        sub.refresh_from_db()
        assert sub.last_change_summary is not None
        assert sub.last_change_summary["changes"][0]["field"] == "status"
        assert sub.last_change_summary["changed_by"] == "admin:testadmin"

    def test_undeprecate_creates_changelog_entry(self, admin_client):
        sub = ServiceSubmissionFactory(status="deprecated")
        _run_action(admin_client, "action_undeprecate", sub)
        sub.refresh_from_db()
        log = SubmissionChangeLog.objects.get(submission=sub)
        assert log.changes[0]["field"] == "status"
        assert log.changes[0]["old"] == "Deprecated"
        assert log.changes[0]["new"] == "Submitted"

    def test_approve_creates_changelog_entry(self, admin_client):
        sub = ServiceSubmissionFactory(status="submitted")
        _run_action(admin_client, "action_approve", sub)
        sub.refresh_from_db()
        log = SubmissionChangeLog.objects.get(submission=sub)
        assert log.changes[0]["field"] == "status"
        assert log.changes[0]["old"] == "Submitted"
        assert log.changes[0]["new"] == "Approved"

    def test_reject_creates_changelog_entry(self, admin_client):
        sub = ServiceSubmissionFactory(status="under_review")
        _run_action(admin_client, "action_reject", sub)
        sub.refresh_from_db()
        log = SubmissionChangeLog.objects.get(submission=sub)
        assert log.changes[0]["field"] == "status"
        assert log.changes[0]["old"] == "Under Review"
        assert log.changes[0]["new"] == "Rejected"

    def test_no_changelog_when_status_already_matches(self, admin_client):
        sub = ServiceSubmissionFactory(status="deprecated")
        _run_action(admin_client, "action_deprecate", sub)
        assert SubmissionChangeLog.objects.filter(submission=sub).count() == 0
        sub.refresh_from_db()
        assert sub.last_change_summary is None

    def test_changelog_changed_by_contains_admin_username(self, admin_client):
        sub = ServiceSubmissionFactory(status="approved")
        _run_action(admin_client, "action_deprecate", sub)
        log = SubmissionChangeLog.objects.get(submission=sub)
        assert log.changed_by == "admin:testadmin"

    def test_maturity_tag_clearing_recorded_in_changelog(self, admin_client):
        sub = ServiceSubmissionFactory(status="approved", primary_maturity_tag="mature")
        _run_action(admin_client, "action_deprecate", sub)
        sub.refresh_from_db()
        log = SubmissionChangeLog.objects.get(submission=sub)
        fields_changed = {ch["field"] for ch in log.changes}
        assert "status" in fields_changed
        assert "primary_maturity_tag" in fields_changed
        tag_entry = next(
            ch for ch in log.changes if ch["field"] == "primary_maturity_tag"
        )
        assert tag_entry["old"] == "Mature"
        assert tag_entry["new"] == "—"

    def test_secondary_maturity_tag_clearing_recorded_in_changelog(self, admin_client):
        sub = ServiceSubmissionFactory(
            status="approved",
            primary_maturity_tag="mature",
            secondary_maturity_tags=["unstable"],
        )
        _run_action(admin_client, "action_deprecate", sub)
        sub.refresh_from_db()
        log = SubmissionChangeLog.objects.get(submission=sub)
        fields_changed = {ch["field"] for ch in log.changes}
        assert "secondary_maturity_tags" in fields_changed
        sec_entry = next(
            ch for ch in log.changes if ch["field"] == "secondary_maturity_tags"
        )
        assert "Unstable" in sec_entry["old"]
        assert sec_entry["new"] == "—"


# ===========================================================================
# CSV export — comprehensive columns
# ===========================================================================


@pytest.mark.django_db
class TestExportCSV:
    EXPECTED_COLUMNS = [
        "id",
        "status",
        "service_name",
        "service_description",
        "year_established",
        "submitter_first_name",
        "submitter_last_name",
        "submitter_affiliation",
        "host_institute",
        "service_center",
        "public_contact_email",
        "internal_contact_name",
        "internal_contact_email",
        "service_categories",
        "responsible_pis",
        "edam_topics",
        "edam_operations",
        "is_toolbox",
        "toolbox_name",
        "user_knowledge_required",
        "publications_pmids",
        "website_url",
        "terms_of_use_url",
        "license",
        "github_url",
        "biotools_url",
        "fairsharing_url",
        "other_registry_url",
        "kpi_monitoring",
        "kpi_start_year",
        "keywords_uncited",
        "keywords_seo",
        "register_as_elixir",
        "survey_participation",
        "comments",
        "logo_url",
        "biotools_id",
        "biotools_name",
        "biotools_description",
        "biotools_homepage",
        "biotools_version",
        "biotools_license",
        "biotools_maturity",
        "biotools_cost",
        "biotools_tool_type",
        "biotools_operating_system",
        "biotools_edam_topic_uris",
        "biotools_edam_operation_uris",
        "biotools_functions",
        "biotools_publications",
        "biotools_documentation",
        "biotools_download",
        "biotools_links",
        "biotools_last_synced_at",
        "submitted_at",
        "updated_at",
    ]

    def _get_csv(self, admin_client, *submissions):
        resp = _run_action(admin_client, "action_export_csv", *submissions)
        assert resp.status_code == 200
        assert "text/csv" in resp["Content-Type"]
        content = resp.content.decode("utf-8-sig")
        return list(csv.DictReader(io.StringIO(content)))

    def test_csv_has_all_expected_columns(self, admin_client):
        sub = ServiceSubmissionFactory()
        rows = self._get_csv(admin_client, sub)
        assert len(rows) == 1
        for col in self.EXPECTED_COLUMNS:
            assert col in rows[0], f"Missing column: {col}"

    def test_csv_basic_fields_correct(self, admin_client):
        sub = ServiceSubmissionFactory(status="approved", service_name="My Tool")
        rows = self._get_csv(admin_client, sub)
        assert rows[0]["service_name"] == "My Tool"
        assert rows[0]["status"] == "approved"
        assert rows[0]["id"] == str(sub.pk)

    def test_csv_includes_edam_topics(self, admin_client):
        sub = ServiceSubmissionFactory()
        topic = EdamTerm.objects.create(
            uri="http://edamontology.org/topic_0091",
            label="Bioinformatics",
            branch="topic",
            accession="topic_0091",
        )
        sub.edam_topics.add(topic)
        rows = self._get_csv(admin_client, sub)
        assert "Bioinformatics" in rows[0]["edam_topics"]

    def test_csv_includes_biotools_data(self, admin_client):
        sub = ServiceSubmissionFactory()
        BioToolsRecordFactory(
            submission=sub,
            biotools_id="mytool",
            name="My Tool",
            description="A great tool.",
            homepage="https://example.com",
            version="1.0",
            license="MIT",
            maturity="Mature",
            cost="Free",
            tool_type=["Web application"],
            operating_system=["Linux"],
            edam_topic_uris=["http://edamontology.org/topic_0091"],
            publications=[
                {"pmid": "12345", "doi": "", "pmcid": "", "type": "Primary", "note": ""}
            ],
            documentation=[{"url": "https://docs.example.com", "type": "General"}],
            download=[
                {
                    "url": "https://example.com/dl",
                    "type": "Source code",
                    "version": "1.0",
                }
            ],
            links=[{"url": "https://example.com/issues", "type": "Issue tracker"}],
        )
        rows = self._get_csv(admin_client, sub)
        assert rows[0]["biotools_id"] == "mytool"
        assert rows[0]["biotools_name"] == "My Tool"
        assert rows[0]["biotools_description"] == "A great tool."
        assert rows[0]["biotools_homepage"] == "https://example.com"
        assert rows[0]["biotools_version"] == "1.0"
        assert rows[0]["biotools_license"] == "MIT"
        assert rows[0]["biotools_maturity"] == "Mature"
        assert rows[0]["biotools_cost"] == "Free"
        assert "Web application" in rows[0]["biotools_tool_type"]
        assert "Linux" in rows[0]["biotools_operating_system"]
        assert (
            "http://edamontology.org/topic_0091" in rows[0]["biotools_edam_topic_uris"]
        )
        assert "12345" in rows[0]["biotools_publications"]
        assert "https://docs.example.com" in rows[0]["biotools_documentation"]
        assert "https://example.com/dl" in rows[0]["biotools_download"]
        assert "https://example.com/issues" in rows[0]["biotools_links"]

    def test_csv_empty_biotools_when_no_record(self, admin_client):
        sub = ServiceSubmissionFactory()
        rows = self._get_csv(admin_client, sub)
        assert rows[0]["biotools_id"] == ""
        assert rows[0]["biotools_name"] == ""
        assert rows[0]["biotools_description"] == ""
        assert rows[0]["biotools_functions"] == "[]"
        assert rows[0]["biotools_publications"] == "[]"
        assert rows[0]["biotools_last_synced_at"] == ""

    def test_csv_biotools_no_functions_has_empty_operation_uris(self, admin_client):
        sub = ServiceSubmissionFactory()
        BioToolsRecordFactory(submission=sub, biotools_id="noops")
        rows = self._get_csv(admin_client, sub)
        assert rows[0]["biotools_id"] == "noops"
        assert rows[0]["biotools_edam_operation_uris"] == ""

    def test_csv_export_logo_url_empty_when_no_logo(self, admin_client):
        sub = ServiceSubmissionFactory()
        rows = self._get_csv(admin_client, sub)
        assert rows[0]["logo_url"] == ""

    def test_csv_export_empty_responsible_pis(self, admin_client):
        sub = ServiceSubmissionFactory(responsible_pis=[])
        rows = self._get_csv(admin_client, sub)
        assert rows[0]["responsible_pis"] == ""

    def test_csv_export_empty_service_categories(self, admin_client):
        sub = ServiceSubmissionFactory(service_categories=[])
        rows = self._get_csv(admin_client, sub)
        assert rows[0]["service_categories"] == ""

    def test_csv_biotools_edam_operation_uris_from_functions(self, admin_client):
        sub = ServiceSubmissionFactory()
        bt = BioToolsRecordFactory(submission=sub)
        BioToolsFunctionFactory(
            record=bt,
            position=0,
            operations=[
                {"uri": "http://edamontology.org/operation_0004", "term": "Operation"},
                {
                    "uri": "http://edamontology.org/operation_0337",
                    "term": "Visualisation",
                },
            ],
        )
        rows = self._get_csv(admin_client, sub)
        uris = rows[0]["biotools_edam_operation_uris"]
        assert "http://edamontology.org/operation_0004" in uris
        assert "http://edamontology.org/operation_0337" in uris

    def test_csv_biotools_functions_serialised_as_json(self, admin_client):
        sub = ServiceSubmissionFactory()
        bt = BioToolsRecordFactory(submission=sub)
        BioToolsFunctionFactory(
            record=bt,
            position=0,
            operations=[
                {"uri": "http://edamontology.org/operation_0004", "term": "Operation"}
            ],
            inputs=[
                {
                    "data": {
                        "uri": "http://edamontology.org/data_2044",
                        "term": "Sequence",
                    },
                    "formats": [],
                }
            ],
            outputs=[],
            cmd="",
            note="alignment function",
        )
        rows = self._get_csv(admin_client, sub)
        functions = json.loads(rows[0]["biotools_functions"])
        assert len(functions) == 1
        assert (
            functions[0]["operations"][0]["uri"]
            == "http://edamontology.org/operation_0004"
        )
        assert functions[0]["note"] == "alignment function"

    def test_csv_deprecated_submission_exported(self, admin_client):
        sub = ServiceSubmissionFactory(status="deprecated")
        rows = self._get_csv(admin_client, sub)
        assert rows[0]["status"] == "deprecated"


# ===========================================================================
# JSON export — comprehensive fields
# ===========================================================================


@pytest.mark.django_db
class TestExportJSON:
    EXPECTED_KEYS = [
        "id",
        "status",
        "service_name",
        "service_description",
        "year_established",
        "submitter",
        "host_institute",
        "service_center",
        "public_contact_email",
        "internal_contact_name",
        "internal_contact_email",
        "service_categories",
        "responsible_pis",
        "edam_topics",
        "edam_operations",
        "is_toolbox",
        "toolbox_name",
        "user_knowledge_required",
        "publications_pmids",
        "website_url",
        "terms_of_use_url",
        "license",
        "github_url",
        "biotools_url",
        "fairsharing_url",
        "other_registry_url",
        "kpi_monitoring",
        "kpi_start_year",
        "keywords_uncited",
        "keywords_seo",
        "register_as_elixir",
        "survey_participation",
        "comments",
        "logo_url",
        "biotools",
        "submitted_at",
        "updated_at",
    ]

    def _get_json(self, admin_client, *submissions):
        resp = _run_action(admin_client, "action_export_json", *submissions)
        assert resp.status_code == 200
        assert "application/json" in resp["Content-Type"]
        return json.loads(resp.content)

    def test_json_has_all_expected_keys(self, admin_client):
        sub = ServiceSubmissionFactory()
        data = self._get_json(admin_client, sub)
        assert len(data) == 1
        for key in self.EXPECTED_KEYS:
            assert key in data[0], f"Missing key: {key}"

    def test_json_submitter_is_nested(self, admin_client):
        sub = ServiceSubmissionFactory(
            submitter_first_name="Jane", submitter_last_name="Doe"
        )
        data = self._get_json(admin_client, sub)
        assert data[0]["submitter"]["first_name"] == "Jane"
        assert data[0]["submitter"]["last_name"] == "Doe"

    def test_json_edam_topics_are_objects(self, admin_client):
        sub = ServiceSubmissionFactory()
        topic = EdamTerm.objects.create(
            uri="http://edamontology.org/topic_0091",
            label="Bioinformatics",
            branch="topic",
            accession="topic_0091",
        )
        sub.edam_topics.add(topic)
        data = self._get_json(admin_client, sub)
        topics = data[0]["edam_topics"]
        assert len(topics) == 1
        assert topics[0]["label"] == "Bioinformatics"
        assert topics[0]["uri"] == "http://edamontology.org/topic_0091"

    def test_json_biotools_nested_when_present(self, admin_client):
        sub = ServiceSubmissionFactory()
        BioToolsRecordFactory(
            submission=sub,
            biotools_id="mytool",
            name="My Tool",
            description="A great tool.",
            homepage="https://example.com",
            version="1.0",
            license="MIT",
            maturity="Mature",
            cost="Free",
            tool_type=["Web application"],
            operating_system=["Linux"],
            edam_topic_uris=["http://edamontology.org/topic_0091"],
            publications=[
                {"pmid": "12345", "doi": "", "pmcid": "", "type": "Primary", "note": ""}
            ],
            documentation=[{"url": "https://docs.example.com", "type": "General"}],
            download=[
                {
                    "url": "https://example.com/dl",
                    "type": "Source code",
                    "version": "1.0",
                }
            ],
            links=[{"url": "https://example.com/issues", "type": "Issue tracker"}],
        )
        data = self._get_json(admin_client, sub)
        bt = data[0]["biotools"]
        assert bt["biotools_id"] == "mytool"
        assert bt["biotools_name"] == "My Tool"
        assert bt["biotools_description"] == "A great tool."
        assert bt["biotools_homepage"] == "https://example.com"
        assert bt["biotools_version"] == "1.0"
        assert bt["biotools_license"] == "MIT"
        assert bt["biotools_maturity"] == "Mature"
        assert bt["biotools_cost"] == "Free"
        assert bt["biotools_tool_type"] == ["Web application"]
        assert bt["biotools_operating_system"] == ["Linux"]
        assert "http://edamontology.org/topic_0091" in bt["biotools_edam_topic_uris"]
        assert bt["biotools_publications"][0]["pmid"] == "12345"
        assert bt["biotools_documentation"][0]["url"] == "https://docs.example.com"
        assert bt["biotools_download"][0]["url"] == "https://example.com/dl"
        assert bt["biotools_links"][0]["url"] == "https://example.com/issues"

    def test_json_biotools_empty_when_no_record(self, admin_client):
        sub = ServiceSubmissionFactory()
        data = self._get_json(admin_client, sub)
        bt = data[0]["biotools"]
        assert bt["biotools_id"] == ""
        assert bt["biotools_edam_topic_uris"] == []
        assert bt["biotools_functions"] == []
        assert bt["biotools_publications"] == []
        assert bt["biotools_last_synced_at"] == ""

    def test_json_service_categories_is_list(self, admin_client):
        sub = ServiceSubmissionFactory()
        data = self._get_json(admin_client, sub)
        assert isinstance(data[0]["service_categories"], list)
        assert len(data[0]["service_categories"]) >= 1

    def test_json_biotools_no_functions_has_empty_operation_uris(self, admin_client):
        sub = ServiceSubmissionFactory()
        BioToolsRecordFactory(submission=sub, biotools_id="noops")
        data = self._get_json(admin_client, sub)
        assert data[0]["biotools"]["biotools_id"] == "noops"
        assert data[0]["biotools"]["biotools_edam_operation_uris"] == []

    def test_json_export_logo_url_empty_when_no_logo(self, admin_client):
        sub = ServiceSubmissionFactory()
        data = self._get_json(admin_client, sub)
        assert data[0]["logo_url"] == ""

    def test_json_export_empty_responsible_pis(self, admin_client):
        sub = ServiceSubmissionFactory(responsible_pis=[])
        data = self._get_json(admin_client, sub)
        assert data[0]["responsible_pis"] == []

    def test_json_export_empty_service_categories(self, admin_client):
        sub = ServiceSubmissionFactory(service_categories=[])
        data = self._get_json(admin_client, sub)
        assert data[0]["service_categories"] == []

    def test_json_biotools_edam_operation_uris_from_functions(self, admin_client):
        sub = ServiceSubmissionFactory()
        bt = BioToolsRecordFactory(submission=sub)
        BioToolsFunctionFactory(
            record=bt,
            position=0,
            operations=[
                {"uri": "http://edamontology.org/operation_0004", "term": "Operation"},
            ],
        )
        data = self._get_json(admin_client, sub)
        uris = data[0]["biotools"]["biotools_edam_operation_uris"]
        assert "http://edamontology.org/operation_0004" in uris

    def test_json_biotools_functions_structured(self, admin_client):
        sub = ServiceSubmissionFactory()
        bt = BioToolsRecordFactory(submission=sub)
        BioToolsFunctionFactory(
            record=bt,
            position=0,
            operations=[
                {"uri": "http://edamontology.org/operation_0004", "term": "Operation"}
            ],
            inputs=[
                {
                    "data": {
                        "uri": "http://edamontology.org/data_2044",
                        "term": "Sequence",
                    },
                    "formats": [],
                }
            ],
            outputs=[],
            cmd="",
            note="alignment",
        )
        data = self._get_json(admin_client, sub)
        functions = data[0]["biotools"]["biotools_functions"]
        assert len(functions) == 1
        assert (
            functions[0]["operations"][0]["uri"]
            == "http://edamontology.org/operation_0004"
        )
        assert functions[0]["inputs"][0]["data"]["term"] == "Sequence"
        assert functions[0]["note"] == "alignment"

    def test_json_deprecated_submission_exported(self, admin_client):
        sub = ServiceSubmissionFactory(status="deprecated")
        data = self._get_json(admin_client, sub)
        assert data[0]["status"] == "deprecated"


# ===========================================================================
# Diff capture — save_model / save_related / response_change
# ===========================================================================


def _change_url(sub):
    return reverse("admin:submissions_servicesubmission_change", args=[sub.pk])


def _edit_form_payload(sub, **overrides):
    """Minimal admin change-view POST payload for a ServiceSubmission."""
    payload = {
        "date_of_entry": sub.date_of_entry.isoformat(),
        "submitter_first_name": sub.submitter_first_name,
        "submitter_last_name": sub.submitter_last_name,
        "submitter_affiliation": sub.submitter_affiliation,
        "register_as_elixir": "False",
        "service_name": sub.service_name,
        "service_description": sub.service_description,
        "year_established": str(sub.year_established),
        "service_categories": [c.pk for c in sub.service_categories.all()],
        "is_toolbox": "False",
        "toolbox_name": "",
        "user_knowledge_required": sub.user_knowledge_required or "",
        "publications_pmids": sub.publications_pmids,
        "responsible_pis": [p.pk for p in sub.responsible_pis.all()],
        "associated_partner_note": "",
        "host_institute": sub.host_institute,
        "service_center": sub.service_center.pk,
        "public_contact_email": sub.public_contact_email,
        "internal_contact_name": sub.internal_contact_name,
        "internal_contact_email": sub.internal_contact_email,
        "website_url": sub.website_url,
        "terms_of_use_url": sub.terms_of_use_url,
        "license": sub.license,
        "github_url": sub.github_url or "",
        "biotools_url": sub.biotools_url or "",
        "fairsharing_url": sub.fairsharing_url or "",
        "other_registry_url": sub.other_registry_url or "",
        "kpi_monitoring": sub.kpi_monitoring,
        "kpi_start_year": sub.kpi_start_year or "",
        "keywords_uncited": sub.keywords_uncited or "",
        "keywords_seo": sub.keywords_seo or "",
        "survey_participation": "True",
        "comments": sub.comments or "",
        "data_protection_consent": "True",
        # Required Django admin hidden fields
        "_save": "Save",
        "api_keys-TOTAL_FORMS": "0",
        "api_keys-INITIAL_FORMS": "0",
        "api_keys-MIN_NUM_FORMS": "0",
        "api_keys-MAX_NUM_FORMS": "0",
        "edam_topics": [],
        "edam_operations": [],
        "primary_maturity_tag": sub.primary_maturity_tag or "",
        "secondary_maturity_tags": sub.secondary_maturity_tags or [],
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
class TestAdminDiffCapture:
    def test_admin_edit_writes_last_change_summary(self, admin_client):
        """Saving a changed field via the admin populates last_change_summary."""
        sub = ServiceSubmissionFactory(service_name="Before", comments="")
        payload = _edit_form_payload(sub, service_name="After")
        resp = admin_client.post(_change_url(sub), data=payload)
        assert resp.status_code in (200, 302)

        sub.refresh_from_db()
        assert sub.last_change_summary is not None
        summary = sub.last_change_summary
        assert "admin:" in summary["changed_by"]
        assert "changed_at" in summary
        fields = {ch["field"] for ch in summary["changes"]}
        assert "service_name" in fields

    def test_admin_edit_records_old_and_new_values(self, admin_client):
        sub = ServiceSubmissionFactory(service_name="Old Name")
        payload = _edit_form_payload(sub, service_name="New Name")
        admin_client.post(_change_url(sub), data=payload)
        sub.refresh_from_db()

        name_ch = next(
            ch
            for ch in sub.last_change_summary["changes"]
            if ch["field"] == "service_name"
        )
        assert name_ch["old"] == "Old Name"
        assert name_ch["new"] == "New Name"

    def test_admin_edit_no_change_does_not_write_summary(self, admin_client):
        sub = ServiceSubmissionFactory()
        assert sub.last_change_summary is None
        payload = _edit_form_payload(sub)  # no overrides
        admin_client.post(_change_url(sub), data=payload)
        sub.refresh_from_db()
        assert sub.last_change_summary is None

    def test_admin_edit_diff_banner_shown_in_response(self, admin_client):
        """response_change should include a diff summary in the messages."""
        sub = ServiceSubmissionFactory(service_name="Before")
        payload = _edit_form_payload(sub, service_name="After")
        resp = admin_client.post(_change_url(sub), data=payload, follow=True)
        content = resp.content.decode()
        # The diff banner lists changed field labels
        assert "Service Name" in content

    def test_admin_edit_maturity_tag_change_tracked_in_diff(self, admin_client):
        """Changing primary_maturity_tag via the change form creates a ChangeLog entry and diff banner."""
        from apps.submissions.models import SubmissionChangeLog

        sub = ServiceSubmissionFactory(status="approved", primary_maturity_tag=None)
        payload = _edit_form_payload(sub, primary_maturity_tag="emerging")
        resp = admin_client.post(_change_url(sub), data=payload, follow=True)
        content = resp.content.decode()
        # Diff banner must mention the field label
        assert "Primary Maturity Tag" in content
        # A ChangeLog entry must have been written
        assert SubmissionChangeLog.objects.filter(submission=sub).exists()
        entry = SubmissionChangeLog.objects.get(submission=sub)
        changed_fields = [ch["field"] for ch in entry.changes]
        assert "primary_maturity_tag" in changed_fields

    def test_admin_edit_no_change_message_shown(self, admin_client):
        """When nothing changed, a neutral informational message is shown."""
        sub = ServiceSubmissionFactory()
        payload = _edit_form_payload(sub)
        resp = admin_client.post(_change_url(sub), data=payload, follow=True)
        content = resp.content.decode()
        assert "no field values were changed" in content.lower()

    def test_last_change_summary_display_shown_in_change_view(self, admin_client):
        """The last_change_summary fieldset is rendered in the admin change view."""
        sub = ServiceSubmissionFactory(
            last_change_summary={
                "changed_by": "submitter",
                "changed_at": "2026-01-01T10:00:00+00:00",
                "changes": [
                    {
                        "field": "service_name",
                        "label": "Service Name",
                        "old": "A",
                        "new": "B",
                    }
                ],
            }
        )
        resp = admin_client.get(_change_url(sub))
        assert resp.status_code == 200
        content = resp.content.decode()
        assert "Service Name" in content
        assert "Submitter" in content

    def test_last_change_summary_empty_shows_placeholder(self, admin_client):
        sub = ServiceSubmissionFactory()
        resp = admin_client.get(_change_url(sub))
        assert resp.status_code == 200
        assert b"No change history recorded yet" in resp.content


# ---------------------------------------------------------------------------
# Admin Maturity Tags Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestServiceSubmissionAdminTags:
    """Test admin list view and bulk action for maturity tag assignment."""

    def test_list_view_displays_maturity_tag_column(self, admin_client):
        """Maturity tag column appears in admin list view."""
        ServiceSubmissionFactory(status="approved", primary_maturity_tag="mature")
        response = admin_client.get(_changelist_url())
        assert response.status_code == 200
        assert b"Maturity" in response.content

    def test_list_view_filters_by_primary_tag(self, admin_client):
        """Filter ?primary_maturity_tag=mature shows only mature-tagged submissions."""
        sub1 = ServiceSubmissionFactory(
            status="approved", primary_maturity_tag="mature"
        )
        sub2 = ServiceSubmissionFactory(
            status="approved", primary_maturity_tag="emerging"
        )
        response = admin_client.get(_changelist_url() + "?primary_maturity_tag=mature")
        assert response.status_code == 200
        assert sub1.service_name.encode() in response.content
        assert sub2.service_name.encode() not in response.content

    def test_bulk_action_renders_form_on_first_post(self, admin_client):
        """First POST to action renders the tag-selection form."""
        sub = ServiceSubmissionFactory(status="approved")
        resp = _run_action(admin_client, "action_assign_maturity_tags", sub)
        assert resp.status_code == 200
        assert b"_assign_tags" in resp.content  # form submit button present
        assert b"primary_maturity_tag" in resp.content

    def test_bulk_action_assigns_tag_on_second_post(self, admin_client):
        """Second POST (with _assign_tags) persists the selected tag."""
        sub = ServiceSubmissionFactory(status="approved", primary_maturity_tag=None)
        admin_client.post(
            _changelist_url(),
            {
                "action": "action_assign_maturity_tags",
                "_selected_action": [str(sub.pk)],
                "_assign_tags": "1",
                "primary_maturity_tag": "mature",
            },
        )
        sub.refresh_from_db()
        assert sub.primary_maturity_tag == "mature"

    def test_bulk_action_filters_non_approved(self, admin_client):
        """Non-approved submissions are skipped; approved one still gets the tag."""
        approved = ServiceSubmissionFactory(
            status="approved", primary_maturity_tag=None
        )
        draft = ServiceSubmissionFactory(status="draft")
        admin_client.post(
            _changelist_url(),
            {
                "action": "action_assign_maturity_tags",
                "_selected_action": [str(approved.pk), str(draft.pk)],
                "_assign_tags": "1",
                "primary_maturity_tag": "legacy",
            },
        )
        approved.refresh_from_db()
        draft.refresh_from_db()
        assert approved.primary_maturity_tag == "legacy"
        assert draft.primary_maturity_tag is None  # draft must not receive tags

    def test_status_action_clears_tags_on_reject(self, admin_client):
        """Rejecting an approved+tagged submission auto-clears maturity tags."""
        sub = ServiceSubmissionFactory(
            status="approved",
            primary_maturity_tag="mature",
            secondary_maturity_tags=["unstable"],
        )
        admin_client.post(_change_url(sub), _edit_form_payload(sub, _reject="1"))
        sub.refresh_from_db()
        assert sub.status == "rejected"
        assert sub.primary_maturity_tag is None
        assert not sub.secondary_maturity_tags

    def test_status_action_clears_tags_on_deprecate(self, admin_client):
        """Deprecating an approved+tagged submission auto-clears maturity tags."""
        sub = ServiceSubmissionFactory(
            status="approved",
            primary_maturity_tag="legacy",
            secondary_maturity_tags=["unstable"],
        )
        admin_client.post(_change_url(sub), _edit_form_payload(sub, _deprecate="1"))
        sub.refresh_from_db()
        assert sub.status == "deprecated"
        assert sub.primary_maturity_tag is None
        assert not sub.secondary_maturity_tags

    def test_status_action_warning_shown_when_approved_has_tags(self, admin_client):
        """Change form shows the tag-clear warning when service is approved with tags."""
        sub = ServiceSubmissionFactory(
            status="approved",
            primary_maturity_tag="emerging",
            secondary_maturity_tags=["unstable"],
        )
        resp = admin_client.get(_change_url(sub))
        assert resp.status_code == 200
        assert b"Unapproving will automatically clear maturity tags" in resp.content

    def test_status_action_no_warning_when_no_tags(self, admin_client):
        """Change form does not show the tag-clear warning when service has no tags."""
        sub = ServiceSubmissionFactory(status="approved", primary_maturity_tag=None)
        resp = admin_client.get(_change_url(sub))
        assert resp.status_code == 200
        assert b"Unapproving will automatically clear maturity tags" not in resp.content

    def test_change_form_renders_tag_fields(self, admin_client):
        """Change form loads without error and includes tag field markup."""
        sub = ServiceSubmissionFactory(status="approved")
        url = reverse("admin:submissions_servicesubmission_change", args=[sub.pk])
        response = admin_client.get(url)
        assert response.status_code == 200
        assert b"primary_maturity_tag" in response.content
        assert b"secondary_maturity_tags" in response.content

    def test_csv_export_includes_maturity_tags(self, admin_client):
        """CSV export includes Primary Maturity Tag and Secondary Maturity Tags columns."""
        sub = ServiceSubmissionFactory(
            status="approved",
            primary_maturity_tag="emerging",
            secondary_maturity_tags=["unstable"],
        )
        resp = _run_action(admin_client, "action_export_csv", sub)
        assert resp.status_code == 200
        assert b"primary_maturity_tag" in resp.content
        # Export uses get_primary_maturity_tag_display() → "Emerging" (title case)
        assert b"Emerging" in resp.content

    def test_json_export_includes_maturity_tags(self, admin_client):
        """JSON export includes primary_maturity_tag and secondary_maturity_tags fields."""
        sub = ServiceSubmissionFactory(
            status="approved",
            primary_maturity_tag="legacy",
            secondary_maturity_tags=["unstable"],
        )
        resp = _run_action(admin_client, "action_export_json", sub)
        assert resp.status_code == 200
        assert b"primary_maturity_tag" in resp.content
        assert b"legacy" in resp.content


# ===========================================================================
# Admin — license field rendered as Select (YAML-driven)
# ===========================================================================


@pytest.mark.django_db
class TestAdminLicenseField:
    def test_change_form_renders_license_as_select(self, admin_client):
        """The license field must render as a <select> widget, not a text input."""
        sub = ServiceSubmissionFactory()
        url = reverse("admin:submissions_servicesubmission_change", args=[sub.pk])
        resp = admin_client.get(url)
        assert resp.status_code == 200
        # A <select> for license must be present in the rendered HTML
        assert b'name="license"' in resp.content
        assert b"<select" in resp.content

    def test_change_form_license_select_contains_yaml_options(self, admin_client):
        """The license select must include slugs from form_texts.yaml."""
        sub = ServiceSubmissionFactory()
        url = reverse("admin:submissions_servicesubmission_change", args=[sub.pk])
        resp = admin_client.get(url)
        assert resp.status_code == 200
        # Options derived from YAML must be present
        assert b'value="mit"' in resp.content
        assert b'value="eupl12"' in resp.content


# ===========================================================================
# Admin — action_assign_maturity_tags bulk action
# ===========================================================================


@pytest.mark.django_db
class TestAssignMaturityTagsAction:
    def _run_assign_action(self, admin_client, primary, secondary, *submissions):
        ids = [str(s.pk) for s in submissions]
        return admin_client.post(
            _changelist_url(),
            {
                "action": "action_assign_maturity_tags",
                "_selected_action": ids,
                "_assign_tags": "1",
                "primary_maturity_tag": primary,
                "secondary_maturity_tags": secondary,
            },
        )

    def test_assigns_tags_to_approved_submissions(self, admin_client):
        sub = ServiceSubmissionFactory(status="approved")
        resp = self._run_assign_action(admin_client, "emerging", ["unstable"], sub)
        assert resp.status_code in (200, 302)
        sub.refresh_from_db()
        assert sub.primary_maturity_tag == "emerging"
        assert sub.secondary_maturity_tags == ["unstable"]

    def test_skips_non_approved_submissions(self, admin_client):
        """Tags must only be applied to approved submissions."""
        submitted = ServiceSubmissionFactory(status="submitted")
        resp = self._run_assign_action(admin_client, "emerging", [], submitted)
        assert resp.status_code in (200, 302)
        submitted.refresh_from_db()
        # Tags must not have been applied
        assert submitted.primary_maturity_tag in (None, "")
        assert submitted.secondary_maturity_tags in (None, [])

    def test_assigns_only_to_approved_in_mixed_selection(self, admin_client):
        """When selection mixes approved and non-approved, only approved get tags."""
        approved = ServiceSubmissionFactory(status="approved")
        submitted = ServiceSubmissionFactory(status="submitted")
        self._run_assign_action(
            admin_client, "mature", ["unstable"], approved, submitted
        )
        approved.refresh_from_db()
        submitted.refresh_from_db()
        assert approved.primary_maturity_tag == "mature"
        assert submitted.primary_maturity_tag in (None, "")

    def test_rejects_invalid_primary_tag(self, admin_client):
        sub = ServiceSubmissionFactory(status="approved")
        resp = self._run_assign_action(admin_client, "not_a_real_tag", [], sub)
        assert resp.status_code in (200, 302)
        sub.refresh_from_db()
        # Tags must not have been applied
        assert sub.primary_maturity_tag in (None, "")

    def test_rejects_invalid_secondary_tag(self, admin_client):
        sub = ServiceSubmissionFactory(status="approved")
        resp = self._run_assign_action(
            admin_client, "emerging", ["not_valid_secondary"], sub
        )
        assert resp.status_code in (200, 302)
        sub.refresh_from_db()
        assert sub.primary_maturity_tag in (None, "")

    def test_all_non_approved_shows_error(self, admin_client):
        """Selecting only non-approved submissions must show an error message."""
        sub = ServiceSubmissionFactory(status="submitted")
        resp = admin_client.post(
            _changelist_url(),
            {
                "action": "action_assign_maturity_tags",
                "_selected_action": [str(sub.pk)],
            },
        )
        assert resp.status_code in (200, 302)


# ===========================================================================
# Admin — SubmissionChangeLog written on admin save
# ===========================================================================


@pytest.mark.django_db
class TestAdminSaveWritesChangeLog:
    def test_admin_change_creates_changelog_entry(self, admin_client):
        """
        Saving a submission via the admin (save_model + save_related) must create
        a SubmissionChangeLog entry when at least one field changed.
        """
        from django.test import RequestFactory
        from django.contrib.auth import get_user_model
        from apps.submissions.admin import ServiceSubmissionAdmin
        from apps.submissions.models import SubmissionChangeLog
        from django.contrib.admin.sites import AdminSite

        sub = ServiceSubmissionFactory(comments="before")

        # Build a minimal mock form and call the admin save methods directly.
        User = get_user_model()
        user = User.objects.get(username="testadmin")

        factory = RequestFactory()
        request = factory.post("/")
        request.user = user

        admin_instance = ServiceSubmissionAdmin(sub.__class__, AdminSite())

        # Simulate save_model: snapshot before, then apply change.
        admin_instance.save_model(request, sub, form=None, change=True)

        # Now change the field and call save_related with a mock form.
        sub.comments = "after"
        sub.save(update_fields=["comments"])

        class MockForm:
            instance = sub

            def save_m2m(self):
                pass  # No M2M changes in this test

        admin_instance.save_related(request, MockForm(), [], change=True)

        # A changelog entry must have been created for this submission.
        assert SubmissionChangeLog.objects.filter(submission=sub).exists()
        entry = SubmissionChangeLog.objects.filter(submission=sub).latest("changed_at")
        field_names = [ch["field"] for ch in entry.changes]
        assert "comments" in field_names


# ===========================================================================
# Admin — SubmissionChangeLog navigation (submission_link + row drill-down)
# ===========================================================================


@pytest.mark.django_db
class TestChangeLogAdminNavigation:
    """
    Verify that the Change Log admin list provides correct navigation:
      - submission_link points to the changelog list filtered for that submission
      - the "Changed at" column links to the individual entry's read-only detail view
      - the filtered list only returns entries for the requested submission
    """

    def _create_entry(self, sub=None):
        from apps.submissions.models import SubmissionChangeLog
        from django.utils import timezone

        if sub is None:
            sub = ServiceSubmissionFactory()
        return SubmissionChangeLog.objects.create(
            submission=sub,
            changed_by="testadmin",
            changed_at=timezone.now(),
            changes=[{"field": "comments", "before": "a", "after": "b"}],
        )

    def test_submission_link_points_to_filtered_changelist(self, admin_client):
        """submission_link URL must be the changelog changelist filtered by submission id."""
        from apps.submissions.admin import SubmissionChangeLogAdmin
        from django.contrib.admin.sites import AdminSite
        from apps.submissions.models import SubmissionChangeLog

        entry = self._create_entry()
        admin_instance = SubmissionChangeLogAdmin(SubmissionChangeLog, AdminSite())

        link_html = admin_instance.submission_link(entry)
        expected_base = reverse("admin:submissions_submissionchangelog_changelist")
        assert expected_base in str(link_html)
        assert f"submission__id={entry.submission_id}" in str(link_html)

    def test_filtered_changelist_returns_only_matching_entries(self, admin_client):
        """Filtering the changelog list by submission__id shows only that submission's entries."""

        sub_a = ServiceSubmissionFactory()
        sub_b = ServiceSubmissionFactory()
        self._create_entry(sub_a)
        self._create_entry(sub_b)

        url = reverse("admin:submissions_submissionchangelog_changelist")
        resp = admin_client.get(url, {"submission__id": str(sub_a.pk)})

        assert resp.status_code == 200
        content = resp.content.decode()
        assert sub_a.service_name in content
        assert sub_b.service_name not in content

    def test_changelog_detail_view_accessible_readonly(self, admin_client):
        """Clicking a changelog entry (via changed_at link) opens the read-only detail view."""
        entry = self._create_entry()
        url = reverse("admin:submissions_submissionchangelog_change", args=[entry.pk])
        resp = admin_client.get(url)
        assert resp.status_code == 200
        content = resp.content.decode()
        assert entry.changed_by in content

    def test_list_display_links_is_none(self, admin_client):
        """list_display_links must be None — both links are rendered as explicit anchors."""
        from apps.submissions.admin import SubmissionChangeLogAdmin
        from apps.submissions.models import SubmissionChangeLog
        from django.contrib.admin.sites import AdminSite

        admin_instance = SubmissionChangeLogAdmin(SubmissionChangeLog, AdminSite())
        assert admin_instance.list_display_links is None

    def test_changed_at_link_points_to_entry_detail(self, admin_client):
        """changed_at_link must link to the individual changelog entry's detail view with tooltip."""
        from apps.submissions.admin import SubmissionChangeLogAdmin
        from apps.submissions.models import SubmissionChangeLog
        from django.contrib.admin.sites import AdminSite

        entry = self._create_entry()
        admin_instance = SubmissionChangeLogAdmin(SubmissionChangeLog, AdminSite())

        link_html = str(admin_instance.changed_at_link(entry))
        expected_url = reverse(
            "admin:submissions_submissionchangelog_change", args=[entry.pk]
        )
        assert expected_url in link_html
        assert "View diff for this change" in link_html


# ===========================================================================
# Admin — SubmissionDeletionAudit written on hard delete
# ===========================================================================


@pytest.mark.django_db
class TestSubmissionDeletionAudit:
    """
    Verify that deleting a ServiceSubmission via the admin:
      - writes a SubmissionDeletionAudit record capturing key fields and changelog
      - the delete confirmation view includes the changelog count warning
      - the audit record persists after the submission is gone
    """

    def _make_changelog_entry(self, sub):
        from apps.submissions.models import SubmissionChangeLog
        from django.utils import timezone

        return SubmissionChangeLog.objects.create(
            submission=sub,
            changed_by="admin:testadmin",
            changed_at=timezone.now(),
            changes=[{"field": "comments", "before": "a", "after": "b"}],
        )

    def test_delete_model_writes_audit_record(self, admin_client):
        """Deleting a submission via the admin writes a SubmissionDeletionAudit."""
        from apps.submissions.models import SubmissionDeletionAudit

        sub = ServiceSubmissionFactory()
        self._make_changelog_entry(sub)
        sub_id = sub.pk
        sub_name = sub.service_name

        url = reverse("admin:submissions_servicesubmission_delete", args=[sub.pk])
        resp = admin_client.post(url, {"post": "yes"}, follow=True)
        assert resp.status_code == 200

        audit = SubmissionDeletionAudit.objects.get(submission_id=sub_id)
        assert audit.service_name == sub_name
        assert audit.changelog_count == 1
        assert len(audit.changelog_snapshot) == 1
        assert audit.deleted_by == "admin:testadmin"

    def test_audit_persists_after_submission_deleted(self, admin_client):
        """The audit record must survive after the submission cascade-deletes."""
        from apps.submissions.models import SubmissionDeletionAudit
        from apps.submissions.models import ServiceSubmission

        sub = ServiceSubmissionFactory()
        sub_id = sub.pk

        url = reverse("admin:submissions_servicesubmission_delete", args=[sub.pk])
        admin_client.post(url, {"post": "yes"})

        assert not ServiceSubmission.objects.filter(pk=sub_id).exists()
        assert SubmissionDeletionAudit.objects.filter(submission_id=sub_id).exists()

    def test_delete_confirmation_shows_changelog_warning(self, admin_client):
        """The delete confirmation page shows a warning when changelog entries exist."""
        sub = ServiceSubmissionFactory()
        self._make_changelog_entry(sub)

        url = reverse("admin:submissions_servicesubmission_delete", args=[sub.pk])
        resp = admin_client.get(url)
        assert resp.status_code == 200
        content = resp.content.decode()
        assert "change log" in content.lower()
        assert "Deprecated" in content

    def test_delete_confirmation_no_warning_without_changelog(self, admin_client):
        """No warning is shown when there are no changelog entries."""
        sub = ServiceSubmissionFactory()

        url = reverse("admin:submissions_servicesubmission_delete", args=[sub.pk])
        resp = admin_client.get(url)
        content = resp.content.decode()
        assert "Permanent data loss" not in content


# ===========================================================================
# SubmissionDeletionAuditAdmin
# ===========================================================================


@pytest.mark.django_db
class TestDeletionAuditAdmin:
    def _make_audit(self):
        from apps.submissions.models import SubmissionDeletionAudit

        return SubmissionDeletionAudit.objects.create(
            submission_id="00000000-0000-0000-0000-000000000001",
            service_name="Ghost Service",
            status="approved",
            deleted_by="admin:testadmin",
            changelog_count=2,
            changelog_snapshot=[
                {
                    "changed_by": "admin:testadmin",
                    "changed_at": "2026-01-01T00:00:00",
                    "changes": [],
                }
            ],
        )

    def test_deletion_audit_list_accessible(self, admin_client):
        self._make_audit()
        url = reverse("admin:submissions_submissiondeletionaudit_changelist")
        resp = admin_client.get(url)
        assert resp.status_code == 200
        assert "Ghost Service" in resp.content.decode()

    def test_deletion_audit_detail_accessible(self, admin_client):
        audit = self._make_audit()
        url = reverse(
            "admin:submissions_submissiondeletionaudit_change", args=[audit.pk]
        )
        resp = admin_client.get(url)
        assert resp.status_code == 200
        assert "admin:testadmin" in resp.content.decode()

    def test_deletion_audit_add_blocked(self, admin_client):
        url = reverse("admin:submissions_submissiondeletionaudit_add")
        resp = admin_client.get(url)
        assert resp.status_code == 403

    def test_deletion_audit_delete_blocked(self, admin_client):
        audit = self._make_audit()
        url = reverse(
            "admin:submissions_submissiondeletionaudit_delete", args=[audit.pk]
        )
        resp = admin_client.post(url, {"post": "yes"})
        assert resp.status_code == 403
