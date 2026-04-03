---
icon: material/plus-box
---

# Extending the Application — Adding New Apps and Features

This guide covers how to add substantial new features (new Django apps,
new API endpoints, new reference data types) without breaking existing functionality.

---

## Adding a New Django App

### When to create a new app vs adding to an existing one

| Scenario                                              | Where to put it                                           |
| ----------------------------------------------------- | --------------------------------------------------------- |
| New fields on ServiceSubmission                       | `apps/submissions/models.py`                              |
| New reference lookup table (e.g. FundingBody)         | `apps/registry/models.py`                                 |
| New EDAM branch or ontology term type                 | `apps/edam/models.py` + `sync_edam` command               |
| New external registry integration (bio.tools pattern) | New app: `apps/biotools/` is the reference implementation |
| New standalone entity (e.g. ServiceUsageReport)       | New app: `apps/reporting/`                                |

### Steps to add a new app

```bash
# 1. Create the app directory
mkdir -p apps/myapp

# 2. Generate the app scaffold
docker compose exec web python manage.py startapp myapp apps/myapp

# 3. Register in settings.py
```

```python
# config/settings.py
INSTALLED_APPS = [
    ...
    "apps.myapp",   # add here
]
```

```bash
# 4. Create initial migration (run locally — container user has no write access to source tree)
python manage.py makemigrations myapp --name initial
# or: make makemigrations
```

---

## Adding a New Reference Data Type

Reference data (lookup tables for dropdowns) follows the pattern established
by `ServiceCategory`, `ServiceCenter`, and `PrincipalInvestigator`.

### Example: add a `FundingBody` model

```python
# apps/registry/models.py

class FundingBody(models.Model):
    """
    A funding body that financially supports de.NBI services.
    Examples: BMBF, DFG, EU Horizon, Helmholtz Association.
    """
    name    = models.CharField(max_length=200, unique=True)
    acronym = models.CharField(max_length=20, blank=True)
    website = models.URLField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "Funding Bodies"
        ordering = ["name"]

    def __str__(self):
        return self.acronym or self.name
```

Add a `ForeignKey` or `ManyToManyField` to `ServiceSubmission`:

```python
# apps/submissions/models.py
class ServiceSubmission(models.Model):
    # ...
    funding_bodies = models.ManyToManyField(
        "registry.FundingBody",
        blank=True,
        related_name="submissions",
        help_text="Funding bodies supporting this service.",
    )
```

Register in admin:

```python
# apps/registry/admin.py
@admin.register(FundingBody)
class FundingBodyAdmin(admin.ModelAdmin):
    list_display = ("name", "acronym", "website", "is_active")
    list_editable = ("is_active",)
    search_fields = ("name", "acronym")
```

Add to form and serialiser following the same pattern as `service_categories`.

---

## Adding a New API Endpoint

### Step 1 — Write the serialiser

```python
# apps/api/serializers.py

class FundingBodySerializer(serializers.ModelSerializer):
    class Meta:
        model = FundingBody
        fields = ["id", "name", "acronym", "website"]
```

### Step 2 — Write the viewset

Reference data viewsets use `ModelViewSet` for full CRUD. `destroy()` is overridden
to soft-delete (set `is_active=False`) rather than remove the record, preserving
referential integrity with existing submissions.

```python
# apps/api/views.py

from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework import status, viewsets
from rest_framework.response import Response

from .authentication import AdminAPIKeyAuthentication
from .permissions import IsAdminOrOwner

@extend_schema(
    tags=["Reference Data"],
    parameters=[OpenApiParameter("is_active", str, description="Filter by active status (true/false)")],
)
class FundingBodyViewSet(viewsets.ModelViewSet):
    """
    CRUD for funding bodies. All operations require admin API key.
    DELETE performs a soft-delete (sets is_active=False).
    Filter: ?is_active=true|false
    """
    serializer_class = FundingBodyAdminSerializer
    permission_classes = [IsAdminOrOwner]
    authentication_classes = [AdminAPIKeyAuthentication]
    pagination_class = None

    def get_queryset(self):
        qs = FundingBody.objects.all().order_by("name")
        value = self.request.query_params.get("is_active")
        if value == "true":
            qs = qs.filter(is_active=True)
        elif value == "false":
            qs = qs.filter(is_active=False)
        return qs

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.is_active = False
        instance.save(update_fields=["is_active"])
        return Response(status=status.HTTP_204_NO_CONTENT)
```

