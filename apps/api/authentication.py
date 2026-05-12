"""
API Authentication
==================
Custom DRF authentication backends for the de.NBI Service Registration Platform.

SubmissionAPIKeyAuthentication
  - Header: ``Authorization: ApiKey <key>``
  - Authenticates per-submission owners via SubmissionAPIKey.
  - Returns (submission, key_obj) as the DRF user/auth pair.
  - Revoked or invalid keys return the same AuthenticationFailed — no state leakage.

AdminAPIKeyAuthentication
  - Header: ``Authorization: AdminKey <key>``
  - Authenticates machine-to-machine admin consumers via AdminAPIKey.
  - Supports ``read`` scope (GET only) and ``full`` scope (all methods).
  - Returns (key, key) as the DRF user/auth pair; permissions check scope.
"""

import logging

from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request

logger = logging.getLogger(__name__)


class SubmissionAPIKeyAuthentication(BaseAuthentication):
    """
    Authenticate requests that carry an ``Authorization: ApiKey <key>`` header.

    On success, sets:
      - ``request.user`` to the associated ``ServiceSubmission`` instance
      - ``request.auth`` to the ``SubmissionAPIKey`` instance

    Permissions (in api/permissions.py) enforce that the authenticated
    submission matches the requested resource.
    """

    keyword = "ApiKey"

    def authenticate(self, request: Request):
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth_header.startswith(f"{self.keyword} "):
            return None  # Not our scheme — let other authenticators try

        plaintext = auth_header[len(self.keyword) + 1 :].strip()
        if not plaintext:
            raise AuthenticationFailed("API key is empty.")

        from apps.submissions.models import SubmissionAPIKey

        key_obj, authenticated = SubmissionAPIKey.verify(plaintext)

        if not authenticated:
            # Identical response for invalid key and revoked key — no leakage
            logger.warning(
                "API authentication failed",
                extra={"key_hint": plaintext[:8] + "..."},
            )
            raise AuthenticationFailed("Invalid or revoked API key.")

        # Return (user, auth) — DRF convention.
        # We use the submission as the "user" object so permissions can check it.
        return (key_obj.submission, key_obj)

    def authenticate_header(self, request: Request) -> str:
        return self.keyword


class AdminAPIKeyAuthentication(BaseAuthentication):
    """
    Authenticate requests that carry an ``Authorization: AdminKey <key>`` header.

    On success, sets:
      - ``request.user`` to the ``AdminAPIKey`` instance
      - ``request.auth`` to the same ``AdminAPIKey`` instance

    Scope enforcement is delegated to ``IsAdminTokenUser`` in permissions.py,
    which blocks non-safe HTTP methods for ``SCOPE_READ`` keys.
    """

    keyword = "AdminKey"

    def authenticate(self, request: Request):
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth_header.startswith(f"{self.keyword} "):
            return None  # Not our scheme — let other authenticators try

        plaintext = auth_header[len(self.keyword) + 1 :].strip()
        if not plaintext:
            raise AuthenticationFailed("Admin API key is empty.")

        from .models import AdminAPIKey

        key = AdminAPIKey.verify(plaintext)
        if key is None:
            logger.warning(
                "AdminAPIKey authentication failed",
                extra={"key_hint": plaintext[:8] + "..."},
            )
            raise AuthenticationFailed("Invalid or revoked admin API key.")

        # Return (key, key) — request.user = AdminAPIKey, request.auth = AdminAPIKey.
        # IsAdminTokenUser checks isinstance(request.auth, AdminAPIKey) for this path.
        return (key, key)

    def authenticate_header(self, request: Request) -> str:
        return self.keyword
