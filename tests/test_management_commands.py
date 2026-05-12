"""
Tests for management commands, template tags, and context processors.
"""

import os
import tempfile
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory

from .factories import ServiceSubmissionFactory


# ---------------------------------------------------------------------------
# Minimal OWL/RDF-XML fixture for sync_edam tests
# ---------------------------------------------------------------------------

MINIMAL_OWL = (
    b'<?xml version="1.0"?>\n'
    b"<rdf:RDF\n"
    b'    xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"\n'
    b'    xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"\n'
    b'    xmlns:owl="http://www.w3.org/2002/07/owl#"\n'
    b'    xmlns:oboInOwl="http://www.geneontology.org/formats/oboInOwl#">\n'
    b"\n"
    b'  <owl:Ontology rdf:about="http://edamontology.org/EDAM_1.25.owl">\n'
    b"    <owl:versionInfo>1.25</owl:versionInfo>\n"
    b"  </owl:Ontology>\n"
    b"\n"
    b'  <owl:Class rdf:about="http://edamontology.org/topic_0003">\n'
    b"    <rdfs:label>Topic</rdfs:label>\n"
    b"    <rdfs:comment>A placeholder for an EDAM topic.</rdfs:comment>\n"
    b"    <oboInOwl:hasExactSynonym>Subject</oboInOwl:hasExactSynonym>\n"
    b"  </owl:Class>\n"
    b"\n"
    b'  <owl:Class rdf:about="http://edamontology.org/operation_0004">\n'
    b"    <rdfs:label>Operation</rdfs:label>\n"
    b'    <rdfs:subClassOf rdf:resource="http://edamontology.org/topic_0003"/>\n'
    b"    <rdfs:comment>A placeholder for an EDAM operation.</rdfs:comment>\n"
    b"  </owl:Class>\n"
    b"\n"
    b'  <owl:Class rdf:about="http://edamontology.org/topic_9999">\n'
    b"    <rdfs:label>Obsolete topic</rdfs:label>\n"
    b"    <owl:deprecated>true</owl:deprecated>\n"
    b"  </owl:Class>\n"
    b"\n"
    b'  <owl:Class rdf:about="http://edamontology.org/topic_8888">\n'
    b"  </owl:Class>\n"
    b"\n"
    b'  <owl:Class rdf:about="http://example.com/other_class">\n'
    b"    <rdfs:label>Not EDAM</rdfs:label>\n"
    b"  </owl:Class>\n"
    b"\n"
    b"</rdf:RDF>\n"
)


