"""
Submission Forms
================
Django forms for the service registration workflow.

Forms:
  - SubmissionForm       : The main registration form (all sections A–G).
  - UpdateKeyForm        : Single-field form to enter an API key for editing.
  - DraftSaveForm        : Minimal form for HTMX auto-save of drafts.

All required validation is enforced server-side here, even if also enforced
client-side via HTML5 attributes. The server is always authoritative.
"""

import unicodedata
from datetime import date
from pathlib import Path

import bleach
import yaml
from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from apps.registry.models import PrincipalInvestigator, ServiceCategory, ServiceCenter
from .models import (
    DESCRIPTION_MAX_LENGTH,
    DESCRIPTION_MIN_LENGTH,
    PUBLICATIONS_MAX_COUNT,
    KpiMonitoring,
    ServiceSubmission,
    _DOI_RE,
    _PMID_RE,
)
from .widgets import (
    EdamAutocompleteWidget,
    AffiliationComboboxWidget,
    CompactSelectWidget,
    CompactSelectSingleWidget,
)

# ---------------------------------------------------------------------------
# Form texts — loaded once from YAML at module import time
# ---------------------------------------------------------------------------
_FORM_TEXTS_PATH = Path(__file__).resolve().parent / "form_texts.yaml"
_FORM_TEXTS: dict = {}
try:
    with open(_FORM_TEXTS_PATH, encoding="utf-8") as f:
        _FORM_TEXTS = yaml.safe_load(f) or {}
except FileNotFoundError:
    pass  # Graceful fallback — fields keep their model help_text


# ---------------------------------------------------------------------------
# Bleach sanitiser — strips HTML tags from free-text fields
# ---------------------------------------------------------------------------
_ALLOWED_TAGS: list[str] = []  # No HTML allowed in any field
_ALLOWED_ATTRS: dict = {}


def _sanitise(value: str) -> str:
    """Strip HTML tags and normalise unicode NFC."""
    if not value:
        return value
    cleaned = bleach.clean(
        value, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS, strip=True
    )
    return unicodedata.normalize("NFC", cleaned).strip()


# ---------------------------------------------------------------------------
# Custom field: comma-separated PMIDs / DOIs
# ---------------------------------------------------------------------------


class PublicationsField(forms.CharField):
    """
    A CharField that validates each comma-separated entry as a PMID or DOI.
    Uses the shared regex constants and limits from models.py.
    """

    def validate(self, value: str) -> None:
        super().validate(value)
        if not value:
            return
        tokens = [t.strip() for t in value.split(",") if t.strip()]
        if not tokens:
            raise ValidationError(_("At least one PMID or DOI is required."))
        if len(tokens) > PUBLICATIONS_MAX_COUNT:
            raise ValidationError(
                _(f"A maximum of {PUBLICATIONS_MAX_COUNT} publications may be listed.")
            )
        invalid = [t for t in tokens if not (_PMID_RE.match(t) or _DOI_RE.match(t))]
        if invalid:
            raise ValidationError(
                _(
                    f"Invalid entries: {', '.join(invalid)}. "
                    "Each must be a PMID (digits only) or DOI (starts with 10.xxxx/)."
                )
            )


# ---------------------------------------------------------------------------
# SubmissionForm
# ---------------------------------------------------------------------------


