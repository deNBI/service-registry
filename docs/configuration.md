---
icon: material/tune
---

# Configuration Reference

Configuration is split into two files with a clear separation of concerns:

| File               | What goes here                                 | Restart needed?                                |
| ------------------ | ---------------------------------------------- | ---------------------------------------------- |
| `config/site.toml` | Branding, contact info, URLs, feature flags    | Yes (`docker compose restart web worker beat`) |
| `.env`             | Secrets, passwords, connection strings, tuning | Yes (full restart)                             |

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
form_version = "1.1"
form_date    = "2026-02-14"
```

| Key                          | Description                                                                                                                                                                                        |
| ---------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `name`                       | Site name shown in the navbar, page titles, and email subjects                                                                                                                                     |
| `tagline`                    | Browser tab subtitle and meta description                                                                                                                                                          |
| `url`                        | Canonical public URL — used in outbound emails and the bio.tools API User-Agent                                                                                                                    |
| `logo_url`                   | Logo image URL. Three options: an absolute URL (`https://…`), a local static path (`/static/img/logo.svg`), or empty string (auto-detects `static/img/logo.*`, then falls back to a CSS text logo) |
| `favicon_url`                | Favicon URL. Same three options as `logo_url`. Empty string auto-detects `static/img/favicon.ico` / `.png` / `.svg`. If nothing is found, no `<link rel="icon">` is rendered                       |
| `form_version` / `form_date` | Shown in the registration form header                                                                                                                                                              |

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
```

All links are rendered dynamically — changing a URL here updates it everywhere in the UI without touching template files.

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
```

### EDAM ontology sync

```toml
[edam]
owl_url = "https://edamontology.org/EDAM_stable.owl"
```

Overridden by `EDAM_OWL_URL` in `.env`. Set to a local file path for air-gapped servers.

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

| Key | Default | Description |
|---|---|---|
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
AXES_FAILURE_LIMIT=5            # Failed login attempts before lockout
AXES_COOLOFF_MINUTES=30         # Lockout duration in minutes
```

### Rate limiting

Format: `<count>/<period>` where period is `s` / `m` / `h` / `d`.

```bash
RATE_LIMIT_SUBMIT=10/h          # Registration form submissions
RATE_LIMIT_UPDATE=20/h          # Submission update form
RATE_LIMIT_API=60/m             # REST API create endpoint
```

### Email (SMTP credentials)

```bash
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.example.org
EMAIL_PORT=587
EMAIL_USE_TLS=true
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
EMAIL_FROM=no-reply@denbi.de    # Overrides [email] from_address in site.toml

# Optional CC address on every submission notification:
# SUBMISSION_NOTIFY_CC=admin@denbi.de

# Override all notification recipients for testing (sends all emails here):
# SUBMISSION_NOTIFY_OVERRIDE=test-inbox@denbi.de
```

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
{{ LOGO_URL }}
{{ FAVICON_URL }}

{# Full SITE dict — mirrors site.toml sections #}
{{ SITE.form_version }}
{{ SITE.contact.email }}
{{ SITE.links.kpi_cheatsheet }}
{{ SITE.features.biotools_prefill }}
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
