---
icon: material/code-braces
---

# Development Setup

## Quick start

Everything you need to go from a fresh clone to a running local stack.

### 1. Prerequisites

| Tool                    | Minimum version | Install                                                                                                                                          |
| ----------------------- | --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| Docker Engine + Compose | 24 / v2         | [docs.docker.com](https://docs.docker.com/engine/install/)                                                                                       |
| Git                     | any             | system package                                                                                                                                   |
| Conda / Miniforge       | any             | [github.com/conda-forge/miniforge](https://github.com/conda-forge/miniforge) — only needed for local Python work (tests, linting) without Docker |

### 2. Clone and configure

```bash
git clone https://github.com/deNBI/service-registry
cd service-registry
cp .env.example .env
```

Open `.env` and set at minimum:

```bash
SECRET_KEY=any-long-random-string    # generate with: python -c "import secrets; print(secrets.token_hex(50))"
DB_PASSWORD=devpassword
REDIS_PASSWORD=devpassword
```

`.env.example` is written for production. For a local stack over plain `http://localhost` also set:

```bash
DEBUG=true
SECURE_SSL_REDIRECT=false     # otherwise http://localhost:8000 redirects to https
SESSION_COOKIE_SECURE=false   # otherwise the browser does not send the session cookie over http
CSRF_COOKIE_SECURE=false      # otherwise form posts fail the CSRF check over http
```

### 3. Build and start

```bash
make build    # builds Docker images from scratch (no cache)
make dev      # starts web + worker + beat + db + redis
```

!!! info "Migrations run automatically"
    The container entrypoint runs `manage.py migrate` before starting. On a fresh database this also auto-seeds the EDAM ontology (about 3,500 terms, ~30 s). No manual migrate step needed.

### 4. Create a superuser

```bash
make superuser
```

### 5. Access the app

| URL                                | What                              |
| ---------------------------------- | --------------------------------- |
| http://localhost:8000              | Public registration form          |
| http://localhost:8000/admin-denbi/ | Admin portal (superuser login)    |
| http://localhost:8000/api/docs/    | Interactive API docs (Swagger UI) |
| http://localhost:8000/api/redoc/   | ReDoc API reference               |

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

| Target                | What it does                                                      |
| --------------------- | ----------------------------------------------------------------- |
| `make build`          | Rebuild all Docker images with `--no-cache`                       |
| `make dev`            | Start full dev stack (web + worker + beat + db + redis)           |
| `make dev-down`       | Stop the dev stack (data preserved)                               |
| `make logs`           | Tail all dev stack logs                                           |
| `make migrate`        | Run pending migrations in the running `web` container             |
| `make makemigrations` | Generate new migration files locally (needed after model changes) |
| `make superuser`      | Create a Django superuser                                         |
| `make shell`          | Open Django `shell_plus` in the `web` container                   |
| `make collectstatic`  | Collect static files into the container                           |

**Testing and quality** (requires `pip install -r requirements/development.txt`)

| Target           | What it does                                                |
| ---------------- | ----------------------------------------------------------- |
| `make test`      | pytest with SQLite in-memory — no Docker needed             |
| `make test-cov`  | pytest + HTML coverage report (`htmlcov/`)                  |
| `make lint`      | ruff check + format check                                   |
| `make lint-fix`  | Auto-fix ruff lint and formatting issues                    |
| `make audit`     | `pip-audit` against production requirements                 |
| `make typecheck` | Run mypy type checker                                       |
| `make dead-code` | `vulture` dead-code detection (unused functions, variables) |
| `make security`  | `bandit` SAST security scan (medium + high severity)        |
| `make pre-commit`| `lint-fix`, `audit`, `test` and `docs-build` in one go      |

**Documentation**

| Target            | What it does                                       |
| ----------------- | -------------------------------------------------- |
| `make docs`       | Serve MkDocs locally at http://127.0.0.1:8001      |
| `make docs-build` | Build static MkDocs site into `site/` (`--strict`) |

**Production**

| Target              | What it does                                    |
| ------------------- | ----------------------------------------------- |
| `make prod-up`      | Start production stack (compose + prod overlay) |
| `make prod-down`    | Stop production stack                           |
| `make prod-migrate` | Run migrations in the production web container  |
| `make prod-logs`    | Tail production logs                            |

**Cleanup**

| Target       | What it does                                                                                       |
| ------------ | -------------------------------------------------------------------------------------------------- |
| `make clean` | Stop containers + remove all volumes — **permanently deletes DB data**, prompts for confirmation   |
| `make nuke`  | Full reset: `clean` → `build` → `dev` → wait for migrations — one command to a fresh working stack |

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
│   ├── catalogue/    — Public Registry Catalogue (feature-flagged)
│   ├── edam/         — EDAM ontology model, sync management command
│   ├── licenses/     — SPDX licence list model, sync management command
│   ├── registry/     — Reference data (PIs, categories, service centres)
│   └── submissions/  — Core model, registration form, views, admin, diff_utils
├── config/
│   ├── settings.py       — Main Django settings
│   ├── settings_test.py  — Test overrides (SQLite, no Redis)
│   ├── celery.py         — Celery app definition
│   └── site.toml         — Non-secret site configuration
├── docs/             — MkDocs documentation source
├── nginx/host/       — Host nginx vhost configuration
├── requirements/     — base.txt, production.txt, development.txt
├── docker/           — Optional compose overlays (backup, staging, CI)
├── scripts/
│   ├── entrypoint.sh             — Docker entrypoint: migrate + setup_groups before CMD
│   ├── import_reference_data.py  — Bulk import of PIs, categories, centres via the admin API
│   └── submit_test_submission.py — Post one sample registration via the API
├── static/           — Vendored static assets (see Vendored Static Assets)
├── templates/        — Django HTML templates
└── tests/            — pytest test suite
```

---
## Where to read next

How the main parts work inside: [Markdown rendering](developer/markdown.md), [logo upload pipeline](developer/logo-pipeline.md), [change tracking and status reset](developer/lifecycle.md), [frontend, widgets and drafts](developer/frontend.md), [catalogue](developer/catalogue.md), [admin permissions](developer/admin-permissions.md) and [vendored assets](developer/vendored-assets.md).

---

## Adding a feature

1. Create a branch from `main`
2. Make changes; add or update tests (and the docs page for the audience the change affects)
3. `make pre-commit` — lint, audit, tests with coverage and the strict docs build must pass
4. Open a pull request against `main`
