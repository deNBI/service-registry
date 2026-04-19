from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path

from .models import SpdxLicense


@admin.register(SpdxLicense)
class SpdxLicenseAdmin(admin.ModelAdmin):
    list_display = (
        "license_id",
        "name",
        "is_osi_approved",
        "is_fsf_libre",
        "is_deprecated",
        "spdx_version",
    )
    list_filter = ("is_osi_approved", "is_fsf_libre", "is_deprecated")
    search_fields = ("license_id", "name")
    readonly_fields = (
        "license_id",
        "name",
        "reference_url",
        "see_also",
        "is_osi_approved",
        "is_fsf_libre",
        "spdx_version",
    )
    ordering = ("name",)
    change_list_template = "admin/licenses/spdxlicense/change_list.html"

    def has_add_permission(self, request):
        return False  # All entries come from upstream sync

    def has_delete_permission(self, request, obj=None):
        return False  # Removed-upstream entries are flagged deprecated, not deleted

    def get_urls(self):
        return [
            path(
                "sync-now/",
                self.admin_site.admin_view(self.sync_now_view),
                name="licenses_sync_now",
            ),
        ] + super().get_urls()

    def sync_now_view(self, request):
        from apps.licenses.tasks import sync_spdx_licenses_task

        sync_spdx_licenses_task.delay()
        self.message_user(
            request,
            "SPDX license sync task queued. The worker will download and import "
            "the latest license list in the background. Refresh this page in a "
            "few minutes to see the updated entries and version.",
            messages.SUCCESS,
        )
        return redirect("admin:licenses_spdxlicense_changelist")
