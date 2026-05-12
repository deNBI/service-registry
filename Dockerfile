# =============================================================================
# de.NBI Service Registration Platform — Dockerfile
# =============================================================================
# Multi-stage build:
#   builder  : installs Python dependencies into a virtual env
#   runtime  : minimal image; copies only the venv and application code
#
# The same image is used for the web, worker, and beat services.
# The CMD is overridden per-service in docker-compose.yml.
# =============================================================================

# ---- builder stage ----
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Create a virtual environment to keep the runtime image clean
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Python dependencies
COPY requirements/ requirements/
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements/production.txt

# ---- runtime stage ----
FROM python:3.12-slim AS runtime

# Install runtime-only system deps (libpq for psycopg)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user — containers must never run as root
RUN groupadd -r django && useradd -r -g django django

WORKDIR /app

# Copy virtual env from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy application code
COPY --chown=django:django config/ config/
COPY --chown=django:django apps/ apps/
COPY --chown=django:django templates/ templates/
COPY --chown=django:django static/ static/
COPY --chown=django:django manage.py manage.py
COPY scripts/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Create static file and celery beat directories with correct ownership
RUN mkdir -p staticfiles mediafiles /var/run/celerybeat \
    && chown -R django:django staticfiles mediafiles /var/run/celerybeat

# # Collect static files
# RUN python manage.py collectstatic --noinput || true

# ---------------------------------------------------------------------------
# Run collectstatic at build time so static files are baked into the image.
#
# collectstatic only reads settings — it does not touch the database or Redis.
# However settings.py declares SECRET_KEY, DB_PASSWORD, and REDIS_PASSWORD
# as required=True, so Django will refuse to start without them even for a
# management command that doesn't use them.
#
# We inject throwaway build-time values via ARG so the real secrets are never
# written into any image layer. ARG values are not persisted after the build.
# ---------------------------------------------------------------------------
ARG BUILD_SECRET_KEY="build-time-only-not-a-real-secret-key-do-not-use"
ARG BUILD_DB_PASSWORD="build-time-placeholder"
ARG BUILD_REDIS_PASSWORD="build-time-placeholder"

RUN SECRET_KEY="${BUILD_SECRET_KEY}" \
    DB_PASSWORD="${BUILD_DB_PASSWORD}" \
    REDIS_PASSWORD="${BUILD_REDIS_PASSWORD}" \
    python manage.py collectstatic --noinput

USER django

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# Expose Gunicorn port (Nginx proxies to this internally)
EXPOSE 8000

# Health check used by Docker
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/live/')"

# Default command — overridden per service in docker-compose
CMD ["gunicorn", "config.wsgi:application", \
    "--bind", "0.0.0.0:8000", \
    "--workers", "4", \
    "--worker-class", "sync", \
    "--timeout", "60", \
    "--access-logfile", "-", \
    "--error-logfile", "-"]
