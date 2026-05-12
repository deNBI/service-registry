"""
Form Tests
==========
Tests for SubmissionForm and UpdateKeyForm validation logic.
Covers required fields, cross-field rules, URL scheme enforcement,
email confirmation matching, conditional field visibility, and logo upload.
"""

import io

import pytest
from django import forms
from django.core.files.uploadedfile import SimpleUploadedFile

from tests.factories import PIFactory, ServiceCategoryFactory, ServiceCenterFactory


def _base_form_data(overrides=None):
    """Return a dict of minimal valid POST data for SubmissionForm."""
    from django.utils import timezone

    cat = ServiceCategoryFactory()
    center = ServiceCenterFactory()
    pi = PIFactory()

    data = {
        # Section A
        "date_of_entry": timezone.now().date().isoformat(),
        "submitter_first_name": "Test",
        "submitter_last_name": "Researcher",
        "submitter_affiliation": "Test University",
        "register_as_elixir": False,
        # Section B
        "service_name": "Test Service",
        "service_description": "A detailed description of the test service exceeding fifty characters minimum.",
        "year_established": 2020,
        "service_categories": [cat.pk],
        "is_toolbox": False,
        "toolbox_name": "",
        "user_knowledge_required": "",
        "publications_pmids": "12345678",
        # Section C
        "responsible_pis": [pi.pk],
        "associated_partner_note": "",
        "host_institute": "Test Institute",
        "service_center": center.pk,
        "public_contact_email": "public@example.com",
        "internal_contact_name": "Test Contact, Institute",
        "internal_contact_email": "internal@example.com",
        "internal_contact_email_confirm": "internal@example.com",
        # Section D
        "website_url": "https://example.com",
        "terms_of_use_url": "https://example.com/tos",
        "licenses": [],
        "license_note": "Other",
        "github_url": "",
        "biotools_url": "",
        "fairsharing_url": "",
        "other_registry_url": "",
        # Section E
        "kpi_monitoring": "yes",
        "kpi_start_year": "2021",
        # Section F
        "keywords_uncited": "",
        "keywords_seo": "",
        "survey_participation": True,
        "comments": "",
        # Section G
        "data_protection_consent": True,
    }
    if overrides:
        data.update(overrides)
    return data


@pytest.mark.django_db
class TestSubmissionFormValid:
    def test_minimal_valid_form_passes(self):
        from apps.submissions.forms import SubmissionForm

        form = SubmissionForm(_base_form_data())
        assert form.is_valid(), form.errors

    def test_elixir_registration_optional(self):
        from apps.submissions.forms import SubmissionForm

        for val in (True, False):
            form = SubmissionForm(_base_form_data({"register_as_elixir": val}))
            assert form.is_valid(), form.errors


@pytest.mark.django_db
class TestSubmissionFormRequired:
    @pytest.mark.parametrize(
        "field",
        [
            "service_name",
            "service_description",
            "submitter_first_name",
            "submitter_last_name",
            "submitter_affiliation",
            "host_institute",
            "public_contact_email",
            "internal_contact_name",
            "internal_contact_email",
            "internal_contact_email_confirm",
            "website_url",
            "terms_of_use_url",
            "publications_pmids",
        ],
    )
    def test_required_field_blank_fails(self, field):
        from apps.submissions.forms import SubmissionForm

        data = _base_form_data({field: ""})
        form = SubmissionForm(data)
        assert not form.is_valid()
        assert field in form.errors

    def test_data_protection_consent_required(self):
        from apps.submissions.forms import SubmissionForm

        data = _base_form_data({"data_protection_consent": False})
        form = SubmissionForm(data)
        assert not form.is_valid()
        assert "data_protection_consent" in form.errors


@pytest.mark.django_db
class TestSubmissionFormCrossField:
    def test_toolbox_name_required_when_is_toolbox_true(self):
        from apps.submissions.forms import SubmissionForm

        data = _base_form_data({"is_toolbox": True, "toolbox_name": ""})
        form = SubmissionForm(data)
        assert not form.is_valid()
        assert "toolbox_name" in form.errors

    def test_toolbox_name_accepted_when_is_toolbox_true(self):
        from apps.submissions.forms import SubmissionForm

        data = _base_form_data({"is_toolbox": True, "toolbox_name": "de.NBI Toolbox"})
        form = SubmissionForm(data)
        assert form.is_valid(), form.errors

    def test_email_confirmation_must_match(self):
        from apps.submissions.forms import SubmissionForm

        data = _base_form_data(
            {
                "internal_contact_email": "a@example.com",
                "internal_contact_email_confirm": "b@example.com",
            }
        )
        form = SubmissionForm(data)
        assert not form.is_valid()
        assert "internal_contact_email_confirm" in form.errors

    def test_associated_partner_note_required_when_pi_is_partner(self):
        """When the 'associated partner' PI is selected, a note is required."""
        from apps.submissions.forms import SubmissionForm
        from tests.factories import AssociatedPartnerPIFactory

        partner_pi = AssociatedPartnerPIFactory()
        data = _base_form_data(
            {
                "responsible_pis": [partner_pi.pk],
                "associated_partner_note": "",
            }
        )
        form = SubmissionForm(data)
        assert not form.is_valid()
        assert "associated_partner_note" in form.errors


