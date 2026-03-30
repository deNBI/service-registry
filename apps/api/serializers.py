"""
API Serializers
===============
DRF serializers for the public REST API.

Security notes:
  - internal_contact_email, internal_contact_name, submission_ip,
    user_agent_hash are excluded from ALL serializer outputs.
  - The api_key field is write-only on creation — it is returned once
    in the POST response and never again.
"""

from rest_framework import serializers

from apps.biotools.models import BioToolsFunction, BioToolsRecord
from apps.edam.models import EdamTerm
from apps.registry.models import PrincipalInvestigator, ServiceCategory, ServiceCenter
from apps.submissions.models import ServiceSubmission


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
            # Section C — public fields only (internal_contact_* excluded)
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
            # Links
            "links",
        ]
        read_only_fields = ["id", "status", "submitted_at", "updated_at"]
        extra_kwargs = {
            # Never echo consent back in responses — it is always True for valid records
            "data_protection_consent": {"write_only": True},
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
        except Exception:
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
        except Exception:
            pass
        return links

    def validate(self, data: dict) -> dict:
        """Cross-field validation mirroring the form."""
        if data.get("is_toolbox") and not data.get("toolbox_name", "").strip():
            raise serializers.ValidationError(
                {"toolbox_name": "Toolbox name is required when is_toolbox is True."}
            )
        # data_protection_consent is mandatory on create; DRF does not call
        # Model.clean() automatically, so we enforce it here.
        if self.instance is None and not data.get("data_protection_consent"):
            raise serializers.ValidationError(
                {
                    "data_protection_consent": (
                        "You must consent to the data protection information to submit this form."
                    )
                }
            )
        return data


class SubmissionListSerializer(SubmissionDetailSerializer):
    """
    Full serializer for the list endpoint — returns identical fields to detail.
    Every record includes all form fields, EDAM annotations, and the embedded
    bio.tools record (if synced). Write-only fields (…_ids) are suppressed
    automatically since they are declared write_only=True on the parent.
    """

    pass


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
    Compact summary of bio.tools data for embedding inside SubmissionDetailSerializer.
    Omits the large raw_json and full function list.
    """

    biotools_url = serializers.SerializerMethodField()
    edam_topic_count = serializers.SerializerMethodField()
    function_count = serializers.SerializerMethodField()

    class Meta:
        model = BioToolsRecord
        fields = [
            "biotools_id",
            "biotools_url",
            "name",
            "description",
            "homepage",
            "version",
            "license",
            "maturity",
            "tool_type",
            "edam_topic_uris",
            "edam_topic_count",
            "function_count",
            "last_synced_at",
            "sync_error",
        ]

    def get_biotools_url(self, obj) -> str:
        return obj.biotools_url

    def get_edam_topic_count(self, obj) -> int:
        return len(obj.edam_topic_uris)

    def get_function_count(self, obj) -> int:
        # Use len() so a prefetched functions cache is not bypassed
        return len(obj.functions.all())
