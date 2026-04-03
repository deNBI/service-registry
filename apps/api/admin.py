"""
API Admin
=========
Admin interface for scoped machine-to-machine API keys.

All API authentication is managed via AdminAPIKey — scoped machine-to-machine
keys independent of user accounts. Supports ``read`` scope (GET only) and
``full`` scope (all HTTP methods).
"""

import hashlib
import secrets

from django.contrib import admin, messages
from django.utils.html import format_html

from .models import AdminAPIKey

# ---------------------------------------------------------------------------
# Celery results admin — read-only, no Add button
# ---------------------------------------------------------------------------

from django_celery_results.admin import GroupResultAdmin, TaskResultAdmin
from django_celery_results.models import GroupResult, TaskResult

admin.site.unregister(TaskResult)
admin.site.unregister(GroupResult)


class _ReadOnlyMixin:
    """Mixin that removes all write permissions and bulk actions."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        return {}


@admin.register(TaskResult)
class ReadOnlyTaskResultAdmin(_ReadOnlyMixin, TaskResultAdmin):
    """TaskResult is written by Celery workers; admins should only read."""

    def get_list_display(self, request):
        # Exclude 'result_data' which can be very long and unwieldy
        return (
            "task_id",
            "task_name",
            "task_args",
            "task_kwargs",
            "status",
            "date_created",
            "date_done",
        )


@admin.register(GroupResult)
class ReadOnlyGroupResultAdmin(_ReadOnlyMixin, GroupResultAdmin):
    """GroupResult is written by Celery workers; admins should only read."""


# ---------------------------------------------------------------------------
# AdminAPIKey admin
# ---------------------------------------------------------------------------

_KEY_BANNER_HTML = (
    "<strong>⚠ Admin API Key created: {label}.</strong> "
    "This key is shown <strong>once only</strong> — "
    "copy it now before navigating away."
    '<div style="margin:8px 0">'
    '<code id="admin-api-key" style="font-size:14px;'
    "user-select:all;padding:6px 10px;background:#f8f9fa;"
    'border:1px solid #dee2e6;border-radius:4px;display:inline-block">'
    "{key}</code>"
    "</div>"
    '<button type="button" onclick="'
    "navigator.clipboard.writeText("
    "document.getElementById('admin-api-key').textContent"
    ").then(function(){{var b=event.target;b.textContent='✓ Copied';"
    "setTimeout(function(){{b.textContent='Copy to clipboard'}},3000)}}"
    ')" style="cursor:pointer;padding:4px 12px;font-size:13px;'
    "border:1px solid #6c757d;border-radius:4px;background:#fff;"
    'color:#333">Copy to clipboard</button>'
    "<p style='margin-top:8px;font-size:12px;color:#666'>"
    "Send requests with: <code>Authorization: AdminKey &lt;key&gt;</code>"
    "</p>"
)


@admin.register(AdminAPIKey)
class AdminAPIKeyAdmin(admin.ModelAdmin):
    """
    Admin interface for scoped machine-to-machine API keys.

    Creation flow
    -------------
    1. Click «Add Admin API Key».
    2. Enter a label and choose a scope (read-only or full).
    3. Save — the plaintext key appears once in a banner.  Copy it immediately.
    4. Revoke later by unchecking «Is active» and saving.
    """

    list_display = (
        "label",
        "masked_hash",
        "scope",
        "is_active",
        "created_by",
        "created_at",
        "last_used_at",
    )
    list_filter = ("scope", "is_active")
    search_fields = ("label",)
    ordering = ("-created_at",)
    readonly_fields = (
        "id",
        "masked_hash",
        "created_at",
        "last_used_at",
        "created_by",
    )

    def get_fields(self, request, obj=None):
        if obj is None:  # creation form
            return ("label", "scope")
        return (
            "id",
            "label",
            "scope",
            "masked_hash",
            "is_active",
            "created_by",
            "created_at",
            "last_used_at",
        )

    @admin.display(description="Hash prefix")
    def masked_hash(self, obj):
        return f"{obj.key_hash[:16]}…" if obj.key_hash else "—"

    def save_model(self, request, obj, form, change):
        if not change:
            # Generate the key here so we can show it once in response_add.
            plaintext = secrets.token_urlsafe(48)
            obj.key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
            obj.created_by = request.user
            # Stash on request (thread-local) for response_add to read.
            request._admin_api_key_plaintext = plaintext
        super().save_model(request, obj, form, change)

    def response_add(self, request, obj, post_url_continue=None):
        plaintext = getattr(request, "_admin_api_key_plaintext", None)
        if plaintext:
            self.message_user(
                request,
                format_html(_KEY_BANNER_HTML, label=obj.label, key=plaintext),
                messages.WARNING,
            )
        return super().response_add(request, obj, post_url_continue)

    def has_delete_permission(self, request, obj=None):
        # Keys are never hard-deleted — revoke via is_active instead.
        return False