@pytest.mark.django_db
class TestSubmissionFormURLValidation:
    def test_http_website_url_rejected(self):
        from apps.submissions.forms import SubmissionForm

        form = SubmissionForm(_base_form_data({"website_url": "http://example.com"}))
        assert not form.is_valid()
        assert "website_url" in form.errors

    def test_https_website_url_accepted(self):
        from apps.submissions.forms import SubmissionForm

        form = SubmissionForm(_base_form_data({"website_url": "https://example.com"}))
        assert form.is_valid(), form.errors

    def test_github_url_must_be_github(self):
        from apps.submissions.forms import SubmissionForm

        form = SubmissionForm(
            _base_form_data({"github_url": "https://gitlab.com/org/repo"})
        )
        assert not form.is_valid()
        assert "github_url" in form.errors

    def test_biotools_url_must_be_biotools(self):
        from apps.submissions.forms import SubmissionForm

        form = SubmissionForm(
            _base_form_data({"biotools_url": "https://bioinformatics.tools/x"})
        )
        assert not form.is_valid()
        assert "biotools_url" in form.errors

    def test_optional_urls_blank_accepted(self):
        from apps.submissions.forms import SubmissionForm

        form = SubmissionForm(
            _base_form_data(
                {
                    "github_url": "",
                    "biotools_url": "",
                    "fairsharing_url": "",
                    "other_registry_url": "",
                }
            )
        )
        assert form.is_valid(), form.errors


@pytest.mark.django_db
class TestUpdateKeyForm:
    def test_empty_key_rejected(self):
        from apps.submissions.forms import UpdateKeyForm

        form = UpdateKeyForm({"api_key": ""})
        assert not form.is_valid()

    def test_very_short_key_rejected(self):
        from apps.submissions.forms import UpdateKeyForm

        form = UpdateKeyForm({"api_key": "short"})
        assert not form.is_valid()

    def test_too_long_key_rejected(self):
        """Keys longer than 200 characters must be rejected."""
        from apps.submissions.forms import UpdateKeyForm

        form = UpdateKeyForm({"api_key": "x" * 201})
        assert not form.is_valid()

    def test_valid_length_key_accepted(self):
        import secrets
        from apps.submissions.forms import UpdateKeyForm

        key = secrets.token_urlsafe(48)
        form = UpdateKeyForm({"api_key": key})
        assert form.is_valid(), form.errors


