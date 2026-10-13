---
icon: material/puzzle
---

# Extending Models and Database Schema

This guide covers every scenario where you need to change the data model:
adding fields, adding whole models, renaming, removing, and handling the
production migration workflow safely.

---

## How Django Migrations Work

Django tracks every schema change in **migration files** under `apps/<app>/migrations/`.
Each file is a numbered Python module (`0001_initial.py`, `0002_add_field.py`, …) that
describes exactly one set of schema operations.

The migration state is recorded in a database table called `django_migrations`.
When you run `manage.py migrate`, Django compares what's in that table against
the migration files and applies anything that hasn't run yet — in dependency order.

**Golden rules:**

- Never edit an existing migration file after it has been applied to any environment.
- Never delete migration files. Squash them instead (see below).
- Always generate migrations in development; commit the file; apply in production.
- Test migrations locally on a copy of production data before deploying.

---

## Scenario 1 — Adding a New Field to an Existing Model

### Example: add `data_access_type` to `ServiceSubmission`

**Step 1 — Edit the model**

```python
# apps/submissions/models.py

class DataAccessType(models.TextChoices):
    OPEN      = "open",       "Open access"
    RESTRICTED = "restricted", "Restricted access"
    CLOSED    = "closed",     "Closed / on request"

class ServiceSubmission(models.Model):
    # ... existing fields ...

    # NEW FIELD — must be nullable or have a default so existing rows are valid
    data_access_type = models.CharField(
        max_length=20,
        choices=DataAccessType.choices,
        default=DataAccessType.OPEN,
        help_text="How users can access this service.",
    )
```

> **Nullable vs default**: If you can supply a sensible default, use `default=`.
> If no default makes sense, use `null=True, blank=True` for optional fields,
> or provide a one-off default in the migration for required fields.

**Step 2 — Generate the migration**

Run locally (not in the container — the `django` container user has no write access to
the bind-mounted source tree):

```bash
make makemigrations
# or: python manage.py makemigrations submissions --name add_data_access_type
# Creates: apps/submissions/migrations/<next number>_add_data_access_type.py
# (the examples below use 0002 as a placeholder number)
```

Open the generated file and verify it looks right:

```python
# apps/submissions/migrations/0002_add_data_access_type.py
class Migration(migrations.Migration):
    dependencies = [("submissions", "0001_initial")]
    operations = [
        migrations.AddField(
            model_name="servicesubmission",
            name="data_access_type",
            field=models.CharField(
                choices=[("open","Open access"),…],
                default="open",
                max_length=20,
            ),
        ),
    ]
```

**Step 3 — Apply locally and run tests**

```bash
docker compose exec web python manage.py migrate
make test        # runs locally; pytest is not installed in the container image
```

**Step 4 — Expose in the API (if needed)**

Add the field to the serialiser's `fields` list:

```python
# apps/api/serializers.py
class SubmissionDetailSerializer(serializers.ModelSerializer):
    class Meta:
        fields = [
            ...,
            "data_access_type",   # add here
        ]
```

**Step 5 — Expose in the admin (if needed)**

```python
# apps/submissions/admin.py
class ServiceSubmissionAdmin(admin.ModelAdmin):
    fieldsets = (
        …
        ("B — Service Master Data", {
            "fields": (…, "data_access_type"),  # add here
        }),
    )
```

**Step 6 — Expose in the form (if needed)**

```python
# apps/submissions/forms.py
class SubmissionForm(forms.ModelForm):
    class Meta:
        widgets = {
            …
            "data_access_type": forms.RadioSelect(),
        }
        labels = {
            …
            "data_access_type": _("Data access type (*)"),
        }
```

And add it to the registration template `templates/submissions/register.html`.

**Step 7 — Commit and deploy** (see [Releases and Rollback](../rollout.md))

```bash
git add apps/submissions/migrations/0002_add_data_access_type.py \
        apps/submissions/models.py \
        apps/api/serializers.py \
        apps/submissions/admin.py \
        apps/submissions/forms.py
git commit -m "add data_access_type field to ServiceSubmission"
```