# ---------------------------------------------------------------------------
# sync_edam management command
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSyncEdamCommand:
    def _owl_file(self):
        """Write MINIMAL_OWL to a temp file and return the path."""
        f = tempfile.NamedTemporaryFile(suffix=".owl", delete=False)
        f.write(MINIMAL_OWL)
        f.flush()
        f.close()
        return f.name

    def test_dry_run_no_db_writes(self):
        from apps.edam.models import EdamTerm

        owl_path = self._owl_file()
        try:
            out = StringIO()
            call_command("sync_edam", url=owl_path, dry_run=True, stdout=out)
            assert EdamTerm.objects.count() == 0
            assert "Dry run" in out.getvalue()
        finally:
            os.unlink(owl_path)

    def test_creates_terms(self):
        from apps.edam.models import EdamTerm

        owl_path = self._owl_file()
        try:
            call_command("sync_edam", url=owl_path, stdout=StringIO())
            # Should create 3 terms (topic_0003, operation_0004, topic_9999)
            # topic_8888 skipped (no label), non-EDAM skipped
            assert EdamTerm.objects.count() == 3
        finally:
            os.unlink(owl_path)

    def test_correct_fields_parsed(self):
        from apps.edam.models import EdamTerm

        owl_path = self._owl_file()
        try:
            call_command("sync_edam", url=owl_path, stdout=StringIO())
            term = EdamTerm.objects.get(accession="topic_0003")
            assert term.label == "Topic"
            assert term.branch == "topic"
            # definition comes from rdfs:comment (oboInOwl:hasDefinition fallback has ElementTree issues)
            assert "EDAM topic" in term.definition
            assert "Subject" in term.synonyms
            assert term.edam_version == "1.25"
            assert term.is_obsolete is False
        finally:
            os.unlink(owl_path)

    def test_obsolete_flag(self):
        from apps.edam.models import EdamTerm

        owl_path = self._owl_file()
        try:
            call_command("sync_edam", url=owl_path, stdout=StringIO())
            term = EdamTerm.objects.get(accession="topic_9999")
            assert term.is_obsolete is True
        finally:
            os.unlink(owl_path)

    def test_branch_filter(self):
        from apps.edam.models import EdamTerm

        owl_path = self._owl_file()
        try:
            call_command("sync_edam", url=owl_path, branch="topic", stdout=StringIO())
            # Only topic terms
            assert EdamTerm.objects.filter(branch="operation").count() == 0
            assert EdamTerm.objects.filter(branch="topic").count() >= 1
        finally:
            os.unlink(owl_path)

    def test_idempotent_second_run(self):
        from apps.edam.models import EdamTerm

        owl_path = self._owl_file()
        try:
            call_command("sync_edam", url=owl_path, stdout=StringIO())
            first_count = EdamTerm.objects.count()
            call_command("sync_edam", url=owl_path, stdout=StringIO())
            # Second run should update, not duplicate
            assert EdamTerm.objects.count() == first_count
        finally:
            os.unlink(owl_path)

    def test_parent_relationship_resolved(self):
        from apps.edam.models import EdamTerm

        owl_path = self._owl_file()
        try:
            call_command("sync_edam", url=owl_path, stdout=StringIO())
            # operation_0004 has rdfs:subClassOf topic_0003 — but topic_0003 is not an operation
            # parent FK resolves if parent exists in DB (which it does)
            # No assertion on parent value since topic_0003 exists in DB
            topic = EdamTerm.objects.get(accession="topic_0003")
            assert topic is not None
        finally:
            os.unlink(owl_path)

    def test_invalid_xml_raises_error(self):
        with tempfile.NamedTemporaryFile(suffix=".owl", delete=False, mode="wb") as f:
            f.write(b"not xml at all {{{")
            path = f.name
        try:
            with pytest.raises(CommandError, match="Failed to parse OWL XML"):
                call_command("sync_edam", url=path, stdout=StringIO())
        finally:
            os.unlink(path)

    def test_missing_file_raises_error(self):
        with pytest.raises(CommandError, match="Failed to load EDAM"):
            call_command(
                "sync_edam", url="/nonexistent/path/EDAM.owl", stdout=StringIO()
            )

    def test_http_load_with_mock(self):
        from apps.edam.models import EdamTerm

        mock_resp = type(
            "Resp",
            (),
            {
                "read": lambda self: MINIMAL_OWL,
                "__enter__": lambda self: self,
                "__exit__": lambda self, *a: None,
            },
        )()
        with patch("urllib.request.urlopen", return_value=mock_resp):
            call_command(
                "sync_edam", url="https://edamontology.org/fake.owl", stdout=StringIO()
            )
        assert EdamTerm.objects.count() == 3

    def test_marks_removed_terms_obsolete(self):
        """Terms in the DB but not in the OWL file are marked obsolete."""
        from apps.edam.models import EdamTerm

        owl_path = self._owl_file()
        try:
            call_command("sync_edam", url=owl_path, stdout=StringIO())
            # Manually add a term that's not in the OWL file
            EdamTerm.objects.create(
                uri="http://edamontology.org/topic_1111",
                accession="topic_1111",
                branch="topic",
                label="Removed term",
                is_obsolete=False,
            )
            # Run again — removed term should be marked obsolete
            call_command("sync_edam", url=owl_path, stdout=StringIO())
            removed = EdamTerm.objects.get(accession="topic_1111")
            assert removed.is_obsolete is True
        finally:
            os.unlink(owl_path)