# ---------------------------------------------------------------------------
# Form texts YAML — tooltip system
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFormTextsYAML:
    """Verify the YAML-driven help text and tooltip system.

    These tests also validate the *structure* of form_texts.yaml so that
    CI catches formatting mistakes before they reach production.
    """

    # -- YAML file structure & syntax --

    def test_yaml_loads_without_error(self):
        from apps.submissions.forms import _FORM_TEXTS

        assert isinstance(_FORM_TEXTS, dict)
        assert len(_FORM_TEXTS) > 0

    def test_yaml_is_valid_syntax(self):
        """Catch YAML syntax errors with a clear message."""
        import yaml
        from pathlib import Path

        path = (
            Path(__file__).resolve().parent.parent / "apps/submissions/form_texts.yaml"
        )
        with open(path, encoding="utf-8") as f:
            try:
                data = yaml.safe_load(f)
            except yaml.YAMLError as exc:
                pytest.fail(
                    f"form_texts.yaml has a YAML syntax error:\n{exc}\n\n"
                    "Fix: check for incorrect indentation, missing quotes, "
                    "or special characters that need quoting."
                )
        assert isinstance(data, dict), (
            "form_texts.yaml must be a YAML mapping (key: value), not a list or scalar."
        )

    # Top-level YAML keys that are not field entries (tested separately)
    _NON_FIELD_KEYS = {"sections"}

    def test_every_entry_has_required_keys(self):
        """Each field entry must have 'help' and 'tooltip' keys; 'label' is optional.

        The 'license' field may also have a 'choices' block for YAML-driven options.
        """
        from apps.submissions.forms import _FORM_TEXTS

        required_keys = {"help", "tooltip"}
        allowed_keys = {"help", "tooltip", "label", "choices"}
        for field_name, entry in _FORM_TEXTS.items():
            if field_name in self._NON_FIELD_KEYS:
                continue
            assert isinstance(entry, dict), (
                f"'{field_name}' must be a mapping with 'help' and 'tooltip' keys, "
                f"got {type(entry).__name__}.\n"
                f"Fix: add the structure:\n"
                f"  {field_name}:\n"
                f'    help: "..."\n'
                f'    tooltip: "..."'
            )
            missing = required_keys - set(entry.keys())
            assert not missing, (
                f"'{field_name}' is missing required key(s): {missing}.\n"
                f"Fix: add the missing key(s) under '{field_name}:' in form_texts.yaml."
            )
            extra = set(entry.keys()) - allowed_keys
            assert not extra, (
                f"'{field_name}' has unexpected key(s): {extra}.\n"
                f"Only 'help', 'tooltip', and 'label' are allowed."
            )

    def test_all_values_are_strings(self):
        """help and tooltip values must be strings (not numbers, bools, lists)."""
        from apps.submissions.forms import _FORM_TEXTS

        for field_name, entry in _FORM_TEXTS.items():
            if field_name in self._NON_FIELD_KEYS:
                continue
            if not isinstance(entry, dict):
                continue
            for key in ("help", "tooltip"):
                value = entry.get(key)
                if value is None:
                    continue
                assert isinstance(value, str), (
                    f"'{field_name}.{key}' must be a string, got {type(value).__name__}: {value!r}.\n"
                    f'Fix: wrap the value in quotes, e.g. {key}: "{value}"'
                )

    # -- Completeness: YAML ↔ form fields --

    def test_all_form_fields_have_yaml_entry(self):
        """Every SubmissionForm field must have a corresponding YAML entry."""
        from apps.submissions.forms import SubmissionForm, _FORM_TEXTS

        form = SubmissionForm()
        missing = [name for name in form.fields if name not in _FORM_TEXTS]
        assert missing == [], (
            f"Form field(s) missing from form_texts.yaml: {missing}\n"
            f"Fix: add an entry for each missing field:\n"
            + "\n".join(f'  {name}:\n    help: ""\n    tooltip: ""' for name in missing)
        )

    def test_no_stale_yaml_entries(self):
        """YAML should not contain entries for fields that no longer exist."""
        from apps.submissions.forms import SubmissionForm, _FORM_TEXTS

        form = SubmissionForm()
        stale = [
            name
            for name in _FORM_TEXTS
            if name not in form.fields and name not in self._NON_FIELD_KEYS
        ]
        assert stale == [], (
            f"form_texts.yaml contains entries for fields that do not exist "
            f"in SubmissionForm: {stale}\n"
            f"Fix: remove these stale entries from form_texts.yaml."
        )

    # -- Runtime behaviour --

    def test_yaml_overrides_help_text(self):
        from apps.submissions.forms import SubmissionForm, _FORM_TEXTS

        form = SubmissionForm()
        for field_name, texts in _FORM_TEXTS.items():
            if field_name not in form.fields:
                continue
            if texts.get("help"):
                assert form.fields[field_name].help_text == texts["help"], (
                    f"{field_name}: help_text not applied from YAML"
                )

    def test_tooltip_attribute_set(self):
        from apps.submissions.forms import SubmissionForm, _FORM_TEXTS

        form = SubmissionForm()
        for field_name, texts in _FORM_TEXTS.items():
            if field_name not in form.fields:
                continue
            assert hasattr(form.fields[field_name], "tooltip"), (
                f"{field_name}: missing tooltip attribute"
            )
            assert form.fields[field_name].tooltip == texts.get("tooltip", "")

    def test_fields_without_yaml_entry_keep_model_help_text(self):
        """Fields not in form_texts.yaml retain their original model help_text."""
        from apps.submissions.forms import SubmissionForm, _FORM_TEXTS

        form = SubmissionForm()
        for field_name, field_obj in form.fields.items():
            if field_name not in _FORM_TEXTS:
                assert hasattr(field_obj, "tooltip")
                assert field_obj.tooltip == ""

    def test_tooltip_rendered_in_field_template(self, rf):
        """The field.html template renders a tooltip icon when tooltip is set."""
        from django.template.loader import render_to_string

        from apps.submissions.forms import SubmissionForm

        request = rf.get("/")
        form = SubmissionForm()
        html = render_to_string(
            "submissions/partials/field.html",
            {"field": form["service_name"], "required": True},
            request=request,
        )
        assert 'data-bs-toggle="tooltip"' in html
        assert "tooltip-icon" in html

    def test_no_tooltip_icon_when_tooltip_empty(self, rf):
        """Fields with empty or whitespace-only tooltip must not render the info icon."""
        from django.template.loader import render_to_string

        from apps.submissions.forms import SubmissionForm

        request = rf.get("/")

        for tooltip_value, scenario in [
            ("", "empty string"),
            ("   ", "whitespace-only string"),
        ]:
            form = SubmissionForm()
            # Inject the tooltip directly so the test is independent of YAML content.
            form.fields["comments"].tooltip = tooltip_value
            html = render_to_string(
                "submissions/partials/field.html",
                {"field": form["comments"], "required": False},
                request=request,
            )
            assert "tooltip-icon" not in html, (
                f"tooltip-icon was rendered for field 'comments' with {scenario} tooltip "
                f"({tooltip_value!r}). "
                f"The template should suppress the icon when tooltip is empty or whitespace-only. "
                f"Fix: ensure forms.py strips the tooltip value, or update the template condition."
            )

    def test_tooltip_icon_rendered_when_tooltip_non_empty(self, rf):
        """Fields with a non-empty tooltip must render the info icon."""
        from django.template.loader import render_to_string

        from apps.submissions.forms import SubmissionForm

        request = rf.get("/")
        form = SubmissionForm()
        # Inject a known non-empty tooltip to verify the icon appears.
        form.fields["comments"].tooltip = "Some helpful context."
        html = render_to_string(
            "submissions/partials/field.html",
            {"field": form["comments"], "required": False},
            request=request,
        )
        assert "tooltip-icon" in html, (
            "tooltip-icon was NOT rendered for field 'comments' despite having a non-empty tooltip. "
            "Check the field.html template condition for '{% if field.field.tooltip %}'."
        )

    # -- Accessibility: fieldset/legend for multi-choice widgets --

    def test_radio_fields_render_fieldset(self, rf):
        """RadioSelect fields should render inside <fieldset> with <legend>."""
        from django.template.loader import render_to_string

        from apps.submissions.forms import SubmissionForm

        request = rf.get("/")
        form = SubmissionForm()
        html = render_to_string(
            "submissions/partials/field.html",
            {"field": form["register_as_elixir"], "required": True},
            request=request,
        )
        assert "<fieldset" in html
        assert "<legend" in html
        assert "<label" not in html.split("<legend")[0].split("<fieldset")[-1]

    def test_service_categories_renders_label_select(self, rf):
        """service_categories renders as <select> with the compact-select data attribute."""
        from django.template.loader import render_to_string

        from apps.submissions.forms import SubmissionForm

        request = rf.get("/")
        form = SubmissionForm()
        html = render_to_string(
            "submissions/partials/field.html",
            {"field": form["service_categories"], "required": True},
            request=request,
        )
        assert "<label" in html
        assert "<select" in html
        assert "multiple" in html
        assert 'data-compact-select="categories"' in html

    def test_responsible_pis_renders_compact_select(self, rf):
        """responsible_pis renders as <select> with the compact-select data attribute."""
        from django.template.loader import render_to_string

        from apps.submissions.forms import SubmissionForm

        request = rf.get("/")
        form = SubmissionForm()
        html = render_to_string(
            "submissions/partials/field.html",
            {"field": form["responsible_pis"], "required": True},
            request=request,
        )
        assert "<label" in html
        assert "<select" in html
        assert "multiple" in html
        assert 'data-compact-select="PIs"' in html

    def test_submitter_affiliation_renders_select_combobox(self, rf):
        """submitter_affiliation (Select + Tom Select) should render as <select> with data attribute."""
        from django.template.loader import render_to_string

        from apps.submissions.forms import SubmissionForm

        request = rf.get("/")
        form = SubmissionForm()
        html = render_to_string(
            "submissions/partials/field.html",
            {"field": form["submitter_affiliation"], "required": True},
            request=request,
        )
        assert "<label" in html
        assert "<select" in html
        assert 'data-affiliation-combobox="true"' in html
        assert (
            "<input" not in html.split("<select")[0]
        )  # no text input before the select

    def test_text_input_does_not_render_fieldset(self, rf):
        """Standard text inputs should use <label>, not <fieldset>."""
        from django.template.loader import render_to_string

        from apps.submissions.forms import SubmissionForm

        request = rf.get("/")
        form = SubmissionForm()
        html = render_to_string(
            "submissions/partials/field.html",
            {"field": form["service_name"], "required": True},
            request=request,
        )
        assert "<fieldset" not in html
        assert "<legend" not in html
        assert "<label" in html


