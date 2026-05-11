---
icon: material/sitemap
---

# Architecture

## System overview

```
                    Internet
                       │
                    [Nginx]               ← host reverse proxy (Ansible-managed)
                       │ HTTPS
                  [Gunicorn]              ← WSGI server (Docker container)
                       │
              [Django Application]
              ┌────────┴────────┐
         [WhiteNoise]      [DRF API]
         (static files)   (REST endpoints)
                       │
          ┌────────────┼────────────┐
     [PostgreSQL]   [Redis]    [Celery]
     (submissions,  (cache,    (async tasks:
      EDAM, bio.    sessions,   email dispatch,
      tools cache)  rate limit) bio.tools sync,
                               EDAM sync,
                               stale draft cleanup)
```

Traffic enters the host nginx, which terminates TLS and proxies to the Gunicorn container on the internal network. Gunicorn serves both the Django web application and the REST API. Static files are served directly through Gunicorn via WhiteNoise — no separate static file server.

---

## Django app structure

The project follows Django's app-per-domain convention. Each app has a single clear responsibility.

| App           | Responsibility                                                                                                                                                                                                              |
| ------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `submissions` | Core domain. The `ServiceSubmission` model, registration form, views, and admin.                                                                                                                                            |
| `api`         | REST API layer. DRF viewsets, serializers, API key authentication, permissions.                                                                                                                                             |
| `registry`    | Reference data. `PrincipalInvestigator`, `ServiceCategory`, `ServiceCenter` models.                                                                                                                                         |
| `biotools`    | bio.tools integration. HTTP client, sync logic, Celery tasks, post_save signal.                                                                                                                                             |
| `edam`        | EDAM ontology. `EdamTerm` model, `sync_edam` management command.                                                                                                                                                            |
| `catalogue`   | Public Registry Catalogue. Read-only browsing of approved services at `/catalogue/`. Isolated app with no new models; queries `ServiceSubmission` via `selectors.py`. Feature-flagged via `site.toml [features] catalogue`. |

---

## Data model overview

```
ServiceSubmission (one per registered service)
├── SubmissionAPIKey (one-to-many — for scoped access)
├── BioToolsRecord (one-to-one — mirrored from bio.tools)
│   └── BioToolsFunction (one-to-many — EDAM function blocks)
├── PrincipalInvestigator (many-to-many — responsible and associated PIs)
├── ServiceCenter (many-to-many)
└── ServiceCategory (many-to-many)

EdamTerm (EDAM ontology cache — seeded via sync_edam)
└── parent → EdamTerm (self-referential, hierarchy)
```

`ServiceSubmission` references EDAM terms by URI strings (`edam_topics`, `edam_operations`) rather than foreign keys, so the submission model is not hard-coupled to the EDAM sync state.

---

## Request flow

### Web registration form

```
Browser → POST /register/
  → SubmissionForm.is_valid()
  → ServiceSubmission.objects.create()
  → post_save signal fires
  → sync_biotools_record.apply_async()  (if biotools_url set)
  → send_submission_notification.delay()
  → redirect → /register/success/
```

### REST API — create submission

```
Client → POST /api/v1/submissions/
  → ApiKeyAuthentication (no key required for create)
  → SubmissionSerializer.is_valid()
  → ServiceSubmission.objects.create()
  → SubmissionAPIKey.objects.create()  (one-time plaintext returned)
  → 201 Created { submission, api_key }
```

### REST API — read/update submission

```
Client → GET/PATCH /api/v1/submissions/{id}/
  → ApiKeyAuthentication  → validates HMAC hash
  → IsSubmissionOwner permission → checks key.submission == instance
  → SubmissionSerializer → excludes sensitive fields
  → 200 OK
```

---

## Async task processing

Celery workers connect to Redis and process three categories of tasks:

| Queue           | Tasks                                                                                                         | Schedule                     |
| --------------- | ------------------------------------------------------------------------------------------------------------- | ---------------------------- |
| Default         | `send_submission_notification` — admin email on create/update/status change; submitter email on status change | On demand                    |
| Default         | bio.tools record sync                                                                                         | On demand (post_save signal) |
| Beat (periodic) | `sync_all_biotools_records` — refresh all bio.tools records                                                   | Daily                        |
| Beat (periodic) | `edam.sync` — refresh EDAM ontology terms                                                                     | Monthly (30 days)            |
| Beat (periodic) | `cleanup_stale_drafts` — purge old incomplete submissions                                                     | Daily (24 hours)             |

Celery beat runs in its own container alongside the worker container. The worker container's Docker healthcheck uses `celery inspect ping` via the Redis broker; beat has no inspection API so its healthcheck is disabled.

**Email flow on status change:** two separate emails are sent — one to the admin (full internal report) and one to `internal_contact_email` (plain-language submitter notification with status-specific messaging). The submitter email is suppressed when `SUBMISSION_NOTIFY_OVERRIDE` is set.

---

## Database query strategy

All list-producing querysets are tuned to avoid N+1 queries. The patterns used throughout the codebase are:

### `SubmissionViewSet` base queryset

```python
ServiceSubmission.objects
    .select_related("service_center", "biotoolsrecord")
    .prefetch_related(
        "service_categories",
        "responsible_pis",
        "edam_topics",
        "edam_operations",
        "biotoolsrecord__functions",
    )
    .order_by("-submitted_at")
```

`select_related` resolves FK/OneToOne fields in a single JOIN.
`prefetch_related` resolves M2M and reverse FK relations in bulk follow-up queries (one per relation, not one per row).

### Admin list views

All admin classes set `list_select_related` for any FK accessed in `list_display`.
Admin actions that iterate querysets always call `select_related` / `prefetch_related` directly on the passed queryset rather than relying on the class-level queryset.

