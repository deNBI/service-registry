---
icon: material/package-up
---

# Releases and Rollback

How a change gets from the repository to production, how to deploy and roll back, and the checks to run after a release. The live production host is managed by Ansible (`roles/registry/templates/docker-compose.yml.j2`); the commands below describe the same steps for a host managed with Docker Compose directly.

---

## Versioning

Releases are tagged in git with plain version numbers (for example `0.9.0`), following `MAJOR.MINOR.PATCH`:

| Increment | When                                                      |
| --------- | --------------------------------------------------------- |
| `PATCH`   | Bug fix, security patch, config-only change               |
| `MINOR`   | New feature, new optional field, new API endpoint         |
| `MAJOR`   | Breaking API change, major migration, architecture change |
 The REST API reports its own version in the OpenAPI schema; it is set in `config/site.toml` under `[api] version` and changes only when the API contract changes.

---

## How releases are built today

1. **Pull request**: the GitHub Actions workflow `.github/workflows/ci.yml` runs on pull requests to `main` and on pushes to `main` and `develop`. It runs `pytest`, `ruff check`, `ruff format --check` and `pip-audit`. Its Docker build and push jobs are commented out, so CI does not publish images.
2. **Merge to `main`**.
3. **Build and push the image** with `ci_build_push.sh`, run by a maintainer from an up-to-date checkout: it logs in to the private registry given in `REGISTRY_HOST`, builds the current directory with `docker buildx` and pushes `<REGISTRY_HOST>/<IMAGE_ORGANIZATION>/<IMAGE_NAME>:<IMAGE_TAG>` (defaults `denbi`, `denbi-registry`, `latest`; use a version for releases). In the committed version the git fetch and the commit-SHA tag are commented out, and the test stage does not run (it uses the unset `GIT_SHA`, and the runtime image has no pytest), so run it with `SKIP_TESTS=1` after `make pre-commit` has passed. The header comment of the script still describes the earlier behaviour.
4. **Staging**: deploy the image to staging and work through the [Staging Verification Checklist](#staging-verification-checklist).
5. **Deploy**: production pulls the new image through Ansible. Migrations run automatically when the `web` container starts.

Before a release, run `make pre-commit` locally (lint fix, audit, full test suite, strict docs build).

---

## Deployment script (example)

For a host without Ansible, a script like this pulls an image and restarts the services. It assumes a compose file whose `web`, `worker` and `beat` services use `image: <registry>/denbi-registry:${IMAGE_TAG}`, as the production Ansible template does. The `docker-compose.yml` and `docker-compose.prod.yml` in the repository build the image locally instead and do not read `IMAGE_TAG`. Replace `<registry>/denbi-registry` with your image name.

```bash
#!/usr/bin/env bash
# Usage: IMAGE_TAG=0.9.0 ./deploy.sh
set -euo pipefail

IMAGE_TAG=${IMAGE_TAG:-latest}
COMPOSE_DIR=/opt/denbi/service-registry
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

echo "=== Deploying denbi-registry:${IMAGE_TAG} ==="
cd "$COMPOSE_DIR"

# 1. Pull the new image
docker pull "<registry>/denbi-registry:${IMAGE_TAG}"
docker tag  "<registry>/denbi-registry:${IMAGE_TAG}" denbi-registry:current

# 2. Restart web, worker and beat on the new image.
#    The container entrypoint runs migrations automatically on startup.
#    Static files are baked into the image at build time — no collectstatic needed.
echo "--- Restarting web, worker, beat ---"
IMAGE_TAG="${IMAGE_TAG}" $COMPOSE up -d --no-deps web worker beat

# 3. Wait for health check to pass
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

---

## Standard deployment

For regular releases with **non-destructive migrations** (adding optional fields,
adding tables, adding indices):

```bash
# On the server
IMAGE_TAG=0.9.0 ./deploy.sh
```

The interruption is short because migrations run before the new code starts serving and the new schema is backward-compatible. Docker Compose replaces each container, so expect a few seconds in which `web` is restarting; nginx shows its error page during that time.

---

## Maintenance Window Deployment

Required when migrations are **destructive** (renaming columns, dropping columns,
changing column types). These cannot be backward-compatible.

While the application is stopped, the bundled nginx vhost answers with its 50x error page (`error_page 502 503 504` in `nginx/host/service-registry.bi.denbi.de.conf`).

```bash
# 1. Stop the application (keep DB and Redis running)
docker compose stop web worker beat

# 2. Apply the migration with a one-off container, so you can check it
#    before bringing traffic back
docker compose run --rm web python manage.py migrate

# 3. Deploy the new image (entrypoint will detect no pending migrations and proceed)
IMAGE_TAG=1.0.0 ./deploy.sh
```

---

## Rollback Procedure

### Rollback application code (no migration rollback needed)

If the new release has a bug but no schema changes, start the previous image (the commands assume the image-based compose file described under [Deployment script](#deployment-script-example)):

```bash
# Restart with the previous image — no migration step needed
IMAGE_TAG=0.9.0 docker compose \
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
IMAGE_TAG=0.9.0 docker compose \
  -f docker-compose.yml -f docker-compose.prod.yml \
  up -d --no-deps web worker beat
```

### Full rollback from database backup

If the migration cannot be reversed and the new code is broken:

```bash
# 1. Stop application
docker compose stop web worker beat

# 2. Restore database (bundled db container; on production run the same
#    statements against the external PostgreSQL server)
docker compose exec db psql -U denbi postgres -c "DROP DATABASE denbi_registry;"
docker compose exec db psql -U denbi postgres -c "CREATE DATABASE denbi_registry;"
docker compose exec -T db psql -U denbi denbi_registry < /path/to/backup.sql

# 3. Deploy previous image
IMAGE_TAG=0.9.0 ./deploy.sh
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
- [ ] The ALTCHA checkbox appears on `/register/` below Section G
- [ ] Ticking the ALTCHA checkbox solves the challenge (spinner, then checkmark)
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
- [ ] Admin → EDAM Ontology → EDAM Terms shows about 3,500 terms
- [ ] Admin → bio.tools Integration shows sync status for submissions with bio.tools URLs
- [ ] Approve a submission via admin → status email sent

**Security checks:**

- [ ] `http://` redirects to `https://` (301)
- [ ] `Strict-Transport-Security` header present in response
- [ ] `X-Frame-Options: DENY` present
- [ ] API call without a key to `GET /api/v1/submissions/{id}/` returns 401 (not 500)
- [ ] An invalid key and a revoked key both return 401
- [ ] `GET /api/v1/edam/` returns 200 without any `Authorization` header (public endpoint)
- [ ] `GET /api/v1/biotools/` without an Admin API key returns 401

---

## Monitoring After Release

```bash
# Watch live logs for errors
docker compose logs -f web | grep -E "ERROR|WARNING|CRITICAL"

# Check that the worker answers and what it is running
docker compose exec worker celery -A config inspect ping
docker compose exec worker celery -A config inspect active

# Check that beat is sending its scheduled tasks
docker compose logs beat --tail 50
```

Finished and failed tasks are listed in the admin under **Celery Results** (task results are stored in the database).

### bio.tools sync health

To list records whose last sync failed, or to run a full sync by hand, see [Reference Data and Ontology Sync](operator/data-sync.md#biotools-records).

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

For ongoing monitoring, see [Operations and Monitoring](operator/operations.md).

---

## Markdown Descriptions Rollout

Markdown rendering of `service_description` is behind the `[features] markdown_descriptions` flag in `config/site.toml`. It involves no data migration and stored rows are never rewritten; the flag only changes how descriptions are displayed. Enable it in production in this order:

1. **Deploy with the flag off** (`markdown_descriptions = false`, the default). Descriptions are still shown as plain, autoescaped text, but this release changes the following regardless of the flag:
    - **How new descriptions are stored.** The web form no longer HTML-escapes or bleach-cleans the description; it is stored as typed, as the API and the admin already did (only line endings, Unicode normalisation and leading/trailing whitespace are normalised). `x > 5` is stored as `x > 5`, not `x &gt; 5`, and text such as `<b>bold</b>` or `List<String>` is kept instead of being removed. Every page escapes or sanitizes it on output, so such text is shown literally: with the flag off, a new description containing `<b>bold</b>` shows that text where the old form would have saved `bold`.
    - **The raw `service_description`** in the API and in the CSV/JSON exports therefore holds the text as typed for rows saved from now on, including `<`, `>`, `&` and tag-like text, instead of HTML entities (rows created through the API could already contain such text). It is plain text and must be HTML-escaped wherever it is inserted into a page. **Before deploying, check every known consumer of the API or the exports** that inserts the raw field into a web page: it should escape it or use `service_description_html`, which is always safe HTML.
    - **Legacy entities are decoded on display**: rows saved by the old web form no longer show literal entities such as `&gt;` in the catalogue and emails (see the note below).
    - **API**: the detail, create and update responses gain a read-only `service_description_html` field.
    - **Line endings** are stored as LF and a line break counts as one character everywhere; re-saving an approved service on the web edit form without changes no longer resets it to "submitted" when its stored description has different line endings from the browser's (for example a row created through the API).
    - **Plain-text emails** are no longer HTML-escaped (`Bob's` used to arrive as `Bob&#x27;s`). Other fields saved by the web form still hold entities and show them (`R&amp;D`).
    - **CSV export**: every cell that starts with `=`, `+`, `-`, `@`, a tab or a carriage return gets a leading `'`, including descriptions starting with a Markdown bullet (`'- item`) and phone numbers such as `'+49 ...`.
2. **Run the audit** against production:
   ```bash
   docker compose exec web python manage.py audit_markdown_descriptions --csv /tmp/md-audit.csv
   ```
   It is read-only and exits non-zero while any row is flagged. Details are in [The description audit](#the-description-audit) below.
3. **Fix flagged rows in the admin** change form. Admin edits do not reset the submission status. Re-run the audit until it reports `0 row(s) flagged.`, or until the remaining rows are acceptable as rendered.
4. **Enable the flag**: set `markdown_descriptions = true` in the bind-mounted `site.toml`.
5. **Restart** so the web, worker and beat processes reload it:
   ```bash
   docker compose restart web worker beat
   ```
6. **Spot-check**: the catalogue list view shows formatted descriptions, cards show plain-text excerpts, the **Write** | **Preview** editor on the forms renders a preview, and `GET /api/v1/submissions/{id}/` returns rendered HTML in `service_description_html`.

### The description audit

```bash
docker compose exec web python manage.py audit_markdown_descriptions
docker compose exec web python manage.py audit_markdown_descriptions --csv /tmp/md-audit.csv
```

The command is read-only (a single query, no database or cache writes). Each flagged row is printed with its reasons and a `today:` / `markdown:` preview of the visible text. For long descriptions both previews are cut to about 200 characters around the first place where the two texts differ, with `…` marking the cut, so the change is visible even deep inside the text:

| Reason            | Meaning                                                                                                         |
| ----------------- | --------------------------------------------------------------------------------------------------------------- |
| `text_changed`    | The visible text would differ once rendered (e.g. `*` or `_` pairs become emphasis, a line starting with `# ` (hash and space) becomes a heading and loses the `#`, a line starting with `- ` becomes a list item even directly under a line of text). A paragraph starting `1990.` becomes a list numbered from 1990, but its visible text is unchanged, so it is not flagged |
| `content_removed` | Sanitization would drop content: code, an image, a horizontal rule, lists nested more than nine levels deep (or quotes about twenty), or a link that is not a full `https://`, `http://` or `mailto:` address (a blocked scheme such as `javascript:`, or a relative link such as `/about`) |

Legacy rows containing HTML entities such as `&gt;` are not flagged, because every surface already displays them as the intended characters. Headings are supported, so a `#` heading is reported only as `text_changed`. The command ends with `N row(s) flagged.` and, when any row is flagged, exits non-zero with `Flagged rows found; fix them in the admin before enabling markdown_descriptions.`, so it can gate a deployment script. `--csv PATH` also writes `id,service_name,status,reasons` for the flagged rows, with the same spreadsheet formula guard as the CSV export (see the [Admin Guide](admin-guide.md#exporting-submissions)).

**Rollback:** set `markdown_descriptions = false` and restart the same services. Display returns to plain text immediately; no data changes are needed.

**Legacy entities and API consumers:** rows saved before this release may contain HTML entities such as `&gt;` or `&amp;` in the raw `service_description`, because the old web form HTML-escaped its input. Rows saved from this release on hold the text as typed. Stored values are never rewritten; instead every display surface (catalogue list and cards, `service_description_html`, notification emails) decodes those entities exactly once in both flag states, so both kinds of row display correctly. Source views (the admin textarea and change history, the raw API field and the CSV/JSON exports) show the stored value. API consumers that display descriptions should therefore prefer `service_description_html` over the raw field.

**Parser:** descriptions are parsed as CommonMark (`markdown-it-py`) with a nesting cap; for the slow-to-parse inputs tested, render time grows linearly with input size. Compared with plain text, a line starting with `# ` (hash and space) becomes a heading, a `- ` or `1. ` line becomes a list even directly under a line of text, and a list may start at another number (`2024. Launched` keeps `2024`). The audit reports every row whose display changes.

Rendered HTML is cached per submission (keyed on its `updated_at`) for 24 hours. The key also contains an automatic fingerprint of the rendering code and of the installed markdown-it-py, mdurl and bleach versions, so a release that changes the rules or upgrades those libraries never serves HTML cached under the old ones; no manual step is needed.

---

## EDAM Ontology Releases

New EDAM releases are a data-only change and need no release: see [New EDAM releases](operator/data-sync.md#new-edam-releases).
