---
icon: material/home
---

# de.NBI Service Registration Platform

<div class="hero" markdown>

A registration platform for de.NBI and ELIXIR-DE bioinformatics services, from submission through review to the public catalogue.

</div>

## Start here

<div class="grid cards" markdown>

- :material-account: **I register or maintain a service**

    Register a service, edit it with your API key, format the description, upload a logo.

    [User Guide](user-guide.md) · [Using the API](api-guide.md)

- :material-shield-account: **I review submissions**

    Approve and reject submissions, manage reference data, API keys and exports in the Django admin.

    [Admin Guide](admin-guide.md) · [API keys](admin/api-keys.md) · [Admin REST API](admin/api.md)

- :material-server: **I deploy and run the registry**

    Install, configure, monitor, back up and update a deployment.

    [Operator Guide](operator/index.md) · [Deployment](deployment.md) · [Configuration](configuration.md)

- :material-code-braces: **I develop the registry**

    Local setup, architecture, tests and internals.

    [Development Setup](development.md) · [Architecture](architecture.md)

</div>

---

## What it does

<div class="grid cards" markdown>

- :material-form-select: **Registration form**

    Sections for service identity, contacts, links, EDAM annotations, publications, licences and KPIs, with inline field validation and a browser draft that survives closing the tab.

- :material-tools: **bio.tools prefill**

    Enter a bio.tools URL to fill form fields from the bio.tools entry.

- :material-tag-multiple: **EDAM and SPDX**

    Tag services with EDAM topics and operations and pick licences from the SPDX list. Both lists are synced from their sources on a schedule.

- :material-language-markdown: **Markdown descriptions**

    Optional (feature flag): descriptions with a small set of Markdown and a Write / Preview editor.

- :material-view-grid: **Registry Catalogue**

    Optional (feature flag): a public page to search, filter, sort and group approved services.

- :material-api: **REST API**

    Register a service, then read and update it with its API key. Registry staff use Admin API keys for listing and reference data. Interactive docs at `/api/docs/` (Swagger UI) and `/api/redoc/` (ReDoc); OpenAPI schema at `/api/schema/`.

- :material-shield-check: **Admin portal**

    Review, approve, reject and export submissions, with role groups, bulk actions, a change log for every edit and an audit record of deletions.

- :material-image: **Service logos**

    PNG, JPEG or SVG logos via the forms, the API or the admin. PNG/JPEG are re-encoded without metadata; SVGs are reduced to standard drawing content.

</div>

---

## Quick start

=== ":material-laptop: Development"

    ```bash
    git clone https://github.com/deNBI/service-registry
    cd service-registry
    cp .env.example .env      # set SECRET_KEY, DB_PASSWORD, REDIS_PASSWORD; for http://localhost also
                              # DEBUG=true and the three *_SECURE/SSL settings (see Development Setup)
    make build                # build the images
    make dev                  # start the stack; migrations and EDAM/SPDX seeding run on first start
    make superuser
    ```

    App at **http://localhost:8000**. See [Development Setup](development.md) for the full local guide.

=== ":material-server: Production"

    ```bash
    cp .env.example .env      # configure all production values
    docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
    docker compose -f docker-compose.yml -f docker-compose.prod.yml exec web python manage.py createsuperuser
    ```

    This overlay is for staging and for validating the production image; it still starts the bundled `db` container. Migrations run automatically on container start and static files are built into the image. The live de.NBI deployment is managed with Ansible. See [Deployment](deployment.md) for the full guide.

---

## Stack

| Layer          | Technology                              |
| -------------- | --------------------------------------- |
| Language       | Python 3.12                             |
| Framework      | Django                                  |
| API            | Django REST Framework + drf-spectacular |
| Database       | PostgreSQL                              |
| Cache / Broker | Redis                                   |
| Task Queue     | Celery + Celery Beat                    |
| Static Files   | WhiteNoise                              |
| Frontend       | Bootstrap 5 + HTMX + Tom Select         |
| Container      | Docker + Docker Compose                 |
| Reverse Proxy  | Nginx (host-managed)                    |