---

## Scenario 2 — Adding a New Model

### Example: add a `ServiceUpdate` changelog model

**Step 1 — Define the model**

```python
# apps/submissions/models.py

class ServiceUpdate(models.Model):
    """
    A timestamped record of each change made to a ServiceSubmission.
    Created automatically on every submission save (see signals.py).
    """
    submission = models.ForeignKey(
        ServiceSubmission,
        on_delete=models.CASCADE,
        related_name="updates",
    )
    changed_by = models.CharField(
        max_length=150,
        help_text="'submitter' or admin username.",
    )
    changed_fields = models.JSONField(
        default=dict,
        help_text="Dict of {field_name: [old_value, new_value]}.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Update to {self.submission.service_name} at {self.created_at:%Y-%m-%d %H:%M}"
```

**Step 2 — Generate migration** (run locally, not in the container)

```bash
make makemigrations
# or: python manage.py makemigrations submissions --name add_serviceupdate_model
```

**Step 3 — Register in admin**

```python
# apps/submissions/admin.py

class ServiceUpdateInline(admin.TabularInline):
    model = ServiceUpdate
    extra = 0
    readonly_fields = ("changed_by", "changed_fields", "created_at")
    can_delete = False

@admin.register(ServiceSubmission)
class ServiceSubmissionAdmin(admin.ModelAdmin):
    inlines = [SubmissionAPIKeyInline, ServiceUpdateInline]
```

**Step 4 — Write tests**

```python
# tests/test_models.py
@pytest.mark.django_db
def test_service_update_created_on_save():
    from apps.submissions.models import ServiceUpdate
    sub = ServiceSubmissionFactory()
    count_before = ServiceUpdate.objects.filter(submission=sub).count()
    sub.comments = "Updated comment"
    sub.save()
    assert ServiceUpdate.objects.filter(submission=sub).count() == count_before + 1
```

---

## Scenario 3 — Adding a Field to a Related/Reference Model

### Example: add `ror_id` (Research Organization Registry ID) to `ServiceCenter`

```python
# apps/registry/models.py
class ServiceCenter(models.Model):
    # ... existing fields ...
    ror_id = models.CharField(
        max_length=50,
        blank=True,
        help_text="ROR identifier, e.g. 'https://ror.org/02nv7yv05'.",
    )
```

```bash
python manage.py makemigrations registry --name add_ror_id_to_servicecenter
```

Because `ror_id` is `blank=True`, existing rows automatically get `""` — no
data migration needed.

---

## Scenario 4 — Renaming a Field

Renaming requires care: Django generates a migration that asks whether you
renamed or deleted+added. Always answer **y** (yes, renamed) to preserve data.

```bash
# After changing the field name in models.py:
python manage.py makemigrations submissions --name rename_kpi_start_year_to_kpi_since
# Django will ask: "Did you rename servicesubmission.kpi_start_year to
#                   servicesubmission.kpi_since? [y/N]"
# Answer: y
```

The generated migration uses `RenameField` — data is preserved.

If the field is exposed in the API serialiser, update `fields` in `serializers.py`
and bump the API version if external consumers depend on the old name:

```python
# apps/api/serializers.py
# Old: "kpi_start_year"
# New: "kpi_since"
# If the API contract must not break, add a SerializerMethodField for the old name
# with a deprecation note in the docstring.
```

---

## Scenario 5 — Removing a Field

**Never drop a column that is still referenced in code.** The safe sequence is:

1. **Deploy a "tombstone" release**: mark the field deprecated in code comments,
   stop writing to it, but keep reading from it. Remove it from forms, API, admin.
2. **Deploy the removal migration**: once tombstone release is stable everywhere.

```python
# Step 2 migration — generated automatically after removing from models.py
python manage.py makemigrations submissions --name remove_deprecated_field
```

Django generates a `RemoveField` operation. **Backup before applying to production.**

