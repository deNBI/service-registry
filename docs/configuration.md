---
icon: material/tune
---

# Configuration Reference

Configuration is split into two files with a clear separation of concerns:

| File               | What goes here                                 | Restart needed?                                |
| ------------------ | ---------------------------------------------- | ---------------------------------------------- |
| `config/site.toml` | Branding, contact info, URLs, feature flags    | Yes (`docker compose restart web worker beat`) |
| `.env`             | Secrets, passwords, connection strings, tuning | Yes (full restart)                             |

### Configuration precedence

Environment variables always take precedence over settings in `config/site.toml`. This allows you to:

- Keep sensitive values in `.env` (never commit it)
- Use different configurations per environment (dev/staging/prod) by setting env vars
- Override site.toml values without editing the file

For example:

- `EDAM_OWL_URL` in `.env` overrides `[edam] owl_url` in site.toml
- `SPDX_LICENSES_URL` in `.env` overrides `[licenses] url` in site.toml
- `ADMIN_URL_PREFIX` in `.env` overrides `[admin] url_prefix` in site.toml
- `EMAIL_FROM` in `.env` overrides `[email] from_address` in site.toml

Only these settings have an environment override; everything else in `site.toml` is read from the file.

---

## Site settings

`config/site.toml` — the single place for all non-secret, human-editable settings.
Editing this file and restarting the containers is all that is needed to rebrand
the registry for a different organisation.

### Core identity

```toml
[site]
name         = "de.NBI Service Registration Platform"
tagline      = "de.NBI & ELIXIR-DE Service Registration Platform"
url          = "https://service-registry.bi.denbi.de"
logo_url     = ""
favicon_url  = ""
```

| Key           | Description                                                                                                                                                                                        |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`        | Site name shown in the navbar, page titles, and email subjects                                                                                                                                     |
| `tagline`     | Browser tab subtitle and meta description                                                                                                                                                          |
| `url`         | Canonical public URL — used in outbound emails and the bio.tools API User-Agent                                                                                                                    |
| `logo_url`    | Logo image URL. Three options: an absolute URL (`https://…`), a local static path (`/static/img/logo.svg`), or empty string (auto-detects `static/img/logo.*`, then falls back to a CSS text logo) |
| `favicon_url` | Favicon URL. Same three options as `logo_url`. Empty string auto-detects `static/img/favicon.ico` / `.png` / `.svg`. If nothing is found, no `<link rel="icon">` is rendered                       |

### Contact details

```toml
[contact]
email        = "servicecoordination@denbi.de"
office       = "Forschungszentrum Jülich GmbH - IBG-5, c/o Bielefeld University"
organisation = "German Network for Bioinformatics Infrastructure"
```

`contact.email` appears in the form sidebar, update page, success page, email footers, OpenAPI metadata, and the site footer.

### Sender identity

```toml
[email]
from_address   = "no-reply@denbi.de"
subject_prefix = "[de.NBI Registry]"
```

`from_address` is overridden by the `EMAIL_FROM` environment variable if set. SMTP credentials stay in `.env`.

