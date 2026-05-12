# de.NBI Service Registration Platform

Registration Platform for de.NBI & ELIXIR-DE services — structured form, REST API, EDAM annotations, bio.tools integration, and admin review portal.

## Quick start

```bash
cp .env.example .env          # set SECRET_KEY, DB_PASSWORD, REDIS_PASSWORD
docker compose up -d
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
docker compose exec web python manage.py sync_edam
# → http://localhost:8000
```

## Key commands

```bash
make test          # run test suite (coverage ≥ 80% required)
make lint          # ruff lint + format check
make lint-fix      # auto-fix lint issues
make docs          # serve MkDocs at http://127.0.0.1:8001
make migrate       # run migrations in web container
make sync-edam     # refresh EDAM ontology
```

## Production

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Host nginx (`nginx/host/service-registry.bi.denbi.de.conf`) terminates TLS and proxies to Gunicorn. PostgreSQL and Redis run externally — not in Docker.

## Documentation

Full docs at `make docs` or in `docs/`:

|                                          |                                         |
| ---------------------------------------- | --------------------------------------- |
| [Development setup](docs/development.md) | Local env, make targets, project layout |
| [Configuration](docs/configuration.md)   | `site.toml` and `.env` reference        |
| [Deployment](docs/deployment.md)         | Production setup, TLS, backups          |
| [Architecture](docs/architecture.md)     | System design, data flow, security      |
| [User guide](docs/user-guide.md)         | Registration form, API keys             |
| [Admin guide](docs/admin-guide.md)       | Review, approve, export                 |
| [API reference](docs/api-guide.md)       | REST endpoints, auth, examples          |
| [Testing](docs/testing.md)               | Test suite, coverage, writing tests     |
| [Rollout](docs/rollout.md)               | Releases, CI, rollback runbook          |

Interactive API docs: `/api/docs/` (Swagger UI) · `/api/redoc/` (ReDoc)

## Stack

Python 3.12 · Django 6 · DRF · PostgreSQL · Redis · Celery · Bootstrap 5 · HTMX · Docker

## License

MIT
