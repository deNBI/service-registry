---
icon: material/api
---

# API Guide

The de.NBI Service Registry REST API allows programmatic access to service registrations.

Interactive documentation is available at:

- **Swagger UI**: `/api/docs/`
- **ReDoc**: `/api/redoc/`
- **OpenAPI schema** (JSON): `/api/schema/`

Both Swagger UI (swagger-ui-dist 5.18.2) and ReDoc (2.2.0) assets are vendored locally in `static/` — no CDN or external requests are made when loading the docs pages.

---

## Authentication

Two authentication schemes are supported.

### Admin Token

`Authorization: Token <key>`

For staff users and trusted integrations. Grants access to the full submission list
and all reference data endpoints.

**Creating a token** (see [Admin Guide → Issuing Admin API Tokens](admin-guide.md#issuing-admin-api-tokens)):

1. Log in to `/admin-denbi/`
2. Go to **Auth Token → Tokens → Add Token**
3. Select the staff user and save
4. Copy the key — it is shown once in full and then masked

**Using it:**

```bash
curl https://service-registry.bi.denbi.de/api/v1/submissions/ \
  -H "Authorization: Token d876555a570df89909058eeeb6d88f4b814a81a1"
```

### Submission API Key

`Authorization: ApiKey <key>`

Issued when a service is registered (via the web form or `POST /api/v1/submissions/`).
Scoped to a single submission. The plaintext key is shown **once** — store it securely.

Two scopes are available (set by admins via the API Key admin):

| Scope   | Allowed methods |
| ------- | --------------- |
| `read`  | GET only        |
| `write` | GET + PATCH     |

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

`GET /api/v1/submissions/` — requires admin Token. Returns paginated full detail for all submissions.

**Query parameters:**

| Parameter            | Example                       | Description                      |
| -------------------- | ----------------------------- | -------------------------------- |
| `status`             | `?status=approved`            | Filter by status                 |
| `service_center`     | `?service_center=BioinfoProt` | Filter by centre short name      |
| `year_established`   | `?year_established=2021`      | Filter by year                   |
| `register_as_elixir` | `?register_as_elixir=true`    | Filter by ELIXIR flag            |
| `ordering`           | `?ordering=-submitted_at`     | Sort (prefix `-` for descending) |

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
      "biotoolsrecord": { ... },
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

Updating an approved submission resets its status to `submitted` for re-review.

```bash
curl -X PATCH https://service-registry.bi.denbi.de/api/v1/submissions/<id>/ \
  -H "Authorization: ApiKey <your-key>" \
  -H "Content-Type: application/json" \
  -d '{"kpi_start_year": "2026"}'
```

Full `PUT` is not supported — use `PATCH`.

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

| Field | Direction | Type | Notes |
|-------|-----------|------|-------|
| `logo` | write-only | file | Accepted in `multipart/form-data` requests only. Omit to leave the existing logo unchanged. |
| `logo_url` | read-only | string \| null | Absolute URL to the stored logo file, or `null` if no logo has been uploaded. Returned in all submission responses. |

**Accepted formats:** PNG, JPEG, SVG — max 10 MB (configurable via `logo_max_bytes` in `config/site.toml`).

**Security processing applied automatically:**

- Magic-byte type detection (file extension and MIME header are never trusted)
- JPEG/PNG: re-encoded via Pillow to strip EXIF metadata and verify integrity
- SVG: parsed with Python's stdlib XML parser (safe on Python 3.12+/Expat 2.7.1, which blocks XXE and entity-expansion attacks), then scrubbed of `<script>` elements, `on*` event-handler attributes, and non-fragment external URLs
- Original filename is discarded; the file is stored under a UUID path (`media/logos/<uuid4>.<ext>`)

Old logos are **not deleted** when a logo is replaced — previous files remain on disk.

---

### Reference data {#reference-data-categories-service-centres-pis}

All reference data endpoints require an admin Token. All three resources support
full CRUD and follow the same pattern.

| Method | URL pattern | Description |
|--------|-------------|-------------|
| `GET` | `/api/v1/categories/` | List all categories (active + inactive) |
| `POST` | `/api/v1/categories/` | Create a category |
| `GET` | `/api/v1/categories/{id}/` | Retrieve a category |
| `PATCH` | `/api/v1/categories/{id}/` | Partial update |
| `PUT` | `/api/v1/categories/{id}/` | Full update |
| `DELETE` | `/api/v1/categories/{id}/` | Soft-delete (sets `is_active=False`) |

Same pattern applies for `/api/v1/service-centers/{id}/` and `/api/v1/pis/{id}/`.

**`DELETE` is a soft-delete** — the record is retained in the database and remains
linked to existing submissions. `is_active=False` hides it from the registration form
but does not break any foreign keys.

**Filter:** `?is_active=true|false` narrows the list to active or inactive records.
Without the filter, both active and inactive records are returned.

#### Fields — `/api/v1/categories/`

| Field | Type | Writable | Notes |
|-------|------|----------|-------|
| `id` | integer | no | Auto-assigned |
| `name` | string | yes | Must be unique |
| `is_active` | boolean | yes | Defaults to `true` |

#### Fields — `/api/v1/service-centers/`

| Field | Type | Writable | Notes |
|-------|------|----------|-------|
| `id` | UUID | no | Auto-assigned |
| `short_name` | string | yes | e.g. `"HD-HuB"` |
| `full_name` | string | yes | Full official name |
| `website` | URL | yes | Optional |
| `is_active` | boolean | yes | Defaults to `true` |

#### Fields — `/api/v1/pis/`

| Field | Type | Writable | Notes |
|-------|------|----------|-------|
| `id` | UUID | no | Auto-assigned |
| `last_name` | string | yes | Required |
| `first_name` | string | yes | Required |
| `display_name` | string | no | Computed (`"Last, First"`) |
| `email` | string | yes | Internal — never exposed in submission responses |
| `institute` | string | yes | Optional |
| `orcid` | string | yes | Optional; validated format + checksum |
| `is_active` | boolean | yes | Defaults to `true` |
| `is_associated_partner` | boolean | yes | Mark `true` for the generic "Associated partner" entry |

#### curl examples

```bash
# List all service centres (active + inactive)
curl https://service-registry.bi.denbi.de/api/v1/service-centers/ \
  -H "Authorization: Token <admin-token>"

# List active categories only
curl "https://service-registry.bi.denbi.de/api/v1/categories/?is_active=true" \
  -H "Authorization: Token <admin-token>"

# Create a new PI
curl -X POST https://service-registry.bi.denbi.de/api/v1/pis/ \
  -H "Authorization: Token <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{"last_name": "Smith", "first_name": "Alice", "email": "a.smith@example.com", "institute": "Example University"}'

# Deactivate a service centre (soft-delete)
curl -X DELETE https://service-registry.bi.denbi.de/api/v1/service-centers/<id>/ \
  -H "Authorization: Token <admin-token>"

# Re-activate a category via PATCH
curl -X PATCH https://service-registry.bi.denbi.de/api/v1/categories/<id>/ \
  -H "Authorization: Token <admin-token>" \
  -H "Content-Type: application/json" \
  -d '{"is_active": true}'
```

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

## Excluded fields

The following fields are **never** included in any API response regardless of authentication level:

- `internal_contact_email`
- `internal_contact_name`
- `submission_ip`
- `user_agent_hash`

---

## curl examples

```bash
# Register a new service (public)
curl -X POST https://service-registry.bi.denbi.de/api/v1/submissions/ \
  -H "Content-Type: application/json" \
  -d @submission.json

# List all submissions (admin token)
curl https://service-registry.bi.denbi.de/api/v1/submissions/ \
  -H "Authorization: Token <admin-token>"

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
