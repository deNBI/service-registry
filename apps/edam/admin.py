from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.urls import path

from .models import EdamTerm


@admin.register(EdamTerm)
class EdamTermAdmin(admin.ModelAdmin):
    list_display = ("accession", "label", "branch", "is_obsolete", "edam_version")
    list_filter = ("branch", "is_obsolete")
    search_fields = ("accession", "label", "definition", "synonyms")
    readonly_fields = (
        "uri",
        "accession",
        "branch",
        "sort_order",
        "edam_version",
        "parent",
    )
    ordering = ("branch", "sort_order")
    change_list_template = "admin/edam/edamterm/change_list.html"

    def has_add_permission(self, request):
        return False  # All terms come from sync

    def has_delete_permission(self, request, obj=None):
        return False  # Obsolete terms are flagged, not deleted

    def get_urls(self):
        return [
            path(
                "sync-now/",
                self.admin_site.admin_view(self.sync_now_view),
                name="edam_sync_now",
            ),
        ] + super().get_urls()

    def sync_now_view(self, request):
        from apps.edam.tasks import sync_edam_task

        sync_edam_task.delay()
        self.message_user(
            request,
            "EDAM sync task queued. The worker will download and import the latest "
            "ontology in the background. Refresh this page in a few minutes to see "
            "the updated term count and version.",
            messages.SUCCESS,
        )
        return HttpResponseRedirect("../")
