"""
API Views
=========
DRF ViewSets for the de.NBI Service Registry REST API.

Authentication strategy
-----------------------
- POST /submissions/           — public, no auth needed
- GET  /submissions/           — admin ApiKey auth (list all)
- GET  /submissions/{id}/      — ApiKey auth (owner sees own record)
- PATCH /submissions/{id}/     — ApiKey auth (owner updates own record)

`get_authenticators` is called *during* request initialisation before
`self.action` is set, so we inspect `self.request.method` and
`self.kwargs` (router-populated) instead.
"""

import logging

from django.utils.timezone import now
from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework import filters, mixins, status, viewsets
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.registry.models import PrincipalInvestigator, ServiceCategory, ServiceCenter
from apps.submissions.diff_utils import build_diff, snapshot, snapshot_m2m
from apps.submissions.http_utils import get_client_ip, hash_user_agent
from apps.submissions.models import (
    CHANGELOG_ACTOR_API_PREFIX,
    ServiceSubmission,
    SubmissionAPIKey,
    SubmissionChangeLog,
)
from apps.submissions.tasks import (
    send_submission_notification,
    send_update_notification,
)

from .authentication import AdminAPIKeyAuthentication, SubmissionAPIKeyAuthentication
from .permissions import IsAdminOrOwner, IsAdminTokenUser
from .serializers import (
    PrincipalInvestigatorAdminSerializer,
    ServiceCategoryAdminSerializer,
    ServiceCenterAdminSerializer,
    SubmissionCreateSerializer,
    SubmissionDetailSerializer,
    SubmissionListSerializer,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_detail_action(view):
    """
    Return True when the request targets a specific object (has a pk/uuid
    in the URL kwargs). Works before self.action is populated.
    """
    return bool(view.kwargs.get(view.lookup_field) or view.kwargs.get("pk"))


# ---------------------------------------------------------------------------
# SubmissionViewSet
# ---------------------------------------------------------------------------


@extend_schema(tags=["Submissions"])
class SubmissionViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """
    Service submission resource.

    | Method | URL | Auth | Description |
    |--------|-----|------|-------------|
    | POST | /api/v1/submissions/ | None | Register a new service |
    | GET | /api/v1/submissions/ | Admin ApiKey | List all submissions |
    | GET | /api/v1/submissions/{id}/ | ApiKey | Retrieve your submission |
    | PATCH | /api/v1/submissions/{id}/ | ApiKey | Update your submission |

    **One-time API key:** A `POST` response includes a plaintext `api_key`.
    Store it immediately — it is shown exactly once and never stored.

    Use it in subsequent requests as:
    ```
    Authorization: ApiKey <your-key>
    ```
    """

    # Accept JSON (default) and multipart/form-data (for logo file uploads)
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    queryset = (
        ServiceSubmission.objects.select_related("service_center", "biotoolsrecord")
        .prefetch_related(
            "service_categories",
            "responsible_pis",
            "edam_topics",
            "edam_operations",
            "biotoolsrecord__functions",
        )
        .order_by("-submitted_at")
    )

    filter_backends = [filters.OrderingFilter]
    ordering_fields = [
        "submitted_at",
        "updated_at",
        "service_name",
        "primary_maturity_tag",
    ]

    # ── Auth / permission — safe to call before self.action is set ───────────

    def get_authenticators(self):
        """
        Called during request initialisation before self.action exists.
        Use method + URL kwargs to decide auth scheme.
        - POST (create) → no authentication required
        - GET list      → admin ApiKey
        - GET/PATCH detail → ApiKey (owner) or ApiKey (admin)
        """
        http_method = (
            self.request.method if hasattr(self, "request") and self.request else "GET"
        )

        # Creation is open — no auth needed
        if http_method == "POST" and not _is_detail_action(self):
            return []

        # All other requests accept both API key schemes; permissions filter further
        return [
            AdminAPIKeyAuthentication(),
            SubmissionAPIKeyAuthentication(),
        ]

    def get_permissions(self):
        """
        Called after get_authenticators; self.action *may* still not be set
        on some Django/DRF paths, so guard with getattr.
        """
        action = getattr(self, "action", None)
        method = (
            getattr(self.request, "method", "GET")
            if hasattr(self, "request")
            else "GET"
        )

        if action == "create" or (method == "POST" and not _is_detail_action(self)):
            return [AllowAny()]
        if action == "list" or (method == "GET" and not _is_detail_action(self)):
            return [IsAdminTokenUser()]
        return [IsAdminOrOwner()]

    # ── Serializer selection ─────────────────────────────────────────────────

    def get_serializer_class(self):
        action = getattr(self, "action", None)
        if action == "create":
            return SubmissionCreateSerializer
        if action == "list":
            return SubmissionListSerializer
        return SubmissionDetailSerializer

    # ── Queryset scoping ─────────────────────────────────────────────────────

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="primary_maturity_tag",
                description="Filter by primary maturity tag (mature, emerging, legacy)",
                required=False,
                type=str,
                enum=["mature", "emerging", "legacy"],
            ),
            OpenApiParameter(
                name="secondary_maturity_tags",
                description="Filter by secondary tag (unstable, etc.). Comma-separated for multiple tags.",
                required=False,
                type=str,
            ),
        ]
    )
    def get_queryset(self):
        qs = super().get_queryset()

        # ApiKey authentication sets request.user to the ServiceSubmission object
        if isinstance(self.request.user, ServiceSubmission):
            return qs.filter(id=self.request.user.id)

        # Admin ApiKey: support optional query-param filters
        params = self.request.query_params

        status_filter = params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)

        center = params.get("service_center")
        if center:
            qs = qs.filter(service_center__short_name__icontains=center)

        year = params.get("year_established")
        if year and year.isdigit():
            qs = qs.filter(year_established=int(year))

        elixir = params.get("register_as_elixir")
        if elixir in ("true", "1"):
            qs = qs.filter(register_as_elixir=True)
        elif elixir in ("false", "0"):
            qs = qs.filter(register_as_elixir=False)

        # Filter by primary maturity tag
        primary_tag = params.get("primary_maturity_tag")
        if primary_tag:
            qs = qs.filter(primary_maturity_tag=primary_tag)

        # Filter by secondary maturity tags (comma-separated; any match).
        # icontains on the JSON text representation works on both SQLite and PostgreSQL.
        # Surrounding the slug with quotes prevents false positives for the fixed set
        # of admin-controlled tag values (e.g. '"unstable"' won't match '"stable"').
        secondary_tags = params.get("secondary_maturity_tags")
        if secondary_tags:
            tags = [t.strip() for t in secondary_tags.split(",") if t.strip()]
            for tag in tags:
                qs = qs.filter(secondary_maturity_tags__icontains=f'"{tag}"')

        return qs

    # ── Actions ──────────────────────────────────────────────────────────────

    @extend_schema(
        summary="Register a new de.NBI service",
        description=(
            "Submit a new service registration. No authentication required.\n\n"
            "**Important:** The response includes a one-time `api_key` field. "
            "This key is shown exactly once — save it immediately. "
            "You will need it to retrieve or update your submission.\n\n"
            "Use it in subsequent requests as:\n"
            "```\nAuthorization: ApiKey <your-key>\n```"
        ),
        responses={201: SubmissionCreateSerializer},
    )
    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        submission = serializer.save(
            status="submitted",
            submission_ip=get_client_ip(request),
            user_agent_hash=hash_user_agent(request),
        )

        _, plaintext = SubmissionAPIKey.create_for_submission(
            submission=submission,
            label="Initial key",
            created_by="submitter",
        )

        logger.info(
            "API submission created",
            extra={"submission_id": str(submission.id)},
        )

        send_submission_notification.delay(str(submission.id), event="created")

        output = SubmissionCreateSerializer(
            submission,
            context={"request": request, "api_key_plaintext": plaintext},
        )
        return Response(output.data, status=status.HTTP_201_CREATED)

    @extend_schema(
        summary="Retrieve your submission",
        description=(
            "Returns the full submission data for the submission linked to your API key.\n\n"
            "Requires `Authorization: ApiKey <your-key>` header."
        ),
        responses={200: SubmissionDetailSerializer},
    )
    def retrieve(self, request, *args, **kwargs):
        return super().retrieve(request, *args, **kwargs)

    @extend_schema(
        summary="List all submissions",
        description=(
            "Returns a paginated list of all service submissions.\n\n"
            "Requires admin `Authorization: ApiKey <admin-key>` header.\n\n"
            "**Filters:**\n"
            "- `?status=submitted|under_review|approved|rejected|deprecated`\n"
            "- `?service_center=<short_name>`\n"
            "- `?year_established=<year>`\n"
            "- `?register_as_elixir=true|false`\n"
            "- `?primary_maturity_tag=mature|emerging|legacy`\n"
            "- `?secondary_maturity_tags=unstable` (comma-separated)\n"
            "- `?ordering=submitted_at|updated_at|service_name|primary_maturity_tag` (prefix `-` for descending)"
        ),
        parameters=[
            OpenApiParameter("status", str, description="Filter by status"),
            OpenApiParameter(
                "service_center", str, description="Filter by service centre short name"
            ),
            OpenApiParameter(
                "year_established", int, description="Filter by year established"
            ),
            OpenApiParameter(
                "register_as_elixir",
                str,
                description="Filter by ELIXIR registration (true/false)",
            ),
            OpenApiParameter(
                "primary_maturity_tag",
                str,
                description="Filter by primary maturity tag (mature, emerging, legacy)",
                enum=["mature", "emerging", "legacy"],
            ),
            OpenApiParameter(
                "secondary_maturity_tags",
                str,
                description="Filter by secondary maturity tags (comma-separated, e.g., unstable)",
            ),
        ],
        responses={200: SubmissionListSerializer(many=True)},
    )
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    @extend_schema(
        summary="Update your submission (partial)",
        description=(
            "Partial update — include only the fields you want to change.\n\n"
            "Requires `Authorization: ApiKey <your-key>` header.\n\n"
            "Note: updating an approved submission resets its status to `submitted` "
            "for re-review."
        ),
    )
    def partial_update(self, request, *args, **kwargs):
        kwargs["partial"] = True
        instance = self.get_object()

        # Snapshot BEFORE serializer.save() — DRF's update() writes the new
        # values to the instance, so we must capture the originals first.
        before_scalar = snapshot(instance)
        before_m2m = snapshot_m2m(instance)

        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        # Reset status to "submitted" in the same save() to avoid a window
        # where the submission is briefly saved as "approved" with new data.
        if instance.status == "approved":
            serializer.save(status="submitted")
        else:
            serializer.save()

        # Compute field-level diff and persist it on the submission.
        after_scalar = snapshot(instance)
        after_m2m = snapshot_m2m(instance)
        changes = build_diff(
            {**before_scalar, **before_m2m},
            {**after_scalar, **after_m2m},
        )

        if changes:
            # Identify who made the change via the API key label (request.auth
            # is the SubmissionAPIKey instance set by SubmissionAPIKeyAuthentication).
            key_label = getattr(request.auth, "label", "api")
            changed_by = f"{CHANGELOG_ACTOR_API_PREFIX}{key_label}"
            changed_at = now()
            instance.last_change_summary = {
                "changed_by": changed_by,
                "changed_at": changed_at.isoformat(),
                "changes": changes,
            }
            instance.save(update_fields=["last_change_summary"])
            SubmissionChangeLog.objects.create(
                submission=instance,
                changed_by=changed_by,
                changed_at=changed_at,
                changes=changes,
            )

        send_update_notification.delay(str(instance.id), changes=changes)
        logger.info(
            "API submission updated (%d field(s) changed)",
            len(changes),
            extra={"submission_id": str(instance.id)},
        )
        return Response(serializer.data)

    def update(self, request, *args, **kwargs):
        """Full PUT is disabled — use PATCH."""
        if not kwargs.get("partial"):
            return Response(
                {
                    "detail": "Full replacement (PUT) is not supported. Use PATCH for partial updates."
                },
                status=status.HTTP_405_METHOD_NOT_ALLOWED,
            )
        return super().update(request, *args, **kwargs)