---

## Scenario 6 — Adding a ManyToMany Relationship

### Example: add `related_services` self-referential M2M on `ServiceSubmission`

```python
class ServiceSubmission(models.Model):
    # ... existing fields ...
    related_services = models.ManyToManyField(
        "self",
        blank=True,
        symmetrical=True,
        related_name="+",
        help_text="Other de.NBI services closely related to this one.",
    )
```

```bash
python manage.py makemigrations submissions --name add_related_services_m2m
```

Django creates a join table `submissions_servicesubmission_related_services`.
No data migration needed — the join table starts empty.

---

## Scenario 7 — Data Migrations (backfilling data)

When you add a required field with no sensible Python default, you need to
backfill existing rows. Do this in a **separate** data migration.

```bash
# After your schema migration:
python manage.py makemigrations submissions --empty --name backfill_data_access_type
```

Edit the generated file:

```python
# apps/submissions/migrations/0003_backfill_data_access_type.py

def backfill_access_type(apps, schema_editor):
    # Use apps.get_model() — NEVER import the real model class in migrations.
    ServiceSubmission = apps.get_model("submissions", "ServiceSubmission")
    ServiceSubmission.objects.filter(
        data_access_type=""
    ).update(data_access_type="open")

def reverse_backfill(apps, schema_editor):
    pass  # Irreversible — leave rows as-is

class Migration(migrations.Migration):
    dependencies = [("submissions", "0002_add_data_access_type")]
    operations = [
        migrations.RunPython(backfill_access_type, reverse_backfill),
    ]
```

> **Critical rule**: Always use `apps.get_model()` inside migration functions,
> never `from apps.submissions.models import ServiceSubmission`. The historical
> model (as it existed at migration time) may differ from the current one.

---

## Scenario 8 — Squashing Migrations

After many cycles of development the migrations directory can grow long.
Squash them to reduce startup time and improve readability:

```bash
# Squash migrations 0001 through 0010 into one
python manage.py squashmigrations submissions 0001 0010

# This creates: 0001_squashed_0010_description.py
# Test it:
python manage.py migrate --run-syncdb
pytest tests/ -v

# Once all environments have applied the squash, delete the originals
# and remove the `replaces` attribute from the squashed file.
```

---

## Production Migration Checklist

Run through this before every release that contains migrations.

### Before deploying

- [ ] `python manage.py migrate --check` on a **copy** of the production database
- [ ] All new fields have either `default=`, `null=True`, or a data migration
- [ ] `python manage.py showmigrations` shows all expected migrations unapplied
- [ ] Tests pass: `pytest tests/ -v`
- [ ] If removing or renaming columns: database backup completed
- [ ] API version bumped if serialiser fields changed in a breaking way

### During deployment

```bash
# 1. Pull new image
docker compose pull web

# 2. Start new application containers
#    The container entrypoint runs migrations automatically before serving traffic.
docker compose up -d --no-deps web worker beat

# 3. Verify
curl https://service-registry.bi.denbi.de/health/ready/
docker compose logs web --tail 50
```

!!! tip "Pre-applying migrations manually"
    For zero-downtime rolling restarts or maintenance window deploys, you may still
    apply migrations explicitly before starting containers:
    ```bash
    docker compose run --rm web python manage.py migrate
    docker compose up -d --no-deps web worker beat
    ```
    The entrypoint detects that no pending migrations remain and proceeds immediately.

### After deploying

- [ ] `/health/ready/` returns `{"status": "ok"}`
- [ ] Smoke-test the form at `/register/`
- [ ] Check admin list view renders correctly
- [ ] Monitor error logs for 5 minutes: `docker compose logs -f web`

### Rollback (if migration fails)

```bash
# Roll back to the migration before yours (replace <previous> with its name,
# e.g. the last one listed by: python manage.py showmigrations submissions)
docker compose run --rm web python manage.py migrate submissions <previous>

# Restore the previous image (see Releases and Rollback for the image-based compose setup)
IMAGE_TAG=0.9.0 docker compose up -d --no-deps web worker beat
```