### Prefetch cache rules

When a relation is prefetched, accessing it with `.all()` hits the in-memory cache.
Calling `.filter()`, `.count()`, or `.values_list()` on a prefetched relation **bypasses the cache** and issues a new query. The codebase consistently uses:

- `len(obj.relation.all())` instead of `.count()`
- `[obj.field for obj in relation.all()]` instead of `.values_list(...)`
- Python `sum()` / list comprehensions on prefetched data

### Batch lookups over loops

Any serializer or task that resolves a list of identifiers against the database uses a single `filter(field__in=ids)` query and builds a dict for O(1) lookups, rather than calling `.get()` in a loop:

```python
terms_by_uri = {t.uri: t for t in EdamTerm.objects.filter(uri__in=uris)}
```

### Indexes

| Field                                             | Reason                                                             |
| ------------------------------------------------- | ------------------------------------------------------------------ |
| `ServiceSubmission.status`                        | `list_filter` in admin; `?status=` API filter                      |
| `ServiceSubmission.submitted_at`                  | Default ordering                                                   |
| `ServiceSubmission.service_center`                | FK lookup + `list_filter`                                          |
| `ServiceSubmission.register_as_elixir`            | `?register_as_elixir=` API filter                                  |
| `ServiceSubmission.year_established`              | `?year_established=` API filter                                    |
| Compound `(-submitted_at, status)`                | Admin default list view: sort + status filter together             |
| `BioToolsRecord.biotools_id`                      | Primary lookup key for the bio.tools API endpoint                  |
| `EdamTerm.uri`, `.accession`, `.branch`, `.label` | EDAM endpoint filters and lookups                                  |
| `EdamTerm (branch, is_obsolete)`                  | Compound — form queryset always filters both                       |
| Compound `(status, -updated_at)`                  | Catalogue sort by "recently updated" filtered to approved services |
| `ServiceSubmission.updated_at`                    | Catalogue sort by recently updated                                 |

---

## Security design

| Control                   | Implementation                                                                                                                              |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| CSRF protection           | Django's built-in CSRF middleware — all POST form views protected                                                                           |
| API authentication        | HMAC-based API key: plaintext hashed with PBKDF2, stored as `key_hash`                                                                      |
| Admin authentication      | Django auth + django-axes (brute-force lockout)                                                                                             |
| Content Security Policy   | `django-csp` middleware — all third-party JS/CSS vendored locally; `script-src 'unsafe-inline'` retained for Django template inline scripts |
| Rate limiting             | `django-ratelimit` on form submit and API create endpoints                                                                                  |
| Input sanitisation        | `bleach` on all free-text fields in `ServiceSubmission.save()`                                                                              |
| Sensitive field isolation | IP, user-agent hash, internal contact excluded from all API serializers                                                                     |
| Logging scrubber          | `ScrubSensitiveFilter` redacts `Authorization` and `Cookie` headers from logs                                                               |
| HSTS                      | Set in host nginx config (`max-age=31536000; includeSubDomains; preload`)                                                                   |
| TLS                       | TLSv1.2+ only; OCSP stapling enabled in nginx                                                                                               |
| Admin URL                 | Configurable prefix via `ADMIN_URL_PREFIX` env var (default: `admin-denbi`)                                                                 |

---

## Configuration layers

```
config/site.toml   ← non-secret, human-editable (name, URLs, features)
      ↓
.env               ← secrets (passwords, keys, SMTP credentials)
      ↓
config/settings.py ← Django settings (merges both, adds defaults)
      ↓
config/settings_test.py ← test overrides (SQLite, no Redis, no throttle)
```

All `site.toml` values are injected into every template via the `site_context` context processor.

---

## Static files

All static assets are vendored locally in `static/` — zero CDN requests at runtime (GDPR requirement):

| File                                                | Version                | Used by                                 |
| --------------------------------------------------- | ---------------------- | --------------------------------------- |
| `static/css/bootstrap.min.css`                      | Bootstrap 5.3.3        | All pages                               |
| `static/js/bootstrap.bundle.min.js`                 | Bootstrap 5.3.3        | All pages                               |
| `static/js/htmx.min.js`                             | HTMX 1.9.12            | bio.tools prefill                       |
| `static/css/tom-select.bootstrap5.min.css`          | Tom-Select 2.3.1       | EDAM multi-select, affiliation combobox |
| `static/js/tom-select.complete.min.js`              | Tom-Select 2.3.1       | EDAM multi-select, affiliation combobox |
| `static/swagger-ui/swagger-ui.css`                  | swagger-ui-dist 5.18.2 | `/api/docs/`                            |
| `static/swagger-ui/swagger-ui-bundle.js`            | swagger-ui-dist 5.18.2 | `/api/docs/`                            |
| `static/swagger-ui/swagger-ui-standalone-preset.js` | swagger-ui-dist 5.18.2 | `/api/docs/`                            |
| `static/swagger-ui/favicon-32x32.png`               | swagger-ui-dist 5.18.2 | `/api/docs/`                            |
| `static/redoc/bundles/redoc.standalone.js`          | ReDoc 2.2.0            | `/api/redoc/`                           |
| `static/img/favicon.ico`                            | de.NBI favicon         | All pages                               |
| `static/css/registry.css`                           | Project custom         | All pages                               |
| `static/img/icons/biotools.svg`                     | —                      | Service card bio.tools link icon        |
| `static/img/icons/fairsharing.svg`                  | —                      | Service card FAIRsharing link icon      |

WhiteNoise serves these through Gunicorn with long `Cache-Control: public, immutable` headers.

When updating a vendored library, replace the file(s) in `static/` and update the version comment in `config/settings.py` (`SPECTACULAR_SETTINGS`) or the relevant widget `Media` class.