### Step 3 — Register the URL

```python
# apps/api/urls.py
router.register("funding-bodies", FundingBodyViewSet, basename="fundingbody")
```

### Step 4 — Write tests

```python
# tests/test_api.py

@pytest.mark.django_db
class TestFundingBodyCRUD:

    def test_requires_admin_token(self, api_client):
        resp = api_client.get("/api/v1/funding-bodies/")
        assert resp.status_code in (401, 403)

    def test_list_returns_all_including_inactive(self, staff_client):
        FundingBodyFactory(name="BMBF", is_active=True)
        FundingBodyFactory(name="Old Body", is_active=False)
        resp = staff_client.get("/api/v1/funding-bodies/")
        names = [b["name"] for b in resp.json()]
        assert "BMBF" in names
        assert "Old Body" in names  # all shown by default

    def test_list_filter_active_only(self, staff_client):
        FundingBodyFactory(name="BMBF", is_active=True)
        FundingBodyFactory(name="Old Body", is_active=False)
        resp = staff_client.get("/api/v1/funding-bodies/?is_active=true")
        names = [b["name"] for b in resp.json()]
        assert "BMBF" in names
        assert "Old Body" not in names

    def test_create_returns_201(self, staff_client):
        resp = staff_client.post(
            "/api/v1/funding-bodies/", {"name": "DFG"}, format="json"
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "DFG"

    def test_delete_soft_deletes(self, staff_client):
        body = FundingBodyFactory(is_active=True)
        resp = staff_client.delete(f"/api/v1/funding-bodies/{body.pk}/")
        assert resp.status_code == 204
        body.refresh_from_db()
        assert body.is_active is False
```

---

## Adding a Completely New Feature Module

### Example: service usage statistics import

Create a new app:

```
apps/
└── statistics/
    ├── __init__.py
    ├── apps.py
    ├── models.py       # UsageReport, MonthlyMetric
    ├── admin.py
    ├── serializers.py
    ├── views.py
    ├── urls.py
    ├── tasks.py        # Celery tasks for async import
    └── migrations/
        └── 0001_initial.py
```

Include its URLs in the root router:

```python
# apps/api/urls.py
from apps.statistics.urls import router as stats_router
# Or add a new router and include it in config/urls.py
```

---

## Using the bio.tools App as a Reference Architecture

`apps/biotools/` is the reference implementation for **external registry integrations**
— apps that fetch data from a third-party API, cache it locally, and expose it via
the REST API. Use it as a template when adding a similar integration.

### Key patterns implemented in apps/biotools/

**1. Thin HTTP client (`client.py`)**

A self-contained class that handles one external API. All HTTP is in one file.
It raises typed exceptions (`BioToolsNotFound`, `BioToolsError`) so callers
don't need to handle raw HTTP status codes.

```
apps/biotools/client.py        ← BioToolsClient class
apps/biotools/sync.py          ← sync_tool() — core upsert logic
apps/biotools/tasks.py         ← Celery task wrapping sync_tool()
apps/biotools/signals.py       ← post_save signal → queues Celery task
apps/biotools/views.py         ← HTMX form prefill endpoint
apps/biotools/management/      ← CLI: python manage.py sync_biotools
```

**2. Separation of sync logic from Celery task**

`sync.py` contains the actual fetch-and-upsert logic. `tasks.py` wraps it.
This makes `sync_tool()` directly unit-testable without Celery infrastructure.

**3. Signal → task trigger pattern**

