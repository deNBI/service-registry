"""
HTTP helpers shared by the web-form views and the REST API views.

Both layers need to record an identical ``submission_ip`` and
``user_agent_hash`` for every inbound request. Keeping the logic in one
place ensures the header-priority order stays in sync with the nginx
configuration (``AXES_IPWARE_META_PRECEDENCE_ORDER``).
"""

from __future__ import annotations

import hashlib

from django.http import HttpRequest


def get_client_ip(request: HttpRequest) -> str:
    """
    Extract the real client IP when Django sits behind a reverse proxy.

    Priority order (matches AXES_IPWARE_META_PRECEDENCE_ORDER in settings):
      1. X-Real-IP       — set by nginx to $remote_addr; single value,
                           not spoofable by downstream clients
      2. X-Forwarded-For — leftmost entry is the originating client;
                           may contain multiple comma-separated hops
      3. REMOTE_ADDR     — the TCP-connecting IP (nginx's own IP in a
                           two-server setup; used as last resort)
    """
    real_ip = request.META.get("HTTP_X_REAL_IP", "").strip()
    if real_ip:
        return real_ip
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "").strip()
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def hash_user_agent(request: HttpRequest) -> str:
    """
    Return the SHA-256 hex digest of the raw User-Agent header.

    The raw UA string is never stored; only the hash is persisted in
    ``user_agent_hash`` for bot-pattern detection.
    """
    ua = request.META.get("HTTP_USER_AGENT", "")
    return hashlib.sha256(ua.encode("utf-8")).hexdigest()