---

## Keeping the API Stable During Model Changes

The API is versioned at `/api/v1/`. Follow these rules when changing models:

| Change type        | Action required                                                                                              |
| ------------------ | ------------------------------------------------------------------------------------------------------------ |
| Add optional field | Add to serialiser `fields`; document in API guide; no version bump                                           |
| Add required field | Add to serialiser `fields`; update OpenAPI schema; no version bump if additive                               |
| Rename a field     | Keep old name as deprecated alias (`SerializerMethodField`); version bump at `/api/v2/` when ready to remove |
| Remove a field     | Two-phase: deprecate in v1 (return `null`), remove in v2                                                     |
| Change field type  | Always a breaking change → new API version                                                                   |

---

## Generating a Fresh OpenAPI Schema After Changes

```bash
# Run locally in the conda environment (the container user cannot write to the source tree)
python manage.py spectacular --file openapi.yaml
# Or as JSON:
python manage.py spectacular --file openapi.json --format json
```

Commit `openapi.yaml` to the repository so that API consumers can diff changes.

---

## Working with EDAM M2M Fields

The `ServiceSubmission` model has two ManyToMany fields linking to `EdamTerm`:
`edam_topics` and `edam_operations`. These follow the same M2M patterns as
`service_categories` but have some specifics worth noting.

### Filtering and querying

```python
from apps.edam.models import EdamTerm

# Submissions tagged with a specific EDAM topic
proteomics = EdamTerm.objects.get(accession="topic_0121")
subs = proteomics.submissions_by_topic.filter(status="approved")

# Submissions tagged with any topic in Proteomics subtree
proteomics_uris = EdamTerm.objects.filter(
    branch="topic", label__icontains="proteomics"
).values_list("id", flat=True)
subs = ServiceSubmission.objects.filter(edam_topics__in=proteomics_uris).distinct()
```

### Adding EDAM terms programmatically (e.g. in tests or data migrations)

```python
# In a test or script — using real model
sub = ServiceSubmission.objects.get(pk=some_uuid)
topic = EdamTerm.objects.get(accession="topic_0121")
sub.edam_topics.add(topic)

# In a data migration — using apps.get_model()
def add_edam_terms(apps, schema_editor):
    ServiceSubmission = apps.get_model("submissions", "ServiceSubmission")
    EdamTerm = apps.get_model("edam", "EdamTerm")
    # ... use .add() / .set() on the M2M manager
```

### Adding a new EDAM branch to the form

The form currently exposes `topic` and `operation` branches. To add `data` or
`format` terms (e.g. to let submitters annotate input/output formats):

1. Add the M2M field to `ServiceSubmission`:
   ```python
   edam_data_types = models.ManyToManyField(
       "edam.EdamTerm",
       blank=True,
       related_name="submissions_by_data",
       limit_choices_to={"branch": "data", "is_obsolete": False},
   )
   ```
2. Generate and apply the migration.
3. Add the widget to `SubmissionForm.Meta.widgets`:
   ```python
   "edam_data_types": EdamAutocompleteWidget(
       branch="data",
       placeholder="Search EDAM Data types…",
   ),
   ```
4. Add queryset restriction in `SubmissionForm.__init__`:
   ```python
   self.fields["edam_data_types"].queryset = (
       EdamTerm.objects.filter(branch="data", is_obsolete=False).order_by("label")
   )
   ```
5. Add to `SubmissionDetailSerializer.Meta.fields` (read + write fields).
6. Update the registration template to include the new field.

---

## Working with bio.tools Records

`BioToolsRecord` is a OneToOne model hanging off `ServiceSubmission`. It is
**never created by form submission** — it is created and updated exclusively
by the `sync_tool()` function called from the Celery task or management command.

### Querying bio.tools data

