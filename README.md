# de.NBI Service Registration Platform

Registration Platform for de.NBI & ELIXIR-DE services — structured form, REST API, EDAM annotations, bio.tools integration, and admin review portal.

## Quick start

```bash
cp .env.example .env          # set SECRET_KEY, DB_PASSWORD, REDIS_PASSWORD; for local http also
                              # DEBUG=true, SECURE_SSL_REDIRECT=false, SESSION_COOKIE_SECURE=false, CSRF_COOKIE_SECURE=false
docker compose up -d          # migrations and EDAM/SPDX seeding run on first start
docker compose exec web python manage.py createsuperuser
# → http://localhost:8000
```

## Key commands

```bash
make test          # run test suite (coverage ≥ 80% required)
make lint          # ruff lint + format check
make lint-fix      # auto-fix lint issues
make docs          # serve MkDocs at http://127.0.0.1:8001
make migrate       # run migrations in web container
make logs          # tail the stack logs
```

## Production

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Host nginx (`nginx/host/service-registry.bi.denbi.de.conf`) terminates TLS and proxies to Gunicorn. In production PostgreSQL runs externally; Redis runs as a Docker container. The live deployment is managed with Ansible.

## Documentation

Full docs at `make docs` or in `docs/`:

|                                          |                                         |
| ---------------------------------------- | --------------------------------------- |
| [User guide](docs/user-guide.md)         | Registering and updating a service      |
| [Using the API](docs/api-guide.md)       | API for service owners (own submission) |
| [Admin guide](docs/admin-guide.md)       | Review, approve, export, reference data |
| [Managing API keys](docs/admin/api-keys.md) · [Admin REST API](docs/admin/api.md) | Submission and Admin API keys, admin endpoints |
| [Operator guide](docs/operator/index.md) | E-mail, data sync, operations, security, backups, troubleshooting |
| [Development setup](docs/development.md) | Local env, make targets, project layout |
| [Configuration](docs/configuration.md)   | `site.toml` and `.env` reference        |
| [Deployment](docs/deployment.md)         | Production setup, TLS, uploads          |
| [Architecture](docs/architecture.md)     | System design, data flow, security      |
| [Testing](docs/testing.md)               | Test suite, coverage, writing tests     |
| [Releases and rollback](docs/rollout.md) | Building images, deploying, rollback    |

Interactive API docs: `/api/docs/` (Swagger UI) · `/api/redoc/` (ReDoc)

## Stack

Python 3.12 · Django 6 · DRF · PostgreSQL · Redis · Celery · Bootstrap 5 · HTMX · Docker

## License

MIT
