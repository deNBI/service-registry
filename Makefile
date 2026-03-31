# =============================================================================
# de.NBI Service Registry — Developer Makefile
# =============================================================================
# Run `make` or `make help` to see available targets.
# =============================================================================

.PHONY: help build dev dev-down logs migrate makemigrations superuser shell \
        test test-cov lint lint-fix audit typecheck collectstatic \
        docs docs-build prod-up prod-down prod-migrate prod-logs clean nuke \
        dead-code security pre-commit

# --- Default -----------------------------------------------------------------

help:
	@echo ""
	@echo "de.NBI Service Registry — make targets"
	@echo ""
	@echo "  Development"
	@echo "    make build            Rebuild images from scratch (no cache)"
	@echo "    make dev              Start full dev stack (web + worker + beat + db + redis)"
	@echo "    make dev-down         Stop dev stack"
	@echo "    make logs             Tail dev stack logs"
	@echo "    make migrate          Run migrations manually (auto-runs on container start)"
	@echo "    make makemigrations   Generate new migration files (runs locally)"
	@echo "    make superuser        Create a Django superuser"
	@echo "    make shell            Open Django shell_plus"
	@echo "    make collectstatic    Collect static files"
	@echo ""
	@echo "  Testing  (no Docker required)"
	@echo "    make test             Run pytest with SQLite in-memory"
	@echo "    make test-cov         Run pytest + HTML coverage report"
	@echo ""
	@echo "  Code quality  (requires: pip install -r requirements/development.txt)"
	@echo "    make lint             ruff check + format check"
	@echo "    make lint-fix         ruff autofix + format"
	@echo "    make audit            pip-audit against production requirements"
	@echo "    make typecheck        mypy type check"
	@echo "    make dead-code        vulture dead-code scan (min confidence 80%)"
	@echo "    make security         bandit SAST scan (low+ severity)"
	@echo "    make pre-commit       ruff format-fix + full test suite + docs strict build"
	@echo ""
	@echo "  Documentation"
	@echo "    make docs             Serve MkDocs at http://127.0.0.1:8001"
	@echo "    make docs-build       Build static site into site/"
	@echo ""
	@echo "  Production"
	@echo "    make prod-up          Start production stack"
	@echo "    make prod-down        Stop production stack"
	@echo "    make prod-migrate     Run migrations in production web container"
	@echo "    make prod-logs        Tail production logs"
	@echo ""
	@echo "  Cleanup"
	@echo "    make clean            Stop containers + remove volumes (WARNING: deletes DB data)"
	@echo "    make nuke             Full reset: clean + rebuild + migrate (fresh start)"
	@echo ""

# --- Development -------------------------------------------------------------

build:
	docker compose build --no-cache

dev:
	docker compose up -d
	@echo "Dev stack running → http://localhost:8000"

dev-down:
	docker compose down

logs:
	docker compose logs -f

migrate:
	docker compose exec web python manage.py migrate

# Run locally so Django can write the new migration files to the source tree.
# The container runs as a non-root user without write access to bind mounts.
makemigrations:
	python manage.py makemigrations

superuser:
	docker compose exec web python manage.py createsuperuser

shell:
	docker compose exec web python manage.py shell_plus

collectstatic:
	docker compose exec web python manage.py collectstatic --noinput

# --- Testing (local — no Docker, no PostgreSQL, no Redis) --------------------

test:
	pytest tests/

test-cov:
	pytest tests/ --cov=apps --cov-report=term-missing --cov-report=html
	@echo "Open htmlcov/index.html to view coverage report."

# --- Code quality (local) ----------------------------------------------------

lint:
	ruff check apps/ config/ tests/
	ruff format --check apps/ config/ tests/

lint-fix:
	ruff check --fix apps/ config/ tests/
	ruff format apps/ config/ tests/

audit:
	pip-audit -r requirements/production.txt

typecheck:
	mypy apps/ config/

dead-code:
	vulture apps/ config/ tests/ --min-confidence 80 \
	  --exclude "apps/submissions/migrations,apps/edam/migrations,apps/registry/migrations,apps/api/migrations"

security:
	bandit -r apps/ config/ -ll -q

pre-commit: lint-fix test docs-build
	@echo ""
	@echo "All pre-commit checks passed. Safe to commit."

# --- Documentation -----------------------------------------------------------

docs:
	mkdocs serve --dev-addr 127.0.0.1:8001

docs-build:
	mkdocs build --strict

# --- Production --------------------------------------------------------------

prod-up:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

prod-down:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml down

prod-migrate:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml exec web python manage.py migrate

prod-logs:
	docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f

# --- Cleanup -----------------------------------------------------------------

clean:
	@echo "WARNING: stops all containers and permanently deletes all volumes including the database."
	@read -p "Are you sure? [y/N] " c && [ "$$c" = "y" ]
	docker compose down -v --remove-orphans

nuke: clean build dev
	@echo "Waiting for container entrypoint to run migrations..."
	@sleep 8
	@echo ""
	@echo "Fresh stack ready → http://localhost:8000"
	@echo "Run 'make superuser' to create an admin account."