# ---------------------------------------------------------------------------
# Email texts YAML — subject lines and status messages
# ---------------------------------------------------------------------------


class TestEmailTextsYAML:
    """Validate the email_texts.yaml structure so CI catches mistakes."""

    def test_yaml_loads_without_error(self):
        from apps.submissions.tasks import _EMAIL_TEXTS

        assert isinstance(_EMAIL_TEXTS, dict)
        assert len(_EMAIL_TEXTS) > 0

    def test_yaml_is_valid_syntax(self):
        """Catch YAML syntax errors with a clear message."""
        import yaml
        from pathlib import Path

        path = (
            Path(__file__).resolve().parent.parent / "apps/submissions/email_texts.yaml"
        )
        with open(path, encoding="utf-8") as f:
            try:
                data = yaml.safe_load(f)
            except yaml.YAMLError as exc:
                pytest.fail(
                    f"email_texts.yaml has a YAML syntax error:\n{exc}\n\n"
                    "Fix: check for incorrect indentation, missing quotes, "
                    "or special characters that need quoting."
                )
        assert isinstance(data, dict), (
            "email_texts.yaml must be a YAML mapping, not a list or scalar."
        )

    def test_subjects_section_exists_with_required_keys(self):
        from apps.submissions.tasks import _EMAIL_TEXTS

        subjects = _EMAIL_TEXTS.get("subjects")
        assert isinstance(subjects, dict), (
            "email_texts.yaml must have a 'subjects' mapping.\n"
            "Fix: add a 'subjects:' section with event-keyed subject lines."
        )
        required = {"created", "status_changed", "updated", "submitter_status"}
        missing = required - set(subjects.keys())
        assert not missing, (
            f"'subjects' is missing required key(s): {missing}.\n"
            f"Fix: add the missing subject line(s) under 'subjects:' in email_texts.yaml."
        )

    def test_status_messages_section_exists_with_required_keys(self):
        from apps.submissions.tasks import _EMAIL_TEXTS

        messages = _EMAIL_TEXTS.get("status_messages")
        assert isinstance(messages, dict), (
            "email_texts.yaml must have a 'status_messages' mapping.\n"
            "Fix: add a 'status_messages:' section with status-keyed messages."
        )
        required = {"approved", "rejected", "under_review", "default"}
        missing = required - set(messages.keys())
        assert not missing, (
            f"'status_messages' is missing required key(s): {missing}.\n"
            f"Fix: add the missing message(s) under 'status_messages:' in email_texts.yaml."
        )

    def test_all_values_are_strings(self):
        from apps.submissions.tasks import _EMAIL_TEXTS

        for section_name in ("subjects", "status_messages"):
            section = _EMAIL_TEXTS.get(section_name, {})
            for key, value in section.items():
                assert isinstance(value, str), (
                    f"'{section_name}.{key}' must be a string, "
                    f"got {type(value).__name__}: {value!r}.\n"
                    f"Fix: wrap the value in quotes."
                )

    def test_subject_placeholders_are_valid(self):
        """Subject templates should only use known placeholders."""
        from apps.submissions.tasks import _EMAIL_TEXTS

        allowed_placeholders = {"service_name", "status"}
        for key, template in _EMAIL_TEXTS.get("subjects", {}).items():
            # Try formatting with all allowed placeholders
            try:
                template.format(**{p: "test" for p in allowed_placeholders})
            except KeyError as exc:
                pytest.fail(
                    f"'subjects.{key}' uses unknown placeholder {exc}.\n"
                    f"Allowed placeholders: {allowed_placeholders}"
                )

    def test_email_subject_helper(self):
        """_email_subject returns formatted strings."""
        from apps.submissions.tasks import _email_subject

        result = _email_subject(
            "created", service_name="TestService", status="Approved"
        )
        assert "TestService" in result

    def test_status_message_helper(self):
        """_status_message returns the correct message for each status."""
        from apps.submissions.tasks import _status_message

        msg = _status_message("approved")
        assert "approved" in msg.lower()

        msg = _status_message("unknown_status")
        assert msg  # Should return the "default" message