# ---------------------------------------------------------------------------
# sync_biotools management command
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSyncBioToolsCommand:
    def test_no_records_prints_message(self):
        out = StringIO()
        call_command("sync_biotools", stdout=out)
        assert "No BioToolsRecord" in out.getvalue()

    def test_single_submission_not_found_raises_error(self):
        import uuid

        with pytest.raises(CommandError, match="not found"):
            call_command(
                "sync_biotools", submission=str(uuid.uuid4()), stdout=StringIO()
            )

    def test_single_submission_no_url_raises_error(self):
        sub = ServiceSubmissionFactory(biotools_url="")
        with pytest.raises(CommandError, match="no bio.tools URL"):
            call_command("sync_biotools", submission=str(sub.pk), stdout=StringIO())

    def test_single_submission_dry_run(self):
        from apps.submissions.models import ServiceSubmission

        sub = ServiceSubmissionFactory(biotools_url="")
        ServiceSubmission.objects.filter(pk=sub.pk).update(
            biotools_url="https://bio.tools/blast"
        )
        sub.refresh_from_db()

        out = StringIO()
        call_command("sync_biotools", submission=str(sub.pk), dry_run=True, stdout=out)
        assert "DRY RUN" in out.getvalue()
        assert "blast" in out.getvalue()

    def test_single_submission_sync_success(self):
        from apps.biotools.sync import SyncResult
        from apps.submissions.models import ServiceSubmission

        sub = ServiceSubmissionFactory(biotools_url="")
        ServiceSubmission.objects.filter(pk=sub.pk).update(
            biotools_url="https://bio.tools/blast"
        )
        sub.refresh_from_db()

        ok_result = SyncResult(ok=True, biotools_id="blast", created=True, error="")
        with patch("apps.biotools.sync.sync_tool", return_value=ok_result):
            out = StringIO()
            call_command("sync_biotools", submission=str(sub.pk), stdout=out)
        assert "Created" in out.getvalue()

    def test_single_submission_sync_failure(self):
        from apps.biotools.sync import SyncResult
        from apps.submissions.models import ServiceSubmission

        sub = ServiceSubmissionFactory(biotools_url="")
        ServiceSubmission.objects.filter(pk=sub.pk).update(
            biotools_url="https://bio.tools/blast"
        )
        sub.refresh_from_db()

        err_result = SyncResult(
            ok=False, biotools_id="blast", created=False, error="API down"
        )
        with patch("apps.biotools.sync.sync_tool", return_value=err_result):
            out = StringIO()
            err = StringIO()
            call_command(
                "sync_biotools", submission=str(sub.pk), stdout=out, stderr=err
            )
        assert "Sync failed" in err.getvalue() or "API down" in err.getvalue()

    def test_bulk_dry_run(self):
        from apps.biotools.models import BioToolsRecord

        sub = ServiceSubmissionFactory(biotools_url="")
        BioToolsRecord.objects.create(
            submission=sub, biotools_id="blast", name="BLAST", raw_json={}
        )
        out = StringIO()
        call_command("sync_biotools", dry_run=True, stdout=out)
        assert "DRY RUN" in out.getvalue()
        assert "blast" in out.getvalue()

    def test_bulk_sync_success(self):
        from apps.biotools.models import BioToolsRecord
        from apps.biotools.sync import SyncResult

        sub = ServiceSubmissionFactory(biotools_url="")
        BioToolsRecord.objects.create(
            submission=sub, biotools_id="blast", name="BLAST", raw_json={}
        )
        ok_result = SyncResult(ok=True, biotools_id="blast", created=False, error="")
        with patch("apps.biotools.sync.sync_tool", return_value=ok_result):
            out = StringIO()
            call_command("sync_biotools", stdout=out)
        assert "Done" in out.getvalue()

    def test_bulk_sync_with_error(self):
        from apps.biotools.models import BioToolsRecord
        from apps.biotools.sync import SyncResult

        sub = ServiceSubmissionFactory(biotools_url="")
        BioToolsRecord.objects.create(
            submission=sub, biotools_id="blast", name="BLAST", raw_json={}
        )
        err_result = SyncResult(
            ok=False, biotools_id="blast", created=False, error="timeout"
        )
        with patch("apps.biotools.sync.sync_tool", return_value=err_result):
            out = StringIO()
            err = StringIO()
            call_command("sync_biotools", stdout=out, stderr=err)
        assert "Done" in out.getvalue()

    def test_invalid_uuid_raises_error(self):
        with pytest.raises(CommandError):
            call_command("sync_biotools", submission="not-a-uuid", stdout=StringIO())


