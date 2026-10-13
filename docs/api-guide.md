---
icon: material/api
---

# Using the API

For service owners who want to register or update their service from a script instead of the web form. You can only read and change **your own** submission with the API key you received at registration. Registry staff: the endpoints that need an Admin API key are in the [Admin REST API](admin/api.md).

Interactive documentation is available at:

- **Swagger UI**: `/api/docs/`
- **ReDoc**: `/api/redoc/`
- **OpenAPI schema**: `/api/schema/` (YAML; `/api/schema/?format=json` for JSON)

The schema lists every field with its type, length limit and description.

---

## Your API key

`Authorization: ApiKey <key>`

You receive the key once, when you register (in the web form's confirmation page, or in the `api_key` field of the `POST` response). It belongs to one submission. Store it securely: it is not e-mailed and cannot be shown again. If you lose it, see [Lost your API key?](user-guide.md#lost-your-api-key).

| Scope   | REST API    | Web edit form (`/update/edit/<submission_id>/`) |
| ------- | ----------- | ----------------------------------------------- |
| `read`  | GET only    | View the form, cannot save                      |
| `write` | GET + PATCH | View and save changes                           |

The key you receive at registration has **write** scope. The registry team can issue additional keys, including read-only ones, on request.

```bash
curl https://service-registry.bi.denbi.de/api/v1/submissions/<id>/ \
  -H "Authorization: ApiKey <your-key>"
```

---

## Endpoints

| Method  | URL                               | Auth                     | Purpose                         |
| ------- | --------------------------------- | ------------------------ | ------------------------------- |
| `POST`  | `/api/v1/submissions/`            | None                     | Register a new service          |
| `GET`   | `/api/v1/submissions/{id}/`       | `ApiKey`                 | Read your submission            |
| `PATCH` | `/api/v1/submissions/{id}/`       | `ApiKey` (write scope)   | Update your submission          |
| `GET`   | `/api/v1/edam/`                   | None                     | List EDAM terms                 |
| `GET`   | `/api/v1/edam/{accession}/`       | None                     | One EDAM term                   |
| `GET`   | `/api/v1/biotools/{biotools_id}/` | None                     | The bio.tools record of an approved service |

### Register a service

`POST /api/v1/submissions/` — no authentication. Send JSON, or `multipart/form-data` if you include a logo.

**Required fields**

| Field                    | Type             | Notes                                                                                   |
| ------------------------ | ---------------- | --------------------------------------------------------------------------------------- |
| `date_of_entry`          | date             | `YYYY-MM-DD`                                                                            |
| `service_name`           | string (300)     | Cannot be changed later                                                                 |
| `service_description`    | string           | 50 to 5,000 characters                                                                  |
| `year_established`       | integer          | Between 1900 and the current year                                                       |
| `service_category_ids`   | list of integers | Category ids                                                                            |
| `service_center_id`      | UUID             | Service centre id                                                                       |
| `responsible_pi_ids`     | list of UUIDs    | PI ids                                                                                  |
| `host_institute`         | string (300)     |                                                                                         |
| `public_contact_email`   | string (2000)    | E-mail address or `https://` support URL, intended for public display (de.NBI services page) |
| `internal_contact_name`  | string (200)     | For the registry office only; never returned by the API                                 |
| `internal_contact_email` | e-mail           | Receives the registry's e-mails; never returned by the API                              |
| `website_url`            | URL              | `https://` only                                                                         |
| `terms_of_use_url`       | URL              | `https://` only                                                                         |
| `publications_pmids`     | string           | Comma-separated PMIDs or DOIs, at most 50                                               |
| `kpi_monitoring`         | string           | `yes` or `planned`                                                                      |
| `data_protection_consent`| boolean          | Must be `true`; never returned by the API                                               |

**Conditional rules**

- `toolbox_name` is required when `is_toolbox` is `true`.
- `kpi_start_year` is required when `kpi_monitoring` is `yes`.
- `associated_partner_note` is required when one of the `responsible_pi_ids` is the "Associated partner" entry.
- At least one entry in `licenses` or a non-empty `license_note` is required. This is checked on registration and on every `PATCH` (against the stored values for fields you do not send).

**Optional fields** include `submitter_first_name`, `submitter_last_name`, `submitter_affiliation`, `register_as_elixir`, `is_toolbox`, `toolbox_name`, `user_knowledge_required`, `edam_topic_ids`, `edam_operation_ids` (see below), `licenses` (SPDX identifiers such as `"MIT"`), `license_note`, `github_url` (`https://github.com/…`), `biotools_url` (`https://bio.tools/…`), `fairsharing_url` (`https://fairsharing.org/…`), `other_registry_url`, `kpi_start_year`, `keywords_uncited`, `keywords_seo`, `survey_participation`, `comments` and `logo`. The schema at `/api/docs/` has the full list.

**Where the ids come from.** Category, service centre and PI ids are listed by endpoints that need an Admin API key, so they are not available to you directly. Ask the registry office ([contact](user-guide.md#questions)) for the ids you need. Once your submission exists, its `GET` response shows the ids of the categories, centre and PIs you chose. `edam_topic_ids` and `edam_operation_ids` take the registry's internal numeric term ids, which the public EDAM endpoint does not return; the simplest way to set EDAM terms is the web edit form.

**Example:**

```bash
curl -X POST https://service-registry.bi.denbi.de/api/v1/submissions/ \
  -H "Content-Type: application/json" \
  -d @submission.json
```

```json
{
  "date_of_entry": "2026-10-12",
  "service_name": "Example Aligner",
  "service_description": "Example Aligner maps short sequencing reads to a reference genome and reports alignment statistics.",
  "year_established": 2021,
  "service_category_ids": [1],
  "service_center_id": "<service-centre-uuid>",
  "responsible_pi_ids": ["<pi-uuid>"],
  "host_institute": "Example University",
  "public_contact_email": "support@example.org",
  "internal_contact_name": "Jane Doe, Example University",
  "internal_contact_email": "jane.doe@example.org",
  "website_url": "https://example.org/aligner",
  "terms_of_use_url": "https://example.org/aligner/terms",
  "publications_pmids": "12345678",
  "licenses": ["MIT"],
  "kpi_monitoring": "planned",
  "data_protection_consent": true
}
```

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

A missing or invalid field returns `400` with one message per field:

```json
{
  "error": {
    "service_description": ["Service description must be at least 50 characters."],
    "website_url": ["URL must use the https:// scheme. Plain http:// and other schemes are not accepted."]
  },
  "request_id": "…"
}
```

The registry office and your internal contact address receive an e-mail for every new registration.

---

### Retrieve your submission

`GET /api/v1/submissions/{id}/` — requires your `ApiKey`. Returns your submission in full detail. Any other id returns `404`.

**Description fields.** `service_description` is the raw text as submitted (only line endings, Unicode normalisation and leading/trailing whitespace are normalised): plain text that may contain Markdown and any characters, including `<`, `>` and `&` and text that looks like HTML tags (rows saved by older versions of the web form may hold HTML entities such as `&gt;` instead). It is not HTML: HTML-escape it wherever you insert it into a page, or use `service_description_html`, which is always safe to insert. The detail response (this endpoint, and the create and update responses) also includes a read-only `service_description_html`:

- It is always safe HTML, ready to insert into a page.
- When Markdown rendering is enabled on the server, it is the sanitized rendered HTML (CommonMark): paragraphs, line breaks, bold/italic, lists (an ordered list may carry a numeric `start` attribute), blockquotes, headings and links. Headings are shifted down three levels, so it may contain `h4`, `h5` and `h6` (`#` becomes `h4`, `##` `h5`, `###` and deeper `h6`) but never `h1` to `h3`. Only absolute `http://` or `https://` links with a host and non-empty `mailto:` links are kept; other links (including relative ones and host-less ones such as `https:example.org`) are reduced to their text, and so are links with no visible text. Kept links carry `rel="nofollow noopener noreferrer"`; `http`/`https` links also get `target="_blank"`, `mailto` links do not.
- When it is disabled, it is the plain text HTML-escaped once, with no paragraph or line-break markup.

Rows saved by older versions of the web form may hold HTML entities such as `&gt;` in the raw field; `service_description_html` decodes them, so prefer it for display.

`service_description_html` is ignored if sent in a `POST` or `PATCH`. Length validation applies to the raw `service_description`.

```json
{
  "service_description": "Aligns **short reads**.\n\n- fast\n- accurate",
  "service_description_html": "<p>Aligns <strong>short reads</strong>.</p>\n<ul>\n<li>fast</li>\n<li>accurate</li>\n</ul>"
}
```

**bio.tools record.** When your service has a bio.tools URL, `biotoolsrecord` holds the synced metadata:

```json
"biotoolsrecord": {
  "id": "...",
  "biotools_id": "my-tool",
  "biotools_url": "https://bio.tools/my-tool",
  "name": "My Tool",
  "description": "A tool for ...",
  "homepage": "https://example.org/my-tool",
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
  "documentation": [{"url": "https://example.org/docs", "type": "General", "note": null}],
  "download": [],
  "links": [],
  "last_synced_at": "2024-03-15T10:30:00Z",
  "sync_error": ""
}
```

**Related records** are returned as objects: `service_categories` (`id`, `name`), `service_center` (`id`, `short_name`, `full_name`, `website`), `responsible_pis` (`id`, `last_name`, `first_name`, `display_name`, `institute`, `orcid`), `edam_topics` and `edam_operations`, `licenses` (SPDX ids). `biotoolsrecord` is `null` when there is no bio.tools record. `links` holds URLs to this record, the schema and the docs.

---

### Update your submission

`PATCH /api/v1/submissions/{id}/` — requires your `ApiKey` with `write` scope. Partial update — include only changed fields. Full `PUT` is not supported and returns `405`.

```bash
curl -X PATCH https://service-registry.bi.denbi.de/api/v1/submissions/<id>/ \
  -H "Authorization: ApiKey <your-key>" \
  -H "Content-Type: application/json" \
  -d '{"website_url": "https://new-url.example.org"}'
```

**Approved services and re-review.** If your service is approved and the update changes a field that the registry treats as core content, the service goes back to **Submitted** for re-review and its maturity tags are cleared. Changing only supplementary fields (for example links, EDAM annotations, keywords, publications, contact fields, KPI fields or comments in the default configuration) keeps it approved. The exact list is set by the registry operators. Sending a field with the value it already has is not a change, so a GET, modify, PATCH round-trip that echoes unchanged fields keeps the service approved; differences only in surrounding whitespace, line endings or Unicode normalisation do not count either. Your confirmation e-mail says when a re-review was triggered.

!!! warning "The service name cannot be changed"
    `service_name` is read-only on update. A `PATCH` that includes a **different** `service_name` still succeeds (`200`): the name is left unchanged, all other fields are applied, and the response gains a `warnings` array. Sending the same name is accepted without a warning. Contact the registry office if the name must be corrected.

```json
{
  "id": "…",
  "service_name": "Original Name",
  "warnings": [
    "The service name cannot be changed after a service is submitted. Your requested name change was not applied; all other changes were accepted."
  ]
}
```

**E-mails.** Every successful `PATCH` notifies the registry office. If anything actually changed, you also receive a confirmation e-mail at the internal contact address listing what changed (and whether a re-review was triggered). A `PATCH` with no actual changes sends you no e-mail.

The fields you cannot set yourself (`status`, `primary_maturity_tag`, `secondary_maturity_tags`) are ignored if sent.

---

### Upload or replace your logo

Logos can be sent at registration or later with `PATCH`. Send the request as `multipart/form-data` (`-F` in curl); JSON requests cannot carry files. You can mix file and text fields in the same request.

```bash
curl -X PATCH https://service-registry.bi.denbi.de/api/v1/submissions/<id>/ \
  -H "Authorization: ApiKey <your-key>" \
  -F "logo=@service-logo.svg"
```

To send a logo at registration, add `-F "logo=@service-logo.png"` to a `multipart/form-data` registration request that also carries all required fields.

| Field      | Direction  | Type           | Notes                                                                                                               |
| ---------- | ---------- | -------------- | ------------------------------------------------------------------------------------------------------------------- |
| `logo`     | write-only | file           | Accepted in `multipart/form-data` requests only, on create (`POST`) and update (`PATCH`). Omit to leave the existing logo unchanged. |
| `logo_url` | read-only  | string \| null | Absolute URL to the stored logo file, or `null` if no logo has been uploaded. Returned in all submission responses. |

Accepted formats are PNG, JPEG and SVG. The size limits are shown in the `logo` field description at `/api/docs/` (by default PNG/JPEG up to 10 MB and 25 megapixels, SVG up to 1 MB). A file over the limits, of another type, or one that cannot be processed is rejected with `400` and a message for the `logo` field, and nothing is stored. What happens to a logo after upload, and which SVGs are rejected, is described in the [User Guide](user-guide.md#logo).

---

### Maturity tags

Approved services can carry a **primary maturity tag** (`mature`, `emerging`, `legacy`) and **secondary tags** (for example `unstable`), assigned by the registry team.

- `primary_maturity_tag`: `"mature"` | `"emerging"` | `"legacy"` | `null`
- `secondary_maturity_tags`: array of strings (e.g., `["unstable"]` or `[]`)

They are read-only in the API. They are cleared when a service leaves the Approved status, for example when an update sends it back to review.

---

### EDAM ontology terms

`GET /api/v1/edam/` — public, no authentication required. Returns all non-obsolete EDAM terms (not paginated).

Filter: `?branch=topic|operation|data|format`, `?q=<search>` (searches label, definition and synonyms).

`GET /api/v1/edam/{accession}/` — public. Look up by accession (e.g. `topic_0091`).

```bash
curl "https://service-registry.bi.denbi.de/api/v1/edam/?branch=topic&q=proteomics"
```

### bio.tools record

`GET /api/v1/biotools/{biotools_id}/` — public. Returns the synced bio.tools record of an approved service; `404` if there is none or the service is not approved.

---

## Field visibility

| Field                     | POST/PATCH | GET response | Notes                                                                            |
| ------------------------- | ---------- | ------------ | -------------------------------------------------------------------------------- |
| `internal_contact_name`   | required   | never        | Write-only; stored for admin use only                                            |
| `internal_contact_email`  | required   | never        | Write-only; stored for admin use only                                            |
| `data_protection_consent` | required   | never        | Write-only; must be `true`                                                       |
| `primary_maturity_tag`    | ignored    | yes          | Set by the registry team. See [Maturity tags](#maturity-tags)                    |
| `secondary_maturity_tags` | ignored    | yes          | Set by the registry team. See [Maturity tags](#maturity-tags)                    |
| `service_description_html` | ignored   | yes          | Read-only; safe HTML rendering of `service_description`. See [Retrieve your submission](#retrieve-your-submission) |
| `submission_ip`           | —          | never        | Server-generated; not exposed via API                                            |
| `user_agent_hash`         | —          | never        | Server-generated; not exposed via API                                            |

---

## Errors

All error responses follow this envelope:

```json
{
  "error": { "detail": "Authentication credentials were not provided." },
  "request_id": "8e26f6d5-0094-48ea-9a36-c417921815a9"
}
```

| Status | Meaning                                                                                     |
| ------ | ------------------------------------------------------------------------------------------- |
| 400    | A field is missing or invalid; `error` lists the messages per field                         |
| 401    | No key, an unknown or revoked key, or a wrong scheme in the `Authorization` header          |
| 403    | The key is valid but not allowed to do this, for example a read-scope key sending `PATCH`   |
| 404    | The submission does not exist or does not belong to your key                                |
| 405    | `PUT` (use `PATCH`)                                                                         |
| 429    | Too many requests; wait and retry                                                           |

Every response carries the request id in the `X-Request-ID` header. Include it when you report a problem.

## Limits

- Requests without a key (registering, EDAM lookups) are limited to 10 per minute per client.
- Requests with your key are limited per submission (by default 60 per minute).
- Browsers on other sites cannot call the API unless the registry operators allow that origin (CORS is off by default).