# ---------------------------------------------------------------------------
# Reference data viewsets (admin-only, full CRUD)
# ---------------------------------------------------------------------------


def _active_filter(qs, params):
    """Apply optional ?is_active=true|false filter to a queryset."""
    value = params.get("is_active")
    if value == "true":
        return qs.filter(is_active=True)
    if value == "false":
        return qs.filter(is_active=False)
    return qs


class SoftDeleteMixin:
    """
    Mixin for ModelViewSet subclasses whose model has an ``is_active`` flag.
    DELETE sets ``is_active=False`` and returns 204 — the row is never removed.
    """

    def perform_destroy(self, instance):
        instance.is_active = False
        instance.save(update_fields=["is_active"])
        logger.info(
            "%s deactivated (soft-delete) via API. id=%s",
            instance.__class__.__name__,
            instance.pk,
        )

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)


_is_active_param = OpenApiParameter(
    "is_active",
    str,
    description="Filter by active status. `true` = active only, `false` = inactive only. Omit to return all.",
)


@extend_schema(tags=["Reference Data"], parameters=[_is_active_param])
class ServiceCategoryViewSet(SoftDeleteMixin, viewsets.ModelViewSet):
    """
    CRUD for service categories. All operations require admin API key.

    | Method | URL | Description |
    |--------|-----|-------------|
    | GET | /api/v1/categories/ | List all (active + inactive) |
    | POST | /api/v1/categories/ | Create a new category |
    | GET | /api/v1/categories/{id}/ | Retrieve a category |
    | PATCH | /api/v1/categories/{id}/ | Partial update |
    | PUT | /api/v1/categories/{id}/ | Full update |
    | DELETE | /api/v1/categories/{id}/ | Soft-delete (sets is_active=False) |

    **Filter:** `?is_active=true|false`
    """

    serializer_class = ServiceCategoryAdminSerializer
    permission_classes = [IsAdminTokenUser]
    authentication_classes = [AdminAPIKeyAuthentication]
    pagination_class = None

    def get_queryset(self):
        return _active_filter(
            ServiceCategory.objects.all().order_by("name"),
            self.request.query_params,
        )

    def perform_create(self, serializer):
        instance = serializer.save()
        logger.info(
            "ServiceCategory created via API. id=%s name=%r", instance.pk, instance.name
        )

    def perform_update(self, serializer):
        instance = serializer.save()
        logger.info(
            "ServiceCategory updated via API. id=%s name=%r", instance.pk, instance.name
        )


