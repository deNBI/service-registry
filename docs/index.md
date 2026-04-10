---
icon: material/home
---

# de.NBI Service Registry

<div class="hero" markdown>

A structured registration platform for de.NBI and ELIXIR-DE bioinformatics services — from initial submission through review and publication.

[:material-rocket-launch: Get Started](development.md){ .md-button .md-button--primary }
[:material-api: API Reference](api-guide.md){ .md-button }

</div>

---

## What it does

<div class="grid cards" markdown>

- :material-form-select: **Structured registration form**

    Sections for service identity, contact, technical details, EDAM annotations, publications, and KPIs. Validated at every step.

- :material-tools: **bio.tools prefill**

    Paste a bio.tools URL to pre-populate form fields from the bio.tools database automatically.

- :material-tag-multiple: **EDAM ontology integration**

    Tag services with EDAM Topic and Operation terms. Ontology is synced automatically from edamontology.org.

- :material-api: **REST API**

    Full CRUD access with token and API-key authentication. Interactive docs at `/api/docs/` (Swagger UI) and `/api/redoc/` (ReDoc). Machine-readable OpenAPI schema at `/api/schema/`.

- :material-shield-check: **Admin portal**

    Review, approve, reject, and export submissions. Bulk actions, full audit trail, CSV and JSON export.

- :material-key-variant: **Scoped API keys**

    Each submission gets a scoped key for future programmatic updates — read or read+write scope.

- :material-image: **Service logo upload**

    Attach a PNG, JPEG, or SVG logo to any service registration — via the web form, the edit form, or the REST API. Images are automatically sanitised (EXIF stripped, SVG scripts removed) before storage.

</div>

---

## Quick start

=== ":material-laptop: Development"

    ```bash
    git clone https://github.com/denbi/service-registry
    cd service-registry
    cp .env.example .env      # set SECRET_KEY, DB_PASSWORD, REDIS_PASSWORD
    make build                # builds images + starts stack + runs migrations
    make superuser
    ```

    App at **http://localhost:8000** — migrations and EDAM seeding run automatically on first start.
    See [Development Setup](development.md) for the full local guide.

=== ":material-server: Production"

    ```bash
    cp .env.example .env      # configure all production values
    docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
    docker compose exec web python manage.py collectstatic --noinput
    docker compose exec web python manage.py createsuperuser
    ```

    Migrations run automatically on container start. See [Deployment](deployment.md) for the full guide.

---

## Documentation

<div class="grid cards" markdown>

- :material-code-braces: **[Development Setup](development.md)**

    Local environment, conda setup, Make targets, and the development workflow.

- :material-tune: **[Configuration](configuration.md)**

    All `site.toml` and `.env` settings — branding, email, security, rate limits.

- :material-rocket-launch: **[Deployment](deployment.md)**

    Production setup: Docker, TLS, nginx, EDAM seeding, backups, updates.

- :material-account: **[User Guide](user-guide.md)**

    How to register a service, use the update form, and manage API keys.

- :material-shield-account: **[Admin Guide](admin-guide.md)**

    Reviewing submissions, managing EDAM terms, bio.tools records, and email settings.

- :material-api: **[API Reference](api-guide.md)**

    Endpoints, authentication schemes, filters, and curl examples.

- :material-sitemap: **[Architecture](architecture.md)**

    System design, data model, Docker services, request lifecycle, and static assets.

- :material-test-tube: **[Testing](testing.md)**

    Running the test suite, coverage targets, and adding new tests.

- :material-database: **[Database Schema](database-schema.md)**

    Full field-level reference for all models.

- :material-package-up: **[Rollout & Releases](rollout.md)**

    Release process, migrations, rollback, and upgrade runbook.

- :material-puzzle: **[Extending Models](extending/model-changes.md)**

    How to add or change model fields safely.

- :material-plus-box: **[Adding Apps](extending/adding-apps.md)**

    Adding a new Django app with API endpoints.

</div>

---

## Stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| Framework | Django |
| API | Django REST Framework + drf-spectacular |
| Database | PostgreSQL |
| Cache / Broker | Redis |
| Task Queue | Celery + Celery Beat |
| Static Files | WhiteNoise |
| Frontend | Bootstrap 5 + HTMX + Tom Select |
| Container | Docker + Docker Compose |
| Reverse Proxy | Nginx (host-managed) |
