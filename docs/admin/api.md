---
icon: material/api
---

# Admin REST API

Endpoints that need an **Admin API key** (`Authorization: AdminKey <key>`). Creating and revoking these keys, and what a `read` key can see, is described in [Managing API Keys](api-keys.md#admin-api-keys). The endpoints submitters use with their own key are in [Using the API](../api-guide.md).

The interactive documentation at `/api/docs/` (Swagger UI) and `/api/redoc/` lists every endpoint, including these.

## Authentication and scopes

```
Authorization: AdminKey <key>
```

| Scope  | Allowed                    |
| ------ | -------------------------- |
| `read` | `GET`, `HEAD`, `OPTIONS`   |
| `full` | All methods                |

| Response | When                                                                                       |
| -------- | ------------------------------------------------------------------------------------------ |
| 401      | No key, an unknown or revoked key, or `ApiKey` sent where only `AdminKey` is accepted       |
| 403      | Valid key, but not allowed: a `read` key sending a write request, or a submission key calling an admin endpoint |

Errors use the same envelope as the rest of the API: `{"error": {...}, "request_id": "..."}`.

## Submissions

### List all submissions

`GET /api/v1/submissions/` returns all submissions in every status, 20 per page (`API_PAGE_SIZE`); page through with `?page=`. The page size cannot be changed per request.

| Parameter                 | Example                            | Description                                                                                   |
| ------------------------- | ---------------------------------- | --------------------------------------------------------------------------------------------- |
| `status`                  | `?status=approved`                 | Filter by status (`draft`, `submitted`, `under_review`, `approved`, `rejected`, `deprecated`) |
| `service_center`          | `?service_center=BioinfoProt`      | Service centre short name (case-insensitive, partial match)                                   |
| `year_established`        | `?year_established=2021`           | Filter by year                                                                                |
| `register_as_elixir`      | `?register_as_elixir=true`         | Filter by ELIXIR flag (`true`/`false`)                                                        |
| `primary_maturity_tag`    | `?primary_maturity_tag=mature`     | `mature`, `emerging` or `legacy`                                                              |
| `secondary_maturity_tags` | `?secondary_maturity_tags=unstable`| Comma-separated; every listed tag must be present                                             |
| `ordering`                | `?ordering=-submitted_at`          | `submitted_at`, `updated_at`, `service_name`, `primary_maturity_tag`; prefix `-` for descending |

```bash
# All approved services tagged Mature
curl "https://service-registry.bi.denbi.de/api/v1/submissions/?status=approved&primary_maturity_tag=mature" \
  -H "Authorization: AdminKey <key>"
```

List rows contain the same fields as the [detail response](../api-guide.md#retrieve-your-submission) with two differences: `service_description_html` is not included, and `biotoolsrecord` is a summary (counts `edam_topic_count` and `function_count` instead of the full `functions`, `publications`, `documentation`, `download` and `links`).

```json
{
  "count": 42,
  "next": "https://service-registry.bi.denbi.de/api/v1/submissions/?page=2",
  "previous": null,
  "results": [
    {
      "id": "…",
      "service_name": "…",
      "status": "approved",
      "responsible_pis": [{"id": "…", "last_name": "…", "first_name": "…", "display_name": "…", "institute": "…", "orcid": "…"}],
      "biotoolsrecord": {
        "biotools_id": "my-tool",
        "name": "My Tool",
        "edam_topic_count": 3,
        "function_count": 1,
        "last_synced_at": "2024-03-15T10:30:00Z",
        "sync_error": ""
      }
    }
  ]
}
```

### Read or update any submission

An Admin API key can also `GET` and `PATCH` `/api/v1/submissions/{id}/` for **any** submission (a `PATCH` needs a `full` key). The rules are the same as for a submission owner, described in [Using the API](../api-guide.md#update-your-submission): partial updates only (`PUT` returns 405), `service_name` cannot be changed, an approved service goes back to review when a non-exempt field changes, and the same notification e-mails are sent. The change log records the edit as `api:<key label>`. To change a service name or a status, use the Django admin.

## Reference data: categories, service centres, PIs {#reference-data-categories-service-centres-pis}

These endpoints accept **only** the `AdminKey` scheme. All three resources follow the same pattern and are not paginated.

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
but does not break any foreign keys. The response is `204`.

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
  -H "Authorization: AdminKey <admin-key>"

# List active categories only
curl "https://service-registry.bi.denbi.de/api/v1/categories/?is_active=true" \
  -H "Authorization: AdminKey <admin-key>"

# Create a new PI
curl -X POST https://service-registry.bi.denbi.de/api/v1/pis/ \
  -H "Authorization: AdminKey <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{"last_name": "Doe", "first_name": "Jane", "email": "jane.doe@example.org", "institute": "Example University"}'

# Deactivate a service centre (soft-delete)
curl -X DELETE https://service-registry.bi.denbi.de/api/v1/service-centers/<id>/ \
  -H "Authorization: AdminKey <admin-key>"

# Re-activate a category via PATCH
curl -X PATCH https://service-registry.bi.denbi.de/api/v1/categories/<id>/ \
  -H "Authorization: AdminKey <admin-key>" \
  -H "Content-Type: application/json" \
  -d '{"is_active": true}'
```

To create or update many records from a spreadsheet, use `scripts/import_reference_data.py` (see [Bulk import](../operator/data-sync.md#bulk-import-of-pis-categories-and-service-centres)).

## bio.tools records

bio.tools metadata is fetched for submissions that have a `biotools_url` and refreshed daily. It is embedded in submission responses under `biotoolsrecord`.

| Method | URL                               | Auth            | Description                                                      |
| ------ | --------------------------------- | --------------- | ---------------------------------------------------------------- |
| `GET`  | `/api/v1/biotools/`               | Admin API key   | List all bio.tools records; filter with `?biotools_id=` or `?submission=<uuid>` |
| `GET`  | `/api/v1/biotools/{biotools_id}/` | None            | One record, only if its submission is approved (404 otherwise)   |

Both return the full record (the same fields as `biotoolsrecord` in a submission detail response).

```bash
# List all synced bio.tools records
curl https://service-registry.bi.denbi.de/api/v1/biotools/ \
  -H "Authorization: AdminKey <admin-key>"

# Retrieve a specific record (public for approved services)
curl https://service-registry.bi.denbi.de/api/v1/biotools/my-tool/
```

To re-sync records by hand, select them in **Admin → bio.tools Integration → bio.tools Records** and run **Sync selected records from bio.tools now** (see the [Admin Guide](../admin-guide.md#biotools-records)).

## Rate limits

Requests with an Admin API key count against `RATE_LIMIT_API` (default 60 per minute, per key); over the limit the API answers `429`.
