---
icon: material/database
---

# Database Schema

## Entity Relationship Overview

```
ServiceSubmission (UUID PK)
├── SubmissionAPIKey (FK → submission, CASCADE)         — one-to-many
├── BioToolsRecord (OneToOne → submission, CASCADE)     — one-to-one
│   └── BioToolsFunction (FK → record, CASCADE)        — one-to-many
├── service_categories → ServiceCategory               — many-to-many
├── service_center → ServiceCenter (FK, PROTECT)        — many-to-one
├── responsible_pis → PrincipalInvestigator             — many-to-many
├── edam_topics → EdamTerm (branch=topic)               — many-to-many
└── edam_operations → EdamTerm (branch=operation)       — many-to-many

EdamTerm
└── parent → EdamTerm (self-referential FK, SET_NULL)
```

---

## `submissions_servicesubmission`

The core domain model. One row per registered service.

### Metadata fields

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | UUID | PK | Auto-generated (`uuid4`), never changes |
| `status` | varchar(20) | NOT NULL | `draft` / `submitted` / `under_review` / `approved` / `rejected` / `deprecated` |
| `submitted_at` | timestamptz | NOT NULL | Set on creation (`auto_now_add`) |
| `updated_at` | timestamptz | NOT NULL | Updated on every save (`auto_now`) |
| `submission_ip` | inet | nullable | Source IP stored for abuse investigation only |
| `user_agent_hash` | varchar(64) | NOT NULL, default `""` | SHA-256 of raw User-Agent; raw UA never stored |

### Section A — General

| Column | Type | Notes |
|---|---|---|
| `date_of_entry` | date | Date the form was filled in |
| `submitter_first_name` | varchar(100) | |
| `submitter_last_name` | varchar(100) | |
| `submitter_affiliation` | varchar(300) | Institute or organisation |
| `register_as_elixir` | boolean | `false` by default |

**Computed property (not a column):** `submitter_name` — `"First Last, Affiliation"` string.

### Section B — Service Master Data

| Column | Type | Notes |
|---|---|---|
| `service_name` | varchar(300) | NOT NULL |
| `service_description` | text | NOT NULL; minimum 50 characters enforced on `clean()` |
| `year_established` | integer | NOT NULL; validated 1900–current year |
| `is_toolbox` | boolean | `false` by default |
| `toolbox_name` | varchar(200) | Required when `is_toolbox=True` |
| `user_knowledge_required` | text | Optional prerequisites for users |
| `publications_pmids` | text | Comma-separated PMIDs or DOIs; max 50 entries |
| `logo` | varchar (file path) | nullable; stored as `logos/<uuid4>.<ext>` under `MEDIA_ROOT`; original filename discarded; accepted types: PNG, JPEG, SVG; max 10 MB (configurable) |

M2M relations (via junction tables):

| Relation | Target model | Filter |
|---|---|---|
| `service_categories` | `ServiceCategory` | active only on form |
| `edam_topics` | `EdamTerm` | `branch=topic`, `is_obsolete=False` |
| `edam_operations` | `EdamTerm` | `branch=operation`, `is_obsolete=False` |

### Section C — Responsibilities

| Column | Type | Notes |
|---|---|---|
| `host_institute` | varchar(300) | NOT NULL |
| `associated_partner_note` | text | Required when "Associated partner" PI is selected |
| `public_contact_email` | varchar(254) | Publicly visible on the services catalogue |
| `internal_contact_name` | varchar(200) | Admin use only |
| `internal_contact_email` | varchar(254) | Never exposed in API responses |
| `service_center_id` | UUID FK | → `ServiceCenter.id`; `ON DELETE PROTECT` |

M2M:

| Relation | Target model |
|---|---|
| `responsible_pis` | `PrincipalInvestigator` |

### Section D — Websites & Links

All URL fields must use `https://`. Domain-specific validators are applied on save.

| Column | Type | Validator | Notes |
|---|---|---|---|
| `website_url` | varchar(2000) | HTTPS only | Required |
| `terms_of_use_url` | varchar(2000) | HTTPS only | Required |
| `license` | varchar(20) | choices | `agpl3`, `gpl3`, `lgpl3`, `mpl2`, `apache2`, `mit`, `boost`, `unlicense`, `other`, `na` |
| `github_url` | varchar(2000) | `https://github.com/` prefix | Optional |
| `biotools_url` | varchar(2000) | `https://bio.tools/` prefix | Optional; triggers bio.tools sync on save |
| `fairsharing_url` | varchar(2000) | `https://fairsharing.org/` prefix | Optional |
| `other_registry_url` | varchar(2000) | HTTPS only | Optional |

### Section E — KPIs

| Column | Type | Notes |
|---|---|---|
| `kpi_monitoring` | varchar(10) | `yes` or `planned` |
| `kpi_start_year` | varchar(100) | Year or short description |

### Section F — Discoverability & Outreach