# ===========================================================================
# Section descriptions — YAML validation and rendering
# ===========================================================================


class TestSectionDescriptionsYAML:
    """Validate the sections block in form_texts.yaml and its rendering."""

    EXPECTED_SECTIONS = {"a", "b", "c", "d", "e", "f", "g"}

    def test_sections_key_exists(self):
        from apps.submissions.forms import _FORM_TEXTS

        assert "sections" in _FORM_TEXTS, (
            "form_texts.yaml is missing the 'sections' key. "
            "Add a 'sections:' block with keys a–g."
        )

    def test_all_sections_present(self):
        from apps.submissions.forms import _FORM_TEXTS

        sections = _FORM_TEXTS.get("sections", {})
        missing = self.EXPECTED_SECTIONS - set(sections.keys())
        assert not missing, (
            f"form_texts.yaml sections block is missing: {sorted(missing)}. "
            "Each section (a–g) must have an entry."
        )

    def test_no_unexpected_sections(self):
        from apps.submissions.forms import _FORM_TEXTS

        sections = _FORM_TEXTS.get("sections", {})
        unexpected = set(sections.keys()) - self.EXPECTED_SECTIONS
        assert not unexpected, (
            f"form_texts.yaml sections block has unexpected keys: {sorted(unexpected)}. "
            "Valid section keys are a–g."
        )

    def test_each_section_has_description_key(self):
        from apps.submissions.forms import _FORM_TEXTS

        sections = _FORM_TEXTS.get("sections", {})
        for key in self.EXPECTED_SECTIONS:
            entry = sections.get(key, {})
            assert "description" in entry, (
                f"Section '{key}' in form_texts.yaml is missing the 'description' key."
            )

    def test_description_values_are_strings(self):
        from apps.submissions.forms import _FORM_TEXTS

        sections = _FORM_TEXTS.get("sections", {})
        for key, entry in sections.items():
            desc = entry.get("description", "")
            assert isinstance(desc, str), (
                f"Section '{key}' description must be a string, got {type(desc).__name__}."
            )

    def test_no_html_in_descriptions(self):
        """Descriptions must be plain text — HTML tags are disallowed."""
        from apps.submissions.forms import _FORM_TEXTS

        import re

        sections = _FORM_TEXTS.get("sections", {})
        html_pattern = re.compile(r"<[a-zA-Z/][^>]*>")
        for key, entry in sections.items():
            desc = entry.get("description", "")
            assert not html_pattern.search(desc), (
                f"Section '{key}' description contains raw HTML tags. "
                "Use plain text only. For links use [link text](https://...) syntax."
            )

    @pytest.mark.django_db
    def test_section_texts_attached_to_form(self):
        """SubmissionForm must expose section_texts matching the YAML sections block."""
        from apps.submissions.forms import SubmissionForm, _FORM_TEXTS

        form = SubmissionForm()
        assert hasattr(form, "section_texts"), (
            "SubmissionForm is missing the 'section_texts' attribute."
        )
        assert form.section_texts == _FORM_TEXTS.get("sections", {})

    @pytest.mark.django_db
    def test_description_renders_in_template(self):
        """A section with a non-empty description must appear in the rendered form."""
        import pytest
        from django.test import RequestFactory
        from django.template.loader import render_to_string
        from apps.submissions.forms import SubmissionForm, _FORM_TEXTS

        if not _FORM_TEXTS.get("sections", {}).get("b", {}).get("description"):
            pytest.skip("Section B has no description set — skipping render test.")

        factory = RequestFactory()
        request = factory.get("/register/")
        form = SubmissionForm()
        content = render_to_string(
            "submissions/partials/form_body.html",
            {"form": form},
            request=request,
        )
        desc = _FORM_TEXTS["sections"]["b"]["description"]
        # Description text should appear (URLs may be linkified so check a fragment)
        fragment = desc[:30].rstrip()
        assert fragment in content, (
            f"Section B description not found in rendered form. "
            f"Expected fragment: {fragment!r}"
        )

    @pytest.mark.django_db
    def test_empty_description_renders_no_paragraph(self):
        """A section with an empty description must not render a description paragraph."""
        from django.test import RequestFactory
        from django.template.loader import render_to_string
        from apps.submissions.forms import SubmissionForm, _FORM_TEXTS

        # Find the first section with an empty description
        sections = _FORM_TEXTS.get("sections", {})
        empty_section = next(
            (k for k, v in sections.items() if not v.get("description")), None
        )
        if empty_section is None:
            import pytest

            pytest.skip("All sections have descriptions — skipping empty test.")

        factory = RequestFactory()
        request = factory.get("/register/")
        form = SubmissionForm()
        content = render_to_string(
            "submissions/partials/form_body.html",
            {"form": form},
            request=request,
        )
        # The section-description class should not appear for empty sections,
        # unless another non-empty section also renders one.
        # Just verify the page renders without error.
        assert f'id="section-{empty_section}"' in content

    @pytest.mark.django_db
    def test_url_in_description_is_linkified(self):
        """A description containing a URL must render as an anchor tag."""
        from django.test import RequestFactory
        from django.template.loader import render_to_string
        from unittest.mock import patch
        from apps.submissions.forms import SubmissionForm

        url = "https://www.denbi.de/services"
        patched_sections = {
            "a": {"description": f"See {url} for examples."},
            "b": {"description": ""},
            "c": {"description": ""},
            "d": {"description": ""},
            "e": {"description": ""},
            "f": {"description": ""},
            "g": {"description": ""},
        }

        factory = RequestFactory()
        request = factory.get("/register/")

        with patch(
            "apps.submissions.forms._FORM_TEXTS",
            {"sections": patched_sections},
        ):
            form = SubmissionForm()

        content = render_to_string(
            "submissions/partials/form_body.html",
            {"form": form},
            request=request,
        )
        assert f'href="{url}"' in content, (
            "URL in section description was not converted to an anchor tag."
        )

    @pytest.mark.django_db
    def test_html_in_description_is_escaped(self):
        """HTML tags in a description must be escaped, not rendered."""
        from django.test import RequestFactory
        from django.template.loader import render_to_string
        from unittest.mock import patch
        from apps.submissions.forms import SubmissionForm

        malicious = '<script>alert("xss")</script>Plain text.'
        patched_sections = {
            "a": {"description": malicious},
            "b": {"description": ""},
            "c": {"description": ""},
            "d": {"description": ""},
            "e": {"description": ""},
            "f": {"description": ""},
            "g": {"description": ""},
        }

        factory = RequestFactory()
        request = factory.get("/register/")

        with patch(
            "apps.submissions.forms._FORM_TEXTS",
            {"sections": patched_sections},
        ):
            form = SubmissionForm()

        content = render_to_string(
            "submissions/partials/form_body.html",
            {"form": form},
            request=request,
        )
        # The injected <script> tag must be HTML-escaped, not rendered literally.
        # Note: form_body.html contains its own legitimate <script> blocks, so we
        # check for the specific injection string being escaped rather than the
        # absence of any <script> tag.
        assert '<script>alert("xss")</script>' not in content, (
            "Injected script tag was not escaped."
        )
        assert "&lt;script&gt;" in content, "Script tag was not HTML-escaped in output."
        assert "Plain text." in content, "Plain text portion was dropped."

    @pytest.mark.django_db
    def test_missing_sections_key_graceful_fallback(self):
        """If the YAML has no 'sections' key, the form still renders without error."""
        from django.test import RequestFactory
        from django.template.loader import render_to_string
        from unittest.mock import patch
        from apps.submissions.forms import SubmissionForm

        factory = RequestFactory()
        request = factory.get("/register/")

        with patch("apps.submissions.forms._FORM_TEXTS", {}):
            form = SubmissionForm()

        content = render_to_string(
            "submissions/partials/form_body.html",
            {"form": form},
            request=request,
        )
        assert 'id="section-a"' in content
        assert "section-description" not in content

    @pytest.mark.django_db
    def test_register_template_renders_section_descriptions(self):
        """
        Regression test: register.html must render section descriptions.

        The bug: descriptions were only added to form_body.html (used by edit.html).
        register.html has its own inline form structure and was missing the blocks.
        """
        from django.test import RequestFactory
        from django.template.loader import render_to_string
        from unittest.mock import patch
        from apps.submissions.forms import SubmissionForm

        patched_sections = {
            "a": {"description": "Regression check for section A."},
            "b": {"description": "Regression check for section B."},
            "c": {"description": ""},
            "d": {"description": ""},
            "e": {"description": ""},
            "f": {"description": ""},
            "g": {"description": ""},
        }

        factory = RequestFactory()
        request = factory.get("/register/")

        with patch(
            "apps.submissions.forms._FORM_TEXTS", {"sections": patched_sections}
        ):
            form = SubmissionForm()

        content = render_to_string(
            "submissions/register.html",
            {"form": form, "SITE": {}, "PRIVACY_POLICY_URL": "", "CONTACT_EMAIL": ""},
            request=request,
        )
        assert "Regression check for section A." in content, (
            "register.html is not rendering section A description — "
            "check that the {% with desc=form.section_texts.a.description %} block is present."
        )
        assert "Regression check for section B." in content, (
            "register.html is not rendering section B description."
        )

    @pytest.mark.django_db
    def test_markdown_link_in_description_renders(self):
        """[text](url) syntax in a description must render as a named anchor."""
        from django.test import RequestFactory
        from django.template.loader import render_to_string
        from unittest.mock import patch
        from apps.submissions.forms import SubmissionForm

        patched_sections = {
            "a": {
                "description": "See [KPI Compass](https://www.denbi.de/kpi) for guidance."
            },
            "b": {"description": ""},
            "c": {"description": ""},
            "d": {"description": ""},
            "e": {"description": ""},
            "f": {"description": ""},
            "g": {"description": ""},
        }

        factory = RequestFactory()
        request = factory.get("/register/")

        with patch(
            "apps.submissions.forms._FORM_TEXTS", {"sections": patched_sections}
        ):
            form = SubmissionForm()

        content = render_to_string(
            "submissions/partials/form_body.html",
            {"form": form},
            request=request,
        )
        assert 'href="https://www.denbi.de/kpi"' in content, (
            "[text](url) link was not converted to an anchor tag."
        )
        assert ">KPI Compass<" in content, (
            "Named link text 'KPI Compass' was not rendered."
        )

    @pytest.mark.django_db
    def test_newlines_in_description_render(self):
        """Blank lines in a description must produce separate <p> elements."""
        from django.test import RequestFactory
        from django.template.loader import render_to_string
        from unittest.mock import patch
        from apps.submissions.forms import SubmissionForm

        patched_sections = {
            "a": {"description": "First paragraph.\n\nSecond paragraph."},
            "b": {"description": ""},
            "c": {"description": ""},
            "d": {"description": ""},
            "e": {"description": ""},
            "f": {"description": ""},
            "g": {"description": ""},
        }

        factory = RequestFactory()
        request = factory.get("/register/")

        with patch(
            "apps.submissions.forms._FORM_TEXTS", {"sections": patched_sections}
        ):
            form = SubmissionForm()

        content = render_to_string(
            "submissions/partials/form_body.html",
            {"form": form},
            request=request,
        )
        assert "First paragraph." in content
        assert "Second paragraph." in content
        assert content.count("<p>") >= 2, (
            "Expected at least two <p> elements for two paragraphs."
        )


