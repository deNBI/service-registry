"""
API Serializers
===============
DRF serializers for the public REST API.

Security notes:
  - internal_contact_email and internal_contact_name are write-only:
    submitters must provide them on POST/PATCH but they are never returned
    in any GET response.
  - submission_ip and user_agent_hash are excluded from ALL serializer
    fields (neither readable nor writable via the API).
  - primary_maturity_tag and secondary_maturity_tags are read-only:
    visible in GET responses but only settable by admins via the backend.
  - The api_key field is write-only on creation — it is returned once
    in the POST response and never again.
"""

from django.core.exceptions import ObjectDoesNotExist
from rest_framework import serializers

from apps.biotools.models import BioToolsFunction, BioToolsRecord
from apps.edam.models import EdamTerm
from apps.registry.models import PrincipalInvestigator, ServiceCategory, ServiceCenter
from apps.submissions.models import (
    DESCRIPTION_MAX_LENGTH,
    DESCRIPTION_MIN_LENGTH,
    ServiceSubmission,
)


# ---------------------------------------------------------------------------
# Reference data serializers (read-only, admin-authenticated)
# ---------------------------------------------------------------------------


class ServiceCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceCategory
        fields = ["id", "name"]


class ServiceCenterSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceCenter
        fields = ["id", "short_name", "full_name", "website"]


class PrincipalInvestigatorSerializer(serializers.ModelSerializer):
    display_name = serializers.SerializerMethodField()

    class Meta:
        model = PrincipalInvestigator
        fields = ["id", "last_name", "first_name", "display_name", "institute", "orcid"]

    def get_display_name(self, obj) -> str:
        return obj.display_name


# ---------------------------------------------------------------------------
# Admin CRUD serializers — extend read-only counterparts with write fields.
# Used exclusively by the admin-authenticated CRUD viewsets; never embedded
# inside submission responses (which use the compact serializers above).
# ---------------------------------------------------------------------------


class ServiceCategoryAdminSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceCategory
        fields = ["id", "name", "is_active"]
        read_only_fields = ["id"]


class ServiceCenterAdminSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceCenter
        fields = ["id", "short_name", "full_name", "website", "is_active"]
        read_only_fields = ["id"]


class PrincipalInvestigatorAdminSerializer(serializers.ModelSerializer):
    """
    Full PI representation for admin CRUD.
    Includes email (not publicly visible) and status flags.
    """

    display_name = serializers.SerializerMethodField()

    class Meta:
        model = PrincipalInvestigator
        fields = [
            "id",
            "last_name",
            "first_name",
            "display_name",
            "email",
            "institute",
            "orcid",
            "is_active",
            "is_associated_partner",
        ]
        read_only_fields = ["id", "display_name"]

    def get_display_name(self, obj) -> str:
        return obj.display_name


# ---------------------------------------------------------------------------
# Submission serializers
# ---------------------------------------------------------------------------