@extend_schema(tags=["Reference Data"], parameters=[_is_active_param])
class ServiceCenterViewSet(SoftDeleteMixin, viewsets.ModelViewSet):
    """
    CRUD for de.NBI service centres. All operations require admin API key.

    | Method | URL | Description |
    |--------|-----|-------------|
    | GET | /api/v1/service-centers/ | List all (active + inactive) |
    | POST | /api/v1/service-centers/ | Create a new centre |
    | GET | /api/v1/service-centers/{id}/ | Retrieve a centre |
    | PATCH | /api/v1/service-centers/{id}/ | Partial update |
    | PUT | /api/v1/service-centers/{id}/ | Full update |
    | DELETE | /api/v1/service-centers/{id}/ | Soft-delete (sets is_active=False) |

    **Filter:** `?is_active=true|false`
    """

    serializer_class = ServiceCenterAdminSerializer
    permission_classes = [IsAdminTokenUser]
    authentication_classes = [AdminAPIKeyAuthentication]
    pagination_class = None

    def get_queryset(self):
        return _active_filter(
            ServiceCenter.objects.all().order_by("full_name"),
            self.request.query_params,
        )

    def perform_create(self, serializer):
        instance = serializer.save()
        logger.info(
            "ServiceCenter created via API. id=%s name=%r",
            instance.pk,
            instance.full_name,
        )

    def perform_update(self, serializer):
        instance = serializer.save()
        logger.info(
            "ServiceCenter updated via API. id=%s name=%r",
            instance.pk,
            instance.full_name,
        )


