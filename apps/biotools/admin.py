from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from .models import BioToolsFunction, BioToolsRecord


class BioToolsFunctionInline(admin.TabularInline):
    model = BioToolsFunction
    extra = 0
    readonly_fields = (
        "position",
        "operations_display",
        "inputs_display",
        "outputs_display",
        "note",
    )
    fields = (
        "position",
        "operations_display",
        "inputs_display",
        "outputs_display",
        "note",
    )
    can_delete = False

    def operations_display(self, obj):
        return ", ".join(op.get("term", op.get("uri", "")) for op in obj.operations)

    operations_display.short_description = "Operations"

    def inputs_display(self, obj):
        return (
            "; ".join(
                inp.get("data", {}).get("term", "")
                for inp in obj.inputs
                if inp.get("data")
            )
            or "—"
        )

    inputs_display.short_description = "Input data"

    def outputs_display(self, obj):
        return (
            "; ".join(
                out.get("data", {}).get("term", "")
                for out in obj.outputs
                if out.get("data")
            )
            or "—"
        )

    outputs_display.short_description = "Output data"


@admin.register(BioToolsRecord)
class BioToolsRecordAdmin(admin.ModelAdmin):
    list_display = (
        "biotools_id",
        "name",
        "submission_link",
        "last_synced_at",
        "sync_status",
        "license",
    )
    list_filter = ("maturity", "cost")
    search_fields = ("biotools_id", "name", "description")
    list_select_related = ("submission",)
    readonly_fields = (
        "id",
        "submission",
        "biotools_id",
        "name",
        "description",
        "homepage",
        "version",
        "license",
        "maturity",
        "cost",
        "tool_type",
        "operating_system",
        "edam_topic_uris",
        "publications",
        "documentation",
        "download",
        "links",
        "last_synced_at",
        "sync_error",
        "created_at",
        "updated_at",
        "biotools_url_link",
    )
    inlines = [BioToolsFunctionInline]

    def submission_link(self, obj):
        url = reverse(
            "admin:submissions_servicesubmission_change", args=[obj.submission_id]
        )
        return format_html('<a href="{}">{}</a>', url, obj.submission.service_name)

    submission_link.short_description = "Submission"

    def sync_status(self, obj):
        if obj.sync_ok:
            return format_html('<span style="color:green">{}</span>', "✓ OK")
        return format_html('<span style="color:red">{}</span>', "✗ Error")

    sync_status.short_description = "Sync"

    def biotools_url_link(self, obj):
        url = obj.biotools_url
        return format_html('<a href="{}" target="_blank">{}</a>', url, url)

    biotools_url_link.short_description = "bio.tools URL"

    actions = ["action_sync_now"]

    def action_sync_now(self, request, queryset):
        from .tasks import sync_biotools_record

        for record in queryset:
            sync_biotools_record.delay(str(record.submission_id))
        self.message_user(request, f"Queued sync for {queryset.count()} record(s).")

    action_sync_now.short_description = "Sync selected records from bio.tools now"

    def has_add_permission(self, request):
        return False
