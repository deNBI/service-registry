"""
Submission Models
=================
Core models for the de.NBI service registration system.

Models:
  - ServiceSubmission  : The full registration record for one service.
  - SubmissionAPIKey   : API keys linked to a submission (one or more per submission).

Security notes:
  - SubmissionAPIKey stores SHA-256 hashes only; plaintext keys are never persisted.
  - hmac.compare_digest is used for all key lookups (constant-time comparison).
  - Revoked keys (is_active=False) return the same 403 as invalid keys.
"""

import hashlib
import hmac
import re
import secrets
import unicodedata
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.registry.models import PrincipalInvestigator, ServiceCategory, ServiceCenter


# ---------------------------------------------------------------------------
# Shared validation constants (imported by forms.py and serializers.py)
# ---------------------------------------------------------------------------

#: Allowed length range for service_description (enforced in clean() and the form).
DESCRIPTION_MIN_LENGTH: int = 50
DESCRIPTION_MAX_LENGTH: int = 5000

#: Upper bound on comma-separated publication entries.
PUBLICATIONS_MAX_COUNT: int = 50

#: Pre-compiled regexes for publication validation (reused by PublicationsField).
_PMID_RE = re.compile(r"^\d{1,8}$")
_DOI_RE = re.compile(r"^10\.\d{4,}/\S+$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _logo_upload_to(instance, filename: str) -> str:
    """
    Generate a UUID-based storage path for logo uploads.
    The original filename is discarded — only the extension is reused after
    logo_utils.py has already validated and normalised it.
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"logos/{uuid.uuid4()}.{ext}"


def _sanitise_text(value: str) -> str:
    """
    Sanitise free-text input:
      - Strip null bytes (prevent DB errors and log injection)
      - Normalise to Unicode NFC (prevent homoglyph attacks)
      - Strip leading/trailing whitespace
    """
    if not value:
        return value
    value = value.replace("\x00", "")
    value = unicodedata.normalize("NFC", value)
    return value.strip()


def _validate_https_url(value: str) -> None:
    """Reject any URL that does not use the https:// scheme."""
    if value and not value.startswith("https://"):
        raise ValidationError(
            _(
                "URL must use the https:// scheme. Plain http:// and other schemes are not accepted."
            ),
            code="insecure_url",
        )


def _validate_github_url(value: str) -> None:
    if value and not value.startswith("https://github.com/"):
        raise ValidationError(_("GitHub URL must start with https://github.com/"))


def _validate_biotools_url(value: str) -> None:
    if value and not value.startswith("https://bio.tools/"):
        raise ValidationError(_("bio.tools URL must start with https://bio.tools/"))


def _validate_fairsharing_url(value: str) -> None:
    if value and not value.startswith("https://fairsharing.org/"):
        raise ValidationError(
            _("FAIRsharing URL must start with https://fairsharing.org/")
        )


def _validate_publications(value: str) -> None:
    """Validate comma-separated PMIDs or DOIs."""
    if not value:
        return
    tokens = [t.strip() for t in value.split(",") if t.strip()]
    if not tokens:
        raise ValidationError(_("At least one PMID or DOI is required."))
    if len(tokens) > PUBLICATIONS_MAX_COUNT:
        raise ValidationError(
            _(f"A maximum of {PUBLICATIONS_MAX_COUNT} publications may be listed.")
        )
    for token in tokens:
        if not (_PMID_RE.match(token) or _DOI_RE.match(token)):
            raise ValidationError(
                _(
                    f"'{token}' is not a valid PMID (digits only) or DOI (starts with 10.xxxx/)."
                )
            )


# ---------------------------------------------------------------------------
# ServiceSubmission
# ---------------------------------------------------------------------------


class SubmissionStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    SUBMITTED = "submitted", _("Submitted")
    UNDER_REVIEW = "under_review", _("Under Review")
    APPROVED = "approved", _("Approved")
    REJECTED = "rejected", _("Rejected")
    DEPRECATED = "deprecated", _("Deprecated")


class KpiMonitoring(models.TextChoices):
    YES = "yes", _("Yes")
    PLANNED = "planned", _("Planned")


LICENSE_CHOICES = [
    ("agpl3", "GNU AGPLv3"),
    ("gpl3", "GNU GPLv3"),
    ("lgpl3", "GNU LGPLv3"),
    ("mpl2", "Mozilla Public License 2.0"),
    ("apache2", "Apache License 2.0"),
    ("mit", "MIT License"),
    ("boost", "Boost Software License 1.0"),
    ("unlicense", "The Unlicense"),
    ("other", "None of the above"),
    ("na", "Not applicable"),
]


class ServiceSubmission(models.Model):
    """
    A single service registration submitted to de.NBI & ELIXIR-DE.

    The full form maps to sections A–G. All required fields raise
    ValidationError if blank on clean(). The status lifecycle is:

        draft → submitted → under_review → approved / rejected / deprecated

    Sensitive internal fields (internal_contact_email, submission_ip,
    user_agent_hash) are never serialised in API responses.
    """

    # -- Meta --
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
        help_text="Unique identifier for this submission (UUID).",
    )
    status = models.CharField(
        max_length=20,
        choices=SubmissionStatus.choices,
        default=SubmissionStatus.SUBMITTED,
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Stored for abuse investigation; never exposed in API or admin list views
    submission_ip = models.GenericIPAddressField(null=True, blank=True)
    # SHA-256 of raw User-Agent — used for bot pattern detection; raw UA not stored
    user_agent_hash = models.CharField(max_length=64, blank=True)

    # -- Section A: General --
    date_of_entry = models.DateField(
        help_text="Date this form was filled in.",
    )
    submitter_first_name = models.CharField(
        max_length=100,
        default="",
        help_text="First name of the person filling in this form.",
    )
    submitter_last_name = models.CharField(
        max_length=100,
        default="",
        help_text="Last name (surname) of the person filling in this form.",
    )
    submitter_affiliation = models.CharField(
        max_length=300,
        default="",
        help_text="Institute or organisation affiliation of the person filling in this form.",
    )

    @property
    def submitter_name(self) -> str:
        """Legacy computed property — full name + affiliation as a single string."""
        parts = []
        name = f"{self.submitter_first_name} {self.submitter_last_name}".strip()
        if name:
            parts.append(name)
        if self.submitter_affiliation:
            parts.append(self.submitter_affiliation)
        return ", ".join(parts)

    register_as_elixir = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Whether to also register this service as an ELIXIR-DE service.",
    )

    # -- Section B: Service Master Data --
    service_name = models.CharField(
        max_length=300,
        help_text="Official name of the service.",
    )
    service_description = models.TextField(
        help_text=(
            "Description of the service, including technology used "
            "(e.g. AI/ML, programming language, data types handled)."
        ),
    )
    year_established = models.IntegerField(
        db_index=True,
        help_text="Year the service was first established (YYYY).",
    )
    service_categories = models.ManyToManyField(
        ServiceCategory,
        related_name="submissions",
        help_text="Select all service types that apply.",
    )

    # EDAM ontology annotations — chosen by the submitter via the form
    # These represent how the team classifies their own service and may differ
    # from bio.tools annotations (which are stored separately in BioToolsRecord).
    edam_topics = models.ManyToManyField(
        "edam.EdamTerm",
        blank=True,
        related_name="submissions_by_topic",
        limit_choices_to={"branch": "topic", "is_obsolete": False},
        help_text=(
            "EDAM Topic terms describing the scientific domain of this service "
            "(e.g. Proteomics, Genomics). Select up to 6."
        ),
    )
    edam_operations = models.ManyToManyField(
        "edam.EdamTerm",
        blank=True,
        related_name="submissions_by_operation",
        limit_choices_to={"branch": "operation", "is_obsolete": False},
        help_text=(
            "EDAM Operation terms describing what this service does "
            "(e.g. Sequence alignment, Visualisation). Select up to 6."
        ),
    )

    is_toolbox = models.BooleanField(
        default=False,
        help_text="True if this service is a toolbox of consolidated services or part of one.",
    )
    toolbox_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Name of the toolbox (required if is_toolbox is True).",
    )
    user_knowledge_required = models.TextField(
        blank=True,
        help_text="Any prerequisites for users to install, run, or use this service.",
    )
    publications_pmids = models.TextField(
        help_text=(
            "Comma-separated list of PMIDs or DOIs for publications connected to this service. "
            "PMIDs are required for ELIXIR impact assessment."
        ),
        validators=[_validate_publications],
    )

    # -- Section C: Responsibilities --
    responsible_pis = models.ManyToManyField(
        PrincipalInvestigator,
        related_name="submissions",
        help_text="PI(s) responsible for this service.",
    )
    associated_partner_note = models.TextField(
        blank=True,
        help_text=(
            "Required if 'Associated partner' is selected as a responsible PI. "
            "Provide full name and affiliation."
        ),
    )
    host_institute = models.CharField(
        max_length=300,
        help_text="The institute hosting this service.",
    )
    service_center = models.ForeignKey(
        ServiceCenter,
        on_delete=models.PROTECT,  # Prevent accidental centre deletion
        related_name="submissions",
        help_text="Associated de.NBI service centre.",
    )
    public_contact_email = models.EmailField(
        help_text=(
            "Contact email displayed publicly on the de.NBI services page. "
            "This address is publicly visible."
        ),
    )
    internal_contact_name = models.CharField(
        max_length=200,
        help_text="Name and affiliation of the internal contact person (admin use only).",
    )
    internal_contact_email = models.EmailField(
        help_text=(
            "Email address of the internal contact for administration office use. "
            "This address is NEVER publicly visible."
        ),
    )

    # -- Section D: Websites & Links --
    website_url = models.URLField(
        max_length=2000,
        validators=[_validate_https_url],
        help_text="Public website URL of the service (must use https://).",
    )
    terms_of_use_url = models.URLField(
        max_length=2000,
        validators=[_validate_https_url],
        help_text="URL to the service's terms of use (must use https://).",
    )
    license = models.CharField(
        max_length=20,
        choices=LICENSE_CHOICES,
        help_text="License governing use of this service.",
    )
    github_url = models.URLField(
        max_length=2000,
        blank=True,
        validators=[_validate_https_url, _validate_github_url],
        help_text="Link to GitHub repository (optional; must be https://github.com/...).",
    )
    biotools_url = models.URLField(
        max_length=2000,
        blank=True,
        validators=[_validate_https_url, _validate_biotools_url],
        help_text="Link to bio.tools entry (optional).",
    )
    fairsharing_url = models.URLField(
        max_length=2000,
        blank=True,
        validators=[_validate_https_url, _validate_fairsharing_url],
        help_text="Link to FAIRsharing.org entry (optional).",
    )
    other_registry_url = models.URLField(
        max_length=2000,
        blank=True,
        validators=[_validate_https_url],
        help_text="Link to any other registry entry (optional).",
    )

    # -- Section E: KPIs --
    kpi_monitoring = models.CharField(
        max_length=10,
        choices=KpiMonitoring.choices,
        help_text="Whether KPI monitoring is currently in place or planned.",
    )
    kpi_start_year = models.CharField(
        max_length=100,
        blank=True,
        default="",
        help_text=(
            "Year KPI monitoring started, or estimated start year if planned. "
            "Use YYYY format or a short description. "
            "Not required when KPI monitoring is set to 'Planned'."
        ),
    )

    # -- Section F: Discoverability & Outreach --
    keywords_uncited = models.TextField(
        blank=True,
        help_text=(
            "Search keywords to identify usage without formal citation "
            "(e.g. tool name mentioned in methods but not in bibliography)."
        ),
    )
    keywords_seo = models.TextField(
        blank=True,
        help_text="Keywords for search engine optimisation of the service's listing page.",
    )
    survey_participation = models.BooleanField(
        default=True,
        help_text="Willingness to participate in de.NBI user surveys.",
    )
    comments = models.TextField(
        blank=True,
        help_text="Any additional comments for the de.NBI administration office.",
    )

    # -- Logo --
    logo = models.FileField(
        upload_to=_logo_upload_to,
        null=True,
        blank=True,
        help_text=(
            "Optional service logo (PNG, JPEG, or SVG). "
            "Max 10 MB. Old logo files are retained on disk when replaced."
        ),
    )

    # -- Section G: Consent --
    data_protection_consent = models.BooleanField(
        default=False,
        help_text="Consent to data protection information and privacy policy.",
    )

    # -- Change tracking --
    last_change_summary = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "Structured record of the most recent field-level change. "
            "Written by EditView (submitter edits) and ServiceSubmissionAdmin "
            "(admin edits). Schema: "
            '{"changed_by": "submitter|admin:<username>", '
            '"changed_at": "<ISO-8601>", '
            '"changes": [{"field":…, "label":…, "old":…, "new":…}]}'
        ),
    )

    # DRF's throttle system calls request.user.is_authenticated.
    # ServiceSubmission is used as request.user by SubmissionAPIKeyAuthentication,
    # so it must satisfy this interface. A keyed submission is always "authenticated".
    is_authenticated = True

    class Meta:
        verbose_name = "Service Submission"
        verbose_name_plural = "Service Submissions"
        ordering = ["-submitted_at"]
        permissions = [
            # Semantic permission for the approve/reject status transitions.
            # Separating this from change_servicesubmission lets organisations
            # grant editing rights without granting final-decision rights.
            (
                "approve_servicesubmission",
                "Can approve or reject service submissions",
            ),
            # Guards all three key-management operations (issue, reset, revoke).
            # Kept separate so read-only auditors can view key metadata without
            # being able to create credentials that grant submitter write access.
            (
                "manage_apikeys",
                "Can issue, reset, and revoke submission API keys",
            ),
        ]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["submitted_at"]),
            models.Index(fields=["service_center"]),
            # Compound index for the admin list view default sort + status filter
            models.Index(fields=["-submitted_at", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.service_name} ({self.get_status_display()})"

    def clean(self) -> None:
        """Cross-field validation called by forms and serialisers."""
        errors = {}

        # Toolbox name required when is_toolbox=True
        if self.is_toolbox and not self.toolbox_name:
            errors["toolbox_name"] = _(
                "Please provide the toolbox name since this service is part of a toolbox."
            )

        # KPI start year required when monitoring is active (not "planned")
        if self.kpi_monitoring and self.kpi_monitoring != "planned":
            if not (self.kpi_start_year or "").strip():
                errors["kpi_start_year"] = _(
                    "Please provide the year KPI monitoring started."
                )

        # Year range check
        from django.utils import timezone as tz

        current_year = tz.now().year
        if self.year_established and not (
            1900 <= self.year_established <= current_year
        ):
            errors["year_established"] = _(
                f"Year must be between 1900 and {current_year}."
            )

        # Description length bounds
        if self.service_description:
            desc_len = len(self.service_description.strip())
            if desc_len < DESCRIPTION_MIN_LENGTH:
                errors["service_description"] = _(
                    f"Service description must be at least {DESCRIPTION_MIN_LENGTH} characters."
                )
            elif desc_len > DESCRIPTION_MAX_LENGTH:
                errors["service_description"] = _(
                    f"Service description must not exceed {DESCRIPTION_MAX_LENGTH} characters."
                )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs) -> None:
        """Sanitise free-text fields before saving."""
        text_fields = [
            "submitter_first_name",
            "submitter_last_name",
            "submitter_affiliation",
            "service_name",
            "service_description",
            "toolbox_name",
            "user_knowledge_required",
            "host_institute",
            "internal_contact_name",
            "associated_partner_note",
            "kpi_start_year",
            "keywords_uncited",
            "keywords_seo",
            "comments",
        ]
        for field in text_fields:
            value = getattr(self, field, "")
            if value:
                setattr(self, field, _sanitise_text(value))
        super().save(**kwargs)


