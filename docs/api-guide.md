---
icon: material/api
---

# API Guide

The de.NBI Service Registration Platform REST API allows programmatic access to service registrations.

Interactive documentation is available at:

- **Swagger UI**: `/api/docs/`
- **ReDoc**: `/api/redoc/`
- **OpenAPI schema** (JSON): `/api/schema/`

Both Swagger UI (swagger-ui-dist 5.18.2) and ReDoc (2.2.0) assets are vendored locally in `static/` — no CDN or external requests are made when loading the docs pages.

---

## Authentication

Two authentication schemes are supported.

### Admin API Key (scoped, independent of user accounts)

`Authorization: AdminKey <key>`

Machine-to-machine keys that are **independent of any staff user account** and carry
an explicit scope. Create as many as needed — one per consumer, rotate individually.

| Scope  | HTTP methods allowed                | Typical use case                               |
| ------ | ----------------------------------- | ---------------------------------------------- |
| `read` | GET / HEAD / OPTIONS only           | Public-facing website, dashboard, read-only CI |
| `full` | All methods (GET/POST/PATCH/DELETE) | Trusted back-end integration                   |

**Why use a `read` scope key?**

If you give a `read` key to a third-party website that renders registry data, leaking
the key is low-risk: the worst-case outcome is that someone reads data that might
already be public. They **cannot** modify submissions, create or delete reference
data, or access any field that the serialisers exclude from responses (`submission_ip`, etc.).

