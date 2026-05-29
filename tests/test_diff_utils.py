"""
Diff Utility Tests
==================
Unit tests for apps/submissions/diff_utils.py.

All tests are pure (no database required) unless they create model instances.
"""

import pytest

from apps.submissions.diff_utils import (
    DIFFABLE_FIELDS,
    DIFFABLE_M2M,
    _display,
    build_diff,
    filter_sanitization_artifacts,
    snapshot,
    snapshot_m2m,
)


# ---------------------------------------------------------------------------
# _display helper
# ---------------------------------------------------------------------------


class TestDisplay:
    def test_string_value(self):
        assert _display("hello") == "hello"

    def test_empty_string(self):
        assert _display("") == "—"

    def test_none(self):
        assert _display(None) == "—"

    def test_non_empty_list(self):
        assert _display(["Beta", "Alpha"]) == "Beta, Alpha"

    def test_empty_list(self):
        assert _display([]) == "—"

    def test_integer(self):
        assert _display(2020) == "2020"


# ---------------------------------------------------------------------------
# build_diff
# ---------------------------------------------------------------------------


class TestBuildDiff:
    def test_no_changes(self):
        before = {"service_name": "Tool A", "year_established": "2020"}
        after = {"service_name": "Tool A", "year_established": "2020"}
        assert build_diff(before, after) == []

    def test_single_scalar_change(self):
        before = {"service_name": "Tool A"}
        after = {"service_name": "Tool B"}
        diff = build_diff(before, after)
        assert len(diff) == 1
        assert diff[0]["field"] == "service_name"
        assert diff[0]["label"] == "Service Name"
        assert diff[0]["old"] == "Tool A"
        assert diff[0]["new"] == "Tool B"

    def test_multiple_changes(self):
        before = {"service_name": "A", "year_established": "2018", "comments": "old"}
        after = {"service_name": "B", "year_established": "2018", "comments": "new"}
        diff = build_diff(before, after)
        fields = {ch["field"] for ch in diff}
        assert fields == {"service_name", "comments"}

    def test_m2m_list_change(self):
        before = {"service_categories": ["Cat A", "Cat B"]}
        after = {"service_categories": ["Cat A", "Cat C"]}
        diff = build_diff(before, after)
        assert len(diff) == 1
        assert diff[0]["field"] == "service_categories"

    def test_m2m_no_change(self):
        before = {"service_categories": ["Cat A", "Cat B"]}
        after = {"service_categories": ["Cat A", "Cat B"]}
        assert build_diff(before, after) == []

    def test_missing_key_in_one_snapshot_is_skipped(self):
        """Keys absent from either snapshot are silently skipped."""
        before = {"service_name": "A"}
        after = {"service_name": "B", "extra_key": "x"}
        diff = build_diff(before, after)
        fields = {ch["field"] for ch in diff}
        assert "extra_key" not in fields

    def test_old_value_displayed_as_dash_when_empty(self):
        before = {"github_url": ""}
        after = {"github_url": "https://github.com/org/repo"}
        diff = build_diff(before, after)
        assert diff[0]["old"] == "—"
        assert diff[0]["new"] == "https://github.com/org/repo"

    def test_result_sorted_by_field_name(self):
        before = {"comments": "a", "service_name": "x", "year_established": "2019"}
        after = {"comments": "b", "service_name": "y", "year_established": "2020"}
        diff = build_diff(before, after)
        field_names = [ch["field"] for ch in diff]
        assert field_names == sorted(field_names)

    def test_unknown_field_gets_title_case_label(self):
        before = {"my_custom_field": "a"}
        after = {"my_custom_field": "b"}
        diff = build_diff(before, after)
        assert diff[0]["label"] == "My Custom Field"


