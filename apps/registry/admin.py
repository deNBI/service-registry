"""
Registry Admin
==============
Provides Django admin interfaces for the reference data models:
ServiceCategory, ServiceCenter, PrincipalInvestigator.

Deletion guard
--------------
All three models use a soft-delete flag (``is_active``) as the canonical way
to retire a record without breaking existing submission links.  Hard deletion
via the admin "Delete" button or "Delete selected" action is **blocked** for
any record that is referenced by at least one ServiceSubmission (in any status).

  • Single delete (detail-page Delete button)
      Intercepted *before* the confirmation page.  The admin is redirected to
      the changelist with an error message and a pointer to ``is_active``.

  • Bulk "Delete selected" (list-page action)
      Replaced with a custom guarded action.  If *any* selected record is in
      use the entire batch is aborted — no partial deletes.  A detailed error
      message lists every blocked record and its submission count.

  • Records with zero submissions
      Hard deletion proceeds normally for both single and bulk operations.

  • Linked-submissions column
      Each list view shows a "Submissions" column with the count of linked
      submissions.  For Service Categories and Service Centres the count is a
      hyperlink that opens the submission changelist pre-filtered to that record.
"""

from django.contrib import admin, messages
from django.db import models
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.html import format_html

from .models import PrincipalInvestigator, ServiceCategory, ServiceCenter


# ---------------------------------------------------------------------------
# Deletion-guard mixin
# ---------------------------------------------------------------------------


class _SubmissionGuardMixin:
    """
    Mixin for ModelAdmin classes whose records must not be hard-deleted while
    they are referenced by one or more ServiceSubmissions (in any status).

    Subclasses must set:
        _submission_filter_param (str | None)
            The query-string parameter used to link the count badge to the
            filtered submission changelist.  ``None`` renders the count as
            plain text.
            Example: ``"service_center__id__exact"``
    """

    _submission_filter_param: str | None = None

    # ------------------------------------------------------------------
    # Queryset — annotate with linked submission count for sortable column
    # ------------------------------------------------------------------

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(_submission_count=models.Count("submissions", distinct=True))
        )

    # ------------------------------------------------------------------
    # List column — submissions count with optional changelist hyperlink
    # ------------------------------------------------------------------

    @admin.display(description="Submissions", ordering="_submission_count")
    def linked_submissions_display(self, obj):
        count = getattr(obj, "_submission_count", obj.submissions.count())
        if count == 0:
            return "—"
        label = f"{count}\xa0submission{'s' if count != 1 else ''}"
        if self._submission_filter_param:
            url = (
                reverse("admin:submissions_servicesubmission_changelist")
                + f"?{self._submission_filter_param}={obj.pk}"
            )
            return format_html('<a href="{}">{}</a>', url, label)
        return label

    # ------------------------------------------------------------------
    # Single-record delete — intercept before the confirmation page
    # ------------------------------------------------------------------

    def delete_view(self, request, object_id, extra_context=None):
        """
        Block hard deletion before the confirmation page is rendered when
        the record is still referenced by one or more submissions.
        """
        obj = self.get_object(request, object_id)
        if obj is not None:
            count = obj.submissions.count()
            if count > 0:
                self.message_user(
                    request,
                    (
                        f"Cannot delete \u201c{obj}\u201d \u2014 it is referenced by "
                        f"{count}\xa0submission{'s' if count != 1 else ''} "
                        f"(across all statuses). "
                        f"Set \u201cis_active\u201d to \u201cFalse\u201d to hide it "
                        f"from the submission form without breaking existing data."
                    ),
                    messages.ERROR,
                )
                return HttpResponseRedirect(
                    reverse(
                        f"admin:{self.opts.app_label}_{self.opts.model_name}_changelist"
                    )
                )
        return super().delete_view(request, object_id, extra_context)

    def delete_model(self, request, obj):
        """Safety net: skip silently if the record is in use."""
        if obj.submissions.count() > 0:
            return
        super().delete_model(request, obj)

    # ------------------------------------------------------------------
    # Bulk delete — replace the default action with a guarded version
    # ------------------------------------------------------------------

    def get_actions(self, request):
        actions = super().get_actions(request)
        # Remove Django's site-wide delete_selected; add the guarded version.
        actions.pop("delete_selected", None)
        action_func = self.__class__.guarded_delete_selected
        actions["guarded_delete_selected"] = (
            action_func,
            "guarded_delete_selected",
            action_func.short_description,
        )
        return actions

    @admin.action(
        description="Delete selected \u2014 blocked if linked to any submission"
    )
    def guarded_delete_selected(self, request, queryset):
        """
        Custom bulk-delete action.

        Checks every selected record before touching the database.  If any
        record is linked to at least one submission the **entire batch** is
        aborted and a detailed error message lists each blocked record and its
        submission count.  Only when all selected records have no submissions
        does deletion proceed.
        """
        in_use: list[tuple[str, int]] = []

        for obj in queryset:
            count = obj.submissions.count()
            if count > 0:
                in_use.append((str(obj), count))

        if in_use:
            lines = "; ".join(
                f"\u201c{name}\u201d\xa0({n}\xa0submission{'s' if n != 1 else ''})"
                for name, n in in_use
            )
            n_blocked = len(in_use)
            self.message_user(
                request,
                (
                    f"Deletion blocked \u2014 "
                    f"{n_blocked}\xa0record{'s' if n_blocked != 1 else ''} "
                    f"{'are' if n_blocked != 1 else 'is'} still referenced by "
                    f"submissions: {lines}. "
                    f"Set \u201cis_active\u201d to \u201cFalse\u201d to hide "
                    f"{'them' if n_blocked != 1 else 'it'} from the submission "
                    f"form instead."
                ),
                messages.ERROR,
            )
            return

        deleted_count, _ = queryset.delete()
        self.message_user(
            request,
            f"Successfully deleted {deleted_count}\xa0"
            f"record{'s' if deleted_count != 1 else ''}.",
            messages.SUCCESS,
        )