`signals.py` fires on `ServiceSubmission.post_save` and queues a Celery task
with `countdown=2` (waits 2 seconds for the transaction to commit).
Register signals in `AppConfig.ready()` in `apps.py`, not at module level.

**4. raw_json as a safety net**

The `BioToolsRecord.raw_json` field stores the complete API response verbatim.
When you need a field that the external API returns but you haven't extracted yet,
it's always available in `raw_json` without requiring a new sync.

### Adapting this pattern for a new integration (e.g. FAIRsharing)

```
apps/fairsharing/
├── __init__.py
├── apps.py            ← FairSharingConfig with ready() registering signals
├── client.py          ← FairSharingClient(timeout=10)
├── models.py          ← FairSharingRecord(OneToOne → ServiceSubmission)
├── sync.py            ← sync_fairsharing(fairsharing_id, submission_id)
├── tasks.py           ← sync_fairsharing_record.delay(submission_id)
├── signals.py         ← post_save on fairsharing_url field change
├── views.py           ← /fairsharing/prefill/?id=FAIRsharing.xxx
├── urls.py
├── admin.py
├── management/commands/sync_fairsharing.py
└── migrations/0001_initial.py
```

Steps:

1. Copy the `apps/biotools/` directory structure and rename throughout.
2. Implement `client.py` for the target API.
3. Define a `FairSharingRecord` model with a `OneToOne` to `ServiceSubmission`.
4. Implement `sync.py` using `client.py` — keep it free of Celery imports.
5. Wire the Celery task in `tasks.py`.
6. Register the signal in `signals.py` + `apps.py`.
7. Add a prefill view in `views.py` and include its URLs in `config/urls.py`.
8. Add a management command for manual/bulk sync.
9. Add the app to `INSTALLED_APPS` and a beat schedule entry to `CELERY_BEAT_SCHEDULE`.
10. Write tests mocking the HTTP client (see testing pattern in `model-changes.md`).

---

## Using the EDAM App as a Reference Architecture

`apps/edam/` is the reference implementation for **locally-seeded ontology/vocabulary
apps** — apps that mirror an external controlled vocabulary into the database and
expose it as a public read-only API endpoint with full-text search.

### Key patterns implemented in apps/edam/

```
apps/edam/
├── models.py          ← EdamTerm with branch, uri, label, parent (self-FK)
├── admin.py           ← Read-only admin (no add/delete — all from sync command)
├── management/commands/sync_edam.py   ← Downloads + upserts vocabulary
└── migrations/0001_initial.py
```

The public API is in `apps/api/views.py` (`EdamTermViewSet`) and serialisers in
`apps/api/serializers.py` — not in `apps/edam/` itself. This separation keeps
the ontology app focused on data storage and sync, not HTTP concerns.

### Adapting this pattern for a new vocabulary (e.g. MeSH terms, Species list)

1. Create `apps/mesh/` with a `MeshTerm` model (uri, label, tree_number, parent,
   is_obsolete, sort_order, version).
2. Write a `sync_mesh` management command that downloads and upserts terms.
3. Add a `ManyToManyField("mesh.MeshTerm", ...)` to `ServiceSubmission` if submitters
   should be able to tag their service with MeSH terms.
4. Add a `MeshTermSerializer` and `MeshTermViewSet` in `apps/api/` — mark the
   endpoint public (`AllowAny`) so external tools can resolve MeSH URIs.
5. Add an `EdamAutocompleteWidget`-style widget or reuse the existing one (it works
   with any `SelectMultiple` queryset — the Tom Select enhancement is generic).

---

## Dynamic Form Fields (Phase 2 Feature)

The requirements mention a `DynamicField` model for adding fields at runtime
without code changes. Here is the extension point:

```python
# apps/submissions/models.py

class DynamicFieldDefinition(models.Model):
    """
    Admin-configurable extra fields that appear on the submission form.
    Implements the Entity-Attribute-Value (EAV) pattern.
    Use sparingly — prefer static fields where possible.
    """
    name        = models.SlugField(unique=True)
    label       = models.CharField(max_length=200)
    field_type  = models.CharField(
        max_length=20,
        choices=[("text","Text"), ("boolean","Yes/No"), ("url","URL"), ("date","Date")],
        default="text",
    )
    is_required = models.BooleanField(default=False)
    is_active   = models.BooleanField(default=True)
    sort_order  = models.PositiveIntegerField(default=0)
    help_text   = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["sort_order", "name"]


class DynamicFieldValue(models.Model):
    """Stores a single dynamic field value for one submission."""
    submission  = models.ForeignKey(
        ServiceSubmission, on_delete=models.CASCADE, related_name="dynamic_values"
    )
    field       = models.ForeignKey(DynamicFieldDefinition, on_delete=models.PROTECT)
    value       = models.TextField(blank=True)

    class Meta:
        unique_together = [("submission", "field")]
```

The form can then dynamically inject these fields using `__init__`:

```python
# apps/submissions/forms.py
class SubmissionForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Inject active dynamic fields
        for field_def in DynamicFieldDefinition.objects.filter(is_active=True):
            if field_def.field_type == "boolean":
                self.fields[f"dynamic_{field_def.name}"] = forms.BooleanField(
                    label=field_def.label, required=field_def.is_required,
                    help_text=field_def.help_text,
                )
            # ... other types ...
```

---

## Style Conventions for New Code

All new code must follow these conventions to pass CI:

```bash
# Linting (must pass before merge)
ruff check apps/ config/ tests/

# Formatting check
ruff format --check apps/ config/ tests/

# Type checking (optional but encouraged)
mypy apps/ config/
```

### Model conventions

- `id` as `UUIDField` for new top-level entities
- `is_active` soft-delete on all reference data
- `created_at = DateTimeField(auto_now_add=True)` and `updated_at = DateTimeField(auto_now=True)` on mutable entities
- `__str__` must return a human-readable string
- All fields need `help_text` (model-level `help_text` is overridden by `apps/submissions/form_texts.yaml` for the registration form)
- Email subject lines and status messages live in `apps/submissions/email_texts.yaml` — update if adding new notification types

### Serialiser conventions

- Always use an explicit `fields = [...]` list — never `fields = "__all__"`
- Exclude all internal/sensitive fields by omission (not by `exclude`)
- Add `_links` block via `SerializerMethodField` on all top-level serialisers

### Test conventions

- All new model code needs corresponding `test_models.py` tests
- All new views need corresponding `test_views.py` tests
- All new API endpoints need corresponding `test_api.py` tests
- Use factories from `tests/factories.py` — never create fixtures inline

### Database query conventions

**Always tune new querysets for N+1.** The rules applied throughout this codebase:

| Situation                                   | Do                                                                        | Don't                                   |
| ------------------------------------------- | ------------------------------------------------------------------------- | --------------------------------------- |
| ViewSet base queryset                       | `select_related` for FK/OneToOne; `prefetch_related` for M2M / reverse FK | Leave relations unresolved              |
| Admin `list_display` accesses FK            | Set `list_select_related = ("field",)`                                    | Access `obj.fk.attr` without it         |
| Counting a prefetched relation              | `len(obj.relation.all())`                                                 | `obj.relation.count()`                  |
| Listing values from a prefetched relation   | `[x.field for x in obj.relation.all()]`                                   | `obj.relation.values_list(...)`         |
| Resolving a list of IDs/URIs against the DB | `Model.objects.filter(field__in=ids)` → dict                              | `Model.objects.get(field=id)` in a loop |
| Iterating in an admin action                | Call `.select_related()` / `.prefetch_related()` on the passed `queryset` | Rely on the class-level queryset        |

**Add `db_index=True` to any field used in `filter()` or `order_by()`** in views, the API, or admin `list_filter`.
Add compound `Meta.indexes` when two fields are filtered or sorted together (e.g. `["-submitted_at", "status"]`).

See [Architecture → Database query strategy](../architecture.md#database-query-strategy) for the full reference implementation.