```python
from apps.biotools.models import BioToolsRecord, BioToolsFunction

# Get the bio.tools record for a submission
sub = ServiceSubmission.objects.get(pk=some_uuid)
try:
    record = sub.biotoolsrecord
    print(record.name, record.license, record.last_synced_at)
except BioToolsRecord.DoesNotExist:
    print("No bio.tools record yet — either no URL set or sync hasn't run")

# Find all submissions whose bio.tools entry includes a specific EDAM operation URI
target_uri = "http://edamontology.org/operation_0346"
records = BioToolsRecord.objects.filter(
    functions__operations__contains=[{"uri": target_uri}]
)
subs = [r.submission for r in records]

# Find all tools marked as 'Mature' in bio.tools
mature_tools = BioToolsRecord.objects.filter(maturity="Mature").select_related("submission")
```

### Adding fields extracted from bio.tools

The `raw_json` field on `BioToolsRecord` stores the complete bio.tools API
response. If you need a field that bio.tools returns but isn't yet extracted
into a scalar column:

1. Add the field to `BioToolsRecord`:
   ```python
   accessibility = models.JSONField(
       default=list,
       help_text="Accessibility annotations from bio.tools.",
   )
   ```
2. Generate migration.
3. Extract the value in `apps/biotools/sync.py` in the `record_defaults` dict:
   ```python
   "accessibility": raw.get("accessibility") or [],
   ```
   The next sync run will populate the field. Or backfill immediately:
   ```bash
   python manage.py sync_biotools
   ```
4. Expose in `BioToolsRecordSerializer.Meta.fields`.

### Writing tests for bio.tools-dependent code

Always mock the bio.tools API in tests — never make live HTTP calls in the
test suite. Use `unittest.mock.patch` on `apps.biotools.client.BioToolsClient.get_tool`:

```python
from unittest.mock import patch
from apps.biotools.client import BioToolsToolEntry

MOCK_TOOL = BioToolsToolEntry(
    biotools_id="blast",
    name="BLAST",
    description="Basic Local Alignment Search Tool",
    homepage="https://blast.ncbi.nlm.nih.gov",
    edam_topics=[{"uri": "http://edamontology.org/topic_0080", "term": "Sequence analysis"}],
    functions=[{
        "operations": [{"uri": "http://edamontology.org/operation_0346",
                        "term": "Sequence similarity search"}],
        "inputs": [], "outputs": [], "cmd": "", "note": "",
    }],
    raw={"biotoolsID": "blast", "name": "BLAST"},
)

@pytest.mark.django_db
def test_sync_creates_record(submission_factory):
    sub = submission_factory(biotools_url="https://bio.tools/blast")
    with patch("apps.biotools.sync.BioToolsClient") as mock_client:
        mock_client.return_value.get_tool.return_value = MOCK_TOOL
        from apps.biotools.sync import sync_tool
        result = sync_tool("blast", submission_id=str(sub.pk))
    assert result.ok
    assert sub.biotoolsrecord.name == "BLAST"
    assert sub.biotoolsrecord.functions.count() == 1
```

---

## Quick Reference: Common `manage.py` Commands

```bash
# Generate migrations
python manage.py makemigrations <app_name> --name <description>

# Generate empty data migration
python manage.py makemigrations <app_name> --empty --name <description>

# Apply all pending migrations
python manage.py migrate

# Apply migrations for one app only
python manage.py migrate <app_name>

# Roll back to a specific migration
python manage.py migrate <app_name> <migration_number>
# e.g.: python manage.py migrate submissions 0001

# Roll back all migrations for an app (destructive!)
python manage.py migrate <app_name> zero

# Show migration status
python manage.py showmigrations

# Check if any unapplied migrations exist (exits non-zero if so)
python manage.py migrate --check

# Show SQL a migration would execute (dry-run)
python manage.py sqlmigrate <app_name> <migration_number>
# e.g.: python manage.py sqlmigrate submissions 0002

# Squash migrations
python manage.py squashmigrations <app_name> <from> <to>

# Detect model changes not yet captured in a migration
python manage.py makemigrations --check --dry-run
```
