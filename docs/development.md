---
icon: material/code-braces
---

# Development Setup

## Quick start

Everything you need to go from a fresh clone to a running local stack.

### 1. Prerequisites

| Tool | Minimum version | Install |
|---|---|---|
| Docker Engine + Compose | 24 / v2 | [docs.docker.com](https://docs.docker.com/engine/install/) |
| Git | any | system package |
| Conda / Miniforge | any | [github.com/conda-forge/miniforge](https://github.com/conda-forge/miniforge) — only needed for local Python work (tests, linting) without Docker |

### 2. Clone and configure

```bash
git clone https://github.com/denbi/service-registry
cd service-registry
cp .env.example .env
```

Open `.env` and set at minimum:

```bash
SECRET_KEY=any-long-random-string    # generate with: python -c "import secrets; print(secrets.token_hex(50))"
DB_PASSWORD=devpassword
REDIS_PASSWORD=devpassword
```

All other values in `.env.example` have safe defaults for local development.

### 3. Build and start

```bash
make build    # builds Docker images from scratch (no cache)
make dev      # starts web + worker + beat + db + redis
```

!!! info "Migrations run automatically"
    The container entrypoint runs `manage.py migrate` before starting. On a fresh database this also auto-seeds the EDAM ontology (~3 400 terms, ~30 s). No manual migrate step needed.

### 4. Create a superuser

```bash
make superuser
```

### 5. Access the app

| URL | What |
|---|---|
| http://localhost:8000 | Public registration form |
| http://localhost:8000/admin-denbi/ | Admin portal (superuser login) |
| http://localhost:8000/api/docs/ | Interactive API docs (Swagger UI) |
| http://localhost:8000/api/redoc/ | ReDoc API reference |

---

## Day-to-day workflow

### Starting and stopping

```bash
make dev          # start stack (migrations run automatically on first start)
make dev-down     # stop stack (volumes preserved — data survives)
make logs         # tail all service logs
```

### After changing a model

Migrations must be generated **locally** (not inside the container) because the container's non-root user cannot write migration files back to the bind-mounted source tree:

```bash
# 1. Generate the migration file (runs in your local conda env)
make makemigrations

# 2. Apply it to the running dev database
make migrate
```

Commit the generated migration file alongside your model changes.

### Full clean reset

Wipes all containers, volumes, and data then rebuilds from scratch:

```bash
make nuke
```

Use this when you want a guaranteed clean state — e.g. after pulling migrations that conflict with your local DB, or when debugging a migration issue.

### Running the test suite

Tests use SQLite in-memory and a local-memory cache — no Docker or external services required:

```bash
make test          # pytest — must stay ≥ 80% coverage
make test-cov      # pytest + HTML report → open htmlcov/index.html
```

Or activate the conda environment first and run pytest directly:

```bash
conda activate denbi-registry
pytest tests/ -v --tb=short
```

### Linting and formatting

```bash
make lint          # ruff check + format check (read-only)
make lint-fix      # auto-fix all fixable issues
make audit         # pip-audit against production requirements
make typecheck     # mypy
```

---

## Make targets reference

**Development**

| Target | What it does |
|---|---|
| `make build` | Rebuild all Docker images with `--no-cache` |
| `make dev` | Start full dev stack (web + worker + beat + db + redis) |
| `make dev-down` | Stop the dev stack (data preserved) |
| `make logs` | Tail all dev stack logs |
| `make migrate` | Run pending migrations in the running `web` container |
| `make makemigrations` | Generate new migration files locally (needed after model changes) |
| `make superuser` | Create a Django superuser |
| `make shell` | Open Django `shell_plus` in the `web` container |
| `make collectstatic` | Collect static files into the container |

**Testing and quality** (requires `pip install -r requirements/development.txt`)

| Target | What it does |
|---|---|
| `make test` | pytest with SQLite in-memory — no Docker needed |
| `make test-cov` | pytest + HTML coverage report (`htmlcov/`) |
| `make lint` | ruff check + format check |
| `make lint-fix` | Auto-fix ruff lint and formatting issues |
| `make audit` | `pip-audit` against production requirements |
| `make typecheck` | Run mypy type checker |
| `make dead-code` | `vulture` dead-code detection (unused functions, variables) |
| `make security` | `bandit` SAST security scan (medium + high severity) |

**Documentation**

| Target | What it does |
|---|---|
| `make docs` | Serve MkDocs locally at http://127.0.0.1:8001 |
| `make docs-build` | Build static MkDocs site into `site/` (`--strict`) |

**Production**

| Target | What it does |
|---|---|
| `make prod-up` | Start production stack (compose + prod overlay) |
| `make prod-down` | Stop production stack |
| `make prod-migrate` | Run migrations in the production web container |
| `make prod-logs` | Tail production logs |

**Cleanup**

| Target | What it does |
|---|---|
| `make clean` | Stop containers + remove all volumes — **permanently deletes DB data**, prompts for confirmation |
| `make nuke` | Full reset: `clean` → `build` → `dev` → wait for migrations — one command to a fresh working stack |

---

## Conda environment (for local Python work)

The conda environment is used for tests, linting, and generating migrations — tasks where you want a fast feedback loop without Docker.

```bash
conda create -n denbi-registry python=3.12
conda activate denbi-registry
pip install -r requirements/development.txt
```

Point Django at the test settings for anything that needs Django but not a real database:

```bash
export DJANGO_SETTINGS_MODULE=config.settings_test
export SECRET_KEY=any-value
export DB_PASSWORD=any-value
export REDIS_PASSWORD=any-value
```

---

## Project layout

```
denbi_service_registry/
├── apps/
│   ├── api/          — DRF viewsets, serializers, authentication
│   ├── biotools/     — bio.tools HTTP client, sync, signal, Celery tasks
│   ├── edam/         — EDAM ontology model, sync management command
│   ├── registry/     — Reference data (PIs, categories, service centres)
│   └── submissions/  — Core model, registration form, views, admin
├── config/
│   ├── settings.py       — Main Django settings
│   ├── settings_test.py  — Test overrides (SQLite, no Redis)
│   ├── celery.py         — Celery app definition
│   └── site.toml         — Non-secret site configuration
├── docs/             — MkDocs documentation source
├── nginx/host/       — Host nginx vhost configuration
├── requirements/     — base.txt, production.txt, development.txt
├── scripts/
│   └── entrypoint.sh — Docker entrypoint: runs migrate before CMD
├── static/           — Vendored static assets (Bootstrap, HTMX, Tom-Select, swagger-ui, redoc)
├── templates/        — Django HTML templates
└── tests/            — pytest test suite
```

---

## Vendored static assets

All third-party CSS and JavaScript is downloaded once and committed to `static/`. No CDN is contacted at runtime. This is a hard requirement for GDPR compliance — browser requests to jsDelivr, Google Fonts, unpkg, or any other CDN would constitute data transfers to third parties without user consent.

### Current inventory

| Asset | Version | Location | Used by |
|---|---|---|---|
| Bootstrap | 5.3.3 | `static/css/bootstrap.min.css`, `static/js/bootstrap.bundle.min.js` | All pages |
| HTMX | 1.9.12 | `static/js/htmx.min.js` | bio.tools prefill |
| Tom-Select | 2.3.1 | `static/css/tom-select.bootstrap5.min.css`, `static/js/tom-select.complete.min.js` | EDAM multi-select widget |
| ALTCHA | 2.3.0 | `static/js/altcha.min.js` | Registration and edit forms (CAPTCHA widget) |
| swagger-ui-dist | 5.18.2 | `static/swagger-ui/` (4 files) | `/api/docs/` |
| ReDoc | 2.2.0 | `static/redoc/bundles/redoc.standalone.js` | `/api/redoc/` |
| de.NBI favicon | — | `static/img/favicon.ico` | All pages, admin |

### Updating a library

=== "Bootstrap"

    ```bash
    VERSION=5.3.4
    BASE=https://cdn.jsdelivr.net/npm/bootstrap@${VERSION}/dist
    curl -sSfL ${BASE}/css/bootstrap.min.css -o static/css/bootstrap.min.css
    curl -sSfL ${BASE}/js/bootstrap.bundle.min.js -o static/js/bootstrap.bundle.min.js
    ```

=== "HTMX"

    ```bash
    VERSION=1.9.13
    curl -sSfL https://unpkg.com/htmx.org@${VERSION}/dist/htmx.min.js -o static/js/htmx.min.js
    ```

=== "Tom-Select"

    ```bash
    VERSION=2.4.1
    BASE=https://cdn.jsdelivr.net/npm/tom-select@${VERSION}/dist
    curl -sSfL ${BASE}/css/tom-select.bootstrap5.min.css -o static/css/tom-select.bootstrap5.min.css
    curl -sSfL ${BASE}/js/tom-select.complete.min.js -o static/js/tom-select.complete.min.js
    ```

=== "swagger-ui-dist"

    ```bash
    VERSION=5.18.3
    BASE=https://cdn.jsdelivr.net/npm/swagger-ui-dist@${VERSION}
    for f in swagger-ui.css swagger-ui-bundle.js swagger-ui-standalone-preset.js favicon-32x32.png; do
        curl -sSfL ${BASE}/${f} -o static/swagger-ui/${f}
    done
    ```

    Then update the version comment in `config/settings.py`.

=== "ReDoc"

    ```bash
    VERSION=2.2.0
    curl -sSfL https://cdn.jsdelivr.net/npm/redoc@${VERSION}/bundles/redoc.standalone.js \
        -o static/redoc/bundles/redoc.standalone.js
    ```

=== "ALTCHA"

    ```bash
    VERSION=2.3.0
    curl -sSfL https://cdn.jsdelivr.net/gh/altcha-org/altcha@v${VERSION}/dist/altcha.min.js \
        -o static/js/altcha.min.js
    ```

    The version comment at the top of the downloaded file confirms which release was
    fetched.  Update the version entry in the inventory table above when upgrading.

### Can I use an external URL instead of vendoring?

| Asset type | External URL OK? | Notes |
|---|---|---|
| **Logo** (`logo_url` in `site.toml`) | Yes | CSP `img-src` is built dynamically from this URL |
| **Favicon** (`favicon_url` in `site.toml`) | Yes | Same dynamic CSP behaviour |
| **JS/CSS frameworks** | **No** | Would make browser requests to third-party CDNs — GDPR violation |
| **Swagger UI / ReDoc** | **No** | drf-spectacular defaults to jsDelivr; we override to `/static/`. Do not revert |

### Checking for CDN leakage

Open browser DevTools → Network tab. All requests should resolve to `localhost` in dev or your own domain in prod. Any CDN request will also violate `default-src 'self'` and appear as a blocked request in the browser console.

---

## Custom template tags

Custom template tags and filters live in
`apps/submissions/templatetags/registry_tags.py` and are loaded in templates with
`{% load registry_tags %}`.

### Available filters

#### `linkify_description`

Renders a section description string from `form_texts.yaml` as safe HTML.
Use this filter (not Django's built-in `urlize`) for all section description output.

| Input syntax | Output |
|---|---|
| `[link text](https://example.com)` | `<a href="https://example.com">link text</a>` |
| `https://example.com` | auto-linked anchor |
| Blank line (`\n\n`) | paragraph break — wraps each block in `<p>` |
| Single newline (`\n`, using YAML `\|` block) | `<br>` |
| Raw `<html>` | escaped — never rendered as markup |

Only `http://` and `https://` schemes are accepted for links. `javascript:` and other
schemes in `[text](...)` syntax are not matched and pass through as escaped plain text.

**Template usage:**

```django
{% load registry_tags %}
<div class="section-description">{{ desc|linkify_description }}</div>
```

**Extending or testing the filter:**

The filter is unit-tested in `tests/test_template_tags.py` (`TestLinkifyDescriptionFilter`).
Add a new test there whenever you extend the filter's behaviour.
Integration tests that render `form_body.html` or `register.html` with patched YAML
data live in `tests/test_forms.py` (`TestSectionDescriptionsYAML`).

**Styling:**

`.section-description` is styled in `static/css/registry.css` as a light tinted callout
box — subtle `rgba(0,0,0,0.03)` background with rounded corners and muted text — to
visually distinguish it from form input labels and fields. If you change the style, keep
the distinction clear: the description is contextual guidance, not an actionable form
element.

---

## Logo upload security pipeline

Uploaded service logos pass through `apps/submissions/logo_utils.py` before being
stored. Understanding this module is useful when modifying file upload handling.

### Entry point

```python
from apps.submissions.logo_utils import validate_and_process_logo

result = validate_and_process_logo(file_obj)  # → InMemoryUploadedFile
```

### Processing steps (in order)

| Step | What happens |
|------|-------------|
| Size check | Raises `ValidationError` if file exceeds `settings.LOGO_MAX_BYTES` (configurable in `site.toml`) |
| Magic-byte detection | Reads first bytes to determine type — never trusts file extension or MIME header |
| JPEG / PNG | Re-encoded via Pillow: strips EXIF metadata, verifies image integrity |
| SVG | Parsed by stdlib `xml.etree.ElementTree` (safe on Python 3.12+/Expat 2.7.1, which blocks XXE and entity-expansion attacks), then scrubbed of `<script>` elements, `on*` event-handler attributes, non-fragment `href`/`src` URLs |
| UUID filename | Original filename is discarded; `_logo_upload_to()` in `models.py` assigns `logos/<uuid4>.<ext>` |

### Known limitation

CSS-based side-channels in SVG (e.g. `url()` inside `<style>` tags) are not fully
mitigated. If stricter guarantees are needed, reject SVG entirely or render to raster
via `cairosvg` before storage.

### Adding a new allowed format

1. Add magic-byte detection to `_sniff_type()` — return a new type string
2. Add a processing function (strip metadata, verify integrity)
3. Add the new branch to `validate_and_process_logo()`
4. Add tests to `tests/test_logo_utils.py`

### Media files in development

In development (`docker-compose.yml`), the project root is bind-mounted as
`.:/app`. Uploaded files land in `mediafiles/logos/` inside the container,
which maps to `<project-root>/mediafiles/` on your host. The directory is
listed in `.gitignore` — do not commit uploaded logos.

### Media files in tests

`config/settings_test.py` overrides `MEDIA_ROOT` to a temporary directory
(`tempfile.mkdtemp()`), so test file uploads never touch the project's
`mediafiles/` directory. The temp directory is cleaned up by the OS after
the test process exits. Tests that assert on specific file URLs should use
the `settings` + `tmp_path` fixtures to set a deterministic `MEDIA_ROOT`
(see `tests/test_api.py → TestLogoUpload` for the pattern).

---

### Available simple tags

| Tag | Purpose |
|---|---|
| `{% site_logo_url %}` | Returns logo URL from `site.toml`, or empty string |
| `{% site_favicon_url %}` | Returns favicon URL (auto-detects `static/img/favicon.*` as fallback) |
| `{% site_setting section key %}` | Generic accessor for any `site.toml` value |

---

## Adding a feature

1. Create a branch: `git checkout -b feature/my-feature`
2. Make changes; add or update tests
3. `make lint-fix && make test` — lint and coverage must pass
4. Open a pull request against `main`
