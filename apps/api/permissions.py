"""
API Permissions
===============
Custom DRF permission classes enforcing the two-tier access model:

  IsAdminTokenUser   : Accepts an AdminAPIKey (scoped machine-to-machine key).
                       The ``read`` scope restricts access to safe HTTP methods only.
  IsSubmissionOwner  : Requires ApiKey auth whose submission matches the URL.
  IsAdminOrOwner     : Allows either — used for detail GET/PATCH.
"""

from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

_SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


class IsAdminTokenUser(BasePermission):
    """
    Grants access when the request carries a valid admin API key credential.

    Accepted credentials:

    AdminAPIKey (``Authorization: AdminKey <key>``).
    Scope is enforced per key:
      - ``read``  → safe HTTP methods only (GET / HEAD / OPTIONS)
      - ``full``  → all HTTP methods

    Revoked / inactive credentials are rejected.
    """

    message = "Admin API key authentication required."

    def has_permission(self, request: Request, view: APIView) -> bool:
        from .models import AdminAPIKey

        # ── AdminAPIKey (scoped machine-to-machine key) ─────────────────────
        if isinstance(request.auth, AdminAPIKey):
            if not request.auth.is_active:
                return False
            if (
                request.auth.scope == AdminAPIKey.SCOPE_READ
                and request.method not in _SAFE_METHODS
            ):
                self.message = (
                    "This key is read-only. "
                    "Use a full-access Admin API Key to modify data."
                )
                return False
            return True

        return False


class IsSubmissionOwner(BasePermission):
    """
    Allow access when the request is authenticated with a SubmissionAPIKey
    whose submission matches the URL, and whose scope permits the HTTP method.

    Scope rules:
      read  → GET, HEAD, OPTIONS only
      write → GET, HEAD, OPTIONS, PATCH
    """

    message = "You do not have permission to access this submission."

    SAFE_METHODS = ("GET", "HEAD", "OPTIONS")

    def has_permission(self, request: Request, view: APIView) -> bool:
        from apps.submissions.models import ServiceSubmission, SubmissionAPIKey

        if not isinstance(request.user, ServiceSubmission):
            return False
        # Scope check: read-only keys cannot PATCH
        key = request.auth  # SubmissionAPIKey instance set by our auth backend
        if isinstance(key, SubmissionAPIKey):
            if (
                key.scope == SubmissionAPIKey.SCOPE_READ
                and request.method not in self.SAFE_METHODS
            ):
                self.message = (
                    "This API key is read-only. Use a write-scoped key to modify data."
                )
                return False
        return True

    def has_object_permission(self, request: Request, view: APIView, obj) -> bool:
        from apps.submissions.models import ServiceSubmission

        if not isinstance(request.user, ServiceSubmission):
            return False
        return str(request.user.pk) == str(obj.pk)


class IsAdminOrOwner(BasePermission):
    """
    Grants access if either IsAdminTokenUser or IsSubmissionOwner passes.
    """

    def has_permission(self, request: Request, view: APIView) -> bool:
        admin_perm = IsAdminTokenUser()
        owner_perm = IsSubmissionOwner()
        return admin_perm.has_permission(request, view) or owner_perm.has_permission(
            request, view
        )

    def has_object_permission(self, request: Request, view: APIView, obj) -> bool:
        admin_perm = IsAdminTokenUser()
        owner_perm = IsSubmissionOwner()
        return admin_perm.has_permission(
            request, view
        ) or owner_perm.has_object_permission(request, view, obj)