@extend_schema(tags=["Reference Data"], parameters=[_is_active_param])
class PrincipalInvestigatorViewSet(SoftDeleteMixin, viewsets.ModelViewSet):
    """
    CRUD for principal investigators. All operations require admin API key.

    | Method | URL | Description |
    |--------|-----|-------------|
    | GET | /api/v1/pis/ | List all (active + inactive) |
    | POST | /api/v1/pis/ | Create a new PI |
    | GET | /api/v1/pis/{id}/ | Retrieve a PI |
    | PATCH | /api/v1/pis/{id}/ | Partial update |
    | PUT | /api/v1/pis/{id}/ | Full update |
    | DELETE | /api/v1/pis/{id}/ | Soft-delete (sets is_active=False) |

    **Filter:** `?is_active=true|false`

    **Note:** The `email` field is for internal admin use — it is never included
    in submission responses where PIs are embedded.
    """

    serializer_class = PrincipalInvestigatorAdminSerializer
    permission_classes = [IsAdminTokenUser]
    authentication_classes = [AdminAPIKeyAuthentication]
    pagination_class = None

    def get_queryset(self):
        return _active_filter(
            PrincipalInvestigator.objects.all().order_by("last_name", "first_name"),
            self.request.query_params,
        )

    def perform_create(self, serializer):
        instance = serializer.save()
        logger.info(
            "PrincipalInvestigator created via API. id=%s name=%r",
            instance.pk,
            instance.display_name,
        )

    def perform_update(self, serializer):
        instance = serializer.save()
        logger.info(
            "PrincipalInvestigator updated via API. id=%s name=%r",
            instance.pk,
            instance.display_name,
        )