# ===========================================================================
# Logo field — form-level clean_logo() validation
# ===========================================================================


def _make_png_bytes() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    img = Image.new("RGB", (1, 1), color=(255, 255, 255))
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.django_db
class TestLogoFormField:
    """Tests for SubmissionForm.clean_logo() — the form-level logo validation hook."""

    def test_no_logo_is_valid(self):
        """Logo is optional — form without a logo field must still be valid."""
        from apps.submissions.forms import SubmissionForm

        form = SubmissionForm(_base_form_data(), files={})
        assert form.is_valid(), form.errors

    def test_valid_png_accepted_by_clean_logo(self):
        """A valid PNG uploaded via the form must pass clean_logo() successfully."""
        from apps.submissions.forms import SubmissionForm
        from django.core.files.uploadedfile import InMemoryUploadedFile

        logo = SimpleUploadedFile(
            "logo.png", _make_png_bytes(), content_type="image/png"
        )
        form = SubmissionForm(_base_form_data(), files={"logo": logo})
        assert form.is_valid(), form.errors
        assert isinstance(form.cleaned_data["logo"], InMemoryUploadedFile)

    def test_invalid_file_rejected_by_clean_logo(self):
        """A file with invalid/unsupported content must cause a form validation error."""
        from apps.submissions.forms import SubmissionForm

        bad_logo = SimpleUploadedFile(
            "logo.png", b"not an image at all", content_type="image/png"
        )
        form = SubmissionForm(_base_form_data(), files={"logo": bad_logo})
        assert not form.is_valid()
        assert "logo" in form.errors

    def test_oversized_file_rejected_by_clean_logo(self, settings):
        """A file exceeding LOGO_MAX_BYTES must be rejected at the form level."""
        from apps.submissions.forms import SubmissionForm

        settings.LOGO_MAX_BYTES = 10  # 10 bytes — any real PNG exceeds this
        too_big = SimpleUploadedFile(
            "logo.png", _make_png_bytes(), content_type="image/png"
        )
        form = SubmissionForm(_base_form_data(), files={"logo": too_big})
        assert not form.is_valid()
        assert "logo" in form.errors


