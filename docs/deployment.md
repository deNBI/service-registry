---
icon: material/rocket-launch
---

# Deployment Guide

> **Configuration:** see [Configuration Reference](configuration.md) for all settings including branding, contact email, and feature flags. — de.NBI Service Registry

## Prerequisites

- Docker Engine 24+ and Docker Compose v2
- A hostname with DNS pointing to your server (e.g. `service-registry.bi.denbi.de`)
- TLS certificate (Let's Encrypt recommended)
- Access to an SMTP server for email notifications

---

## Configuration

See [Configuration Reference](configuration.md) for all settings including `.env` and `config/site.toml`. The production deployment below uses these settings.

---

## Quick Start (Development)

```bash
# 1. Clone the repository
git clone https://github.com/denbi/service-registry.git
cd service-registry

# 2. Configure environment
cp .env.example .env
# Edit .env — set SECRET_KEY, DB_PASSWORD, REDIS_PASSWORD

# 3. Build and start services
#    Migrations run automatically on container start.
#    On a fresh database, EDAM (~3,400 terms) is seeded automatically (~30 s).
make build
make dev

# 4. Create the first admin user
make superuser

# 5. Visit http://localhost:8000
```

---

## Production Deployment

!!! note "Ansible-managed production"
    The de.NBI production environment is deployed via Ansible. The Ansible role
    (`roles/registry/templates/docker-compose.yml.j2`) generates the authoritative
    production compose file and manages `.env` via vault. The steps below describe
    the infrastructure assumptions — consult the Ansible role for the actual
    deployment procedure.

### Architecture

| Component | How deployed |
|---|---|
| Django (Gunicorn) | Docker container — image from `crate.bi.denbi.de/denbi/denbi-service-registry:stable` |
| Celery worker + beat | Docker containers — same image |
| Redis | Docker container — broker + rate-limit cache |
| PostgreSQL | External managed instance — not a Docker container |
| Nginx + TLS | Host-managed — terminates HTTPS, proxies to Gunicorn on port 8000 |
| `config/site.toml` | Bind-mounted from `/data/denbi-service-registry/config/site.toml` — rebranding requires no image rebuild |
| `mediafiles/` (uploaded logos) | Named Docker volume `media_data` mounted at `/app/mediafiles` — must persist across container restarts and image upgrades |

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
docker compose up -d
docker compose exec web python manage.py createsuperuser
```

!!! info "Migrations, group sync, and EDAM seeding are automatic"
    The `web` container entrypoint runs `manage.py migrate --noinput` before starting,
    then immediately runs `manage.py setup_groups` to keep the three admin role groups
    (`Registry Viewer`, `Registry Editor`, `Registry Manager`) in sync with the codebase.
    On a fresh database, migrations also seed the EDAM ontology (~3,400 terms, ~30 s).
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
# or via Makefile (dev overlay):
make prod-migrate
```

Migrations are tracked in version control under `apps/*/migrations/`. Never edit generated migration files manually.

---

## Initial Data (Fixtures)

Seed the PI list and service centres:

```bash
docker compose exec web python manage.py loaddata apps/registry/fixtures/initial_pis.json
docker compose exec web python manage.py loaddata apps/registry/fixtures/initial_centres.json
```

(Fixture files to be created separately with the full de.NBI PI list.)

---

## EDAM Ontology Seeding

The EDAM ontology (~3,400 terms) powers the Topic and Operation dropdowns on the
registration form. The application handles seeding in three ways — no manual step
is required on a standard first deployment.

### Automatic seeding on first migrate

When `manage.py migrate` runs against a **fresh database** (empty `EdamTerm` table),
it automatically downloads and imports EDAM from `EDAM_OWL_URL`. This happens as
a `post_migrate` signal — you will see progress output at the end of `migrate`:

```
[edam] EdamTerm table is empty — running initial EDAM sync.
[edam] This downloads ~3 MB from edamontology.org and may take ~30 seconds.
[edam] Loading EDAM from: https://edamontology.org/EDAM_stable.owl
...
[edam] Auto-seed complete — 3471 terms loaded (EDAM 1.25).
```

On subsequent `migrate` runs (e.g. applying a new migration), the table is not
empty so the signal is a no-op.

### Ongoing automatic updates

Celery beat runs a full EDAM sync **every 30 days** automatically. EDAM releases
are infrequent (~1–2 per year) so monthly is more than sufficient.

### Manual sync

Force a sync at any time via:

- **Admin UI**: Go to **EDAM Ontology → EDAM Terms** and click **↻ Sync EDAM from upstream**.
  The sync runs as a background Celery task; refresh the page after ~30 seconds.

- **CLI**:
  ```bash
  docker compose exec web python manage.py sync_edam
  ```

Verify the current state:

```bash
docker compose exec web python manage.py shell -c \
  "from apps.edam.models import EdamTerm; t = EdamTerm.objects.first(); print(EdamTerm.objects.count(), 'terms, version', t.edam_version)"
```

### Air-gapped / firewall-restricted servers

If the server cannot reach edamontology.org, download the OWL file on another machine
and copy it across:

```bash
# On a machine with internet access:
curl -o EDAM.owl https://edamontology.org/EDAM_stable.owl
# Copy EDAM.owl to the server, then:
```

Either set the path permanently in `.env`:

```bash
EDAM_OWL_URL=/app/EDAM.owl
```

Or pass it once to the management command:

```bash
docker compose exec web python manage.py sync_edam --url /app/EDAM.owl
```

The auto-seed on first migrate also respects `EDAM_OWL_URL`, so an air-gapped
deployment works without any code changes.

---

## Network Requirements

| Destination        | Port | When needed                       | Purpose                |
| ------------------ | ---- | --------------------------------- | ---------------------- |
| `bio.tools`        | 443  | On form submission + daily        | bio.tools API sync     |
| `edamontology.org` | 443  | Manual `sync_edam` runs only      | EDAM ontology download |
| Your SMTP server   | 587  | On every submission/status change | Email notifications    |

---

## Updating the Application

In the Ansible-managed production environment, Ansible handles image pulls and container
restarts. For manual updates:

```bash
# Pull the new image (Ansible sets image tag to :stable)
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
served by Gunicorn via Django's `django.views.static.serve` — the host Nginx simply
proxies all requests through, so no special Nginx `location /media/` block is needed.

### Persistent storage in production

Uploaded logos are stored in a named Docker volume (`media_data`) that is mounted at
`/app/mediafiles` in both the `web`, `worker`, and `beat` containers. The volume is
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
# web / worker / beat service
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

## Navbar Logo (Branding)

To display your organisation's logo in the site navbar:

1. Place your organisation logo file at `static/img/logo.png` (or `.svg`, `.jpg`)
2. Rebuild static files: `make collectstatic`

Or set `LOGO_URL` in `.env` to point to any image URL:

```bash
# Local static file (after collectstatic)
LOGO_URL=/static/img/logo.png

# External URL
LOGO_URL=https://www.denbi.de/images/logos/denbi-logo.png
```

Recommended logo height is 38px. The image renders in the top-left of the navbar
alongside the site name text.

---

## Backup

### Database

```bash
# Backup
docker compose exec db pg_dump -U denbi denbi_registry > backup_$(date +%Y%m%d).sql

# Restore
docker compose exec -T db psql -U denbi denbi_registry < backup_20260306.sql
```

### Media files (uploaded logos)

If using a bind mount (`/data/denbi-service-registry/media`), include the directory in
your standard host backup. If using a named Docker volume, export it before an upgrade:

```bash
# Export named volume to a tar archive
docker run --rm \
  -v denbi_service_registry_media_data:/data \
  -v $(pwd):/backup \
  alpine tar czf /backup/media_backup_$(date +%Y%m%d).tar.gz -C /data .

# Restore
docker run --rm \
  -v denbi_service_registry_media_data:/data \
  -v $(pwd):/backup \
  alpine tar xzf /backup/media_backup_20260322.tar.gz -C /data
```

### Redis

Redis holds only transient Celery queue data and does not need persistent backup.

---

## Security Checklist (pre-launch)

- [ ] `DEBUG=false` in `.env`
- [ ] `SECRET_KEY` is unique and at least 50 characters
- [ ] Strong passwords for `DB_PASSWORD` and `REDIS_PASSWORD`
- [ ] TLS certificate installed and auto-renewal configured
- [ ] HSTS preload submitted to [hstspreload.org](https://hstspreload.org)
- [ ] `ADMIN_URL_PREFIX` changed from default `admin-denbi`
- [ ] `ALLOWED_HOSTS` set to production hostname only
- [ ] All containers running as non-root (`docker compose ps` → verify USER column)
- [ ] `pip-audit` passes: `make audit`
- [ ] Health checks passing: `curl https://service-registry.bi.denbi.de/health/ready/`
- [ ] Test email notification by submitting a test form
- [ ] `media_data` volume or bind mount confirmed persistent (upload a logo and redeploy — verify it survives)

---

## Troubleshooting

**Web service not starting:**

```bash
docker compose logs web
# Common causes: missing SECRET_KEY, DB_PASSWORD, or REDIS_PASSWORD in .env
# The startup error will name the missing variable explicitly
```

**Database authentication failure:**

```bash
# Verify what password Django is using
docker compose exec web python -c "
from django.conf import settings
d = settings.DATABASES['default'].copy()
d['PASSWORD'] = '***'
print(d)
"
# Production: DB_HOST points to the external PostgreSQL instance — verify DB_HOST,
# DB_PORT, DB_USER, and DB_PASSWORD in .env match the external server's credentials.
# Development: make sure DB_PASSWORD in .env matches the password the DB volume was
# initialised with. If unsure, wipe the volume and start fresh: make nuke
```

**Emails not sending:**

```bash
docker compose logs worker
docker compose exec worker python -c \
  "from django.core.mail import send_mail; send_mail('test','test','from@example.com',['to@example.com'])"
```

**Rate limit 429 errors in testing:**

```bash
# Increase limits in .env:
RATE_LIMIT_SUBMIT=1000/h
RATE_LIMIT_UPDATE=1000/h
```

**EDAM dropdowns empty:**

```bash
docker compose exec web python manage.py sync_edam
```
