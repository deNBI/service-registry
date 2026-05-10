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
- `ADMIN_URL_PREFIX` in `.env` overrides `[admin] url_prefix` in site.toml
- `LOGO_URL` in `.env` overrides `[site] logo_url` in site.toml

---

## Site settings

`config/site.toml` — the single place for all non-secret, human-editable settings.
Editing this file and restarting the containers is all that is needed to rebrand
the registry for a different organisation.

### Core identity

```toml
[site]
name         = "de.NBI Service Registry"
tagline      = "de.NBI & ELIXIR-DE Service Registration System"
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

### External URLs

```toml
[links]
website         = "https://www.denbi.de"
privacy_policy  = "https://www.denbi.de/privacy-policy"
imprint         = "https://www.denbi.de/imprint"
data_protection = "https://www.denbi.de/privacy-policy"
kpi_cheatsheet  = "https://www.denbi.de/images/Service/20210624_KPI_Cheat_Sheet_doi.pdf"
user_guide      = "https://denbi.github.io/service-registry/user-guide/"
```

All links are rendered dynamically — changing a URL here updates it everywhere in the UI without touching template files.

| Key | Description | Default |
|-----|-------------|---------|
| `website` | Main organisation website | `https://www.denbi.de` |
| `privacy_policy` | Privacy policy page | `https://www.denbi.de/privacy-policy` |
| `imprint` | Legal imprint page | `https://www.denbi.de/imprint` |
| `data_protection` | Data protection information | `https://www.denbi.de/privacy-policy` |
| `kpi_cheatsheet` | KPI cheat-sheet PDF | PDF URL |
| `user_guide` | User documentation page (appears in navbar) | `https://denbi.github.io/service-registry/user-guide/` |

### OpenAPI metadata

```toml
[api]
title        = "de.NBI Service Registry API"
version      = "1.0.0"
license_name = "MIT"
```

### Feature flags

```toml
[features]
biotools_prefill  = true   # Show bio.tools prefill banner on the form
edam_annotations  = true   # Show EDAM ontology fields on the form
catalogue         = false  # Enable the public Service Catalogue at /catalogue/
```

### Service Catalogue

```toml
[catalogue]
card_fields      = ["categories", "service_center", "edam_topics", "updated_at", "maturity_tag"]
per_page         = 12
meta_description = "Browse all approved de.NBI & ELIXIR-DE bioinformatics services."
```

The catalogue is a public, read-only page at `/catalogue/` that lets anonymous users browse, search, filter, sort, and group all approved services. It is disabled by default — set `[features] catalogue = true` to enable it.

| Key | Default | Description |
|-----|---------|-------------|
| `card_fields` | `["categories", "service_center", "edam_topics", "updated_at", "maturity_tag"]` | Fields shown on each service card. Remove a key to hide that field. Available: `"categories"`, `"service_center"`, `"edam_topics"`, `"maturity_tag"`, `"updated_at"` |
| `per_page` | `12` | Number of service cards per page (server-side pagination). |
| `meta_description` | `"Browse all approved…"` | Content of the catalogue page `<meta name="description">` and OpenGraph description tags. |