| Column | Type | Notes |
|---|---|---|
| `keywords_uncited` | text | Keywords to detect tool mentions without formal citation |
| `keywords_seo` | text | SEO keywords for the catalogue listing |
| `survey_participation` | boolean | Willingness to participate in de.NBI user surveys; default `true` |
| `comments` | text | Optional notes for the administration office |

### Section G — Consent

| Column | Type | Notes |
|---|---|---|
| `data_protection_consent` | boolean | Mandatory; `clean()` raises error if `False` |

### Indexes

```sql
CREATE INDEX ON submissions_servicesubmission (status);
CREATE INDEX ON submissions_servicesubmission (submitted_at DESC);
CREATE INDEX ON submissions_servicesubmission (service_center_id);
CREATE INDEX ON submissions_servicesubmission (register_as_elixir);
CREATE INDEX ON submissions_servicesubmission (year_established);
-- Compound: default admin list sort + status filter
CREATE INDEX ON submissions_servicesubmission (submitted_at DESC, status);
```

---

## `submissions_submissionapikey`

API keys for programmatic access. One or more per submission. Plaintext is never stored.

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | UUID | PK | |
| `submission_id` | UUID | FK → `ServiceSubmission`, CASCADE | |
| `key_hash` | varchar(64) | UNIQUE, indexed | SHA-256 hex digest of the plaintext key |
| `label` | varchar(100) | default `"Initial key"` | Human-readable description |
| `created_at` | timestamptz | auto_now_add | |
| `created_by` | varchar(150) | default `"submitter"` | `"submitter"` or admin username |
| `scope` | varchar(10) | choices | `read` (GET only) or `write` (GET + PATCH) |
| `is_active` | boolean | default `true` | Set `false` to revoke; never deleted |
| `last_used_at` | timestamptz | nullable | Updated on every successful auth |

**Security design:**

- `key_hash` is `SHA-256(plaintext)`. Plaintext is generated in memory, shown once, and discarded.
- Lookups use `hmac.compare_digest` for constant-time comparison.
- Revoked keys (`is_active=False`) return the same HTTP 403 as an invalid key.
- A dummy `compare_digest` is performed even on miss to prevent timing oracle attacks.

---

## `registry_servicecategory`

Lookup table for service types. Managed via admin.

| Column | Type | Notes |
|---|---|---|
| `id` | serial | PK (auto-increment) |
| `name` | varchar(100) | UNIQUE |
| `is_active` | boolean | `false` hides from form; existing links preserved |

---

## `registry_servicecenter`

de.NBI service centres. Used as an FK on submissions.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | PK |
| `short_name` | varchar(50) | e.g. `HD-HuB`, `BiGi` |
| `full_name` | varchar(300) | Full official name |
| `website` | varchar(200) | Optional URL |
| `is_active` | boolean | `false` hides from form; existing FK links preserved (PROTECT) |

---

## `registry_principalinvestigator`

Named PIs who can be selected as responsible for a service.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | PK |
| `last_name` | varchar(100) | |
| `first_name` | varchar(100) | |
| `email` | varchar(254) | Optional; not publicly visible |
| `institute` | varchar(200) | Optional |
| `orcid` | varchar(30) | Optional; validated with ISO 7064 MOD 11-2 Luhn checksum |
| `is_active` | boolean | `false` hides from form |
| `is_associated_partner` | boolean | Marks the generic "Associated partner" dropdown entry |

**ORCID validation:** Format `0000-0000-0000-000X` plus ISO 7064 MOD 11-2 checksum verification — the last character may be `X` (value 10). The check digit is computed as `(12 − (total mod 11)) mod 11`; ORCIDs whose check digit is `1` (i.e. where the running total mod 11 equals 0) require the outer `mod 11` to be applied correctly.

---

## `edam_edamterm`

Local cache of the EDAM bioscientific ontology. Seeded by `manage.py sync_edam`.

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | serial | PK | |
| `uri` | varchar(200) | UNIQUE, indexed | e.g. `http://edamontology.org/topic_0091` |
| `accession` | varchar(40) | UNIQUE, indexed | e.g. `topic_0091` |
| `branch` | varchar(20) | indexed | `topic`, `operation`, `data`, `format`, `identifier` |
| `label` | varchar(200) | indexed | Human-readable name, e.g. `Proteomics` |
| `definition` | text | | EDAM definition text |
| `synonyms` | jsonb | default `[]` | List of synonym strings |
| `parent_id` | integer FK | nullable, SET_NULL | Self-referential; → `EdamTerm.id` |
| `is_obsolete` | boolean | default `false` | Obsolete terms hidden from form but retained |
| `sort_order` | integer | default `0` | Numeric part of accession for stable ordering |
| `edam_version` | varchar(20) | | Release version, e.g. `1.25` |

**Indexes:**

```sql
CREATE INDEX ON edam_edamterm (branch, is_obsolete);
CREATE INDEX ON edam_edamterm (label);
```

