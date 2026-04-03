---
icon: material/package-up
---

# Release, Rollout, and Rollback Runbook

This document covers the full lifecycle of a code change from development to
production: versioning scheme, CI pipeline steps, staged rollout, zero-downtime
deployment, rollback procedure, and post-release verification.

---

## Versioning

The project uses **Semantic Versioning** (`MAJOR.MINOR.PATCH`):

| Increment            | When                                                      |
| -------------------- | --------------------------------------------------------- |
| `PATCH` (e.g. 1.0.1) | Bug fix, security patch, config-only change               |
| `MINOR` (e.g. 1.1.0) | New feature, new optional field, new API endpoint         |
| `MAJOR` (e.g. 2.0.0) | Breaking API change, major migration, architecture change |

Version is tracked in **two places**:

1. Git tag: `git tag v1.1.0`
2. Docker image tag: `denbi-registry:v1.1.0`

The `SPECTACULAR_SETTINGS["VERSION"]` in `config/settings.py` must match the
API's own version (updated independently from the application version when
API contracts change):

```python
SPECTACULAR_SETTINGS = {
    "VERSION": "1.0.0",   # Update when API surface changes
    ...
}
```

---

## Branch and Release Workflow

```
feature/xyz  →  main  →  tag v1.1.0  →  Docker image  →  staging  →  production
```

1. **Feature branch**: all development in `feature/*` or `fix/*` branches.
2. **Pull Request**: CI runs tests, linting, audit before merge.
3. **Merge to main**: triggers CI build of `denbi-registry:main` (unstable tag).
4. **Tag a release**: `git tag v1.1.0 && git push origin v1.1.0` triggers production build.
5. **Deploy to staging**: automated or manual after CI passes on the tag.
6. **Deploy to production**: manual gate after staging verification.

---

## CI Pipeline (GitHub Actions / GitLab CI)

### GitHub Actions example

Tests, linting, and audits run natively on the GitHub runner (no Docker needed for
the test stage). The build job validates the Docker image separately.

```yaml
# .github/workflows/ci.yml
name: CI

on:
  push:
    branches: [main]
    tags: ['v*']
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: requirements/development.txt
      - name: Install dependencies
        run: pip install -r requirements/development.txt
      - name: Run tests
        run: pytest tests/
        env:
          DJANGO_SETTINGS_MODULE: config.settings_test
          SECRET_KEY: ci-only-not-a-real-key
          DB_PASSWORD: ci
          REDIS_PASSWORD: ci
      - name: Lint
        run: |
          ruff check apps/ config/ tests/
          ruff format --check apps/ config/ tests/
      - name: Audit
        run: pip-audit -r requirements/production.txt

  build:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build Docker image
        run: docker compose build
      - name: Push image (on tag)
        if: startsWith(github.ref, 'refs/tags/v')
        env:
          IMAGE_TAG: ${{ github.ref_name }}
        run: |
          docker build -t ghcr.io/denbi/service-registry:${IMAGE_TAG} .
          docker push ghcr.io/denbi/service-registry:${IMAGE_TAG}
          docker tag ghcr.io/denbi/service-registry:${IMAGE_TAG} \
                     ghcr.io/denbi/service-registry:latest
          docker push ghcr.io/denbi/service-registry:latest
```

### GitLab CI example

```yaml
# .gitlab-ci.yml
stages: [test, build, deploy-staging, deploy-production]

test:
  stage: test
  image: python:3.12-slim
  before_script:
    - pip install -r requirements/development.txt
  script:
    - pytest tests/
    - ruff check apps/ config/ tests/
    - pip-audit -r requirements/production.txt
  variables:
    DJANGO_SETTINGS_MODULE: config.settings_test
    SECRET_KEY: ci-only
    DB_PASSWORD: ci
    REDIS_PASSWORD: ci

build:
  stage: build
  only: [tags]
  script:
    - docker build -t registry.gitlab.com/$CI_PROJECT_PATH:$CI_COMMIT_TAG .
    - docker push registry.gitlab.com/$CI_PROJECT_PATH:$CI_COMMIT_TAG

deploy-staging:
  stage: deploy-staging
  only: [tags]
  environment: staging
  script:
    - ssh $DEPLOY_USER@$STAGING_HOST "IMAGE_TAG=$CI_COMMIT_TAG /opt/denbi/scripts/deploy.sh"

deploy-production:
  stage: deploy-production
  only: [tags]
  environment: production
  when: manual # Requires explicit click in GitLab UI
  script:
    - ssh $DEPLOY_USER@$PROD_HOST "IMAGE_TAG=$CI_COMMIT_TAG /opt/denbi/scripts/deploy.sh"
```

---

## Deployment Script

Place this on the server at `/opt/denbi/scripts/deploy.sh`:

```bash
#!/usr/bin/env bash
# /opt/denbi/scripts/deploy.sh
# Usage: IMAGE_TAG=v1.1.0 ./deploy.sh
set -euo pipefail

IMAGE_TAG=${IMAGE_TAG:-latest}
COMPOSE_DIR=/opt/denbi/service-registry
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

echo "=== Deploying denbi-registry:${IMAGE_TAG} ==="
cd "$COMPOSE_DIR"

# 1. Pull the new image
docker pull "ghcr.io/denbi/service-registry:${IMAGE_TAG}"
docker tag  "ghcr.io/denbi/service-registry:${IMAGE_TAG}" denbi-registry:current

# 2. Rolling restart — start new containers before stopping old
#    The container entrypoint runs migrations automatically on startup.
#    Static files are baked into the image at build time — no collectstatic needed.
echo "--- Restarting web, worker, beat ---"
IMAGE_TAG="${IMAGE_TAG}" $COMPOSE up -d --no-deps web worker beat

# 5. Wait for health check to pass
echo "--- Waiting for health check ---"
for i in $(seq 1 12); do
  STATUS=$(curl -sf http://localhost:8000/health/ready/ | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','error'))" 2>/dev/null || echo "error")
  if [ "$STATUS" = "ok" ]; then
    echo "Health check passed."
    break
  fi
  echo "  Waiting ($i/12)..."
  sleep 5
done

if [ "$STATUS" != "ok" ]; then
  echo "ERROR: Health check failed after 60s. Check logs:" >&2
  $COMPOSE logs web --tail 50
  exit 1
fi

echo "=== Deployment complete: ${IMAGE_TAG} ==="
```

Make it executable: `chmod +x /opt/denbi/scripts/deploy.sh`

---

## Zero-Downtime Deployment (Standard)

For regular releases with **non-destructive migrations** (adding optional fields,
adding tables, adding indices):

```bash
# On the server
IMAGE_TAG=v1.1.0 /opt/denbi/scripts/deploy.sh
```

This is zero-downtime because:

- Migrations run before new code starts (new schema is backward-compatible)
- `docker compose up -d --no-deps` starts new containers before removing old
- Nginx continues serving requests throughout

---

## Maintenance Window Deployment

Required when migrations are **destructive** (renaming columns, dropping columns,
changing column types). These cannot be backward-compatible.

```bash
# 1. Enable maintenance page on host Nginx
sudo cp /var/www/denbi-registry/errors/upstream_down.html \
        /var/www/denbi-registry/maintenance.html
# (Configure host Nginx to serve this file instead of proxying)

# 2. Stop the application (keep DB and Redis running)
docker compose stop web worker beat

# 3. Apply the migration (use --run --rm to bypass the normal entrypoint auto-migrate
#    so you can verify the migration manually before bringing traffic back)
docker compose run --rm web python manage.py migrate

# 4. Deploy the new image (entrypoint will detect no pending migrations and proceed)
IMAGE_TAG=v2.0.0 /opt/denbi/scripts/deploy.sh

# 5. Remove maintenance page / re-enable proxy
```

---

## Rollback Procedure

### Rollback application code (no migration rollback needed)

If the new release has a bug but no schema changes:

```bash
# Restart with the previous image — no migration step needed
IMAGE_TAG=v1.0.0 docker compose \
  -f docker-compose.yml -f docker-compose.prod.yml \
  up -d --no-deps web worker beat

# Verify
curl https://service-registry.bi.denbi.de/health/ready/
```

### Rollback including a migration

Only possible if the migration is reversible. Check with:

```bash
docker compose run --rm web python manage.py sqlmigrate submissions 0003 --backwards
# If this fails, the migration is not reversible — restore from backup instead.
```

If reversible:

```bash
# 1. Roll back to the previous migration
docker compose run --rm web python manage.py migrate submissions 0002

# 2. Deploy previous image
IMAGE_TAG=v1.0.0 docker compose \
  -f docker-compose.yml -f docker-compose.prod.yml \
  up -d --no-deps web worker beat
```

### Full rollback from database backup

If the migration cannot be reversed and the new code is broken:

```bash
# 1. Stop application
docker compose stop web worker beat

# 2. Restore database
docker compose exec db psql -U denbi postgres -c "DROP DATABASE denbi_registry;"
docker compose exec db psql -U denbi postgres -c "CREATE DATABASE denbi_registry;"
docker compose exec -T db psql -U denbi denbi_registry < /path/to/backup.sql

# 3. Deploy previous image
IMAGE_TAG=v1.0.0 /opt/denbi/scripts/deploy.sh
```

---

## Staging Verification Checklist

Run through this on staging after every deployment, before promoting to production.

**Functional checks:**