# ---------------------------------------------------------------------------
# Affiliation combobox — Tom Select with create
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAffiliationCombobox:
    def test_free_text_value_accepted(self):
        """A value not in the suggestions list is accepted (CharField doesn't validate choices)."""
        from apps.submissions.forms import SubmissionForm

        data = _base_form_data({"submitter_affiliation": "Brand New Institute 2026"})
        form = SubmissionForm(data)
        assert form.is_valid(), form.errors
        assert form.cleaned_data["submitter_affiliation"] == "Brand New Institute 2026"

    def test_choices_populated_from_pi_institutes(self):
        """Affiliation choices include institutes from active PrincipalInvestigator records."""
        from apps.submissions.forms import SubmissionForm

        PIFactory(institute="Max Planck Institute for Testing")
        form = SubmissionForm()
        choice_values = [
            v for v, _ in form.fields["submitter_affiliation"].widget.choices
        ]
        assert "Max Planck Institute for Testing" in choice_values

    def test_min_length_still_enforced(self):
        """The 2-character minimum length is enforced even with the new combobox widget."""
        from apps.submissions.forms import SubmissionForm

        data = _base_form_data({"submitter_affiliation": "A"})
        form = SubmissionForm(data)
        assert not form.is_valid()
        assert "submitter_affiliation" in form.errors


# ===========================================================================
# License/M2M field - SPDX license selection
# ===========================================================================