class SubmissionDetailSerializer(serializers.ModelSerializer):
    """
    Full serializer for submission detail (GET) and update (PATCH).
    Excludes all internal / sensitive fields.
    """

    service_center_id = serializers.PrimaryKeyRelatedField(
        queryset=ServiceCenter.objects.filter(is_active=True),
        source="service_center",
        write_only=True,
    )
    service_center = ServiceCenterSerializer(read_only=True)
    service_categories = ServiceCategorySerializer(many=True, read_only=True)
    service_category_ids = serializers.PrimaryKeyRelatedField(
        queryset=ServiceCategory.objects.filter(is_active=True),
        source="service_categories",
        many=True,
        write_only=True,
    )
    responsible_pis = PrincipalInvestigatorSerializer(many=True, read_only=True)
    responsible_pi_ids = serializers.PrimaryKeyRelatedField(
        queryset=PrincipalInvestigator.objects.filter(is_active=True),
        source="responsible_pis",
        many=True,
        write_only=True,
    )
    # EDAM annotations (read: full objects; write: list of PKs)
    edam_topics = serializers.SerializerMethodField()
    edam_topic_ids = serializers.PrimaryKeyRelatedField(
        queryset=EdamTerm.objects.filter(branch="topic", is_obsolete=False),
        source="edam_topics",
        many=True,
        write_only=True,
        required=False,
    )
    edam_operations = serializers.SerializerMethodField()
    edam_operation_ids = serializers.PrimaryKeyRelatedField(
        queryset=EdamTerm.objects.filter(branch="operation", is_obsolete=False),
        source="edam_operations",
        many=True,
        write_only=True,
        required=False,
    )
    # bio.tools nested summary (read-only; updated by sync task)
    biotoolsrecord = serializers.SerializerMethodField()

    # Logo: write accepts a file upload; read returns an absolute URL or null
    logo = serializers.FileField(
        required=False,
        allow_null=True,
        allow_empty_file=False,
        write_only=True,
    )
    logo_url = serializers.SerializerMethodField()

    # License field — CharField so DRF's built-in ChoiceField rejection does not
    # fire before validate_license, which needs to allow legacy slugs on existing
    # submissions. All slug validation is handled in validate_license below.
    license = serializers.CharField(
        help_text="License governing use of this service.",
    )

    links = serializers.SerializerMethodField()

    class Meta:
        model = ServiceSubmission
        # Explicitly list fields — never use __all__ to prevent accidental leakage
        fields = [
            # Meta
            "id",
            "status",
            "submitted_at",
            "updated_at",
            # Section A
            "date_of_entry",
            "submitter_first_name",
            "submitter_last_name",
            "submitter_affiliation",
            "register_as_elixir",
            # Section B
            "service_name",
            "service_description",
            "year_established",
            "service_categories",
            "service_category_ids",
            "is_toolbox",
            "toolbox_name",
            "user_knowledge_required",
            "publications_pmids",
            # EDAM ontology annotations (submitter-selected)
            "edam_topics",
            "edam_topic_ids",
            "edam_operations",
            "edam_operation_ids",
            # Section C — internal contact fields are write-only (never returned in responses)
            "internal_contact_name",
            "internal_contact_email",
            "responsible_pis",
            "responsible_pi_ids",
            "associated_partner_note",
            "host_institute",
            "service_center",
            "service_center_id",
            "public_contact_email",
            # Section D
            "website_url",
            "terms_of_use_url",
            "license",
            "github_url",
            "biotools_url",
            "fairsharing_url",
            "other_registry_url",
            # Section E
            "kpi_monitoring",
            "kpi_start_year",
            # Logo
            "logo",
            "logo_url",
            # Section F
            "keywords_uncited",
            "keywords_seo",
            "survey_participation",
            "comments",
            # Section G — write-only; must be True to create/update
            "data_protection_consent",
            # bio.tools integrated record (auto-synced, read-only)
            "biotoolsrecord",
            # Maturity tags — read-only; set by admins via the backend only
            "primary_maturity_tag",
            "secondary_maturity_tags",
            # Links
            "links",
        ]
        read_only_fields = [
            "id",
            "status",
            "submitted_at",
            "updated_at",
            # Admin-only fields — submitters can read but never write these
            "primary_maturity_tag",
            "secondary_maturity_tags",
        ]
        extra_kwargs = {
            # Never echo consent back in responses — it is always True for valid records
            "data_protection_consent": {"write_only": True},
            # Internal contact fields: writable (POST/PATCH) but never returned in responses
            "internal_contact_name": {"write_only": True},
            "internal_contact_email": {"write_only": True},
        }

    def get_logo_url(self, obj) -> str | None:
        if not obj.logo:
            return None
        request = self.context.get("request")
        return request.build_absolute_uri(obj.logo.url) if request else obj.logo.url

    def validate_logo(self, value):
        if value is None:
            return value
        from apps.submissions.logo_utils import validate_and_process_logo

        return validate_and_process_logo(value)

    def get_edam_topics(self, obj) -> list:
        from apps.api.serializers import (
            EdamTermSerializer,
        )  # avoid circular at class level

        return EdamTermSerializer(obj.edam_topics.all(), many=True).data

    def get_edam_operations(self, obj) -> list:
        from apps.api.serializers import EdamTermSerializer

        return EdamTermSerializer(obj.edam_operations.all(), many=True).data

    def get_biotoolsrecord(self, obj) -> dict | None:
        """
        Embed the full bio.tools record — includes all synced metadata,
        structured function annotations (operations, inputs, outputs),
        resolved EDAM topic objects, publications, documentation links, etc.
        Returns null if no bio.tools record has been synced yet.
        """
        try:
            record = obj.biotoolsrecord
        except ObjectDoesNotExist:
            return None
        from apps.api.serializers import BioToolsRecordSerializer

        return BioToolsRecordSerializer(record, context=self.context).data

    def get_links(self, obj) -> dict:
        request = self.context.get("request")
        base = request.build_absolute_uri("/") if request else ""
        links = {
            "self": f"{base}api/v1/submissions/{obj.id}/",
            "schema": f"{base}api/schema/",
            "docs": f"{base}api/docs/",
        }
        try:
            links["biotoolsrecord"] = (
                f"{base}api/v1/biotools/{obj.biotoolsrecord.biotools_id}/"
            )
        except ObjectDoesNotExist:
            pass
        return links

    def validate_year_established(self, value):
        """Mirror model.clean(): year must be between 1900 and the current year."""
        if value is not None:
            from django.core.exceptions import ValidationError as DjangoVE
            from apps.submissions.validation import (
                validate_year_established as _validate,
            )

            try:
                _validate(value)
            except DjangoVE as e:
                raise serializers.ValidationError(e.message)
        return value

    def validate_service_description(self, value):
        """Mirror model.clean(): description must be within the allowed length range."""
        if value:
            from django.core.exceptions import ValidationError as DjangoVE
            from apps.submissions.validation import validate_description_length

            try:
                validate_description_length(
                    value, DESCRIPTION_MIN_LENGTH, DESCRIPTION_MAX_LENGTH
                )
            except DjangoVE as e:
                raise serializers.ValidationError(e.message)
        return value

    def validate_license(self, value):
        """Validate license slug against YAML choices or allow existing legacy values.

        For new submissions (no instance): only accept YAML-defined licenses.
        For existing submissions (instance exists): also allow previously valid
        licenses that may have been removed from YAML (preserves audit trail).
        """
        if not value:
            return value

        from apps.submissions.forms import _LICENSE_CHOICES

        allowed_slugs = {slug for slug, _ in _LICENSE_CHOICES}

        # If slug is in current YAML choices, it's always valid
        if value in allowed_slugs:
            return value

        # For new submissions (no PK yet), reject unknown slugs
        if self.instance is None or not self.instance.pk:
            raise serializers.ValidationError(
                f"'{value}' is not a valid license. "
                "Please select a license from the list."
            )

        # For existing submissions, allow the value to pass through (legacy data)
        # The diff system will display the raw slug if no label is defined
        return value

    def validate(self, data: dict) -> dict:
        """Cross-field validation mirroring model.clean() and the web form."""
        errors = {}

        # Toolbox name required when is_toolbox=True
        from django.core.exceptions import ValidationError as DjangoVE
        from apps.submissions.validation import (
            validate_toolbox_name,
            validate_kpi_start_year,
        )

        is_toolbox = data.get("is_toolbox", getattr(self.instance, "is_toolbox", False))
        toolbox_name = data.get(
            "toolbox_name", getattr(self.instance, "toolbox_name", "")
        )
        try:
            validate_toolbox_name(is_toolbox, toolbox_name or "")
        except DjangoVE as e:
            errors.update(
                {
                    k: v[0] if isinstance(v, list) else v
                    for k, v in e.message_dict.items()
                }
            )

        # KPI start year required when monitoring is active (not "planned")
        kpi_monitoring = data.get(
            "kpi_monitoring", getattr(self.instance, "kpi_monitoring", "")
        )
        # Prefer the explicitly submitted value over the existing instance value.
        # This ensures that PATCH with kpi_monitoring=yes, kpi_start_year=""
        # correctly fails validation even when the instance already has a year set.
        kpi_start_year = (
            data["kpi_start_year"]
            if "kpi_start_year" in data
            else getattr(self.instance, "kpi_start_year", "")
        )
        try:
            validate_kpi_start_year(kpi_monitoring or "", kpi_start_year or "")
        except DjangoVE as e:
            errors.update(
                {
                    k: v[0] if isinstance(v, list) else v
                    for k, v in e.message_dict.items()
                }
            )

        # data_protection_consent is mandatory on create; DRF does not call
        # model.clean() automatically, so we enforce it here.
        if self.instance is None and not data.get("data_protection_consent"):
            errors["data_protection_consent"] = (
                "You must consent to the data protection information to submit this form."
            )

        # associated_partner_note required when an associated-partner PI is selected.
        # Mirrors SubmissionForm.clean() — DRF does not run the web form's clean().
        responsible_pi_ids = data.get("responsible_pi_ids")
        if responsible_pi_ids is not None:
            # responsible_pi_ids was explicitly submitted — check the resolved objects.
            from apps.registry.models import PrincipalInvestigator

            has_associated = PrincipalInvestigator.objects.filter(
                pk__in=responsible_pi_ids, is_associated_partner=True
            ).exists()
        elif self.instance is not None:
            # Partial update: responsible_pis not changed — check existing instance.
            has_associated = self.instance.responsible_pis.filter(
                is_associated_partner=True
            ).exists()
        else:
            has_associated = False

        if has_associated:
            note = (
                data["associated_partner_note"]
                if "associated_partner_note" in data
                else getattr(self.instance, "associated_partner_note", "")
            )
            if not (note or "").strip():
                errors["associated_partner_note"] = (
                    "Please provide the name and affiliation of the associated partner."
                )

        if errors:
            raise serializers.ValidationError(errors)

        return data


