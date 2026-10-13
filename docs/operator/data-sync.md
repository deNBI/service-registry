---
icon: material/database-sync
---

# Reference Data and Ontology Sync

This page covers the command-line side of the external data the registry keeps in sync (EDAM, SPDX licences, bio.tools records) and the bulk import of PIs, categories and service centres. The admin UI side (sync buttons, status columns, deprecated terms) is in the [Admin Guide](../admin-guide.md#edam-management).

All commands run inside the `web` container (`docker compose exec web …`).

## Schedules at a glance

Defined in `CELERY_BEAT_SCHEDULE` in `config/settings.py` and run by the `beat` container:

| Beat entry                       | Task                            | Interval      |
| -------------------------------- | ------------------------------- | ------------- |
| `sync-biotools-daily`            | `biotools.sync_all`             | Every 24 hours |
| `sync-edam-monthly`              | `edam.sync`                     | Every 30 days |
| `sync-spdx-licenses-fortnightly` | `licenses.sync`                 | Every 30 days (the entry name is historical) |
| `cleanup-stale-drafts`           | `submissions.cleanup_stale_drafts` | Every 24 hours (see [Operations](operations.md#scheduled-tasks)) |

Beat keeps its last-run times in a schedule file (`celery_beat` volume); intervals are counted from the last recorded run, not from a fixed time of day. To change one, edit the `schedule` value (seconds) and rebuild the image:

```python
"sync-biotools-daily": {
    "task": "biotools.sync_all",
    "schedule": 86400,  # seconds — change to 43200 for twice-daily
},
```

## EDAM ontology

The EDAM ontology powers the Topic and Operation pickers on the registration form. A full load from `EDAM_stable.owl` is about 3,500 terms in four branches (topic, operation, data, format), including terms EDAM has deprecated. Deprecated terms are kept but not offered: the form lists the current topics and operations (260 and 537 in EDAM 1.25-20200618), and `/api/v1/edam/` lists the current terms of all four branches.

### Automatic seeding on first migrate

When `manage.py migrate` runs against a **fresh database** (empty `EdamTerm` table),
it automatically downloads and imports EDAM from `EDAM_OWL_URL`. This happens as
a `post_migrate` signal — you will see progress output at the end of `migrate`:

```
[edam] EdamTerm table is empty — running initial EDAM sync.
[edam] This downloads ~3 MB from edamontology.org and may take ~30 seconds.
[edam] Loading EDAM from: https://edamontology.org/EDAM_stable.owl
...
[edam] Auto-seed complete — 3471 terms loaded (EDAM 1.25).
```

On subsequent `migrate` runs (e.g. applying a new migration), the table is not
empty so the signal is a no-op. Celery beat then runs a full sync every 30 days; EDAM publishes new releases a few times a year.

### Manual sync

```bash
# Download and import the latest stable release
docker compose exec web python manage.py sync_edam

# Dry-run — parse and count terms without writing to the database
docker compose exec web python manage.py sync_edam --dry-run

# Sync a single branch only
docker compose exec web python manage.py sync_edam --branch topic

# Load from a local file (air-gapped servers)
docker compose exec web python manage.py sync_edam --url /app/EDAM.owl
```

New terms appear immediately in the form. Obsolete terms are hidden from the form but
retained in the database so existing submissions referencing them are not broken.

Verify the current state:

```bash
docker compose exec web python manage.py shell -c \
  "from apps.edam.models import EdamTerm; t = EdamTerm.objects.first(); print(EdamTerm.objects.count(), 'terms, version', t.edam_version)"
```

### If the form shows no EDAM terms

On a standard deployment this should not happen — the auto-seed fires on first migrate.
If the pickers are empty, check the term count:

```bash
docker compose exec web python manage.py shell -c \
  "from apps.edam.models import EdamTerm; print(EdamTerm.objects.count())"
# Expected: about 3,500 (including obsolete terms). If 0, the auto-seed failed (check migrate output for [edam] lines).
# Fix: docker compose exec web python manage.py sync_edam
```

### New EDAM releases

When a new release is out:

1. Check the [EDAM changelog](https://github.com/edamontology/edamontology/blob/main/changelog.md)
   for any deprecated terms your submissions may be using.
2. Run the sync on staging first: `docker compose exec web python manage.py sync_edam --dry-run`
3. Apply on staging and verify term counts look correct.
4. Apply on production during a low-traffic period:
   ```bash
   docker compose exec web python manage.py sync_edam
   ```
5. No migration, no restart, no downtime needed — terms upsert in place.

This is a data-only change and does not need a release.

## SPDX licences

The licence picker is backed by a local copy of the [SPDX License List](https://spdx.org/licenses/) (700+ entries). On a fresh database it is seeded by a `post_migrate` signal (about 500 KB from `SPDX_LICENSES_URL`), then refreshed every 30 days by Celery beat.

```bash
# Download and import the latest SPDX License List
docker compose exec web python manage.py sync_spdx_licenses

# Dry-run — parse and count licenses without writing to the database
docker compose exec web python manage.py sync_spdx_licenses --dry-run

# Load from a local file (air-gapped servers)
docker compose exec web python manage.py sync_spdx_licenses --url /app/licenses.json
```

If the form shows no licences:

```bash
docker compose exec web python manage.py shell -c \
  "from apps.licenses.models import SpdxLicense; print(SpdxLicense.objects.count())"
# Expected: 700+. If 0, the auto-seed failed.
# Fix: docker compose exec web python manage.py sync_spdx_licenses
```

## bio.tools records

A bio.tools record is fetched when a submission is saved with a new or changed bio.tools URL. Every 24 hours all submissions with a bio.tools URL are synced, which also creates records that are missing. A record is removed when its submission no longer has a bio.tools URL.

```bash
# Refresh all existing records (also removes records of submissions without a URL)
docker compose exec web python manage.py sync_biotools

# Sync one specific submission
docker compose exec web python manage.py sync_biotools --submission <uuid>

# Dry-run
docker compose exec web python manage.py sync_biotools --dry-run
```

If a submission has a bio.tools URL but no record yet (for example because bio.tools was unreachable when the submission was saved), the daily sync creates it. To create it straight away:

```bash
docker compose exec web python manage.py sync_biotools \
  --submission <submission-uuid> \
  --create
```

To list records whose last sync failed:

```bash
docker compose exec web python manage.py shell -c "
from apps.biotools.models import BioToolsRecord
errors = BioToolsRecord.objects.exclude(sync_error='')
print(f'{errors.count()} records with sync errors:')
for r in errors: print(f'  {r.biotools_id}: {r.sync_error[:80]}')
"
```

## Air-gapped or firewall-restricted servers

If the server cannot reach edamontology.org or GitHub, download the files on another machine and copy them into the container's file system (for example via a bind mount):

```bash
# On a machine with internet access:
curl -o EDAM.owl https://edamontology.org/EDAM_stable.owl
curl -o licenses.json https://raw.githubusercontent.com/spdx/license-list-data/main/json/licenses.json
```

Then either set the paths permanently in `.env` (the first-migrate auto-seed and the scheduled syncs use them too):

```bash
EDAM_OWL_URL=/app/EDAM.owl
SPDX_LICENSES_URL=/app/licenses.json
```

or pass them once with `--url` as shown above. bio.tools records cannot be synced without access to `bio.tools`.

## Network requirements

| Destination                 | Port | When needed                                                    | Purpose                    |
| --------------------------- | ---- | -------------------------------------------------------------- | -------------------------- |
| `bio.tools`                 | 443  | On form submission, the form prefill, and the daily sync       | bio.tools API              |
| `edamontology.org`          | 443  | First migrate on an empty database, every 30 days, manual `sync_edam` | EDAM ontology download |
| `raw.githubusercontent.com` | 443  | First migrate on an empty database, every 30 days, manual `sync_spdx_licenses` | SPDX licence list |
| Your SMTP server            | 587  | Every notification                                             | E-mail                     |

## Bulk import of PIs, categories and service centres

`scripts/import_reference_data.py` reads an Excel workbook and creates or updates the records through the [admin REST API](../admin/api.md), so it needs a **full**-scope Admin API key (a read key works with `--dry-run` only). It never deletes anything.

- Sheets (exact names): `pis`, `service_categories`, `service_centers`.
- Columns: `pis`: `last_name`, `first_name` (required), `email`, `institute`, `orcid`, `is_active`, `is_associated_partner`; `service_categories`: `name` (required), `is_active`; `service_centers`: `short_name`, `full_name` (required), `website`, `is_active`.
- Rows are matched on `(last_name, first_name)` for PIs, `name` for categories and `short_name` for centres: no match is created, a match with different values is updated, identical rows are skipped. Records missing from the sheet are left untouched.

The script needs `openpyxl` and `requests`, which are not in the application requirements:

```bash
pip install openpyxl requests
python scripts/import_reference_data.py \
  --api-url https://service-registry.example.org \
  --api-key <admin-key> \
  --file reference_data.xlsx \
  --dry-run          # preview first; drop --dry-run to apply
```

Run `python scripts/import_reference_data.py --help` for all options.

For development, `scripts/submit_test_submission.py --api-url … --api-key <admin-key>` posts one sample registration through the public API, using the admin key only to look up reference ids.