# ---------------------------------------------------------------------------
# snapshot / snapshot_m2m — require a real model instance
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSnapshot:
    def test_snapshot_returns_expected_keys(self, db):
        from tests.factories import ServiceSubmissionFactory

        sub = ServiceSubmissionFactory()
        snap = snapshot(sub)
        expected_keys = {f for f, _ in DIFFABLE_FIELDS}
        assert expected_keys == set(snap.keys())

    def test_snapshot_choice_field_uses_display_value(self, db):
        from tests.factories import ServiceSubmissionFactory

        sub = ServiceSubmissionFactory(status="submitted")
        snap = snapshot(sub)
        assert snap["status"] == "Submitted"

    def test_snapshot_fk_uses_str(self, db):
        from tests.factories import ServiceSubmissionFactory

        sub = ServiceSubmissionFactory()
        snap = snapshot(sub)
        assert snap["service_center"] == str(sub.service_center)

    def test_snapshot_bool_field_yes_no(self, db):
        from tests.factories import ServiceSubmissionFactory

        sub = ServiceSubmissionFactory(
            register_as_elixir=True, survey_participation=False
        )
        snap = snapshot(sub)
        assert snap["register_as_elixir"] == "Yes"
        assert snap["survey_participation"] == "No"

    def test_snapshot_blank_field_is_empty_string(self, db):
        from tests.factories import ServiceSubmissionFactory

        sub = ServiceSubmissionFactory(github_url="", comments="")
        snap = snapshot(sub)
        assert snap["github_url"] == ""
        assert snap["comments"] == ""

    def test_snapshot_strips_text_field_whitespace(self, db):
        """Trailing/leading whitespace in DB values must not cause false diffs."""
        from apps.submissions.models import ServiceSubmission
        from tests.factories import ServiceSubmissionFactory

        sub = ServiceSubmissionFactory()
        # Bypass save() so the value in the DB has leading/trailing whitespace,
        # simulating a row that was inserted before sanitisation was added.
        ServiceSubmission.objects.filter(pk=sub.pk).update(
            service_description="  some description  "
        )
        sub.refresh_from_db()
        snap = snapshot(sub)
        assert snap["service_description"] == "some description"

    def test_snapshot_submitter_fields_present(self, db):
        from tests.factories import ServiceSubmissionFactory

        sub = ServiceSubmissionFactory(
            submitter_first_name="Ada",
            submitter_last_name="Lovelace",
            submitter_affiliation="Uni",
        )
        snap = snapshot(sub)
        assert snap["submitter_first_name"] == "Ada"
        assert snap["submitter_last_name"] == "Lovelace"
        assert snap["submitter_affiliation"] == "Uni"

    def test_submitter_name_change_appears_in_diff(self, db):
        from tests.factories import ServiceSubmissionFactory

        sub = ServiceSubmissionFactory(submitter_first_name="Before")
        before = snapshot(sub)
        sub.submitter_first_name = "After"
        sub.save()
        after = snapshot(sub)
        diff = build_diff(before, after)
        fields = {ch["field"] for ch in diff}
        assert "submitter_first_name" in fields

    def test_snapshot_m2m_returns_sorted_list(self, db):
        from tests.factories import ServiceCategoryFactory, ServiceSubmissionFactory

        cat_b = ServiceCategoryFactory(name="Bioinformatics")
        cat_a = ServiceCategoryFactory(name="Analysis")
        sub = ServiceSubmissionFactory(service_categories=[cat_a, cat_b])
        snap = snapshot_m2m(sub)
        expected_keys = {f for f, _ in DIFFABLE_M2M}
        assert expected_keys == set(snap.keys())
        assert snap["service_categories"] == sorted(["Bioinformatics", "Analysis"])

    def test_snapshot_m2m_empty_relation(self, db):
        from tests.factories import ServiceSubmissionFactory

        sub = ServiceSubmissionFactory()
        sub.edam_topics.clear()
        snap = snapshot_m2m(sub)
        assert snap["edam_topics"] == []


@pytest.mark.django_db
class TestSnapshotRoundtrip:
    """Integration: snapshot before/after an edit produces the correct diff."""

    def test_full_roundtrip(self, db):
        from tests.factories import ServiceSubmissionFactory

        sub = ServiceSubmissionFactory(service_name="Original Name", github_url="")
        before = {**snapshot(sub), **snapshot_m2m(sub)}

        sub.service_name = "New Name"
        sub.github_url = "https://github.com/org/repo"
        sub.save()

        after = {**snapshot(sub), **snapshot_m2m(sub)}
        diff = build_diff(before, after)

        fields = {ch["field"] for ch in diff}
        assert "service_name" in fields
        assert "github_url" in fields

        name_ch = next(ch for ch in diff if ch["field"] == "service_name")
        assert name_ch["old"] == "Original Name"
        assert name_ch["new"] == "New Name"