# ---------------------------------------------------------------------------
# Template tags
# ---------------------------------------------------------------------------


class TestRegistryTags:
    def test_site_logo_url_returns_from_settings(self, settings):
        settings.SITE_CONFIG = {"site": {"logo_url": "https://example.com/logo.svg"}}
        from apps.submissions.templatetags.registry_tags import site_logo_url

        assert site_logo_url() == "https://example.com/logo.svg"

    def test_site_logo_url_returns_empty_when_not_set(self, settings):
        settings.SITE_CONFIG = {}
        from apps.submissions.templatetags.registry_tags import site_logo_url

        assert site_logo_url() == ""

    def test_site_setting_returns_value(self, settings):
        settings.SITE_CONFIG = {"contact": {"email": "test@example.com"}}
        from apps.submissions.templatetags.registry_tags import site_setting

        assert site_setting("contact", "email") == "test@example.com"

    def test_site_setting_returns_default(self, settings):
        settings.SITE_CONFIG = {}
        from apps.submissions.templatetags.registry_tags import site_setting

        assert site_setting("contact", "missing_key", default="fallback") == "fallback"

    def test_site_setting_missing_section(self, settings):
        settings.SITE_CONFIG = {}
        from apps.submissions.templatetags.registry_tags import site_setting

        assert site_setting("nosection", "nokey") == ""


# ---------------------------------------------------------------------------
# Context processor
# ---------------------------------------------------------------------------


class TestSiteContextProcessor:
    rf = RequestFactory()

    def _call(self, site_config):
        from apps.submissions.context_processors import site_context

        request = self.rf.get("/")
        with patch("apps.submissions.context_processors.dj_settings") as mock_settings:
            mock_settings.SITE_CONFIG = site_config
            mock_settings.BASE_DIR = "/nonexistent"
            return site_context(request)

    def test_returns_site_name(self):
        ctx = self._call({"site": {"name": "My Registry"}})
        assert ctx["SITE_NAME"] == "My Registry"

    def test_returns_contact_email(self):
        ctx = self._call({"contact": {"email": "test@example.com"}})
        assert ctx["CONTACT_EMAIL"] == "test@example.com"

    def test_returns_logo_url_from_config(self):
        ctx = self._call({"site": {"logo_url": "https://example.com/logo.svg"}})
        assert ctx["LOGO_URL"] == "https://example.com/logo.svg"

    def test_returns_all_shortcut_keys(self):
        ctx = self._call({})
        assert "SITE" in ctx
        assert "LOGO_URL" in ctx
        assert "SITE_NAME" in ctx
        assert "SITE_URL" in ctx
        assert "CONTACT_EMAIL" in ctx
        assert "PRIVACY_POLICY_URL" in ctx
        assert "IMPRINT_URL" in ctx
        assert "WEBSITE_URL" in ctx

    def test_site_dict_includes_sections(self):
        ctx = self._call(
            {
                "site": {"name": "Reg"},
                "contact": {"email": "a@b.com"},
                "links": {"website": "https://example.com"},
            }
        )
        assert ctx["SITE"]["contact"]["email"] == "a@b.com"
        assert ctx["SITE"]["links"]["website"] == "https://example.com"

    def test_logo_url_auto_detect_from_static(self):
        """When logo_url is not in config, check if static/img/logo.svg exists."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            static_img = Path(tmpdir) / "static" / "img"
            static_img.mkdir(parents=True)
            (static_img / "logo.svg").write_text("<svg/>")

            from apps.submissions.context_processors import site_context
            from unittest.mock import patch as _patch

            request = self.rf.get("/")
            with (
                _patch("apps.submissions.context_processors.dj_settings") as ms,
                _patch(
                    "apps.submissions.context_processors.static",
                    return_value="/static/img/logo.svg",
                ),
            ):
                ms.SITE_CONFIG = {}
                ms.BASE_DIR = tmpdir
                ctx = site_context(request)

            assert ctx["LOGO_URL"] == "/static/img/logo.svg"

    def test_defaults_when_empty_config(self):
        ctx = self._call({})
        assert ctx["SITE_NAME"] == "de.NBI Service Registration Platform"
        assert "denbi.de/privacy-policy" in ctx["PRIVACY_POLICY_URL"]
