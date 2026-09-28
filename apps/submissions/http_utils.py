"""
HTTP helpers shared by the web-form views and the REST API views.

Both layers need to record an identical ``submission_ip`` and
``user_agent_hash`` for every inbound request. Keeping the logic in one
place ensures the header-priority order stays in sync with the nginx
configuration (``AXES_IPWARE_META_PRECEDENCE_ORDER``).
"""

from __future__ import annotations

import hashlib
import ipaddress

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

    Only a syntactically valid IP address (canonical form, no zone id) is
    returned; an invalid candidate falls through to the next source. Returns
    "" when no source holds a valid address. A malformed value would
    otherwise make django-ratelimit fail (HTTP 500) or be rejected by the
    submission_ip GenericIPAddressField.
    """
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    candidates = (
        request.META.get("HTTP_X_REAL_IP", ""),
        forwarded_for.split(",")[0],
        request.META.get("REMOTE_ADDR", ""),
    )
    for candidate in candidates:
        ip = _valid_ip(candidate)
        if ip:
            return ip
    return ""


def _valid_ip(value: str) -> str:
    """Canonical form of value if it is a plain IPv4/IPv6 address, else ""."""
    try:
        addr = ipaddress.ip_address(value.strip())
    except ValueError:
        return ""
    if getattr(addr, "scope_id", None):  # 'fe80::1%eth0' is not a client IP
        return ""
    return str(addr)


# Rate-limit bucket for the (misconfiguration-only) case of a request with no
# valid address anywhere: throttled together instead of crashing the limiter.
_UNKNOWN_CLIENT_IP = "0.0.0.0"


def get_ratelimit_ip(request: HttpRequest) -> str:
    """django-ratelimit key (settings.RATELIMIT_IP_META_KEY): get_client_ip(),
    or a fixed placeholder so the limiter always receives a parseable IP."""
    return get_client_ip(request) or _UNKNOWN_CLIENT_IP


def hash_user_agent(request: HttpRequest) -> str:
    """
    Return the SHA-256 hex digest of the raw User-Agent header.

    The raw UA string is never stored; only the hash is persisted in
    ``user_agent_hash`` for bot-pattern detection.
    """
    ua = request.META.get("HTTP_USER_AGENT", "")
    return hashlib.sha256(ua.encode("utf-8")).hexdigest()
