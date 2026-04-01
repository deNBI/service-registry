---
icon: material/shield-account
---

# Admin Guide — de.NBI Service Registry

## Accessing the Admin Portal

The admin is available at `/<ADMIN_URL_PREFIX>/` (default: `/admin-denbi/`).
The URL prefix is configured via the `ADMIN_URL_PREFIX` environment variable in `.env` (default: `admin-denbi`).

Log in with your Django superuser credentials.

---

## Managing Submissions

### Submission List View

The list shows: service name, submitter, status badge, service centre, ELIXIR-DE flag, submission date.

**Filters** (right sidebar): status, category, service centre, ELIXIR-DE flag, date range.
**Search** (top): service name, submitter name, PI name, host institute.

### Submission Detail View

The detail view shows all form sections A–G plus:
- Submission metadata (ID, timestamps, IP — IP visible only to superusers)
- EDAM Topics and EDAM Operations annotations selected by the submitter
- **Logo** — inline preview and upload field (see [Service Logos](#service-logos))
- **bio.tools Record** section (if a bio.tools URL was entered) — see [bio.tools Records](#biotools-records)
- API key management section at the bottom

### Changing Submission Status

**Individual:** Open a submission, change the Status field, and save. Two emails are sent automatically:

- **Admin notification** — sent to the registry coordination address (`[contact] email` in `site.toml`), CC'd to `SUBMISSION_NOTIFY_CC` if configured.
- **Submitter notification** — sent directly to the `internal_contact_email` of the submission with a plain-language status update ("Your service has been approved / was not approved at this time"). This is separate from the admin email so the submitter receives a clear, action-oriented message rather than the full internal report.

**Bulk:** Select submissions in the list view, then choose an action from the dropdown:

| Action | Result |
|--------|--------|
| Approve selected | Sets status → `Approved` |
| Reject selected | Sets status → `Rejected` |
| Mark selected as Under Review | Sets status → `Under Review` |
| Deprecate selected | Sets status → `Deprecated` |
| Undeprecate selected | Sets status → `Submitted` (returns to review queue) |

All transitions fire the standard admin + submitter email notifications via Celery.

**Individual (change view):** Open a submission — the "Change Status" panel shows buttons for all transitions including **Deprecate** and **Undeprecate**. The current status is highlighted and its button is disabled.

!!! note "Deprecation is owner-reversible only by admins"
    Service owners can mark their own service as deprecated via the edit form. Only admins can reverse a deprecation (via bulk action or the change view button), which resets status to `Submitted` for re-review.

### Filtering by Status

The list view sidebar filters by **status**, making it easy to find submissions in a specific state:
`Draft` · `Submitted` · `Under Review` · `Approved` · `Rejected` · `Deprecated`

Combine status with the category, service centre, or date filters to narrow down exports.

### Exporting Submissions

Select submissions → choose **Export selected as CSV** or **Export selected as JSON**.

Both formats include all submission fields:

| Category | Fields included |
|----------|----------------|
| Identity | `id`, `status`, `submitted_at`, `updated_at` |
| Submitter | `submitter_first_name/last_name/affiliation`, `host_institute`, `public_contact_email`, `internal_contact_name/email` |
| Service | `service_name`, `service_description`, `year_established`, `is_toolbox`, `toolbox_name`, `user_knowledge_required`, `publications_pmids` |
| Relations | `service_categories`, `responsible_pis` (semicolons in CSV, arrays in JSON) |
| EDAM | `edam_topics`, `edam_operations` — label + URI (semicolons in CSV, objects in JSON) |
| Links | `website_url`, `terms_of_use_url`, `license`, `github_url`, `biotools_url`, `fairsharing_url`, `other_registry_url` |
| KPIs | `kpi_monitoring`, `kpi_start_year` |
| Discovery | `keywords_uncited`, `keywords_seo`, `register_as_elixir`, `survey_participation`, `comments` |
| Logo | `logo_url` — absolute URL, or empty if no logo uploaded |
| bio.tools (scalar) | `biotools_id`, `biotools_name`, `biotools_description`, `biotools_homepage`, `biotools_version`, `biotools_license`, `biotools_maturity`, `biotools_cost` — empty strings if no bio.tools record |
| bio.tools (lists) | `biotools_tool_type`, `biotools_operating_system` — semicolons in CSV, arrays in JSON |
| bio.tools (EDAM) | `biotools_edam_topic_uris`, `biotools_edam_operation_uris` — semicolons in CSV, arrays in JSON |
| bio.tools (structured) | `biotools_functions`, `biotools_publications`, `biotools_documentation`, `biotools_download`, `biotools_links` — JSON strings in CSV, arrays of objects in JSON |
| bio.tools (sync) | `biotools_last_synced_at` — ISO datetime of last successful sync, or empty |

---

## API Key Management

Each submission detail page shows the **Submission API Keys** section. This shows all keys ever issued, their label, creation date, last-used timestamp, and whether they are active.

### Available actions

| Action | What it does |
|--------|-------------|
| **Revoke all keys** | Deactivates all active keys. The submitter can no longer edit their submission until a new key is issued. |
| **Reset key** | Revokes all keys and issues one new one. The new plaintext key is shown **once** in a banner. Communicate it to the submitter securely (e.g. encrypted email, phone). |
| **Issue additional key** | Creates a new active key alongside existing ones. Useful for CI/CD pipelines or team members. Enter a descriptive label. |

!!! warning "Key shown once only"
    Key plaintexts are shown once in the admin interface and are never stored anywhere.
    If you accidentally close the browser before copying the key, you must reset it again.

All key operations are logged in Django's admin audit log (History tab on the submission).

---

## Managing Reference Data

Reference data (PIs, service centres, categories) can be managed in two ways:

- **Admin UI** — the Django admin portal (see below)
- **REST API** — `POST/PATCH/DELETE /api/v1/pis/`, `/api/v1/service-centers/`, `/api/v1/categories/` — useful for bulk onboarding or automation (see [API Guide](api-guide.md#reference-data-categories-service-centres-pis))

Both interfaces support soft-delete: `DELETE` via the API (or setting `is_active = False` in the admin) hides the record from the registration form but keeps it linked to existing submissions.

### Deletion guard

Hard deletion of any PI, service centre, or service category is **blocked** whenever the record is referenced by at least one submission (in any status — draft, submitted, approved, etc.).

| Scenario | Behaviour |
|----------|-----------|
| Single delete — record **in use** | **Blocked.** The confirmation screen is never shown. The admin is redirected straight back to the list page with an error message stating how many submissions are affected and instructing them to use `is_active = False` instead. |
| Single delete — record **not in use** | Allowed. The normal Django confirmation page is shown and deletion proceeds only after the admin confirms. |
| Bulk "Delete selected" — **any** selected record in use | **Blocked entirely.** No records in the selection are deleted. A detailed error lists each blocked record and its submission count. |
| Bulk "Delete selected" — **all** selected records have no submissions | Allowed. All selected records are deleted. |

!!! warning "Use `is_active = False` to retire records, not Delete"
    Setting `is_active = False` hides the record from the submission form dropdown
    while preserving all existing submission links.  This is always the correct
    operation for records that are no longer in use.  Hard deletion is only
    appropriate for records that were added by mistake and have never been
    referenced by any submission.

The **Submissions** column in each list view shows how many submissions currently
reference the record.  For Service Categories and Service Centres the count is a
hyperlink that opens the submission changelist pre-filtered to that record, making
it easy to see exactly which submissions are affected before deciding whether to
deactivate or delete.

### Principal Investigators (PIs)

**Location:** Admin → Reference Data → Principal Investigators

- Add new PIs who are not yet listed.
- Set `is_active = False` to hide a PI from the form dropdown without removing them from existing submissions.
- The `is_associated_partner` flag should be `True` for exactly one entry (the generic "Associated partner" option).
- ORCID iDs are validated on save.
- The **Submissions** column shows the total number of submissions that list this PI as responsible (plain count — no hyperlink, as the submission list does not have a per-PI filter).

!!! info "PI institutes populate the affiliation combobox"
    The **Institute** field on each PI record feeds directly into the affiliation
    autocomplete shown to submitters in Section A of the registration form.
    Keeping PI institute names consistent and up-to-date here helps submitters
    find and reuse the correct spelling, reducing data inconsistencies.

### Service Centres

**Location:** Admin → Reference Data → Service Centers

- Each centre has a short name (e.g. "HD-HuB"), full name, and optional website.
- `is_active = False` hides from the form but keeps existing submission links intact.
- The **Submissions** column links to the filtered submission changelist for that centre.

### Service Categories

**Location:** Admin → Reference Data → Service Categories

- Add new category types as needed.
- `is_active = False` hides from the form.
- The **Submissions** column links to the filtered submission changelist for that category.

---

## Service Logos

Submitters can optionally upload a logo for their service during registration or when editing their submission. Logos are also uploadable directly from the admin detail view.

### Accepted formats and limits

| Property | Value |
|---|---|
| Formats | PNG, JPEG, SVG |
| Maximum size | 10 MB (configurable — see [`[uploads]` in configuration](configuration.md#uploads)) |
| Storage | `mediafiles/logos/<uuid>.<ext>` inside the container |
| Served at | `/media/logos/<uuid>.<ext>` — via Gunicorn (nginx proxy_passes everything) |

### Security processing

Every upload goes through automatic validation before being stored:

- **Magic bytes** — the file type is detected from its binary header, not its extension or MIME type
- **JPEG / PNG** — re-encoded via Pillow to strip EXIF metadata and verify file integrity
- **SVG** — parsed with Python's stdlib XML parser (safe on Python 3.12+/Expat 2.7.1, which blocks XXE and entity-expansion attacks), then scrubbed of `<script>` elements, `on*` event-handler attributes, and non-fragment external `href` values
- **Filename** — original filename is discarded; a UUID is assigned before storage

### Admin usage

Open a submission's detail view. The **B — Service Master Data** section shows:

- **Logo** — file upload widget to add or replace the current logo
- **Logo preview** — inline image display of the currently stored logo (or "—" if none)

!!! note "Old logos are retained"
    When a submitter or admin uploads a replacement logo, the previous file remains on disk. No automatic cleanup is performed. If disk space becomes a concern, orphaned logo files can be removed manually or via a future management command.

!!! info "Production persistence"
    Logo files are stored in the `media_data` Docker volume (mounted at `/app/mediafiles`).
    Without a persistent volume or bind mount, logos are lost when the container is replaced.
    See [Deployment → Uploaded Media](deployment.md#uploaded-media-service-logos) for volume
    configuration, bind-mount instructions, and backup procedures.

---

## Customising Form Text & Section Descriptions

The registration form text is controlled entirely from a single YAML file:
`apps/submissions/form_texts.yaml`

The file has two parts:

### Section descriptions

Each of the seven form sections (A–G) can show an introductory paragraph at the
top of its card. Edit the `sections` block:

```yaml
sections:
  a:
    description: ""   # leave empty to show no description
  b:
    description: "Provide accurate information about your service."
  # ... c through g
```

- Leave `description: ""` to hide the paragraph for that section.
- No raw HTML — use the supported syntax below instead.

#### Supported text formatting

**Named hyperlinks** — use `[link text](https://...)` to display a clickable word or phrase
instead of the full URL:

```yaml
  e:
    description: >-
      KPI requirements depend on your service category. See the
      [de.NBI KPI Compass](https://www.denbi.de/images/Service/20210624_KPI_Cheat_Sheet_doi.pdf)
      for guidance.
```

**Bare URLs** — plain `https://` links are also automatically converted to clickable links:

```yaml
  b:
    description: "For examples see https://www.denbi.de/services"
```

**Multiple paragraphs** — use a YAML [literal block scalar](https://yaml.org/spec/1.2/spec.html#id2795688)
(`|`) and leave a blank line between paragraphs:

```yaml
  f:
    description: |
      This section collects keywords and consent information.

      The information helps us improve visibility in search engines and outreach activities.
```

**Line breaks within a paragraph** — also use the `|` block style; each newline becomes a `<br>`:

```yaml
  c:
    description: |
      Please name the PI responsible for this service.
      Use the associated partner option if your PI is not listed.
```

!!! note "Folded (`>-`) vs literal (`|`) block scalars"
    The `>-` style collapses line breaks into spaces — useful for long single-paragraph
    descriptions that you want to wrap neatly in the file. Use `|` when you need actual
    line breaks or blank-line paragraph splits to appear in the rendered output.

For the full technical details of the rendering filter, see
[Custom template tags — `linkify_description`](development.md#linkify_description) in the
Developer Guide.

### Field help text and tooltips

Each field shows two types of guidance:

- **Help text** — a short hint shown below the input field.
- **Tooltip** — a detailed explanation shown when hovering or clicking the info icon.

```yaml
service_name:
  help: "Official name of the service."
  tooltip: "Use the canonical name as it appears on your website."
```

- Set `help: ""` to hide the help text for a field.
- Set `tooltip: ""` to hide the info icon for a field.

### Deploying changes

After editing `form_texts.yaml`, rebuild and redeploy:

```bash
docker compose build web
docker compose up -d web
```

No code changes, no migrations, no template edits required.

---

## Customising Email Notification Text

Email subject lines and status messages sent to submitters are controlled from a
single YAML file:

```
apps/submissions/email_texts.yaml
```

### Subjects

The `subjects` section defines the subject line for each email type.
Placeholders `{service_name}` and `{status}` are replaced automatically:

```yaml
subjects:
  created: "[de.NBI Registry] New service submission: {service_name}"
  status_changed: "[de.NBI Registry] Status updated to '{status}': {service_name}"
  updated: "[de.NBI Registry] Update: {service_name}"
  submitter_status: "Your service registration status: {status} — {service_name}"
```

### Status messages

The `status_messages` section provides the body text included in the submitter
notification when an admin changes the submission status.
A `default` fallback is used for any status not explicitly listed:

```yaml
status_messages:
  approved: "Your service has been approved and is now registered …"
  rejected: "Your service registration was not approved at this time …"
  under_review: "Your submission is currently under review …"
  default: "If you have questions about your submission, please contact us."
```

### Deploying changes

After editing `email_texts.yaml`, rebuild and redeploy — same as form text
changes:

```bash
docker compose build web
docker compose up -d web
```

No code changes, no migrations, no template edits required.

---

## EDAM Ontology Management {#edam-management}

**Location:** Admin → EDAM Ontology → EDAM Terms

EDAM terms are imported from the official EDAM ontology release and are read-only in the admin.
Terms cannot be added or deleted manually — all changes come through a sync.

### How seeding works

| Trigger | When | Notes |
|---|---|---|
| **Auto-seed on first migrate** | Once, on a fresh database | Fires automatically as a `post_migrate` signal when the `EdamTerm` table is empty. Downloads ~3 MB, takes ~30 s. |
| **Monthly beat schedule** | Every 30 days | Celery beat task `edam.sync` keeps terms current as EDAM publishes new releases. |
| **Admin "Sync EDAM" button** | On demand | Queues a background Celery task. Useful after a known EDAM release or if the automatic sync failed. |
| **CLI** | On demand | `python manage.py sync_edam` — synchronous, progress shown in terminal. |

### Viewing Terms

The list shows: accession (e.g. `topic_0121`), label, branch, obsolete flag, EDAM version.

Filter by **branch** to see only topics, operations, data types, formats, or identifiers.
Search by label or definition text.

### Checking the Loaded Version

The **EDAM version** column shows which release each term was last loaded from (e.g. `1.25`).
All terms should show the same version after a successful sync.

### Triggering a manual sync

**From the admin UI** (recommended — no shell access needed):

1. Go to **EDAM Ontology → EDAM Terms**
2. Click **↻ Sync EDAM from upstream** in the top-right toolbar
3. A green message confirms the task was queued
4. Refresh the page after ~30 seconds to see the updated term count and version

**From the CLI**:

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

### If the Form Shows No EDAM Terms

On a standard deployment this should not happen — the auto-seed fires on first migrate.
If the dropdowns are empty, check the term count:

```bash
docker compose exec web python manage.py shell -c \
  "from apps.edam.models import EdamTerm; print(EdamTerm.objects.count())"
# Expected: ~3400. If 0, the auto-seed failed (check migrate output for [edam] lines).
# Fix: docker compose exec web python manage.py sync_edam
```

---

## bio.tools Record Management {#biotools-records}

When a submitter enters a bio.tools URL, the system automatically fetches and stores a local
copy of the tool's bio.tools entry. This is displayed in the submission detail view and
exposed in the API.

### Viewing bio.tools Records

**Location:** Admin → bio.tools Integration → bio.tools Records

Each record shows:
- The linked submission
- The bio.tools ID and link to bio.tools
- Extracted metadata: name, description, license, tool types, maturity
- EDAM topic URIs sourced from bio.tools
- Last sync timestamp and any sync error

The **Functions** inline shows all EDAM Operation/Input/Output annotations from bio.tools,
structured as one row per function block.

### Sync Status

The list view shows a green ✓ or red ✗ for each record's last sync status.
A red ✗ means the last sync failed — check the **sync_error** field on the record.

Common sync errors:
- `bio.tools tool not found (HTTP 404)` — the bio.tools ID in the submission URL is wrong
- `bio.tools network error` — the server could not reach bio.tools (check firewall/proxy)
- `bio.tools API error (HTTP 5xx)` — bio.tools is temporarily unavailable; will retry automatically

### Manually Triggering a Sync

From the admin list, select records and choose **Sync selected records from bio.tools now**.
This queues a background Celery task; the record refreshes within a few seconds.

From the command line:

```bash
# Sync all records
docker compose exec web python manage.py sync_biotools

# Sync one specific submission
docker compose exec web python manage.py sync_biotools --submission <uuid>

# Dry-run
docker compose exec web python manage.py sync_biotools --dry-run
```

### Creating a bio.tools Record Manually

If a submission has a bio.tools URL but no record was created (e.g. bio.tools was unreachable
when the submission was saved), create it manually:

```bash
docker compose exec web python manage.py sync_biotools \
  --submission <submission-uuid> \
  --create
```

### Stale Draft Cleanup {#stale-drafts}

The `cleanup_stale_drafts` Celery beat task runs daily and removes Django session
records that expired more than **24 hours** ago.  This keeps the session table from
accumulating rows left behind by users who opened the form but never submitted.

The task does not delete `ServiceSubmission` records — only the underlying Django
`Session` rows that the browser draft auto-save feature writes to.

To trigger cleanup manually:

```bash
docker compose exec web python manage.py shell -c "
from apps.submissions.tasks import cleanup_stale_drafts
result = cleanup_stale_drafts()
print(result)
"
```

---

### Periodic Sync Schedule

All bio.tools records are refreshed automatically every 24 hours by a Celery beat task.
To verify the scheduler is running:

```bash
docker compose exec worker celery -A config inspect scheduled
# Should show the biotools.sync_all task
```

To change the schedule, edit `CELERY_BEAT_SCHEDULE` in `config/settings.py`:

```python
"sync-biotools-daily": {
    "task": "biotools.sync_all",
    "schedule": 86400,  # seconds — change to 43200 for twice-daily
},
```

---

## Issuing Admin API Tokens

For staff or external systems needing read-all API access:

1. Go to **Admin → Auth Token → Tokens → Add Token**.
2. Select the staff user and save.
3. The full token is displayed **once only** in a warning banner with a
   **Copy to clipboard** button — copy it before navigating away.
4. The consumer uses: `Authorization: Token <token-value>` in API requests.

To revoke: delete the token record from the admin.

!!! warning "Token visibility"
    For security, token keys are **masked** throughout the admin interface.
    The token list shows only the first 8 characters (e.g. `a3f7b2c1…`),
    and the change view never displays the full key. The complete token is
    shown exactly once — at the moment of creation. If lost, delete the
    token and create a new one.

---

## Email Notification Settings

Emails are sent asynchronously via Celery. Configure via environment variables in `.env`:

```bash
EMAIL_HOST=smtp.example.org
EMAIL_PORT=587
EMAIL_USE_TLS=true
EMAIL_FROM=no-reply@denbi.de
SUBMISSION_NOTIFY_CC=admin@denbi.de          # Optional CC on every notification
SUBMISSION_NOTIFY_OVERRIDE=                  # Override recipient for testing
```

Events that trigger emails:

| Event | Recipient(s) | Template |
|---|---|---|
| New submission created | Admin + `SUBMISSION_NOTIFY_CC` (CC: `internal_contact_email`) | `notification.html` |
| Submitter edits via update form | Admin + `SUBMISSION_NOTIFY_CC` | `notification.html` |
| Status changed by admin | Admin + `SUBMISSION_NOTIFY_CC` **and** `internal_contact_email` (separate submitter email) | `notification.html` + `status_update_submitter.html` |

The submitter email on status change is suppressed when `SUBMISSION_NOTIFY_OVERRIDE` is set (e.g. in staging/testing), so test environments do not accidentally send submitter-facing emails.

---

## Monitoring

### Health Checks

- `GET /health/live/` — 200 if the process is running (no DB check)
- `GET /health/ready/` — 200 only if DB and Redis are reachable; 503 otherwise

### Logs

Logs are structured JSON on stdout (captured by Docker). Key fields:
`timestamp`, `levelname`, `name`, `message`, `request_id`.

View live logs:
```bash
make logs
# or
docker compose logs -f web
docker compose logs -f worker
```

### Celery / Task Queue

Check task queue health:
```bash
docker compose exec worker celery -A config inspect active
docker compose exec worker celery -A config inspect stats

# Check scheduled tasks (should include cleanup-stale-drafts, sync-biotools-daily, sync-edam-monthly)
docker compose exec worker celery -A config inspect scheduled

# Ping the worker directly (same check used by the Docker healthcheck)
docker compose exec worker celery -A config inspect ping
```

The `worker` container reports a Docker health status based on `celery inspect ping`. The `beat` container has no inspection API so its healthcheck is disabled — liveness is inferred from the process staying up.

---

## ALTCHA CAPTCHA

The registration and edit forms are protected by [ALTCHA](https://altcha.org/) — a
self-hosted, privacy-respecting proof-of-work CAPTCHA.  The browser widget is served
from `static/js/altcha.min.js` (no CDN, no third-party requests) and the challenge
endpoint is `GET /captcha/`.

### How it works

1. When the form loads, the widget automatically fetches a signed challenge from `/captcha/`.
2. The browser solves a small SHA-256 proof-of-work puzzle (invisible to the user).
3. On submit, the solved payload is included in the form POST as the `altcha` field.
4. The server verifies the signature and expiry before processing the form.

No user interaction is required — ALTCHA runs silently in the background.

### Setting the HMAC key

`ALTCHA_HMAC_KEY` in `.env` is the secret used to sign and verify challenges.

Generate a strong key:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Then set it in `.env`:

```bash
ALTCHA_HMAC_KEY=<your-generated-key>
```

Restart the web service to apply: `docker compose restart web`

!!! warning "Required in production"
    When `ALTCHA_HMAC_KEY` is empty, ALTCHA verification is **bypassed entirely** — safe
    for local development but must be configured before deploying publicly.

### Rotating the ALTCHA HMAC key

1. Generate a new key: `python -c "import secrets; print(secrets.token_hex(32))"`
2. Update `ALTCHA_HMAC_KEY` in `.env`.
3. Restart the web service: `docker compose restart web`

Any challenges signed with the old key will immediately become invalid.  Users who
opened the form before the rotation will see a CAPTCHA failure on submit — they need
to reload the page to fetch a new challenge signed with the new key.

---

## Rotating the SECRET_KEY

1. Generate a new key: `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`
2. Update `SECRET_KEY` in `.env` (or your deployment environment / CI secret store).
3. Restart the web and worker services: `docker compose restart web worker`
4. All existing sessions will be invalidated (users will need to log in again).

## Rotating Database Password

1. Update the PostgreSQL password: `docker compose exec db psql -U denbi -c "ALTER USER denbi PASSWORD 'new-password';"`
2. Update `DB_PASSWORD` in `.env` to the new password.
3. Restart services: `docker compose restart web worker beat`