# ---------------------------------------------------------------------------
# EDAM ViewSet
# ---------------------------------------------------------------------------


@extend_schema(tags=["EDAM Ontology"])
class EdamTermViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    """
    EDAM ontology terms — public, no authentication required.

    **Filters:**
    - `?branch=topic|operation|data|format`
    - `?q=<search term>` — searches label and definition
    """

    from apps.edam.models import EdamTerm
    from apps.api.serializers import EdamTermSerializer, EdamTermDetailSerializer

    queryset = EdamTerm.objects.filter(is_obsolete=False).select_related("parent")
    permission_classes = [AllowAny]
    authentication_classes = []
    pagination_class = None

    def get_serializer_class(self):
        from apps.api.serializers import EdamTermSerializer, EdamTermDetailSerializer

        if getattr(self, "action", None) == "retrieve":
            return EdamTermDetailSerializer
        return EdamTermSerializer

    def get_queryset(self):
        from apps.edam.models import EdamTerm
        from django.db.models import Q

        qs = EdamTerm.objects.filter(is_obsolete=False).select_related("parent")
        branch = self.request.query_params.get("branch")
        if branch:
            qs = qs.filter(branch=branch)
        q = self.request.query_params.get("q")
        if q:
            qs = qs.filter(Q(label__icontains=q) | Q(definition__icontains=q))
        return qs.order_by("branch", "sort_order")

    def get_object(self):
        from apps.edam.models import EdamTerm

        lookup = self.kwargs.get(self.lookup_field)
        try:
            return EdamTerm.objects.get(accession=lookup)
        except EdamTerm.DoesNotExist:
            return super().get_object()


# ---------------------------------------------------------------------------
# bio.tools ViewSet
# ---------------------------------------------------------------------------


@extend_schema(tags=["bio.tools Integration"])
class BioToolsRecordViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    bio.tools integration records — locally cached snapshots linked to
    de.NBI service registrations, refreshed daily by a Celery task.

    - List: requires admin API key.
    - Retrieve (by biotoolsID): public for approved submissions.

    **Filters:** `?submission=<uuid>`, `?biotools_id=<id>`
    """

    from apps.api.serializers import BioToolsRecordSerializer

    serializer_class = BioToolsRecordSerializer

    def get_queryset(self):
        from apps.biotools.models import BioToolsRecord

        qs = BioToolsRecord.objects.select_related("submission").prefetch_related(
            "functions"
        )
        biotools_id = self.request.query_params.get("biotools_id")
        if biotools_id:
            qs = qs.filter(biotools_id=biotools_id)
        submission_id = self.request.query_params.get("submission")
        if submission_id:
            qs = qs.filter(submission_id=submission_id)
        return qs

    def get_permissions(self):
        if getattr(self, "action", None) == "retrieve":
            return [AllowAny()]
        return [IsAdminTokenUser()]

    def get_authenticators(self):
        return [
            AdminAPIKeyAuthentication(),
            SubmissionAPIKeyAuthentication(),
        ]

    def get_object(self):
        from apps.biotools.models import BioToolsRecord
        from django.http import Http404

        lookup = self.kwargs.get(self.lookup_field)
        try:
            return (
                BioToolsRecord.objects.select_related("submission")
                .prefetch_related("functions")
                .filter(submission__status="approved")
                .get(biotools_id=lookup)
            )
        except BioToolsRecord.DoesNotExist:
            raise Http404
