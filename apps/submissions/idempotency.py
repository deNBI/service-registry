"""
One-time request tokens
=======================
A registration creates a service and a one-time API key, so sending the same
request twice (a double click, a browser resend, an API client retrying after
a timeout) must not create a second service.

The registration form carries a random token (``form_token``) and API clients
may send an ``Idempotency-Key`` header. The first request with a token claims
it; later requests with the same token do not create anything:

  - web form: they are sent to the success page of the service the first
    request created (waiting briefly if that request is still running);
  - API: they are answered with 409.

State lives in the cache (``cache.add`` is atomic in the production Redis
cache and in the local-memory cache used by tests), so no database column is
needed. A token that was never claimed, or whose request failed, is free to be
used again. Requests without a token behave as before.
"""

import hashlib
import re
import time

from django.core.cache import cache

WEB_FORM = "web-form"
API_REQUEST = "api-request"

# Value stored while the request that claimed the token is still running.
PENDING = "pending"

# A claim that is never completed (the worker died) frees itself after this.
PENDING_TTL = 120
# How long a completed token keeps pointing at the service it created.
DONE_TTL = 24 * 60 * 60

# A request that finds the token claimed polls this long for the result.
WAIT_ATTEMPTS = 50
WAIT_SECONDS = 0.1

_FORM_TOKEN = re.compile(r"[0-9a-f]{32}")
_API_KEY = re.compile(r"[\x20-\x7e]{1,255}")


def valid_form_token(value: str) -> bool:
    return bool(_FORM_TOKEN.fullmatch(value or ""))


def valid_api_key(value: str) -> bool:
    return bool(_API_KEY.fullmatch(value or ""))


def cache_key(kind: str, token: str) -> str:
    digest = hashlib.sha256(token.encode()).hexdigest()
    return f"once:{kind}:{digest}"


def claim(kind: str, token: str) -> bool:
    """Claim ``token``; False when another request already holds or finished it."""
    return cache.add(cache_key(kind, token), PENDING, PENDING_TTL)


def complete(kind: str, token: str, submission_id) -> None:
    """Record the service the claiming request created."""
    cache.set(cache_key(kind, token), str(submission_id), DONE_TTL)


def release(kind: str, token: str) -> None:
    """Free a claimed token whose request failed."""
    cache.delete(cache_key(kind, token))


def result(kind: str, token: str) -> str | None:
    """Id of the service created for ``token``; PENDING while running; None if free."""
    return cache.get(cache_key(kind, token))


def wait_for_result(kind: str, token: str) -> str | None:
    """Like ``result``, but give a running request a moment to finish.

    Returns the service id, ``PENDING`` if it is still running after the wait,
    or None if the claiming request failed and freed the token.
    """
    for _ in range(WAIT_ATTEMPTS):
        value = result(kind, token)
        if value != PENDING:
            return value
        time.sleep(WAIT_SECONDS)
    return result(kind, token)