`subject_prefix` is currently not used: the subject lines, including their prefix, are set in `apps/submissions/email_texts.yaml` (see [Customising Texts](operator/customising-texts.md#email-notification-texts)).

### External URLs

```toml
[links]
website         = "https://www.denbi.de"
privacy_policy  = "https://www.denbi.de/privacy-policy"
imprint         = "https://www.denbi.de/imprint"
data_protection = "https://www.denbi.de/privacy-policy"
kpi_cheatsheet  = "https://www.denbi.de/images/Service/20210624_KPI_Cheat_Sheet_doi.pdf"
user_guide      = "https://denbi.github.io/service-registry/user-guide/"
repository      = "https://github.com/deNBI/service-registry"
```

All links are rendered dynamically — changing a URL here updates it everywhere in the UI without touching template files.

| Key               | Description                                 | Default                                                |
| ----------------- | ------------------------------------------- | ------------------------------------------------------ |
| `website`         | Main organisation website                   | `https://www.denbi.de`                                 |
| `privacy_policy`  | Privacy policy page                         | `https://www.denbi.de/privacy-policy`                  |
| `imprint`         | Legal imprint page                          | `https://www.denbi.de/imprint`                         |
| `data_protection` | Data protection information                 | `https://www.denbi.de/privacy-policy`                  |
| `kpi_cheatsheet`  | KPI cheat-sheet PDF                         | PDF URL                                                |
| `user_guide`      | User documentation page (appears in navbar) | `https://denbi.github.io/service-registry/user-guide/` |
| `repository`      | Public source-code repo — shown as a "GitHub" link in the footer; set `""` to hide it | `https://github.com/deNBI/service-registry` |

### OpenAPI metadata

```toml
[api]
title        = "de.NBI Service Registration Platform API"
version      = "1.0.0"
license_name = "MIT"
```

### Feature flags

```toml
[features]
biotools_prefill  = true   # Show bio.tools prefill banner on the form
edam_annotations  = true   # Currently has no effect (see below)
catalogue         = true   # Public Registry Catalogue at /catalogue/ (false if the key is absent)
markdown_descriptions = false  # Render service descriptions as Markdown
```

`edam_annotations` is not read by the application at the moment: the EDAM fields are always shown, whatever its value.

`markdown_descriptions` (default `false`) renders `service_description` as sanitized Markdown in the catalogue, the admin and the API `service_description_html` field, and turns the description field on the forms and in the admin into the **Write** | **Preview** editor. With the flag off every surface shows the plain, autoescaped text. In both flag states every display surface (catalogue, API `service_description_html`, notification emails) decodes HTML entities stored by the old escaping web form exactly once, so a legacy `x &gt; 5` reads `x > 5` (see [Using the API](api-guide.md#retrieve-your-submission)). Run `python manage.py audit_markdown_descriptions` before enabling it in production (see [Markdown descriptions rollout](rollout.md#markdown-descriptions-rollout)). Like all `site.toml` changes, it takes effect after `docker compose restart web worker beat`.

### Registry Catalogue

```toml
[catalogue]
card_fields      = ["categories", "service_center", "edam_topics", "updated_at", "maturity_tag"]
per_page         = 12
meta_description = "Browse all approved de.NBI & ELIXIR-DE bioinformatics services."
```

The catalogue is a public, read-only page at `/catalogue/` that lets anonymous users browse, search, filter, sort, and group all approved services. It is on in the shipped `site.toml` (`[features] catalogue = true`) and off when the key is absent.

| Key                | Default                                                                         | Description                                                                                                                                                          |
| ------------------ | ------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `card_fields`      | `["categories", "service_center", "edam_topics", "updated_at", "maturity_tag"]` | Fields shown on each service card. Remove a key to hide that field. Available: `"categories"`, `"service_center"`, `"edam_topics"`, `"maturity_tag"`, `"updated_at"` |
| `per_page`         | `12`                                                                            | Number of service cards per page (server-side pagination).                                                                                                           |
| `meta_description` | `"Browse all approved…"`                                                        | Content of the catalogue page `<meta name="description">` and OpenGraph description tags.                                                                            |

After enabling, the "Registry Catalogue" link appears automatically in the navbar. Disabling the flag hides the link and makes the route return 404.

### EDAM ontology sync

```toml
[edam]
owl_url = "https://edamontology.org/EDAM_stable.owl"
```

Overridden by `EDAM_OWL_URL` in `.env`. Set to a local file path for air-gapped servers.

### SPDX licenses sync

```toml
[licenses]
url = "https://raw.githubusercontent.com/spdx/license-list-data/main/json/licenses.json"
```

Overridden by `SPDX_LICENSES_URL` in `.env`. Set to a local file path for air-gapped servers.

### Admin interface

```toml
[admin]
url_prefix = "admin-denbi"
```

Overridden by `ADMIN_URL_PREFIX` in `.env`. Changes the URL of the Django admin interface (`/<prefix>/`). Obfuscates the admin URL to reduce automated scanning noise — not a security boundary on its own.

### `[uploads]`

```toml
[uploads]
logo_max_bytes = 10_485_760
logo_max_pixels = 25_000_000
logo_max_svg_bytes = 1_048_576
```

| Key              | Default            | Description                                                                                                                        |
| ---------------- | ------------------ | ---------------------------------------------------------------------------------------------------------------------------------- |
| `logo_max_bytes` | `10485760` (10 MB) | Maximum allowed size in bytes for service logo uploads. Reduce to tighten limits. Requires a web container restart after changing. |
| `logo_max_pixels` | `25000000` (25 MP) | Maximum PNG/JPEG logo size in pixels (width × height), checked before the image is decoded. Requires a web container restart after changing. |
| `logo_max_svg_bytes` | `1048576` (1 MB) | Maximum allowed size in bytes for SVG logo uploads (also bound by `logo_max_bytes`). SVGs are parsed and checked element by element, so processing time grows with their size; this keeps it short. Checked before the file is parsed. Requires a web container restart after changing. |

The limits are shown to users next to the logo field and in the API schema. The
registration form text can embed them with the `{logo_limits}` placeholder in
`apps/submissions/form_texts.yaml`.

### `[submission]` — Submission lifecycle

Controls how the platform handles status resets when a submitter edits an already-approved service, and how long the browser-side form draft is retained.

```toml
[submission]
# Illustrative subset — the shipped config/site.toml enables a broader default
# set (external links, EDAM annotations, keywords, publications, contact fields,
# KPI fields, comments). See that file for the authoritative default list.
no_reset_fields = ["logo", "github_url", "biotools_url", "fairsharing_url",
                   "edam_topics", "edam_operations"]
draft_ttl_days = 7
```

!!! note "The service name is never editable via a reset-exemption"
    `service_name` is immutable after the initial submission on both the edit form and the REST API — independent of `no_reset_fields`. It cannot be made editable through this setting; only a Django admin can change a submitted name.

#### `no_reset_fields`

Fields listed here do **not** trigger a status reset to `submitted` when a submitter edits an already-approved service. Use this for supplementary or reference fields (logos, external URLs, ontology annotations) that do not change the core service description and should not force a full re-review cycle.

| Configuration | Behaviour |
|---|---|
| Empty list `[]` | Any edit of an approved service resets status to `submitted` (legacy default). |
| Non-empty list | Status resets only when at least one **non-exempt** field changes. Editing only exempt fields preserves `approved`. |

Rules and constraints:

- Email notifications are always sent, regardless of this setting.
- When status **is** reset, the submitter update email includes a lifecycle notice.
- System-controlled fields (`status`, `date_of_entry`, `primary_maturity_tag`, `secondary_maturity_tags`, `register_as_elixir`) cannot be made exempt and are silently rejected with a startup warning if included.
- Unknown field names are also logged at startup and ignored.
- The same exemption is applied consistently to both the submitter web form (`/update/edit/<submission_id>/`) and the REST API (`PATCH /api/v1/submissions/{id}/`).
- Validation runs at startup via `SubmissionsConfig.ready()`. Misconfigured entries appear in the server log immediately, not silently at request time.

Available field names (use the model-level name, not the form or serializer alias):

```
Scalar:  submitter_first_name  submitter_last_name  submitter_affiliation
         service_name          service_description  year_established
         is_toolbox            toolbox_name         user_knowledge_required
         publications_pmids    logo                 associated_partner_note
         host_institute        public_contact_email internal_contact_name
         internal_contact_email  website_url        terms_of_use_url
         license_note          github_url           biotools_url
         fairsharing_url       other_registry_url   kpi_monitoring
         kpi_start_year        keywords_uncited     keywords_seo
         survey_participation  comments

M2M:     service_categories    responsible_pis      edam_topics
         edam_operations       licenses
```

#### `draft_ttl_days`

Number of days after which an **abandoned** browser-side form draft is automatically discarded. The draft is removed from `localStorage` on the next form load once the TTL has elapsed. Successfully submitting the form always clears the draft immediately, regardless of this setting.

Default: `7`. Minimum meaningful value: `1`.

After changing either setting, restart the containers:

```bash
docker compose restart web worker beat
```

---

## Secrets and connection strings

`.env` — copy `.env.example` to `.env` and fill in the required values. Every variable is documented below with its default.

Any variable can also be read from a file: if `<NAME>_FILE` is set (for example `SECRET_KEY_FILE=/run/secrets/secret_key`), the value is read from that file instead (Docker secrets).

### Required — startup fails without these

```bash
SECRET_KEY=<generate: python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())">
DB_PASSWORD=<strong-password>
REDIS_PASSWORD=<strong-password>
```

### Django core

```bash
DEBUG=false
ALLOWED_HOSTS=service-registry.bi.denbi.de,www.service-registry.bi.denbi.de
TIME_ZONE=Europe/Berlin
```

### Database (PostgreSQL)

```bash
DB_NAME=denbi_registry
DB_USER=denbi
DB_HOST=db          # Docker Compose service name in dev; real hostname in prod
DB_PORT=5432
```

### Redis

```bash
REDIS_HOST=redis    # Docker Compose service name in dev; real hostname in prod
REDIS_PORT=6379
```

### Reverse proxy / real IP

```bash
# IP(s) of the direct upstream that connects to Gunicorn — i.e. the machine
# whose TCP connection Gunicorn sees. Gunicorn rewrites REMOTE_ADDR (and its
# own access log) from X-Forwarded-For only when the connection arrives from a
# trusted address listed here. Comma-separated; CIDR ranges are accepted.
#
# Same-machine proxy → Gunicorn:          FORWARDED_ALLOW_IPS=127.0.0.1
# Docker bridge (proxy on host):          FORWARDED_ALLOW_IPS=172.17.0.0/16
# Remote proxy server (internal IP):      FORWARDED_ALLOW_IPS=192.168.x.x
FORWARDED_ALLOW_IPS=127.0.0.1
```

!!! note "Two separate concerns: Gunicorn log vs. application IP"
    **`FORWARDED_ALLOW_IPS`** only affects Gunicorn's own access log (stdout).
    It has no effect on the IP that Django views or django-axes record.

    **Application-level IP** (axes lockout log, `submission_ip` field, **and
    per-client rate limiting**) is resolved from the `X-Real-IP` header, which the
    upstream proxy sets to `$remote_addr` — the real client IP. django-axes reads
    it via `django-ipware`; `submission_ip` and django-ratelimit read it via
    `get_client_ip` (`apps/submissions/http_utils.py`; the limiter uses its wrapper `get_ratelimit_ip`, wired up as
    `RATELIMIT_IP_META_KEY`), which tries `X-Real-IP` → `X-Forwarded-For` →
    `REMOTE_ADDR` and uses the first syntactically valid address (a malformed header falls through to the next source; with none valid, `submission_ip` stays empty and the limiter uses one shared placeholder bucket). This path is independent of `FORWARDED_ALLOW_IPS`.

    For the application IP to be correct, two things must be true:

    1. The upstream proxy sets `proxy_set_header X-Real-IP $remote_addr` (already
       in `nginx/host/service-registry.bi.denbi.de.conf`).
    2. `django-ipware` is installed (listed in `requirements/base.txt`).

    If `X-Real-IP` is missing, every request resolves to the proxy's own IP, so
    django-axes and django-ratelimit collapse to a single shared bucket and one
    client can exhaust everyone's budget. See [Rate limiting](#rate-limiting).

### Security

```bash
HSTS_SECONDS=31536000           # HSTS max-age in seconds (default: 1 year)
SECURE_SSL_REDIRECT=true        # Force HTTP → HTTPS at Django layer
SESSION_COOKIE_SECURE=true      # Mark session cookie as HTTPS-only
SESSION_COOKIE_AGE=3600         # Session lifetime in seconds (default: 1 hour)
SESSION_ENGINE=django.contrib.sessions.backends.db  # Session store (default: DB)
CSRF_COOKIE_SECURE=true         # Mark CSRF cookie as HTTPS-only
```

`SESSION_ENGINE` chooses **where session data is stored**. It must be a
**server-side** backend, because the one-time plaintext API key is kept in the
session. The `signed_cookies` backend is rejected at startup — it would put the
session (and therefore the key) into a client-readable cookie (signed, but
**not** encrypted). Leave it at the default unless you have a specific reason to
change it.

| Value (`SESSION_ENGINE=…`)                          | What it does                                                        | When to use it                                                                                 |
| --------------------------------------------------- | ------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------- |
| `django.contrib.sessions.backends.db` *(default)*   | Stores sessions in the database.                                   | The safe default. Use this unless you have a reason not to.                                     |
| `django.contrib.sessions.backends.cached_db`        | Reads from cache, falls back to the DB; writes to both.            | High traffic: faster reads while the DB stays the durable source of truth.                      |
| `django.contrib.sessions.backends.cache`            | Stores sessions **only** in the cache (e.g. Redis).               | Fastest, but sessions vanish if the cache is cleared/restarted — only with a persistent cache. |
| `django.contrib.sessions.backends.file`             | Stores each session as a file on disk.                            | Single-server setups without a shared DB/cache; not suitable across multiple app servers.       |
| `django.contrib.sessions.backends.signed_cookies`   | Stores the whole session in the browser cookie. **Rejected here.** | Never — the app refuses to start with it (would expose the plaintext API key).                  |

Entering an API key on `/update/` unlocks the web edit form for the rest of the
browsing session. That unlock is bounded by the session: `SESSION_COOKIE_AGE`
caps it (1 hour by default), and `SESSION_EXPIRE_AT_BROWSER_CLOSE = True`
(set in `settings.py`) ends it when the browser is closed. Lower
`SESSION_COOKIE_AGE` if you want a tighter window on shared machines.

### Admin brute-force protection

```bash
AXES_FAILURE_LIMIT=5                    # Failed login attempts before lockout
AXES_COOLOFF_MINUTES=30                 # Lockout duration in minutes
```

Lockouts count per IP address and username. Two related settings are fixed in `config/settings.py`, not read from `.env`: failed attempts are kept after a successful login (`AXES_RESET_ON_SUCCESS = False`), and every failed login is recorded in `axes_accessfailurelog` (`AXES_ENABLE_ACCESS_FAILURE_LOG = True`).

### Rate limiting

Format: `<count>/<period>` where period is `s` / `m` / `h` / `d`.

```bash
RATE_LIMIT_SUBMIT=10/h          # Registration form submissions (POST /register/)
RATE_LIMIT_UPDATE=20/h          # Key-entry and edit form submissions (POST /update/ and /update/edit/<submission_id>/)
RATE_LIMIT_API=60/m             # REST API requests with an API key (per key, or per submission for ApiKey)
RATE_LIMIT_CHALLENGE=60/h       # ALTCHA challenge generation (GET /captcha/)
RATE_LIMIT_BIOTOOLS=60/h        # bio.tools prefill/search proxy (GET /biotools/*)
RATE_LIMIT_VALIDATE=120/h       # Inline field validation (POST /register/validate/)
RATE_LIMIT_PREVIEW=120/h        # Markdown description preview (POST /markdown-preview/); per signed-in user, else per IP
```

REST API requests **without** a key (registering via the API, EDAM lookups) are limited to 10 per minute per client. This value is fixed in `config/settings.py`. The REST API answers `429` when a limit is exceeded; the web form endpoints answer `403`, except the description preview, which shows a "Too many previews" message instead.

!!! warning "Limits are bucketed per real client IP"
    The django-ratelimit limits (all of the above except `RATE_LIMIT_API`, which DRF applies per authenticated user or API key, and `RATE_LIMIT_PREVIEW` for signed-in users) are keyed on the real client IP, not `REMOTE_ADDR`
    (`RATELIMIT_IP_META_KEY = apps.submissions.http_utils.get_ratelimit_ip`, which
    reads the first valid address from `X-Real-IP` → `X-Forwarded-For` → `REMOTE_ADDR`). Behind a reverse
    proxy the proxy **must** set `X-Real-IP` to the client address (see the
    `X-Real-IP` note above); otherwise all users share one global bucket and the
    limits become effectively site-wide.

### ALTCHA CAPTCHA

ALTCHA is a self-hosted, privacy-respecting proof-of-work CAPTCHA that protects
the registration and edit forms from automated submissions. No external service
is contacted at runtime — the JS widget is vendored in `static/js/altcha.min.js`
and challenge generation/verification happens entirely inside Django.

```bash
ALTCHA_HMAC_KEY=                # Secret key for signing challenges (required in production)
```

| Variable          | Default | Description                                                                                                           |
| ----------------- | ------- | --------------------------------------------------------------------------------------------------------------------- |
| `ALTCHA_HMAC_KEY` | `""`    | HMAC-SHA256 key used to sign and verify challenges. When empty, ALTCHA is bypassed — safe for local development only. |

Generate a strong key with:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

> **Production requirement:** `ALTCHA_HMAC_KEY` must be set to a non-empty secret before deploying publicly.
> Leaving it empty disables CAPTCHA protection entirely.

**How it works at runtime:**

- The form shows the ALTCHA checkbox; when the user ticks it, the browser fetches a fresh challenge from `GET /captcha/`. When the key is empty, the widget is not shown.
- Challenges are signed with `ALTCHA_HMAC_KEY`, expire after **10 minutes**, and carry `Cache-Control: no-store` so proxies cannot cache and re-serve them.
- The widget performs a SHA-256 proof-of-work in a Web Worker (search space up to 100 000) then posts the solution with the form.
- Django verifies the HMAC signature and expiry before accepting the submission. An expired or tampered challenge is rejected with HTTP 400.
- The widget and all verification logic run on your own server — no external service is contacted.

Key rotation is described in [Security Operations](operator/security.md#rotating-the-altcha-hmac-key).

### Email (SMTP credentials)

```bash
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.example.org
EMAIL_PORT=587
EMAIL_USE_TLS=true
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
EMAIL_FROM=no-reply@denbi.de    # Overrides [email] from_address in site.toml

# Optional CC address on every admin notification email (never added to submitter emails):
# SUBMISSION_NOTIFY_CC=admin@denbi.de

# Override ALL notification recipients for testing (sends every email here instead).
# When set, submitter-facing emails (status update, edit confirmation) are suppressed
# to prevent accidentally emailing real submitters from staging/test environments.
# SUBMISSION_NOTIFY_OVERRIDE=test-inbox@denbi.de
```

Who receives which e-mail, and how `SUBMISSION_NOTIFY_OVERRIDE` behaves, is described in [E-mail and Notifications](operator/email.md). Subject lines and status messages are edited in `apps/submissions/email_texts.yaml` ([Customising Texts](operator/customising-texts.md#email-notification-texts)).

### REST API

```bash
API_PAGE_SIZE=20                # Page size of the paginated lists (GET /api/v1/submissions/, /api/v1/biotools/)
API_MAX_PAGE_SIZE=100           # Currently has no effect (clients cannot choose a page size)

# Comma-separated allowed origins for cross-origin API requests.
# Leave empty to disallow all cross-origin requests (safe default).
CORS_ALLOWED_ORIGINS=
CORS_ALLOW_CREDENTIALS=false
```

### API key security

```bash
API_KEY_ENTROPY_BYTES=48        # Entropy bytes → 64-char URL-safe token
API_KEY_HASH_ALGORITHM=sha256   # Hash algorithm for stored key hashes
```

Both apply to submission API keys only; Admin API keys always use 48 bytes and SHA-256. Leave the algorithm at `sha256`: the stored hash column holds 64 characters, and existing keys stop working if the algorithm changes.

### EDAM ontology

```bash
# Overrides [edam] owl_url in site.toml.
# Pin a specific release: https://edamontology.org/EDAM_1.25.owl
# Air-gapped servers:     /app/EDAM.owl
EDAM_OWL_URL=https://edamontology.org/EDAM_stable.owl
```

### SPDX licenses

```bash
# Overrides [licenses] url in site.toml.
# Default is the canonical SPDX GitHub raw URL.
# Air-gapped servers: /app/licenses.json
SPDX_LICENSES_URL=https://raw.githubusercontent.com/spdx/license-list-data/main/json/licenses.json
```

### Admin URL override

Can also be set in `site.toml` (`[admin] url_prefix`); the environment variable takes precedence.

```bash
# ADMIN_URL_PREFIX=admin-denbi
```

`.env.example` also lists `LOGO_URL`, but nothing reads it; set the logo with `[site] logo_url` in `site.toml`.

### Error tracking (Sentry)

```bash
SENTRY_DSN=                     # Leave empty to disable
SENTRY_TRACES_SAMPLE_RATE=0.1   # Fraction of transactions sent (0–1)
```

---

## Using site.toml values in custom templates

All values from `site.toml` are injected into every Django template via the
`site_context` context processor:

```django
{# Top-level shortcuts #}
{{ SITE_NAME }}
{{ SITE_URL }}
{{ CONTACT_EMAIL }}
{{ CONTACT_OFFICE }}
{{ CONTACT_ORG }}
{{ PRIVACY_POLICY_URL }}
{{ IMPRINT_URL }}
{{ WEBSITE_URL }}
{{ USER_GUIDE_URL }}
{{ REPOSITORY_URL }}
{{ LOGO_URL }}
{{ FAVICON_URL }}
{{ FORM_DRAFT_TTL_DAYS }}

{# Full SITE dict — mirrors site.toml sections #}
{{ SITE.contact.email }}
{{ SITE.links.kpi_cheatsheet }}
{{ SITE.features.biotools_prefill }}
{{ SITE.features.catalogue }}

{# Catalogue-specific variables (from apps/catalogue/context_processors.py) #}
{{ CATALOGUE_CARD_FIELDS }}
{{ CATALOGUE_PER_PAGE }}
{{ CATALOGUE_ENABLED }}
{{ CATALOGUE_META_DESCRIPTION }}
{{ SITE.email.subject_prefix }}
```

---

## Deploying for a different organisation

To white-label this registry for another institution, only `config/site.toml`
needs to be edited:

1. Update `[site]`, `[contact]`, and `[links]` sections
2. Set the logo: either an absolute URL in `logo_url`, or place a file at `static/img/logo.svg` (or `.png`, `.jpg`) and set `logo_url = "/static/img/logo.svg"` (an empty `logo_url` auto-detects `static/img/logo.*`). The logo is shown in the navbar next to the site name; about 38 px high works well.
3. Place your favicon at `static/img/favicon.ico` (auto-detected, no config needed)
4. Restart: `docker compose restart web worker beat`

No Python and no template edits are needed. Files added under `static/` are collected when the image is built (`collectstatic` runs in the Docker build), so after adding a logo or favicon file rebuild the image; in the development stack `make collectstatic` is enough. Changing only `site.toml` needs a restart, not a rebuild.