# ---------------------------------------------------------------------------
# File fields (_FILE_FIELDS) — logo
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFileFieldSnapshot:
    def test_logo_no_file_returns_empty(self, db):
        from tests.factories import ServiceSubmissionFactory

        sub = ServiceSubmissionFactory(logo=None)
        snap = snapshot(sub)
        assert snap["logo"] == ""

    def test_logo_with_file_returns_basename(self, db, tmp_path, settings):
        from django.core.files.base import ContentFile
        from tests.factories import ServiceSubmissionFactory

        settings.MEDIA_ROOT = str(tmp_path)
        sub = ServiceSubmissionFactory()
        sub.logo.save("test_logo.png", ContentFile(b"fake"), save=True)
        snap = snapshot(sub)
        # The model renames uploads to a UUID; check it's a non-empty .png basename
        assert snap["logo"] != ""
        assert snap["logo"].endswith(".png")
        assert "/" not in snap["logo"]

    def test_logo_change_appears_in_diff(self, db, tmp_path, settings):
        from django.core.files.base import ContentFile
        from tests.factories import ServiceSubmissionFactory

        settings.MEDIA_ROOT = str(tmp_path)
        sub = ServiceSubmissionFactory(logo=None)
        before = snapshot(sub)

        sub.logo.save("mylogo.png", ContentFile(b"data"), save=True)
        after = snapshot(sub)

        diff = build_diff(before, after)
        fields = {ch["field"] for ch in diff}
        assert "logo" in fields


# ---------------------------------------------------------------------------
# Model.clean() — service_description max length
# ---------------------------------------------------------------------------


class TestServiceDescriptionModelValidation:
    def test_description_over_5000_chars_raises(self):
        from tests.factories import ServiceSubmissionFactory
        from django.core.exceptions import ValidationError

        sub = ServiceSubmissionFactory.build(service_description="x" * 5001)
        with pytest.raises(ValidationError) as exc:
            sub.clean()
        assert "service_description" in str(exc.value)

    def test_description_exactly_5000_chars_is_valid(self):
        from tests.factories import ServiceSubmissionFactory

        sub = ServiceSubmissionFactory.build(service_description="x" * 5000)
        sub.clean()  # must not raise