# ---------------------------------------------------------------------------
# SubmissionChangeLog
# ---------------------------------------------------------------------------


class SubmissionChangeLog(models.Model):
    """
    Append-only audit log for field-level changes to a ServiceSubmission.

    One row is written per edit event — regardless of whether the edit came
    from the submitter's web form, an admin save, or an API PATCH.

    Schema for ``changes`` (mirrors diff_utils.build_diff output):
        [{"field": "service_name", "label": "Service Name", "old": "...", "new": "..."}]
    """

    submission = models.ForeignKey(
        ServiceSubmission,
        on_delete=models.CASCADE,
        related_name="change_log",
    )
    changed_by = models.CharField(
        max_length=200,
        help_text=(
            'Who made this change. Format: "submitter", "admin:<username>", '
            'or "api:<key_label>".'
        ),
    )
    changed_at = models.DateTimeField()
    changes = models.JSONField(
        help_text="List of changed fields: [{field, label, old, new}, ...]",
    )

    class Meta:
        ordering = ["-changed_at"]
        verbose_name = "Change Log Entry"
        verbose_name_plural = "Change Log"

    def __str__(self) -> str:
        return f"{self.changed_by} @ {self.changed_at:%Y-%m-%d %H:%M} ({len(self.changes)} fields)"


# ---------------------------------------------------------------------------
# SubmissionAPIKey
# ---------------------------------------------------------------------------


