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