class SubmissionListSerializer(SubmissionDetailSerializer):
    """
    Serializer for the list endpoint.

    Returns all submission fields but embeds a compact bio.tools summary instead
    of the full nested record, keeping list payloads significantly smaller.
    Write-only fields (…_ids) are suppressed automatically since they are
    declared write_only=True on the parent.
    """

    def get_biotoolsrecord(self, obj) -> dict | None:
        try:
            record = obj.biotoolsrecord
        except ObjectDoesNotExist:
            return None
        return BioToolsRecordSummarySerializer(record, context=self.context).data


class SubmissionCreateSerializer(SubmissionDetailSerializer):
    """
    Serializer for POST /api/v1/submissions/.
    Returns the plaintext API key in the response — it is write-only and
    never returned again.
    """

    api_key = serializers.CharField(read_only=True)

    class Meta(SubmissionDetailSerializer.Meta):
        fields = SubmissionDetailSerializer.Meta.fields + ["api_key"]

    def to_representation(self, instance):
        """Inject the one-time plaintext key if present in context."""
        data = super().to_representation(instance)
        plaintext = self.context.get("api_key_plaintext")
        if plaintext:
            data["api_key"] = plaintext
            data["api_key_warning"] = (
                "This key is shown ONCE. Store it securely — it cannot be retrieved."
            )
        return data