**EDAM branches used in the submission form:**

| Branch | Usage |
|---|---|
| `topic` | Section B — scientific domain of the service |
| `operation` | Section B — what the service does computationally |
| `data`, `format`, `identifier` | Stored via bio.tools sync; not directly selectable in the form |

---

## `biotools_biotoolsrecord`

Locally cached snapshot of a bio.tools entry. One-to-one with `ServiceSubmission`.

| Column | Type | Notes |
|---|---|---|
| `id` | UUID | PK |
| `submission_id` | UUID | OneToOne FK → `ServiceSubmission`, CASCADE |
| `biotools_id` | varchar(200) | indexed; the slug from `https://bio.tools/<id>` |
| `name` | varchar(200) | Tool name as in bio.tools |
| `description` | text | |
| `homepage` | varchar(200) | |
| `version` | varchar(100) | Latest version string |
| `license` | varchar(100) | SPDX identifier |
| `maturity` | varchar(50) | `Emerging` / `Mature` / `Legacy` |
| `cost` | varchar(50) | `Free` / `Commercial` / etc. |
| `tool_type` | jsonb | List of strings, e.g. `["Web application", "Command-line tool"]` |
| `operating_system` | jsonb | List of OS names |
| `publications` | jsonb | List of `{pmid, doi, pmcid, type, note}` |
| `documentation` | jsonb | List of `{url, type}` |
| `download` | jsonb | List of `{url, type, version}` |
| `links` | jsonb | List of `{url, type}` |
| `edam_topic_uris` | jsonb | List of EDAM topic URI strings |
| `raw_json` | jsonb | Full raw API response, stored verbatim |
| `last_synced_at` | timestamptz | nullable; set on successful sync |
| `sync_error` | text | Last error message; empty on success |
| `created_at` | timestamptz | auto_now_add |
| `updated_at` | timestamptz | auto_now |

**Computed properties (not columns):**

- `biotools_url` → `https://bio.tools/<biotools_id>`
- `sync_ok` → `True` when `sync_error == ""` and `last_synced_at is not None`

---

## `biotools_biotoolsfunction`

One functional annotation block from bio.tools. A tool may have several.

| Column | Type | Constraints | Notes |
|---|---|---|---|
| `id` | serial | PK | |
| `record_id` | UUID | FK → `BioToolsRecord`, CASCADE | |
| `position` | smallint | default `0` | 0-indexed position in bio.tools function list |
| `operations` | jsonb | | `[{uri, term}, ...]` — EDAM Operation annotations |
| `inputs` | jsonb | | `[{data: {uri, term}, formats: [{uri, term}]}, ...]` |
| `outputs` | jsonb | | Same structure as `inputs` |
| `cmd` | text | | Optional command-line note |
| `note` | text | | Optional free-text note |

**Constraint:** `UNIQUE (record_id, position)` — each position within a record is unique.

---

## Many-to-Many Junction Tables

These are automatically managed by Django. They have no extra columns.

| Table | Left FK | Right FK |
|---|---|---|
| `submissions_servicesubmission_service_categories` | `servicesubmission_id` | `servicecategory_id` |
| `submissions_servicesubmission_responsible_pis` | `servicesubmission_id` | `principalinvestigator_id` |
| `submissions_servicesubmission_edam_topics` | `servicesubmission_id` | `edamterm_id` |
| `submissions_servicesubmission_edam_operations` | `servicesubmission_id` | `edamterm_id` |

---

## Status Lifecycle

```
          ┌──────────┐
          │  draft   │  ← saved by form before submission (rare)
          └────┬─────┘
               │ submit
          ┌────▼──────┐
          │ submitted │  ← default on form POST
          └────┬──────┘
               │ admin action
        ┌──────▼───────┐
        │ under_review │
        └──┬───────────┘
           │           │
    ┌──────▼─┐     ┌───▼──────┐
    │approved│     │ rejected │
    └────────┘     └──────────┘
```

If a submitter edits an **approved** submission, the status resets to `submitted` for re-review.

---

## Input Sanitisation

All free-text fields are sanitised on every `save()`:

1. Null bytes stripped (prevents DB errors and log injection)
2. Unicode NFC normalisation (prevents homoglyph attacks)
3. Leading/trailing whitespace stripped

The sanitised fields are: `submitter_first_name`, `submitter_last_name`, `submitter_affiliation`, `service_name`, `service_description`, `toolbox_name`, `user_knowledge_required`, `host_institute`, `internal_contact_name`, `associated_partner_note`, `kpi_start_year`, `keywords_uncited`, `keywords_seo`, `comments`.

---

## Data That Is Never Stored

| Data | Why |
|---|---|
| API key plaintext | Only the SHA-256 hash is stored; plaintext shown once then discarded |
| Raw User-Agent string | Only SHA-256 hash stored in `user_agent_hash` |
| Session data in DB | Sessions use Redis |