# ---------------------------------------------------------------------------
# ServiceCategory
# ---------------------------------------------------------------------------


@admin.register(ServiceCategory)
class ServiceCategoryAdmin(_SubmissionGuardMixin, admin.ModelAdmin):
    """Admin for service category lookup table."""

    # Submission list can be filtered by category → provide a link
    _submission_filter_param = "service_categories__id__exact"

    list_display = ("name", "is_active", "linked_submissions_display")
    list_editable = ("is_active",)
    list_filter = ("is_active",)
    search_fields = ("name",)
    ordering = ("name",)


# ---------------------------------------------------------------------------
# ServiceCenter
# ---------------------------------------------------------------------------


@admin.register(ServiceCenter)
class ServiceCenterAdmin(_SubmissionGuardMixin, admin.ModelAdmin):
    """Admin for de.NBI service centres."""

    # Submission list can be filtered by service_center → provide a link
    _submission_filter_param = "service_center__id__exact"

    list_display = (
        "short_name",
        "full_name",
        "website_link",
        "is_active",
        "linked_submissions_display",
    )
    list_editable = ("is_active",)
    list_filter = ("is_active",)
    search_fields = ("short_name", "full_name")
    ordering = ("full_name",)
    readonly_fields = ("id",)

    fieldsets = (
        (
            None,
            {
                "fields": ("id", "short_name", "full_name", "website", "is_active"),
            },
        ),
    )

    @admin.display(description="Website")
    def website_link(self, obj):
        if obj.website:
            return format_html(
                '<a href="{}" target="_blank">{}</a>', obj.website, obj.website
            )
        return "\u2014"


# ---------------------------------------------------------------------------
# PrincipalInvestigator
# ---------------------------------------------------------------------------


@admin.register(PrincipalInvestigator)
class PrincipalInvestigatorAdmin(_SubmissionGuardMixin, admin.ModelAdmin):
    """Admin for named PIs in the de.NBI network."""

    # responsible_pis is not in the submission changelist filters,
    # so we show the count as plain text (no hyperlink).
    _submission_filter_param = None

    list_display = (
        "last_name",
        "first_name",
        "institute",
        "orcid_link",
        "is_active",
        "is_associated_partner",
        "linked_submissions_display",
    )
    list_editable = ("is_active",)
    list_filter = ("is_active", "is_associated_partner", "institute")
    search_fields = ("last_name", "first_name", "email", "institute")
    ordering = ("last_name", "first_name")
    readonly_fields = ("id",)

    fieldsets = (
        (
            "Identity",
            {
                "fields": ("id", "last_name", "first_name", "orcid"),
            },
        ),
        (
            "Affiliation",
            {
                "fields": ("institute", "email"),
            },
        ),
        (
            "Status",
            {
                "fields": ("is_active", "is_associated_partner"),
                "description": (
                    "Set is_active=False to hide this PI from the form without "
                    "removing existing submission links. "
                    "is_associated_partner should be True for only one entry."
                ),
            },
        ),
    )

    @admin.display(description="ORCID")
    def orcid_link(self, obj):
        if obj.orcid:
            url = f"https://orcid.org/{obj.orcid}"
            return format_html('<a href="{}" target="_blank">{}</a>', url, obj.orcid)
        return "\u2014"