# ---------------------------------------------------------------------------
# SubmissionChangeLog write paths
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSubmissionChangeLogWrites:
    def _setup_session(self, client, sub):
        """Authenticate the test client for EditView by seeding the session."""
        from tests.factories import APIKeyFactory

        key_obj, _ = APIKeyFactory.create_with_plaintext(submission=sub)
        session = client.session
        session["edit_key_id"] = str(key_obj.pk)
        session["edit_submission_id"] = str(sub.pk)
        session.save()

    def _edit_data(self, sub, **overrides):
        """Build a complete valid POST payload for EditView."""
        data = {
            "date_of_entry": sub.date_of_entry.isoformat(),
            "submitter_first_name": sub.submitter_first_name,
            "submitter_last_name": sub.submitter_last_name,
            "submitter_affiliation": sub.submitter_affiliation,
            "register_as_elixir": str(sub.register_as_elixir),
            "service_name": sub.service_name,
            "service_description": sub.service_description,
            "year_established": sub.year_established,
            "service_categories": [c.pk for c in sub.service_categories.all()],
            "is_toolbox": str(sub.is_toolbox),
            "toolbox_name": sub.toolbox_name or "",
            "user_knowledge_required": sub.user_knowledge_required or "",
            "publications_pmids": sub.publications_pmids or "",
            "responsible_pis": [p.pk for p in sub.responsible_pis.all()],
            "associated_partner_note": sub.associated_partner_note or "",
            "host_institute": sub.host_institute,
            "service_center": sub.service_center.pk,
            "public_contact_email": sub.public_contact_email,
            "internal_contact_name": sub.internal_contact_name,
            "internal_contact_email": sub.internal_contact_email,
            "internal_contact_email_confirm": sub.internal_contact_email,
            "website_url": sub.website_url,
            "terms_of_use_url": sub.terms_of_use_url,
            "licenses": [lic.pk for lic in sub.licenses.all()],
            # For M2M licenses, at least one license or non-empty license_note must be provided
            # If submission has licenses, use them; otherwise use an empty list (form validation will require one)
            "license_note": sub.license_note
            if sub.license_note
            else "No license specified",
            "github_url": sub.github_url or "",
            "biotools_url": sub.biotools_url or "",
            "fairsharing_url": sub.fairsharing_url or "",
            "other_registry_url": sub.other_registry_url or "",
            "kpi_monitoring": sub.kpi_monitoring,
            "kpi_start_year": sub.kpi_start_year or "",
            "keywords_uncited": sub.keywords_uncited or "",
            "keywords_seo": sub.keywords_seo or "",
            "survey_participation": str(sub.survey_participation),
            "comments": sub.comments or "",
            "data_protection_consent": "True",
        }
        data.update(overrides)
        return data

    def test_web_form_edit_writes_log_entry(self, client):
        """EditView.post() creates a SubmissionChangeLog row when fields change."""
        from apps.submissions.models import SubmissionChangeLog
        from tests.factories import ServiceSubmissionFactory

        sub = ServiceSubmissionFactory(service_name="Before")
        self._setup_session(client, sub)
        data = self._edit_data(sub, service_name="After")

        resp = client.post("/update/edit/", data)
        assert resp.status_code == 302
        assert SubmissionChangeLog.objects.filter(submission=sub).exists()
        entry = SubmissionChangeLog.objects.filter(submission=sub).first()
        assert entry.changed_by == "submitter"
        fields_changed = {ch["field"] for ch in entry.changes}
        assert "service_name" in fields_changed

    def test_no_change_does_not_write_log_entry(self, client):
        """EditView.post() with no actual changes must not write a log entry."""
        from apps.submissions.models import SubmissionChangeLog
        from tests.factories import ServiceSubmissionFactory

        sub = ServiceSubmissionFactory()
        self._setup_session(client, sub)
        data = self._edit_data(sub)

        client.post("/update/edit/", data)
        assert not SubmissionChangeLog.objects.filter(submission=sub).exists()

    def test_api_patch_writes_log_entry(self):
        """API PATCH creates a SubmissionChangeLog row when fields change."""
        from rest_framework.test import APIClient
        from tests.factories import APIKeyFactory, ServiceSubmissionFactory
        from apps.submissions.models import SubmissionChangeLog

        sub = ServiceSubmissionFactory(service_name="Before")
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)

        api = APIClient()
        resp = api.patch(
            f"/api/v1/submissions/{sub.id}/",
            {"service_name": "After"},
            HTTP_AUTHORIZATION=f"ApiKey {plaintext}",
        )
        assert resp.status_code == 200
        assert SubmissionChangeLog.objects.filter(submission=sub).exists()
        entry = SubmissionChangeLog.objects.filter(submission=sub).first()
        assert entry.changed_by.startswith("api:")

    def test_api_patch_no_change_does_not_write_log_entry(self):
        """API PATCH with unchanged fields must not write a log entry."""
        from rest_framework.test import APIClient
        from tests.factories import APIKeyFactory, ServiceSubmissionFactory
        from apps.submissions.models import SubmissionChangeLog

        sub = ServiceSubmissionFactory(service_name="Same")
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)

        api = APIClient()
        api.patch(
            f"/api/v1/submissions/{sub.id}/",
            {"service_name": "Same"},
            HTTP_AUTHORIZATION=f"ApiKey {plaintext}",
        )
        assert not SubmissionChangeLog.objects.filter(submission=sub).exists()


# ===========================================================================
# License field — YAML-based label lookup
# ===========================================================================


@pytest.mark.django_db
class TestSnapshotLicense:
    def test_snapshot_licenses_empty_returns_empty_list(self, db):
        """licenses M2M field should snapshot as an empty list when nothing selected."""
        from tests.factories import ServiceSubmissionFactory

        sub = ServiceSubmissionFactory()
        snap = snapshot_m2m(sub)
        assert snap["licenses"] == []

    def test_snapshot_license_note_used_when_no_licenses(self, db):
        """license_note should be in the diff when provided without licenses."""
        from tests.factories import ServiceSubmissionFactory

        sub = ServiceSubmissionFactory(license_note="Custom license")
        snap = snapshot(sub)
        assert snap["license_note"] == "Custom license"

    def test_snapshot_m2m_licenses_shows_ids(self, db):
        """licenses M2M field should show license IDs in the snapshot."""
        from apps.licenses.models import SpdxLicense
        from tests.factories import ServiceSubmissionFactory

        mit = SpdxLicense.objects.create(
            license_id="MIT-TEST",
            name="MIT Test License",
            is_deprecated=False,
        )
        sub = ServiceSubmissionFactory()
        sub.licenses.add(mit)
        snap = snapshot_m2m(sub)
        assert snap["licenses"] == ["MIT-TEST"]

    def test_snapshot_license_unknown_slug_returns_slug(self, db):
        """For a legacy license_note value, return the raw value."""
        from apps.submissions.models import ServiceSubmission
        from tests.factories import ServiceSubmissionFactory

        sub = ServiceSubmissionFactory()
        # Write a custom license note directly to bypass form validation
        ServiceSubmission.objects.filter(pk=sub.pk).update(
            license_note="some_old_license"
        )
        sub.refresh_from_db()
        snap = snapshot(sub)
        assert snap["license_note"] == "some_old_license"