# ---------------------------------------------------------------------------
# EDAM serializers
# ---------------------------------------------------------------------------


class EdamTermSerializer(serializers.ModelSerializer):
    """
    Compact EDAM term representation for embedding in submission responses.
    Full EDAM detail is available at GET /api/v1/edam/{accession}/.
    """

    url = serializers.SerializerMethodField()

    class Meta:
        model = EdamTerm
        fields = [
            "uri",  # canonical, globally unique (use this in machine consumers)
            "accession",  # short form, e.g. topic_0091
            "branch",  # topic | operation | data | format | identifier
            "label",  # human-readable name
            "url",  # EDAM ontology page
        ]

    def get_url(self, obj) -> str:
        return obj.url


class EdamTermDetailSerializer(EdamTermSerializer):
    """Full EDAM term including definition, synonyms, parent."""

    parent = EdamTermSerializer(read_only=True)

    class Meta(EdamTermSerializer.Meta):
        fields = EdamTermSerializer.Meta.fields + [
            "definition",
            "synonyms",
            "parent",
            "edam_version",
        ]


# ---------------------------------------------------------------------------
# bio.tools serializers
# ---------------------------------------------------------------------------


class BioToolsFunctionSerializer(serializers.ModelSerializer):
    """
    One functional annotation block from bio.tools.
    operations/inputs/outputs are structured JSON — EDAM URIs are included
    so machine consumers can resolve them against the EDAM endpoint.
    """

    class Meta:
        model = BioToolsFunction
        fields = ["position", "operations", "inputs", "outputs", "cmd", "note"]


