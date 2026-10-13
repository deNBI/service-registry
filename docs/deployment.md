---
icon: material/rocket-launch
---

# Deployment Guide

For operators who install and update the registry. For a local development stack, see [Development Setup](development.md). Day-to-day operation is covered in the [Operator Guide](operator/index.md).

## Prerequisites

- Docker Engine 24+ and Docker Compose v2
- A hostname with DNS pointing to your server (e.g. `service-registry.bi.denbi.de`)
- TLS certificate (Let's Encrypt recommended)
- Access to an SMTP server for email notifications

---

## Configuration

See [Configuration Reference](configuration.md) for all settings including `.env` and `config/site.toml`. The production deployment below uses these settings.

---

## Production Deployment

!!! note "Ansible-managed production"
    The de.NBI production environment is deployed via Ansible. The Ansible role
    (`roles/registry/templates/docker-compose.yml.j2`) generates the authoritative
    production compose file and manages `.env` via vault. The steps below describe
    the infrastructure assumptions — consult the Ansible role for the actual
    deployment procedure.

### Architecture

| Component                      | How deployed                                                                                                              |
| ------------------------------ | ------------------------------------------------------------------------------------------------------------------------- |
| Django (Gunicorn)              | Docker container — image from the private registry; name and tag are set in the Ansible role (outside this repository). `ci_build_push.sh` pushes `<REGISTRY_HOST>/denbi/denbi-registry:<IMAGE_TAG>` by default, so the Ansible side must use the name and tag that were pushed |
| Celery worker + beat           | Docker containers — same image                                                                                            |
| Redis                          | Docker container — broker + rate-limit cache                                                                              |
| PostgreSQL                     | External managed instance — not a Docker container                                                                        |
| Nginx + TLS                    | Host-managed — terminates HTTPS, proxies to Gunicorn on port 8000                                                         |
| `config/site.toml`             | Bind-mounted from `/data/denbi-service-registry/config/site.toml` — rebranding requires no image rebuild                  |
| `mediafiles/` (uploaded logos) | Volume mounted at `/app/mediafiles` in the `web` container (`media_data` in `docker-compose.prod.yml`) — must persist across container restarts and image upgrades |

### Step 1 — Configure environment

On the production server, create `.env` with production values (managed by Ansible vault):

```bash
SECRET_KEY=<generate-a-long-random-key>
DB_NAME=denbi_registry
DB_USER=denbi
DB_HOST=<external-postgres-host>
DB_PORT=5432
DB_PASSWORD=<strong-random-password>
REDIS_PASSWORD=<strong-random-password>

DEBUG=false
ALLOWED_HOSTS=service-registry.bi.denbi.de

EMAIL_HOST=smtp.your-provider.org
EMAIL_PORT=587
EMAIL_USE_TLS=true
EMAIL_HOST_USER=your-smtp-user
EMAIL_HOST_PASSWORD=your-smtp-password
EMAIL_FROM=no-reply@denbi.de

ADMIN_URL_PREFIX=your-secret-admin-path
FORWARDED_ALLOW_IPS=<nginx-server-ip>
ALTCHA_HMAC_KEY=<generate: python -c "import secrets; print(secrets.token_hex(32))">
```

### Step 2 — TLS and Nginx

TLS termination is handled by the host Nginx. Traffic flows:

```
Internet → host Nginx (port 443, TLS) → Gunicorn (web:8000, Docker-internal)
```

Configure the host Nginx vhost to proxy to `localhost:8000`. See `nginx/host/` for
a reference vhost configuration. Let's Encrypt is recommended for TLS:

```bash
certbot certonly --standalone -d service-registry.bi.denbi.de
```

### Step 3 — Start production services

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec web python manage.py createsuperuser
```

On the Ansible-managed host the generated compose file is used instead; note that the repository's `docker-compose.prod.yml` overlay cannot remove the bundled `db` service, so with the overlay alone a local PostgreSQL container also starts.

!!! info "Migrations, group sync, and EDAM seeding are automatic"
    The `web` container entrypoint runs `manage.py migrate --noinput` before starting,
    then immediately runs `manage.py setup_groups` to keep the three admin role groups
    (`Registry Viewer`, `Registry Editor`, `Registry Manager`) in sync with the codebase.
    On a fresh database, migrations also seed the EDAM ontology (about 3,500 terms, about 30 s).
    Worker and beat containers set `SKIP_MIGRATE=true` so they do not race web on startup.

!!! info "Static files are baked into the image"
    `collectstatic` runs at Docker build time — no separate `collectstatic` step needed.

!!! warning "SKIP_MIGRATE on worker and beat"
    The Ansible-generated compose must set `SKIP_MIGRATE: "true"` in the environment
    of `worker` and `beat` services. Without this, all three containers race to run
    migrations simultaneously on a fresh database, causing a PostgreSQL `UniqueViolation`
    error. The `web` service is the sole migration runner.

---

## Migrations

The `web` container entrypoint runs `manage.py migrate --noinput` automatically before
Gunicorn starts. Worker and beat containers skip this step (`SKIP_MIGRATE=true`) to
avoid a race condition on fresh databases.

To run migrations manually (e.g. to pre-apply before a rolling restart):

```bash
docker compose exec web python manage.py migrate
# or via Makefile (base compose + docker-compose.prod.yml):
make prod-migrate
```

Migrations are tracked in version control under `apps/*/migrations/`. Never edit generated migration files manually.

---

## Initial Data

- **EDAM terms and SPDX licences** are downloaded automatically by the first `migrate` on an empty database (about 30 seconds) and refreshed every 30 days. Air-gapped servers, manual syncs and the outbound network access the server needs are described in [Reference Data and Ontology Sync](operator/data-sync.md).
- **Service categories, service centres and PIs** are entered in the admin (**Reference Data**), through the [Admin REST API](admin/api.md#reference-data-categories-service-centres-pis), or in bulk from a spreadsheet with `scripts/import_reference_data.py` ([Bulk import](operator/data-sync.md#bulk-import-of-pis-categories-and-service-centres)). The repository ships no fixture files for them.

---

## Updating the Application

In the Ansible-managed production environment, Ansible handles image pulls and container
restarts. For manual updates:

```bash
# Pull the new image (the tag comes from the compose file Ansible generates)
docker compose pull

# Restart containers — web entrypoint applies pending migrations automatically
docker compose up -d --no-deps web worker beat

# Verify
curl http://localhost:8000/health/ready/
docker compose logs web --tail 50
```

---

## Uploaded Media (Service Logos)

Submitters can upload a logo image (PNG, JPEG, or SVG) with each service registration.
Uploaded files are stored under `mediafiles/logos/<uuid>.<ext>` inside the container and
served by Gunicorn via Django's `django.views.static.serve`. The host Nginx proxies
these requests through; the bundled config's `location /media/` block only adds
cache headers.

Django serves uploaded media with its own restrictive `Content-Security-Policy`
header (`default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; sandbox`).
Nginx must pass this header through unchanged: do not add `proxy_hide_header
Content-Security-Policy` or an `add_header Content-Security-Policy` for `/media/`.

### Request size for uploads

Logos are uploaded through `/register/`, `/update/`, `/api/` and the admin URL
prefix (`ADMIN_URL_PREFIX`, default `admin-denbi`). On all of these the proxy's
`client_max_body_size` must be a little larger than `logo_max_bytes` (10 MB by
default), because the request also carries the other form fields; `12m` gives
enough headroom. With a lower limit, a large upload is refused by nginx with a
bare HTTP 413 page instead of the application's validation message.

The bundled `nginx/host/service-registry.bi.denbi.de.conf` sets `12m` for
`/api/`, `/register/`, `/update/` and `/admin-denbi/`, and keeps a server-wide
default of `64k` for everything else. Its admin `location` uses the default
prefix: if you change `ADMIN_URL_PREFIX`, change that `location` path to match,
otherwise admin logo uploads over 64 KB are refused by nginx.

To check the effective limits on a server:

```bash
sudo nginx -T 2>/dev/null | grep -nE "server_name|client_max_body_size|location "
```

### Persistent storage in production

Uploaded logos are stored in a named Docker volume (`media_data`) that is mounted at
`/app/mediafiles` in the `web` container, the only service that reads or writes logos. The volume is
declared in `docker-compose.prod.yml`:

```yaml
volumes:
  media_data:
```

This keeps logos alive across container restarts and image upgrades. **Without a
persistent volume or bind mount, logos are lost whenever the container is replaced.**

### Binding to a host path (Ansible)

To store logos on the host filesystem (recommended for backups), replace the named
volume with a bind mount in your Ansible Compose template:

```yaml
# web service
volumes:
  - /data/denbi-service-registry/media:/app/mediafiles

# Remove the named volume declaration entirely when using a bind mount
```

### Verifying media serving

```bash
# Check a logo URL returned by the API
curl -I https://service-registry.bi.denbi.de/media/logos/<uuid>.png
# → HTTP/1.1 200 OK  (or 404 if the file doesn't exist)
```

---

## Branding

The navbar logo, favicon and site name are set in `config/site.toml`; see [Configuration → Deploying for a different organisation](configuration.md#deploying-for-a-different-organisation). Backups of the database and the uploaded logos are described in [Backups](operator/backups.md).

---

## Security Checklist (pre-launch)

- [ ] `DEBUG=false` in `.env`
- [ ] `SECRET_KEY` is unique and at least 50 characters
- [ ] Strong passwords for `DB_PASSWORD` and `REDIS_PASSWORD`
- [ ] TLS certificate installed and auto-renewal configured
- [ ] HSTS preload submitted to [hstspreload.org](https://hstspreload.org)
- [ ] `ADMIN_URL_PREFIX` changed from default `admin-denbi`
- [ ] `ALLOWED_HOSTS` set to production hostname only
- [ ] `ALTCHA_HMAC_KEY` set (otherwise the CAPTCHA is skipped)
- [ ] Containers run as non-root: `docker compose exec web id -u` does not print `0`
- [ ] `pip-audit` passes: `make audit`
- [ ] Health checks passing: `curl https://service-registry.bi.denbi.de/health/ready/`
- [ ] Test email notification by submitting a test form
- [ ] `media_data` volume or bind mount confirmed persistent (upload a logo and redeploy — verify it survives)

---

## Troubleshooting

See [Troubleshooting](operator/troubleshooting.md) in the Operator Guide.