- [ ] `GET /health/ready/` returns `{"status": "ok"}`
- [ ] `GET /` loads the home page without errors
- [ ] `GET /register/` loads the full form
- [ ] Section B of the form shows EDAM Topics and EDAM Operations searchable fields
- [ ] Typing "prote" in the EDAM Topics field filters to proteomics-related terms
- [ ] Entering `https://bio.tools/blast` in the bio.tools URL field and tabbing out triggers the prefill banner
- [ ] Clicking "Apply prefill" populates name, description, and EDAM fields from bio.tools
- [ ] `GET /captcha/` returns `200` with JSON containing `algorithm`, `challenge`, `salt`, `signature`, and `maxNumber` fields
- [ ] `GET /captcha/` response has `Cache-Control: no-store` header
- [ ] The ALTCHA widget appears on `/register/` (checkbox or spinner visible below Section G)
- [ ] Clicking the Submit button on `/register/` triggers the ALTCHA proof-of-work solve (spinner, then checkmark) before the form posts
- [ ] Submit a test registration with EDAM terms selected → confirm redirect to success page with API key
- [ ] Copy the API key → go to `/update/` → enter key → form pre-populates including EDAM selections
- [ ] Submit an update → confirm notification email received
- [ ] `GET /api/docs/` loads Swagger UI
- [ ] `GET /api/schema/` returns 200 with valid OpenAPI YAML
- [ ] `POST /api/v1/submissions/` with valid JSON payload returns 201 with `api_key`
- [ ] `GET /api/v1/submissions/{id}/` response includes `edam_topics`, `edam_operations`, and `biotoolsrecord` fields
- [ ] `GET /api/v1/edam/?branch=topic` returns list of EDAM topic terms (no auth required)
- [ ] `GET /api/v1/edam/topic_0121/` returns full Proteomics term with definition and parent
- [ ] After bio.tools sync runs: `GET /api/v1/biotools/blast/` returns structured record with functions
- [ ] Admin portal at `/<ADMIN_URL_PREFIX>/` loads and shows submissions list
- [ ] Admin → EDAM Ontology → EDAM Terms shows ~4000 terms
- [ ] Admin → bio.tools Integration shows sync status for submissions with bio.tools URLs
- [ ] Approve a submission via admin → status email sent

**Security checks:**

- [ ] `http://` redirects to `https://` (301)
- [ ] `Strict-Transport-Security` header present in response
- [ ] `X-Frame-Options: DENY` present
- [ ] API call without auth returns 403 (not 401, not 500)
- [ ] Invalid API key returns same 403 as revoked key
- [ ] `GET /api/v1/edam/` returns 200 without any `Authorization` header (public endpoint)
- [ ] `GET /api/v1/biotools/` without admin API key returns 403

---

## Monitoring After Release

```bash
# Watch live logs for errors
docker compose logs -f web | grep -E "ERROR|WARNING|CRITICAL"

# Check Celery task queue
docker compose exec worker celery -A config inspect active

# Check that beat is running scheduled tasks (should include sync-biotools-daily)
docker compose exec worker celery -A config inspect scheduled

# Check for failed tasks
docker compose exec worker celery -A config inspect reserved
```

### bio.tools Sync Health

After releasing a version that adds or changes bio.tools integration:

```bash
# Count records with sync errors
docker compose exec web python manage.py shell -c "
from apps.biotools.models import BioToolsRecord
errors = BioToolsRecord.objects.exclude(sync_error='')
print(f'{errors.count()} records with sync errors:')
for r in errors: print(f'  {r.biotools_id}: {r.sync_error[:80]}')
"

# Manually trigger a full sync if the scheduled task missed
docker compose exec web python manage.py sync_biotools
```

### EDAM Term Count

After any deployment that updates EDAM (or after running `sync_edam`):

```bash
docker compose exec web python manage.py shell -c "
from apps.edam.models import EdamTerm
from django.db.models import Count
qs = EdamTerm.objects.values('branch').annotate(n=Count('id')).order_by('branch')
for row in qs: print(f"  {row['branch']:12s}: {row['n']}")
print(f"  {'TOTAL':12s}: {EdamTerm.objects.count()}")
"
```

Set up an alert if `/health/ready/` returns non-200 for more than 60 seconds.
Tools: Uptime Kuma (self-hosted), Healthchecks.io, or your institution's monitoring stack.

---

## EDAM Ontology Releases

EDAM publishes new releases several times a year. When a new release is out:

1. Check the [EDAM changelog](https://github.com/edamontology/edamontology/blob/main/changelog.md)
   for any deprecated terms your submissions may be using.
2. Run the sync on staging first: `docker compose exec web python manage.py sync_edam --dry-run`
3. Apply on staging and verify term counts look correct.
4. Apply on production during a low-traffic period:
   ```bash
   docker compose exec web python manage.py sync_edam
   ```
5. No migration, no restart, no downtime needed — terms upsert in place.

This is a `PATCH`-level release (no code change, data-only) and does **not** require
going through the full CI/deploy pipeline.
