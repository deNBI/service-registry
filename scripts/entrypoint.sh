#!/bin/sh
# =============================================================================
# Docker entrypoint — runs before the container's main CMD.
#
# Applies any pending Django migrations automatically on every container start
# of the web container. Worker and beat containers set SKIP_MIGRATE=true to
# avoid a race condition on a fresh database where concurrent CREATE TABLE
# statements from multiple containers can collide before Django's advisory lock
# is in place.
# =============================================================================

set -e

if [ "${SKIP_MIGRATE:-false}" = "false" ]; then
    echo "[entrypoint] Running database migrations..."
    python manage.py migrate --noinput
    echo "[entrypoint] Migrations done."
    echo "[entrypoint] Syncing admin role groups..."
    python manage.py setup_groups
    echo "[entrypoint] Groups synced."
else
    echo "[entrypoint] Skipping migrations (SKIP_MIGRATE=true)."
fi

exec "$@"