After enabling, the "Service Catalogue" link appears automatically in the navbar. Disabling the flag hides the link and makes the route return 404.

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
```

| Key              | Default            | Description                                                                                                                        |
| ---------------- | ------------------ | ---------------------------------------------------------------------------------------------------------------------------------- |
| `logo_max_bytes` | `10485760` (10 MB) | Maximum allowed size in bytes for service logo uploads. Reduce to tighten limits. Requires a web container restart after changing. |

---

## Secrets and connection strings

`.env` — copy `.env.example` to `.env` and fill in the required values. Every variable is documented below with its default.

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

    **Application-level IP** (axes lockout log, `submission_ip` field) is resolved
    by `django-ipware` reading the `X-Real-IP` header, which the upstream proxy sets
    to `$remote_addr` — the real client IP.  This path is independent of
    `FORWARDED_ALLOW_IPS`.

    For the application IP to be correct, two things must be true:

    1. The upstream proxy sets `proxy_set_header X-Real-IP $remote_addr` (already
       in `nginx/host/service-registry.bi.denbi.de.conf`).
    2. `django-ipware` is installed (listed in `requirements/base.txt`).

### Security

```bash
HSTS_SECONDS=31536000           # HSTS max-age in seconds (default: 1 year)
SECURE_SSL_REDIRECT=true        # Force HTTP → HTTPS at Django layer
SESSION_COOKIE_SECURE=true      # Mark session cookie as HTTPS-only
SESSION_COOKIE_AGE=3600         # Session lifetime in seconds (default: 1 hour)
CSRF_COOKIE_SECURE=true         # Mark CSRF cookie as HTTPS-only
```

### Admin brute-force protection

```bash
AXES_FAILURE_LIMIT=5                    # Failed login attempts before lockout
AXES_COOLOFF_MINUTES=30                 # Lockout duration in minutes
AXES_RESET_ON_SUCCESS=false             # Keep access attempts after successful login (for audit)
AXES_ENABLE_ACCESS_FAILURE_LOG=true     # Record every failed login event
```

- `AXES_RESET_ON_SUCCESS=false` ensures the `axes_accessattempt` table retains failure state until timeout/cleanup.
- `AXES_ENABLE_ACCESS_FAILURE_LOG=true` ensures a full event trail in `axes_accessfailurelog`.

### Rate limiting

Format: `<count>/<period>` where period is `s` / `m` / `h` / `d`.

```bash
RATE_LIMIT_SUBMIT=10/h          # Registration form submissions (POST /register/)
RATE_LIMIT_UPDATE=20/h          # Key-entry and edit form submissions (POST /update/ and /update/edit/)
RATE_LIMIT_API=60/m             # REST API (authenticated users)
RATE_LIMIT_CHALLENGE=60/h       # ALTCHA challenge generation (GET /captcha/)
RATE_LIMIT_BIOTOOLS=60/h        # bio.tools prefill/search proxy (GET /biotools/*)
RATE_LIMIT_VALIDATE=120/h       # Inline field validation (POST /register/validate/)
```

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

- The browser widget fetches a fresh challenge from `GET /captcha/` when the form is submitted.
- Challenges are signed with `ALTCHA_HMAC_KEY`, expire after **10 minutes**, and carry `Cache-Control: no-store` so proxies cannot cache and re-serve them.
- The widget performs a SHA-256 proof-of-work in a Web Worker (search space up to 100 000) then posts the solution with the form.
- Django verifies the HMAC signature and expiry before accepting the submission. An expired or tampered challenge is rejected with HTTP 400.
- The widget and all verification logic run entirely in your infrastructure — no external service is contacted.

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

### Email notification texts (`apps/submissions/email_texts.yaml`)

Subject lines and submitter-facing status messages are configured in `apps/submissions/email_texts.yaml`. Edit the file and rebuild the container image to apply changes.

**Subject line keys:**

| Key                 | Event                                                   | Placeholders                 |
| ------------------- | ------------------------------------------------------- | ---------------------------- |
| `created`           | New submission received (admin notification)            | `{service_name}`             |
| `status_changed`    | Status changed (admin notification)                     | `{service_name}`, `{status}` |
| `updated`           | Submitter edited their service (admin notification)     | `{service_name}`             |
| `submitter_created` | New submission received (submitter confirmation)        | `{service_name}`             |
| `submitter_status`  | Status changed (submitter notification)                 | `{service_name}`, `{status}` |
| `submitter_updated` | Submitter edited their service (submitter notification) | `{service_name}`             |

**Email routing summary:**

| Event                             | Admin email                             | Submitter email                        |
| --------------------------------- | --------------------------------------- | -------------------------------------- |
| New submission (`created`)        | ✓ (with admin portal link)              | ✓ (receipt confirmation, no admin URL) |
| Edit submitted (`updated`)        | ✓ (with diff table + admin portal link) | ✓ (with diff table, no admin URL)      |
| Status changed (`status_changed`) | ✓                                       | ✓ (plain-language status message)      |

The submitter is **never** CC'd on admin emails. All submitter communication goes through dedicated separate emails so the admin portal URL is never accidentally forwarded to submitters.

### REST API

```bash
API_PAGE_SIZE=20                # Default page size for list endpoints
API_MAX_PAGE_SIZE=100           # Maximum page size clients may request

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

### Branding overrides

These can also be set in `site.toml`. Env vars take precedence.

```bash
# ADMIN_URL_PREFIX=admin-denbi
# LOGO_URL=/static/img/logo.svg
```

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
{{ LOGO_URL }}
{{ FAVICON_URL }}

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
2. Place your logo at `static/img/logo.svg` and set `logo_url = "/static/img/logo.svg"`
3. Place your favicon at `static/img/favicon.ico` (auto-detected, no config needed)
4. Run `docker compose exec web python manage.py collectstatic --noinput`
5. Restart: `docker compose restart web worker beat`

No Python, no template edits, no rebuild required.