def _generate_key() -> str:
    """
    Generate a cryptographically secure URL-safe API key.

    Uses ``secrets.token_urlsafe(n)`` which draws from OS entropy.
    The entropy byte count is read from Django settings.
    Returns the plaintext key — the caller is responsible for hashing it
    before storage. The plaintext must never be written to the database.
    """
    n = getattr(settings, "API_KEY_ENTROPY_BYTES", 48)
    return secrets.token_urlsafe(n)


def _hash_key(plaintext: str) -> str:
    """
    Return the SHA-256 hex digest of a plaintext API key.

    This is the only form in which keys are stored persistently.
    """
    algorithm = getattr(settings, "API_KEY_HASH_ALGORITHM", "sha256")
    return hashlib.new(algorithm, plaintext.encode("utf-8")).hexdigest()


class SubmissionAPIKey(models.Model):
    """
    An API key granting access to a specific ServiceSubmission.

    A submission may have multiple active keys (e.g. original submitter key
    plus a key issued to a CI pipeline). Each key can be revoked independently.

    Security design:
      - ``key_hash`` stores SHA-256(plaintext). The plaintext is generated in
        memory, shown once to the user, and then discarded — it is never
        written to the database, emails, or log files.
      - Lookups use ``hmac.compare_digest`` for constant-time comparison to
        prevent timing-oracle attacks.
      - Revoked keys (is_active=False) return the same HTTP 403 as an invalid
        key — the response gives no information about whether a key exists.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    submission = models.ForeignKey(
        ServiceSubmission,
        on_delete=models.CASCADE,
        related_name="api_keys",
    )
    key_hash = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="SHA-256 hash of the plaintext API key. The plaintext is never stored.",
        editable=False,
    )
    label = models.CharField(
        max_length=100,
        default="Initial key",
        help_text="Human-readable label describing why this key was issued.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.CharField(
        max_length=150,
        default="submitter",
        help_text="'submitter' for keys issued on form submission, or admin username.",
    )
    SCOPE_READ = "read"
    SCOPE_WRITE = "write"
    SCOPE_CHOICES = [
        (SCOPE_READ, "Read-only  (GET retrieve only)"),
        (SCOPE_WRITE, "Read-write (GET retrieve + PATCH update)"),
    ]

    is_active = models.BooleanField(
        default=True,
        help_text=(
            "Set False to revoke this key. Revoked keys are retained for audit purposes "
            "and cannot be re-activated."
        ),
    )
    scope = models.CharField(
        max_length=10,
        choices=SCOPE_CHOICES,
        default=SCOPE_WRITE,
        help_text="'read' = GET only; 'write' = GET + PATCH.",
    )
    last_used_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of the most recent successful authentication with this key.",
    )

    class Meta:
        verbose_name = "Submission API Key"
        verbose_name_plural = "Submission API Keys"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        status = "active" if self.is_active else "revoked"
        return f"{self.label} [{status}] — {self.submission.service_name}"

    # ------------------------------------------------------------------
    # Class-level key operations
    # ------------------------------------------------------------------

    @classmethod
    def create_for_submission(
        cls,
        submission: "ServiceSubmission",
        label: str = "Initial key",
        created_by: str = "submitter",
        scope: str = "write",
    ) -> tuple["SubmissionAPIKey", str]:
        """
        Generate a new API key for a submission.

        Returns:
            (SubmissionAPIKey instance, plaintext_key)

        The plaintext key must be shown to the user immediately and then
        discarded. It is the caller's responsibility to never store it.
        """
        plaintext = _generate_key()
        key_hash = _hash_key(plaintext)
        instance = cls.objects.create(
            submission=submission,
            key_hash=key_hash,
            label=label,
            created_by=created_by,
            scope=scope,
        )
        return instance, plaintext

    @classmethod
    def verify(cls, plaintext: str) -> tuple["SubmissionAPIKey | None", bool]:
        """
        Verify a plaintext API key and return the matching key object.

        Uses SHA-256 hashing and hmac.compare_digest for constant-time
        comparison. Returns (key_instance, authenticated) — authenticated
        is False if the key does not exist or is revoked.

        Never raises an exception on invalid keys — always returns a tuple.
        """
        candidate_hash = _hash_key(plaintext)

        # Iterate through all active keys whose hash matches.
        # Using select_related to avoid N+1 when accessing submission.
        try:
            # Use a constant-time approach: hash the input, then look up.
            # The DB lookup itself is O(1) thanks to the unique index.
            key = cls.objects.select_related("submission").get(
                key_hash=candidate_hash,
            )
        except cls.DoesNotExist:
            # Still do a dummy compare_digest to prevent timing oracle
            hmac.compare_digest(candidate_hash, "0" * 64)
            return None, False

        # Double-check with hmac.compare_digest (constant-time)
        if not hmac.compare_digest(key.key_hash, candidate_hash):
            return None, False

        if not key.is_active:
            return key, False  # Key exists but is revoked — return False

        # Update last_used_at without triggering model save signals
        cls.objects.filter(pk=key.pk).update(last_used_at=timezone.now())
        return key, True

    def revoke(self) -> None:
        """Revoke this key. Revoked keys cannot be re-activated."""
        self.is_active = False
        self.save(update_fields=["is_active"])