# ---------------------------------------------------------------------------
# filter_sanitization_artifacts
# ---------------------------------------------------------------------------


class TestFilterSanitizationArtifacts:
    """
    filter_sanitization_artifacts() must remove diff entries that arise from
    form sanitization (NFC normalisation, bleach escaping, whitespace
    stripping) on fields the user never actually changed.
    """

    _form_fields = frozenset(
        {"service_name", "service_description", "github_url", "logo", "comments"}
    )

    def _make_change(self, field, old="old", new="new"):
        return {
            "field": field,
            "label": field.replace("_", " ").title(),
            "old": old,
            "new": new,
        }

    # ── fields the user changed ──────────────────────────────────────────────

    def test_user_changed_field_is_kept(self):
        changes = [self._make_change("service_name", "Old", "New")]
        result = filter_sanitization_artifacts(
            changes,
            form_changed_data=["service_name"],
            form_field_names=self._form_fields,
        )
        assert len(result) == 1
        assert result[0]["field"] == "service_name"

    def test_multiple_user_changed_fields_all_kept(self):
        changes = [
            self._make_change("service_name", "Old", "New"),
            self._make_change("github_url", "—", "https://github.com/org/repo"),
        ]
        result = filter_sanitization_artifacts(
            changes,
            form_changed_data=["service_name", "github_url"],
            form_field_names=self._form_fields,
        )
        assert len(result) == 2

    # ── sanitization artifacts (form field, not in changed_data) ─────────────

    def test_unchanged_form_field_is_removed(self):
        """Description appearing in diff without user changing it is a false positive."""
        changes = [self._make_change("service_description", "côté", "côté")]
        result = filter_sanitization_artifacts(
            changes,
            form_changed_data=[],  # user changed nothing
            form_field_names=self._form_fields,
        )
        assert result == []

    def test_only_sanitization_artifact_removed_real_change_kept(self):
        """Logo change kept; description artifact removed."""
        changes = [
            self._make_change("logo", "—", "logo_new.png"),
            self._make_change("service_description", "AT&T", "AT&amp;T"),
        ]
        result = filter_sanitization_artifacts(
            changes,
            form_changed_data=["logo"],
            form_field_names=self._form_fields,
        )
        assert len(result) == 1
        assert result[0]["field"] == "logo"

    # ── system-managed fields (not in form) ───────────────────────────────────

    def test_status_change_kept_even_though_not_in_changed_data(self):
        """status is not a form field — always included regardless of changed_data."""
        changes = [self._make_change("status", "Approved", "Submitted")]
        result = filter_sanitization_artifacts(
            changes,
            form_changed_data=[],
            form_field_names=self._form_fields,  # status not in form_fields
        )
        assert len(result) == 1
        assert result[0]["field"] == "status"

    def test_maturity_tag_clear_kept_when_not_in_form(self):
        """primary_maturity_tag excluded from form → change always kept in diff."""
        changes = [self._make_change("primary_maturity_tag", "Mature", "—")]
        result = filter_sanitization_artifacts(
            changes,
            form_changed_data=[],
            form_field_names=self._form_fields,
        )
        assert len(result) == 1

    # ── M2M fields ────────────────────────────────────────────────────────────

    def test_m2m_change_kept_even_if_not_in_changed_data(self):
        """edam_topics is an M2M field — snapshot comparison is reliable."""
        changes = [self._make_change("edam_topics", "—", "Topic A")]
        result = filter_sanitization_artifacts(
            changes,
            form_changed_data=[],
            form_field_names=self._form_fields,
        )
        assert len(result) == 1
        assert result[0]["field"] == "edam_topics"

    # ── empty inputs ─────────────────────────────────────────────────────────

    def test_empty_changes_returns_empty(self):
        assert filter_sanitization_artifacts([], [], frozenset()) == []

    def test_empty_form_fields_keeps_all_changes(self):
        """If the form has no fields, all changes are system-managed → all kept."""
        changes = [self._make_change("service_name")]
        result = filter_sanitization_artifacts(
            changes, form_changed_data=[], form_field_names=frozenset()
        )
        assert len(result) == 1