**Creating a key** (see [Admin Guide → Admin API Keys](admin-guide.md#admin-api-keys)):

1. Log in to `/admin-denbi/`
2. Go to **API → Admin API Keys → Add Admin API Key**
3. Enter a label (e.g. `Public website`) and choose the scope
4. Save — the full plaintext key appears once. Copy it now.

**Using it:**

```bash
# Read-only access — safe to embed in a public application
curl https://service-registry.bi.denbi.de/api/v1/submissions/ \
  -H "Authorization: AdminKey <your-key>"

# Attempt to write with a read-scope key → 403 Forbidden
curl -X POST https://service-registry.bi.denbi.de/api/v1/categories/ \
  -H "Authorization: AdminKey <read-key>" \
  -H "Content-Type: application/json" \
  -d '{"name": "New"}'
# → {"detail": "This key is read-only. Use a full-access Admin API Key to modify data."}
```

**Revoking a key:**

Set **Is active** to unchecked in the admin UI. The record is retained for audit
purposes; the key immediately stops authenticating.

---

### Submission API Key

`Authorization: ApiKey <key>`

Issued when a service is registered (via the web form or `POST /api/v1/submissions/`).
Scoped to a single submission. The plaintext key is shown **once** — store it securely.

Two scopes are available (set by admins via the API Key admin):

| Scope   | REST API    | Web edit form (`/update/edit/`) |
| ------- | ----------- | ------------------------------- |
| `read`  | GET only    | View form (GET), cannot submit  |
| `write` | GET + PATCH | View and submit changes         |

Scope is enforced consistently in both the REST API and the web edit form.

**Note:** The `scope` field on `SubmissionAPIKey` defaults to `write`. Read-only keys can only be set via the standalone Submission API Keys list view in the admin.
A `read` key stored in the update session can load the pre-populated form
for inspection but any POST attempt is rejected and redirected to the key-entry page.

**Using it:**

```bash
curl https://service-registry.bi.denbi.de/api/v1/submissions/<id>/ \
  -H "Authorization: ApiKey <your-key>"
```

---

## Endpoints

### Register a service

`POST /api/v1/submissions/` — no authentication required. Submits a new service registration.

**Response (201):**

```json
{
  "id": "26a59fcb-...",
  "service_name": "MyTool",
  "api_key": "oGzQk9...",
  "api_key_warning": "This key is shown ONCE. Store it securely.",
  "status": "submitted",
  ...
}
```

---

### List all submissions

`GET /api/v1/submissions/` — requires admin API Key. Returns paginated full detail for all submissions.

**Query parameters:**

| Parameter            | Example                       | Description                                                                                   |
| -------------------- | ----------------------------- | --------------------------------------------------------------------------------------------- |
| `status`             | `?status=approved`            | Filter by status (`draft`, `submitted`, `under_review`, `approved`, `rejected`, `deprecated`) |
| `service_center`     | `?service_center=BioinfoProt` | Filter by centre short name                                                                   |
| `year_established`   | `?year_established=2021`      | Filter by year                                                                                |
| `register_as_elixir` | `?register_as_elixir=true`    | Filter by ELIXIR flag                                                                         |
| `ordering`           | `?ordering=-submitted_at`     | Sort (prefix `-` for descending)                                                              |

**Response (200):**

```json
{
  "count": 42,
  "next": "http://.../api/v1/submissions/?page=2",
  "previous": null,
  "results": [
    {
      "id": "...",
      "service_name": "...",
      "status": "approved",
      "edam_topics": [{"uri": "...", "accession": "topic_0091", "label": "Proteomics"}],
      "edam_operations": [...],
      "responsible_pis": [{"last_name": "...", "orcid": "..."}],
      "biotoolsrecord": {
        "id": "...",
        "biotools_id": "my-tool",
        "biotools_url": "https://bio.tools/my-tool",
        "name": "My Tool",
        "description": "A tool for ...",
        "homepage": "https://example.com/my-tool",
        "version": ["1.0", "1.1"],
        "license": "MIT",
        "maturity": "Mature",
        "cost": "Free of charge",
        "tool_type": ["Web application", "Command-line tool"],
        "operating_system": ["Linux", "Mac"],
        "edam_topic_uris": ["http://edamontology.org/topic_0091"],
        "edam_topics_resolved": [
          {"uri": "http://edamontology.org/topic_0091", "accession": "topic_0091", "label": "Proteomics"}
        ],
        "functions": [
          {
            "position": 0,
            "operations": [{"uri": "http://edamontology.org/operation_0004", "term": "Operation"}],
            "inputs": [{"data": {"uri": "...", "term": "Sequence"}, "formats": []}],
            "outputs": [{"data": {"uri": "...", "term": "Report"}, "formats": []}],
            "cmd": null,
            "note": null
          }
        ],
        "publications": [{"doi": "10.1000/xyz", "pmid": null, "pmcid": null, "type": "Primary", "note": null}],
        "documentation": [{"url": "https://example.com/docs", "type": "General", "note": null}],
        "download": [],
        "links": [],
        "last_synced_at": "2024-03-15T10:30:00Z",
        "sync_error": null
      },
      ...
    }
  ]
}
```

---

### Retrieve a submission

`GET /api/v1/submissions/{id}/` — requires `ApiKey`. Returns your own submission in full detail.
Returns 403 if the key does not belong to this submission.

---

### Update a submission

`PATCH /api/v1/submissions/{id}/` — requires `ApiKey` with `write` scope. Partial update — include only changed fields.

Updating an approved submission resets its status to `submitted` for re-review **unless every submitted field is listed in `no_reset_fields`** (configured in `site.toml [submission]`). By default, `logo`, `github_url`, `biotools_url`, `fairsharing_url`, `edam_topics`, and `edam_operations` are exempt — patching only these fields on an approved submission preserves its status and maturity tags.

When a reset does occur, `primary_maturity_tag` and `secondary_maturity_tags` are also cleared (they are only valid on approved services). The submitter update email includes a lifecycle notice.

```bash
curl -X PATCH https://service-registry.bi.denbi.de/api/v1/submissions/<id>/ \
  -H "Authorization: ApiKey <your-key>" \
  -H "Content-Type: application/json" \
  -d '{"kpi_start_year": "2026"}'
```

Full `PUT` is not supported — use `PATCH`.

!!! info "Email notifications on PATCH"
Every successful `PATCH` triggers the same notification flow as a submitter web-form edit: an admin email with the full submission report, a field-level **what changed** diff table, and a direct link to the admin change view. If any fields actually changed, the submitter also receives a separate confirmation email with the same diff table. If the edit resets the status (non-exempt field change on an approved service), the submitter email includes a lifecycle notice explaining the reset. No notification is sent when the request body contains no actual changes.

---

## Custom permissions

Two semantic permissions are defined on `ServiceSubmission` beyond the standard CRUD permissions:

| Codename                                | What it gates                                                       | API endpoint impact                            |
| --------------------------------------- | ------------------------------------------------------------------- | ---------------------------------------------- |
| `submissions.approve_servicesubmission` | Approve and reject status transitions; bulk approve/reject actions. | Admin-only via `/admin/`. Not exposed via API. |
| `submissions.manage_apikeys`            | Issue, reset, and revoke `SubmissionAPIKey` objects.                | Admin-only via `/admin/`. Not exposed via API. |

These permissions are enforced solely in the Django admin backend. See [Admin Guide → Custom permission codenames](admin-guide.md#custom-permission-codenames) for more detail.

---

### Upload or update a service logo

Service logos can be attached to a submission at registration time or added/replaced later via `PATCH`.

**On `POST` (new registration):**

```bash
curl -X POST https://service-registry.bi.denbi.de/api/v1/submissions/ \
  -H "Authorization: ApiKey <your-key>" \
  -F "service_name=MyTool" \
  -F "logo=@service-logo.png"
```

**On `PATCH` (update existing submission):**

```bash
curl -X PATCH https://service-registry.bi.denbi.de/api/v1/submissions/<id>/ \
  -H "Authorization: ApiKey <your-key>" \
  -F "logo=@service-logo.svg"
```

!!! note "Use `multipart/form-data` for logo uploads"
When uploading a logo, send the request as `multipart/form-data` (`-F` flags in curl) instead of `application/json`. You can mix file and text fields in the same request. JSON-only requests (`Content-Type: application/json`) cannot carry file data.

**Logo field behaviour:**

| Field      | Direction  | Type           | Notes                                                                                                               |
| ---------- | ---------- | -------------- | ------------------------------------------------------------------------------------------------------------------- |
| `logo`     | write-only | file           | Accepted in `multipart/form-data` requests only. Omit to leave the existing logo unchanged.                         |
| `logo_url` | read-only  | string \| null | Absolute URL to the stored logo file, or `null` if no logo has been uploaded. Returned in all submission responses. |

**Accepted formats:** PNG, JPEG, SVG — max 10 MB (configurable via `logo_max_bytes` in `config/site.toml`).

**Security processing applied automatically:**

- Magic-byte type detection (file extension and MIME header are never trusted)
- JPEG/PNG: re-encoded via Pillow to strip EXIF metadata and verify integrity
- SVG: parsed with Python's stdlib XML parser (safe on Python 3.12+/Expat 2.7.1, which blocks XXE and entity-expansion attacks), then scrubbed of `<script>` elements, `on*` event-handler attributes, and non-fragment external URLs
- Original filename is discarded; the file is stored under a UUID path (`media/logos/<uuid4>.<ext>`)

Old logos are **not deleted** when a logo is replaced — previous files remain on disk.

---

### Maturity Tags

Approved services can be tagged with a **primary maturity stage** (Mature, Emerging, Legacy) and optional **secondary tags** (Unstable, etc.). Tags provide lifecycle metadata for users browsing the registry.

**Response fields (read-only):**

- `primary_maturity_tag`: `"mature"` | `"emerging"` | `"legacy"` | `null`
- `secondary_maturity_tags`: array of strings (e.g., `["unstable"]` or `[]`)

!!! warning "Admin-only — read-only in the API"
Maturity tags are assigned exclusively by admins via the Django admin backend. Any `primary_maturity_tag` or `secondary_maturity_tags` values included in a `POST` or `PATCH` request body are **silently ignored**. Tags appear in GET responses so consumers can read the admin-assigned lifecycle stage.

**Filter by maturity:**

```bash
# List all approved Mature services
curl https://service-registry.bi.denbi.de/api/v1/submissions/?primary_maturity_tag=mature \
  -H "Authorization: AdminKey <your-key>"

# List all services tagged Unstable
curl https://service-registry.bi.denbi.de/api/v1/submissions/?secondary_maturity_tags=unstable \
  -H "Authorization: AdminKey <your-key>"
```

!!! info "Tags are cleared on unapproval"
When an admin changes a service's status away from Approved (via the admin backend), all maturity tags are automatically cleared. The API will return `null` / `[]` for the tag fields on such services. Tags can be reassigned after the service is re-approved.

---

### Reference data {#reference-data-categories-service-centres-pis}

All reference data endpoints require an admin API Key. All three resources support
full CRUD and follow the same pattern.

| Method   | URL pattern                | Description                             |
| -------- | -------------------------- | --------------------------------------- |
| `GET`    | `/api/v1/categories/`      | List all categories (active + inactive) |
| `POST`   | `/api/v1/categories/`      | Create a category                       |
| `GET`    | `/api/v1/categories/{id}/` | Retrieve a category                     |
| `PATCH`  | `/api/v1/categories/{id}/` | Partial update                          |
| `PUT`    | `/api/v1/categories/{id}/` | Full update                             |
| `DELETE` | `/api/v1/categories/{id}/` | Soft-delete (sets `is_active=False`)    |

Same pattern applies for `/api/v1/service-centers/{id}/` and `/api/v1/pis/{id}/`.

**`DELETE` is a soft-delete** — the record is retained in the database and remains
linked to existing submissions. `is_active=False` hides it from the registration form
but does not break any foreign keys.

**Filter:** `?is_active=true|false` narrows the list to active or inactive records.
Without the filter, both active and inactive records are returned.

#### Fields — `/api/v1/categories/`

| Field       | Type    | Writable | Notes              |
| ----------- | ------- | -------- | ------------------ |
| `id`        | integer | no       | Auto-assigned      |
| `name`      | string  | yes      | Must be unique     |
| `is_active` | boolean | yes      | Defaults to `true` |

#### Fields — `/api/v1/service-centers/`

| Field        | Type    | Writable | Notes              |
| ------------ | ------- | -------- | ------------------ |
| `id`         | UUID    | no       | Auto-assigned      |
| `short_name` | string  | yes      | e.g. `"HD-HuB"`    |
| `full_name`  | string  | yes      | Full official name |
| `website`    | URL     | yes      | Optional           |
| `is_active`  | boolean | yes      | Defaults to `true` |

#### Fields — `/api/v1/pis/`

| Field                   | Type    | Writable | Notes                                                  |
| ----------------------- | ------- | -------- | ------------------------------------------------------ |
| `id`                    | UUID    | no       | Auto-assigned                                          |
| `last_name`             | string  | yes      | Required                                               |
| `first_name`            | string  | yes      | Required                                               |
| `display_name`          | string  | no       | Computed (`"Last, First"`)                             |
| `email`                 | string  | yes      | Internal — never exposed in submission responses       |
| `institute`             | string  | yes      | Optional                                               |
| `orcid`                 | string  | yes      | Optional; validated format + checksum                  |
| `is_active`             | boolean | yes      | Defaults to `true`                                     |
| `is_associated_partner` | boolean | yes      | Mark `true` for the generic "Associated partner" entry |

#### curl examples

```bash
# List all service centres (active + inactive)
curl https://service-registry.bi.denbi.de/api/v1/service-centers/ \
  -H "Authorization: ApiKey <admin-key>"

# List active categories only
curl "https://service-registry.bi.denbi.de/api/v1/categories/?is_active=true" \
  -H "Authorization: ApiKey <admin-key>"

# Create a new PI
curl -X POST https://service-registry.bi.denbi.de/api/v1/pis/ \
  -H "Authorization: ApiKey <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{"last_name": "Smith", "first_name": "Alice", "email": "a.smith@example.com", "institute": "Example University"}'

# Deactivate a service centre (soft-delete)
curl -X DELETE https://service-registry.bi.denbi.de/api/v1/service-centers/<id>/ \
  -H "Authorization: ApiKey <admin-key>"

# Re-activate a category via PATCH
curl -X PATCH https://service-registry.bi.denbi.de/api/v1/categories/<id>/ \
  -H "Authorization: ApiKey <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{"is_active": true}'
```

### bio.tools records

Bio.tools metadata is automatically fetched and kept in sync when a submission includes a `biotools_id`.
The data is embedded directly in every submission response under the `biotoolsrecord` key (see above).

A standalone read-only endpoint is also available for admin API keys:

| Method | URL                               | Description                                       |
| ------ | --------------------------------- | ------------------------------------------------- |
| `GET`  | `/api/v1/biotools/`               | List all bio.tools records                        |
| `GET`  | `/api/v1/biotools/{biotools_id}/` | Retrieve one bio.tools record by its bio.tools ID |

**Authentication:** admin API Key required.

Both endpoints return the same field set as the `biotoolsrecord` object shown in the submission response above, plus a `submission` link field pointing to the associated submission.

```bash
# List all synced bio.tools records
curl https://service-registry.bi.denbi.de/api/v1/biotools/ \
  -H "Authorization: AdminKey <admin-key>"

# Retrieve a specific record
curl https://service-registry.bi.denbi.de/api/v1/biotools/my-tool/ \
  -H "Authorization: AdminKey <admin-key>"
```

#### bio.tools sync actions

Admins can trigger a manual re-sync from the bio.tools record list in the Django admin
(**Bio.tools records → select records → "Sync selected records from bio.tools now"**).
Syncs are queued as Celery tasks and run in the background.

---

### EDAM ontology terms

`GET /api/v1/edam/` — public, no authentication required. Returns all non-obsolete EDAM terms.

Filter: `?branch=topic|operation|data|format`, `?q=<search>`

### EDAM term detail

`GET /api/v1/edam/{accession}/` — public. Look up by accession (e.g. `topic_0091`) or UUID.

---

## Response shape

All error responses follow this envelope:

```json
{
  "error": { "detail": "Authentication credentials were not provided." },
  "request_id": "8e26f6d5-0094-48ea-9a36-c417921815a9"
}
```

The `request_id` is included in every response (success and error) as `X-Request-ID`
header and in error bodies. Use it when reporting issues.

---

## Field visibility

| Field                     | POST/PATCH | GET response | Notes                                                                            |
| ------------------------- | ---------- | ------------ | -------------------------------------------------------------------------------- |
| `internal_contact_name`   | required   | never        | Write-only; stored for admin use only                                            |
| `internal_contact_email`  | required   | never        | Write-only; stored for admin use only                                            |
| `primary_maturity_tag`    | ignored    | yes          | Read-only in API; set by admins via backend. See [Maturity Tags](#maturity-tags) |
| `secondary_maturity_tags` | ignored    | yes          | Read-only in API; set by admins via backend. See [Maturity Tags](#maturity-tags) |
| `submission_ip`           | —          | never        | Server-generated; not exposed via API                                            |
| `user_agent_hash`         | —          | never        | Server-generated; not exposed via API                                            |

---

## curl examples

```bash
# Register a new service (public) — internal_contact_* are required
curl -X POST https://service-registry.bi.denbi.de/api/v1/submissions/ \
  -H "Content-Type: application/json" \
  -d @submission.json
# submission.json must include internal_contact_name and internal_contact_email

# List all submissions (admin key)
curl https://service-registry.bi.denbi.de/api/v1/submissions/ \
  -H "Authorization: AdminKey <admin-key>"

# Retrieve your submission (ApiKey)
curl https://service-registry.bi.denbi.de/api/v1/submissions/<id>/ \
  -H "Authorization: ApiKey <your-key>"

# Update a field (ApiKey, write scope)
curl -X PATCH https://service-registry.bi.denbi.de/api/v1/submissions/<id>/ \
  -H "Authorization: ApiKey <your-key>" \
  -H "Content-Type: application/json" \
  -d '{"website_url": "https://new-url.example.com"}'

# Upload or replace a logo (ApiKey, write scope — multipart/form-data)
curl -X PATCH https://service-registry.bi.denbi.de/api/v1/submissions/<id>/ \
  -H "Authorization: ApiKey <your-key>" \
  -F "logo=@service-logo.png"

# Browse EDAM topics (public)
curl "https://service-registry.bi.denbi.de/api/v1/edam/?branch=topic&q=proteomics"
```