class SubmissionForm(forms.ModelForm):
    """
    Full service registration form.

    Sections match the original de.NBI form (v1.1):
      A — General information
      B — Service master data
      C — Responsibilities
      D — Websites and links
      E — KPIs
      F — Discoverability and outreach
      G — Data protection consent

    Client-side behaviour (HTMX):
      - toolbox_name is shown/hidden via HTMX based on is_toolbox value
      - associated_partner_note is shown when "Associated partner" PI is selected
      - Email confirmation validated on blur
    """

    # Logo upload field (optional)
    logo = forms.FileField(
        label=_("Service logo"),
        required=False,
        help_text=_("Optional. PNG, JPEG, or SVG. Maximum 10 MB."),
        widget=forms.FileInput(
            attrs={
                "class": "form-control",
                "accept": ".png,.jpg,.jpeg,.svg,image/png,image/jpeg,image/svg+xml",
            }
        ),
    )

    # Email confirmation field (not stored — form-only)
    internal_contact_email_confirm = forms.EmailField(
        label=_("Confirm internal contact email"),
        help_text=_("Re-enter the internal contact email to confirm."),
        widget=forms.EmailInput(attrs={"class": "form-control", "autocomplete": "off"}),
    )

    # Override publications field to use custom validator
    publications_pmids = PublicationsField(
        label=_("Publication(s) connected to the service (PMIDs/DOIs)"),
        help_text=_(
            "Comma-separated PMIDs or DOIs. "
            "For ELIXIR impact assessment, PMIDs are required."
        ),
        widget=forms.Textarea(
            attrs={
                "rows": 2,
                "class": "form-control",
                "placeholder": "e.g. 12345678, 10.1000/xyz123",
            }
        ),
    )

    class Meta:
        model = ServiceSubmission
        exclude = [
            "status",
            "submitted_at",
            "updated_at",
            "submission_ip",
            "user_agent_hash",
        ]
        widgets = {
            # Section A
            "date_of_entry": forms.DateInput(
                attrs={
                    "type": "date",
                    "class": "form-control",
                    "id": "id_date_of_entry",
                }
            ),
            "submitter_first_name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "e.g. Ada"}
            ),
            "submitter_last_name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "e.g. Lovelace"}
            ),
            "submitter_affiliation": AffiliationComboboxWidget(
                placeholder="e.g. Forschungszentrum Jülich"
            ),
            "register_as_elixir": forms.RadioSelect(
                choices=[(True, "Yes"), (False, "No")]
            ),
            # Section B
            "service_name": forms.TextInput(attrs={"class": "form-control"}),
            "service_description": forms.Textarea(
                attrs={"class": "form-control", "rows": 5}
            ),
            "year_established": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "min": 1900,
                    "max": 2100,
                    "placeholder": "YYYY",
                }
            ),
            "service_categories": CompactSelectWidget(label="categories"),
            "is_toolbox": forms.RadioSelect(choices=[(True, "Yes"), (False, "No")]),
            "toolbox_name": forms.TextInput(
                attrs={"class": "form-control", "id": "id_toolbox_name"}
            ),
            "user_knowledge_required": forms.Textarea(
                attrs={"class": "form-control", "rows": 3}
            ),
            # EDAM ontology — searchable multi-select (Tom Select enhanced)
            "edam_topics": EdamAutocompleteWidget(
                branch="topic",
                placeholder="Search EDAM Topics (e.g. Proteomics, Genomics)…",
                attrs={"data-max-items": "6"},
            ),
            "edam_operations": EdamAutocompleteWidget(
                branch="operation",
                placeholder="Search EDAM Operations (e.g. Sequence alignment)…",
                attrs={"data-max-items": "6"},
            ),
            # Section C
            "responsible_pis": CompactSelectWidget(label="PIs"),
            "associated_partner_note": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "id": "id_associated_partner_note",
                }
            ),
            "host_institute": AffiliationComboboxWidget(
                placeholder="e.g. Forschungszentrum Jülich"
            ),
            "service_center": CompactSelectSingleWidget(label="de.NBI Service Center"),
            "public_contact_email": forms.EmailInput(attrs={"class": "form-control"}),
            "internal_contact_name": forms.TextInput(attrs={"class": "form-control"}),
            "internal_contact_email": forms.EmailInput(attrs={"class": "form-control"}),
            # Section D
            "website_url": forms.URLInput(
                attrs={"class": "form-control", "placeholder": "https://"}
            ),
            "terms_of_use_url": forms.URLInput(
                attrs={"class": "form-control", "placeholder": "https://"}
            ),
            "license": forms.Select(attrs={"class": "form-select"}),
            "github_url": forms.URLInput(
                attrs={"class": "form-control", "placeholder": "https://github.com/..."}
            ),
            "biotools_url": forms.URLInput(
                attrs={"class": "form-control", "placeholder": "https://bio.tools/..."}
            ),
            "fairsharing_url": forms.URLInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "https://fairsharing.org/...",
                }
            ),
            "other_registry_url": forms.URLInput(
                attrs={"class": "form-control", "placeholder": "https://"}
            ),
            # Section E
            "kpi_monitoring": forms.Select(
                attrs={"class": "form-select"}, choices=KpiMonitoring.choices
            ),
            "kpi_start_year": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "YYYY"}
            ),
            # Section F
            "keywords_uncited": forms.Textarea(
                attrs={"class": "form-control", "rows": 2}
            ),
            "keywords_seo": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "survey_participation": forms.RadioSelect(
                choices=[(True, "Yes"), (False, "No")]
            ),
            "comments": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            # Section G
            "data_protection_consent": forms.CheckboxInput(
                attrs={"class": "form-check-input"}
            ),
        }
        labels = {
            "date_of_entry": _("Date of information entered"),
            "submitter_first_name": _("First name"),
            "submitter_last_name": _("Last name"),
            "submitter_affiliation": _("Institute / Affiliation"),
            "register_as_elixir": _("Also register as an ELIXIR-DE Service?"),
            "service_name": _("Name of the Service"),
            "service_description": _("Service description"),
            "year_established": _("Year of Service establishment"),
            "service_categories": _("Service category — select all that apply"),
            "is_toolbox": _("Is this service a toolbox or part of a toolbox?"),
            "toolbox_name": _("Name of the de.NBI toolbox"),
            "user_knowledge_required": _("User knowledge required"),
            "publications_pmids": _("Publication(s) connected to the service"),
            "edam_topics": _("EDAM Topics — scientific domain of this service"),
            "edam_operations": _("EDAM Operations — what does this service do?"),
            "responsible_pis": _("Name(s) of the PI(s) responsible for the service"),
            "associated_partner_note": _("Associated partner details"),
            "host_institute": _("Host institute of the Service"),
            "service_center": _("Associated de.NBI Service Center"),
            "public_contact_email": _("Public contact email or support form"),
            "internal_contact_name": _("Internal contact person (name & affiliation)"),
            "internal_contact_email": _("Internal contact email"),
            "website_url": _("Link to the service website"),
            "terms_of_use_url": _("Link to the service terms of use"),
            "license": _("License attached to the service"),
            "github_url": _("Link to GitHub repository"),
            "biotools_url": _("Link to bio.tools entry"),
            "fairsharing_url": _("Link to FAIRsharing.org entry"),
            "other_registry_url": _("Link to any other registry"),
            "kpi_monitoring": _("Is KPI monitoring in place?"),
            "kpi_start_year": _("Year KPI monitoring started"),
            "keywords_uncited": _("Keywords to identify usage without proper citation"),
            "keywords_seo": _("SEO-relevant keywords"),
            "survey_participation": _("User survey participation"),
            "comments": _("Any Comments"),
            "data_protection_consent": _(
                "I agree and give consent to the data protection information described above"
            ),
        }

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # Auto-populate date_of_entry with today if not already set
        if not self.instance.pk and not self.data.get("date_of_entry"):
            self.fields["date_of_entry"].initial = date.today()

        # kpi_start_year is conditionally required (not required when monitoring="planned")
        # The actual validation logic lives in clean() — mark it not required here
        # so Django's field-level validation doesn't fire before clean() can check.
        self.fields["kpi_start_year"].required = False

        # Limit dropdowns to active entries only
        self.fields["service_categories"].queryset = ServiceCategory.objects.filter(
            is_active=True
        )
        self.fields["service_center"].queryset = ServiceCenter.objects.filter(
            is_active=True
        )
        self.fields["responsible_pis"].queryset = PrincipalInvestigator.objects.filter(
            is_active=True
        )

        # EDAM term querysets — only active, non-obsolete terms per branch
        from apps.edam.models import EdamTerm

        self.fields["edam_topics"].queryset = EdamTerm.objects.filter(
            branch="topic", is_obsolete=False
        ).order_by("label")
        self.fields["edam_operations"].queryset = EdamTerm.objects.filter(
            branch="operation", is_obsolete=False
        ).order_by("label")

        # Affiliation combobox — suggestions from PI institutes + past submissions
        pi_institutes = set(
            PrincipalInvestigator.objects.filter(is_active=True)
            .exclude(institute="")
            .values_list("institute", flat=True)
        )
        past_affiliations = set(
            ServiceSubmission.objects.exclude(submitter_affiliation="").values_list(
                "submitter_affiliation", flat=True
            )
        )
        suggestions = sorted(pi_institutes | past_affiliations, key=str.casefold)
        # Include the current instance value so edit pre-fill works even if not in list
        current_affiliation = (
            self.instance.submitter_affiliation or ""
            if self.instance and self.instance.pk
            else ""
        )
        if current_affiliation and current_affiliation not in suggestions:
            suggestions = [current_affiliation] + suggestions
        self.fields["submitter_affiliation"].widget.choices = [("", "")] + [
            (s, s) for s in suggestions
        ]

        # Host institute — same suggestion pool, but also include the current value
        current_host = (
            self.instance.host_institute or ""
            if self.instance and self.instance.pk
            else ""
        )
        host_suggestions = suggestions
        if current_host and current_host not in host_suggestions:
            host_suggestions = [current_host] + list(host_suggestions)
        self.fields["host_institute"].widget.choices = [("", "")] + [
            (s, s) for s in host_suggestions
        ]

        # Pre-fill email confirmation from instance
        if self.instance.pk:
            self.fields[
                "internal_contact_email_confirm"
            ].initial = self.instance.internal_contact_email

        # Apply YAML-driven help text, tooltip, and label attributes
        for field_name, field_obj in self.fields.items():
            texts = _FORM_TEXTS.get(field_name, {})
            if texts.get("help"):
                field_obj.help_text = texts["help"]
            if texts.get("label"):
                field_obj.label = texts["label"]
            field_obj.tooltip = texts.get("tooltip", "").strip()

        # Expose section descriptions for template rendering
        self.section_texts = _FORM_TEXTS.get("sections", {})

    # -- Cross-field validation --

    def clean_submitter_first_name(self) -> str:
        value = _sanitise(self.cleaned_data.get("submitter_first_name", ""))
        if len(value) < 2:
            raise ValidationError(_("First name must be at least 2 characters."))
        return value

    def clean_submitter_last_name(self) -> str:
        value = _sanitise(self.cleaned_data.get("submitter_last_name", ""))
        if len(value) < 2:
            raise ValidationError(_("Last name must be at least 2 characters."))
        return value

    def clean_submitter_affiliation(self) -> str:
        value = _sanitise(self.cleaned_data.get("submitter_affiliation", ""))
        if len(value) < 2:
            raise ValidationError(_("Affiliation must be at least 2 characters."))
        return value

    def clean_service_name(self) -> str:
        value = self.cleaned_data.get("service_name", "")
        value = _sanitise(value)
        if len(value) < 3:
            raise ValidationError(_("Service name must be at least 3 characters."))
        return value

    def clean_service_description(self) -> str:
        value = _sanitise(self.cleaned_data.get("service_description", ""))
        if len(value) < DESCRIPTION_MIN_LENGTH:
            raise ValidationError(
                _(f"Description must be at least {DESCRIPTION_MIN_LENGTH} characters.")
            )
        if len(value) > DESCRIPTION_MAX_LENGTH:
            raise ValidationError(
                _(f"Description must not exceed {DESCRIPTION_MAX_LENGTH} characters.")
            )
        return value

    def clean_internal_contact_email_confirm(self) -> str:
        email = self.cleaned_data.get("internal_contact_email")
        confirm = self.cleaned_data.get("internal_contact_email_confirm")
        if email and confirm and email != confirm:
            raise ValidationError(_("Email addresses do not match."))
        return confirm

    def clean_logo(self):
        f = self.cleaned_data.get("logo")
        if not f:
            return f  # Optional — None/empty is valid
        from .logo_utils import validate_and_process_logo

        return validate_and_process_logo(f)

    def clean_data_protection_consent(self) -> bool:
        value = self.cleaned_data.get("data_protection_consent")
        if not value:
            raise ValidationError(
                _("You must consent to the data protection information to submit.")
            )
        return value

    def clean(self) -> dict:
        cleaned = super().clean()

        # Toolbox name required when is_toolbox=True
        is_toolbox = cleaned.get("is_toolbox")
        toolbox_name = cleaned.get("toolbox_name", "").strip()
        if is_toolbox and not toolbox_name:
            self.add_error(
                "toolbox_name",
                _("Toolbox name is required when the service is part of a toolbox."),
            )

        # associated_partner_note required if "Associated partner" PI is selected
        responsible_pis = cleaned.get("responsible_pis")
        if responsible_pis:
            has_associated = responsible_pis.filter(is_associated_partner=True).exists()
            if (
                has_associated
                and not cleaned.get("associated_partner_note", "").strip()
            ):
                self.add_error(
                    "associated_partner_note",
                    _(
                        "Please provide the name and affiliation of the associated partner."
                    ),
                )

        # kpi_start_year is only required when KPI monitoring is already active (not "planned")
        kpi_monitoring = cleaned.get("kpi_monitoring", "")
        kpi_start_year = (
            cleaned.get("kpi_start_year", "").strip()
            if cleaned.get("kpi_start_year")
            else ""
        )
        if kpi_monitoring and kpi_monitoring != "planned":
            if not kpi_start_year:
                self.add_error(
                    "kpi_start_year",
                    _("Please provide the year KPI monitoring started."),
                )
        else:
            # Clear any "required" error Django may have raised on this optional-when-planned field
            if "kpi_start_year" in self._errors:
                del self._errors["kpi_start_year"]
            cleaned["kpi_start_year"] = ""

        return cleaned


# ---------------------------------------------------------------------------
# UpdateKeyForm — enter API key to retrieve a submission for editing
# ---------------------------------------------------------------------------


class UpdateKeyForm(forms.Form):
    """
    Simple form shown at /update/ asking for an API key.
    The key is verified against SubmissionAPIKey.verify().
    """

    api_key = forms.CharField(
        label=_("Your API Key"),
        max_length=200,
        widget=forms.TextInput(
            attrs={
                "class": "form-control font-monospace",
                "placeholder": "Paste your API key here",
                "autocomplete": "off",
                "spellcheck": "false",
            }
        ),
        help_text=_(
            "Enter the API key you received when you submitted your service registration. "
            "The key is case-sensitive."
        ),
    )

    def clean_api_key(self) -> str:
        value = self.cleaned_data.get("api_key", "").strip()
        if not value:
            raise ValidationError(_("API key is required."))
        # Basic length sanity check (64-char urlsafe base64 from token_urlsafe(48))
        if len(value) < 20 or len(value) > 200:
            raise ValidationError(_("Invalid API key format."))
        return value