class BioToolsRecordSerializer(serializers.ModelSerializer):
    """
    Full bio.tools record — returned nested inside submission detail responses
    AND available standalone at GET /api/v1/biotools/{biotoolsID}/.
    """

    functions = BioToolsFunctionSerializer(many=True, read_only=True)
    biotools_url = serializers.SerializerMethodField()
    edam_topics_resolved = serializers.SerializerMethodField()

    class Meta:
        model = BioToolsRecord
        fields = [
            # Identifiers
            "id",
            "biotools_id",
            "biotools_url",
            # Core metadata (from bio.tools)
            "name",
            "description",
            "homepage",
            "version",
            "license",
            "maturity",
            "cost",
            "tool_type",
            "operating_system",
            # EDAM — raw URIs for machine consumers + resolved objects
            "edam_topic_uris",
            "edam_topics_resolved",
            # Structured functional annotation
            "functions",
            # Publications, docs, links
            "publications",
            "documentation",
            "download",
            "links",
            # Sync metadata
            "last_synced_at",
            "sync_error",
        ]

    def get_biotools_url(self, obj) -> str:
        return obj.biotools_url

    def get_edam_topics_resolved(self, obj) -> list:
        """
        Resolve raw bio.tools EDAM topic URIs against our local EdamTerm table.
        Returns EdamTermSerializer-shaped objects for any URI we have locally.
        URIs not in our database (e.g. from a newer EDAM release) are returned
        as {uri, accession: null, label: null} stubs.
        """
        from apps.edam.models import EdamTerm
        from urllib.parse import urlparse

        uris = obj.edam_topic_uris
        if not uris:
            return []

        # Single query for all URIs — avoids one query per URI in a loop
        terms_by_uri = {t.uri: t for t in EdamTerm.objects.filter(uri__in=uris)}

        resolved = []
        for uri in uris:
            term = terms_by_uri.get(uri)
            if term:
                resolved.append(EdamTermSerializer(term).data)
            else:
                # URI exists in bio.tools but not yet in our local EDAM snapshot
                path = urlparse(uri).path
                accession = path.split("/")[-1] if "/" in path else ""
                resolved.append(
                    {
                        "uri": uri,
                        "accession": accession,
                        "branch": None,
                        "label": None,
                        "url": uri,
                    }
                )
        return resolved


class BioToolsRecordSummarySerializer(serializers.ModelSerializer):
    """
    Compact bio.tools summary for embedding inside list responses.

    Includes all lightweight scalar fields from the full BioToolsRecordSerializer.
    Omits the heavy nested collections (functions, publications, documentation,
    download, links, edam_topics_resolved) to keep list payloads small — these
    are replaced by counts. Used by SubmissionListSerializer; the full
    BioToolsRecordSerializer is used for detail responses and the standalone
    /api/v1/biotools/{id}/ endpoint.
    """

    biotools_url = serializers.SerializerMethodField()
    edam_topic_count = serializers.SerializerMethodField()
    function_count = serializers.SerializerMethodField()

    class Meta:
        model = BioToolsRecord
        fields = [
            # Identifiers
            "id",
            "biotools_id",
            "biotools_url",
            # Core metadata (lightweight scalars)
            "name",
            "description",
            "homepage",
            "version",
            "license",
            "maturity",
            "cost",
            "tool_type",
            "operating_system",
            # EDAM — raw URIs + count (resolved objects omitted; use detail for those)
            "edam_topic_uris",
            "edam_topic_count",
            # Function count only (full function list omitted; use detail for that)
            "function_count",
            # Sync metadata
            "last_synced_at",
            "sync_error",
        ]

    def get_biotools_url(self, obj) -> str:
        return obj.biotools_url

    def get_edam_topic_count(self, obj) -> int:
        return len(obj.edam_topic_uris)

    def get_function_count(self, obj) -> int:
        return len(obj.functions.all())
