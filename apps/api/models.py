"""
API Models
==========
AdminAPIKey — scoped long-lived keys for machine-to-machine admin access.

These keys are independent of Django's per-user Token model and are managed
through the Django admin under **API → Admin API Keys**.  Each key carries
an explicit scope:

  read  — HTTP GET / HEAD / OPTIONS only.
           Safe to embed in third-party applications (websites, dashboards).
           Even if a read key leaks it cannot mutate any registry data.

  full  — All HTTP methods (GET, POST, PATCH, DELETE).
           Treat like a password; restrict to trusted internal consumers.

Keys are stored as SHA-256 hashes only.  The plaintext is shown once at
creation time via the admin UI and never stored or logged.
"""

import hashlib
import uuid

from django.db import models
from django.utils import timezone


class AdminAPIKey(models.Model):
    """
    A scoped machine-to-machine API key for admin-level access to the REST API.

    Workflow
    --------
    1. Create a key in the Django admin (Auth → Admin API Keys → Add).
    2. Copy the plaintext from the one-time banner — it is never shown again.
    3. Send requests with:  Authorization: AdminKey <plaintext>
    4. Revoke by setting is_active = False.
    """

    SCOPE_READ = "read"
    SCOPE_FULL = "full"
    SCOPE_CHOICES = [
        (SCOPE_READ, "Read-only — GET / HEAD / OPTIONS only"),
        (SCOPE_FULL, "Full access — all HTTP methods"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key_hash = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text="SHA-256 hex digest of the plaintext key.  Never stored in cleartext.",
    )
    label = models.CharField(
        max_length=100,
        help_text="Human-readable name for this key (e.g. 'Public website', 'CI pipeline').",
    )
    scope = models.CharField(
        max_length=10,
        choices=SCOPE_CHOICES,
        default=SCOPE_FULL,
        help_text=(
            "read = safe HTTP methods only (GET/HEAD/OPTIONS); "
            "full = all HTTP methods including POST/PATCH/DELETE."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        "auth.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="admin_api_keys",
        help_text="Staff user who created this key.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Revoke by setting to False.  The record is retained for audit purposes.",
    )
    last_used_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp of the most recent successfully authenticated request.",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Admin API Key"
        verbose_name_plural = "Admin API Keys"

    def __str__(self) -> str:
        status = "active" if self.is_active else "revoked"
        return f"{self.label} [{self.get_scope_display()}] ({status})"

    @property
    def is_authenticated(self) -> bool:
        """
        Return True when the key is active (authenticated and granted).
        DRF's throttling and middleware expect this property on user-like objects
        to determine authentication status for rate limiting and other checks.
        """
        return self.is_active

    @classmethod
    def verify(cls, plaintext: str) -> "AdminAPIKey | None":
        """
        Verify a plaintext key against stored hashes using constant-time lookup.

        Returns the matching active ``AdminAPIKey`` (and updates ``last_used_at``)
        or ``None`` if the key is invalid or revoked.
        """
        key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
        try:
            key = cls.objects.get(key_hash=key_hash, is_active=True)
        except cls.DoesNotExist:
            return None
        key.last_used_at = timezone.now()
        key.save(update_fields=["last_used_at"])
        return key