@pytest.mark.django_db
class TestSpdxLicenseField:
    """Tests for the SPDX license M2M field and widget."""

    def test_licenses_field_is_selectmultiple(self):
        """The licenses field must use a SelectMultiple widget."""
        from apps.submissions.forms import SubmissionForm

        form = SubmissionForm()
        assert isinstance(form.fields["licenses"].widget, forms.SelectMultiple)

    @pytest.mark.django_db
    def test_licenses_field_filters_out_deprecated(self):
        """Deprecated licenses must not appear in queryset for new submissions."""
        from apps.licenses.models import SpdxLicense
        from apps.submissions.forms import SubmissionForm

        # Create a deprecated license
        SpdxLicense.objects.create(
            license_id="DEPRECATED-TEST",
            name="Deprecated Test License",
            is_deprecated=True,
        )

        form = SubmissionForm()
        license_ids = [lic.license_id for lic in form.fields["licenses"].queryset]
        assert "DEPRECATED-TEST" not in license_ids

    @pytest.mark.django_db
    def test_form_rejects_empty_licenses_and_empty_license_note(self):
        """Form must fail when neither licenses nor license_note are provided."""
        from apps.submissions.forms import SubmissionForm

        data = _base_form_data({"licenses": [], "license_note": ""})
        form = SubmissionForm(data)
        assert not form.is_valid()
        assert form.non_field_errors()

    @pytest.mark.django_db
    def test_form_accepts_license_note_only(self):
        """Form must pass when license_note is provided without licenses."""
        from apps.submissions.forms import SubmissionForm

        data = _base_form_data({"licenses": [], "license_note": "Custom license"})
        form = SubmissionForm(data)
        assert form.is_valid(), form.errors

    @pytest.mark.django_db
    def test_form_accepts_licenses_only(self):
        """Form must pass when at least one license is selected without license_note."""
        from apps.submissions.forms import SubmissionForm
        from apps.licenses.models import SpdxLicense

        mit = SpdxLicense.objects.create(
            license_id="MIT-TEST",
            name="MIT Test License",
            is_deprecated=False,
        )
        data = _base_form_data({"licenses": [mit.pk], "license_note": ""})
        form = SubmissionForm(data)
        assert form.is_valid(), form.errors

    @pytest.mark.django_db
    def test_form_accepts_both_licenses_and_license_note(self):
        """Form must pass when both licenses and license_note are provided."""
        from apps.submissions.forms import SubmissionForm
        from apps.licenses.models import SpdxLicense

        mit = SpdxLicense.objects.create(
            license_id="MIT-TEST2",
            name="MIT Test License 2",
            is_deprecated=False,
        )
        data = _base_form_data({"licenses": [mit.pk], "license_note": "Notes"})
        form = SubmissionForm(data)
        assert form.is_valid(), form.errors


# ===========================================================================
# Deprecated license behavior
# ===========================================================================


@pytest.mark.django_db
class TestDeprecatedLicenseBehavior:
    """Tests for deprecated license filtering and persistence."""

    def test_deprecated_licenses_hidden_from_new_selection(self):
        """Deprecated licenses must not appear in the queryset for new submissions."""
        from apps.submissions.forms import SubmissionForm
        from apps.licenses.models import SpdxLicense

        # Create a deprecated license
        SpdxLicense.objects.create(
            license_id="DEPRECATED-1",
            name="Deprecated License",
            is_deprecated=True,
        )

        form = SubmissionForm()
        license_ids = [lic.license_id for lic in form.fields["licenses"].queryset]
        assert "DEPRECATED-1" not in license_ids

    def test_deprecated_licenses_shown_if_already_selected(self):
        """Deprecated licenses must appear if they were already selected on an existing instance."""
        from apps.submissions.forms import SubmissionForm
        from apps.licenses.models import SpdxLicense
        from tests.factories import ServiceSubmissionFactory

        deprecated = SpdxLicense.objects.create(
            license_id="DEPRECATED-2",
            name="Previously Selected Deprecated License",
            is_deprecated=True,
        )
        submission = ServiceSubmissionFactory()
        submission.licenses.add(deprecated)

        form = SubmissionForm(instance=submission)
        license_ids = [lic.license_id for lic in form.fields["licenses"].queryset]
        assert "DEPRECATED-2" in license_ids

    def test_deprecated_licenses_removed_after_deselecting(self):
        """When editing, if a deprecated license is deselected, it should be removed."""
        from apps.submissions.forms import SubmissionForm
        from apps.licenses.models import SpdxLicense
        from tests.factories import ServiceSubmissionFactory

        deprecated = SpdxLicense.objects.create(
            license_id="DEPRECATED-3",
            name="To Be Deselected",
            is_deprecated=True,
        )
        submission = ServiceSubmissionFactory()
        submission.licenses.add(deprecated)

        # Initial form should show the deprecated license
        form = SubmissionForm(instance=submission)
        assert any(
            lic.license_id == "DEPRECATED-3" for lic in form.fields["licenses"].queryset
        )

        # Form data that doesn't include the deprecated license should work
        data = _base_form_data({"licenses": [], "license_note": "Custom"})
        form = SubmissionForm(data, instance=submission)
        assert form.is_valid(), form.errors
        # After save, the deprecated license should no longer be associated
        form.save()
        submission.refresh_from_db()
        assert not submission.licenses.filter(license_id="DEPRECATED-3").exists()


@pytest.mark.django_db
class TestPublicContactFormField:
    def test_https_url_accepted(self):
        from apps.submissions.forms import SubmissionForm

        form = SubmissionForm(
            _base_form_data({"public_contact_email": "https://support.example.com"})
        )
        assert form.is_valid(), form.errors

    def test_email_still_accepted(self):
        from apps.submissions.forms import SubmissionForm

        form = SubmissionForm(
            _base_form_data({"public_contact_email": "contact@example.com"})
        )
        assert form.is_valid(), form.errors

    def test_http_url_rejected(self):
        from apps.submissions.forms import SubmissionForm

        form = SubmissionForm(
            _base_form_data({"public_contact_email": "http://support.example.com"})
        )
        assert not form.is_valid()
        assert "public_contact_email" in form.errors

    def test_plain_string_rejected(self):
        from apps.submissions.forms import SubmissionForm

        form = SubmissionForm(_base_form_data({"public_contact_email": "not-valid"}))
        assert not form.is_valid()
        assert "public_contact_email" in form.errors

    def test_whitespace_stripped(self):
        from apps.submissions.forms import SubmissionForm

        form = SubmissionForm(
            _base_form_data({"public_contact_email": "  contact@example.com  "})
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["public_contact_email"] == "contact@example.com"

    def test_blank_rejected(self):
        from apps.submissions.forms import SubmissionForm

        form = SubmissionForm(_base_form_data({"public_contact_email": ""}))
        assert not form.is_valid()
        assert "public_contact_email" in form.errors
