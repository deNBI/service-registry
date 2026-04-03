"""
Health Check URLs and Views
============================
Two endpoints used by Docker health checks and load balancers:

  GET /health/live/   — Returns 200 if the Django process is alive.
                        Does not check DB or Redis. Used by Docker HEALTHCHECK.

  GET /health/ready/  — Returns 200 only if DB and Redis are reachable.
                        Used by orchestrators before routing traffic.
"""

import logging

from django.db import connection, OperationalError
from django.http import JsonResponse
from django.urls import path

logger = logging.getLogger(__name__)


def liveness(request):
    """Process is alive — no external dependency checks."""
    return JsonResponse({"status": "ok"})


def readiness(request):
    """
    Check DB and Redis connectivity before declaring ready.

    Returns HTTP 200 (ready) or HTTP 503 (not ready).  The response body
    contains only a top-level status string — never the per-service breakdown.
    Detailed check results are logged server-side so ops can diagnose failures
    without exposing internal service topology to unauthenticated callers.
    """
    checks = {}

    # Database check
    try:
        connection.ensure_connection()
        checks["database"] = "ok"
    except OperationalError as e:
        logger.error("Readiness: database check failed: %s", e)
        checks["database"] = "error"

    # Redis check
    try:
        from django.core.cache import cache

        cache.set("_health_check", "1", timeout=5)
        val = cache.get("_health_check")
        checks["redis"] = "ok" if val == "1" else "error"
    except Exception as e:
        logger.error("Readiness: Redis check failed: %s", e)
        checks["redis"] = "error"

    all_ok = all(v == "ok" for v in checks.values())
    if not all_ok:
        failed = [svc for svc, result in checks.items() if result != "ok"]
        logger.error("Readiness: failing checks: %s", ", ".join(failed))

    # Return only the top-level status — never the per-service breakdown.
    # The HTTP status code (200/503) is sufficient for orchestrators; the
    # detailed breakdown would reveal internal service topology to scanners.
    http_status = 200 if all_ok else 503
    return JsonResponse({"status": "ok" if all_ok else "degraded"}, status=http_status)


urlpatterns = [
    path("live/", liveness, name="health-live"),
    path("ready/", readiness, name="health-ready"),
]
