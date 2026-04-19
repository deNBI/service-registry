"""
Submissions Admin
=================
Features:
  - Rich list view with colour-coded status badges and key metrics
  - Custom change view with API-key management panel
  - Bulk actions: approve, reject, mark-under-review, deprecate, undeprecate, CSV/JSON export
  - Status transitions fire email notifications via Celery
  - All admin key operations logged to Django LogEntry
"""

import csv
import json
import logging
from datetime import datetime

from django import forms
from django.contrib import admin, messages
from django.forms.widgets import CheckboxSelectMultiple
from django.contrib.admin.models import CHANGE, LogEntry
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.template import loader
from django.utils import timezone
from django.utils.html import escape, format_html, format_html_join, mark_safe

from .diff_utils import build_diff, snapshot, snapshot_m2m
from .models import (
    CHANGELOG_ACTOR_ADMIN_PREFIX,
    CHANGELOG_ACTOR_API_PREFIX,
    CHANGELOG_ACTOR_SUBMITTER,
    PRIMARY_MATURITY_TAG_CHOICES,
    SECONDARY_MATURITY_TAG_CHOICES,
    ServiceSubmission,
    SubmissionAPIKey,
    SubmissionChangeLog,
    SubmissionDeletionAudit,
    SubmissionStatus,
)
from .tasks import send_submission_notification

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# API Key inline
# ─────────────────────────────────────────────────────────────────────────────


class SubmissionAPIKeyInline(admin.TabularInline):
    """Read-only inline — plaintext keys are never stored or displayed."""

    model = SubmissionAPIKey
    extra = 0
    can_delete = False
    show_change_link = False
    readonly_fields = (
        "key_hash_preview",
        "label",
        "scope",
        "created_by",
        "created_at",
        "last_used_at",
        "status_display",
    )
    fields = readonly_fields

    @admin.display(description="Hash prefix")
    def key_hash_preview(self, obj):
        return format_html(
            '<code style="font-size:.8rem">{}&hellip;</code>',
            obj.key_hash[:16],
        )

    @admin.display(description="Status")
    def status_display(self, obj):
        if obj.is_active:
            return mark_safe(
                '<span style="color:var(--link-fg);font-weight:600;font-size:.8rem">'
                "● Active</span>"
            )
        return mark_safe(
            '<span style="color:var(--body-quiet-color);font-size:.8rem">○ Revoked</span>'
        )

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        # All fields are readonly — no in-place editing is possible.
        return False

    def has_delete_permission(self, request, obj=None):
        # Deletion is handled through the dedicated SubmissionAPIKeyAdmin.
        return False

    def has_view_permission(self, request, obj=None):
        # Mirror SubmissionAPIKeyAdmin: either view_submissionapikey or manage_apikeys.
        return request.user.has_perm(
            "submissions.view_submissionapikey"
        ) or request.user.has_perm("submissions.manage_apikeys")


# ─────────────────────────────────────────────────────────────────────────────
# ServiceSubmission admin
# ─────────────────────────────────────────────────────────────────────────────


class ServiceSubmissionAdminForm(forms.ModelForm):
    class Meta:
        model = ServiceSubmission
        fields = "__all__"

    def clean(self):
        cleaned = super().clean()
        licenses = cleaned.get("licenses")
        license_note = (cleaned.get("license_note") or "").strip()
        if not licenses and not license_note:
            raise forms.ValidationError(
                "Please select at least one license, or fill in a license note "
                "if no standard license applies."
            )
        return cleaned


@admin.register(ServiceSubmission)
class ServiceSubmissionAdmin(admin.ModelAdmin):
    form = ServiceSubmissionAdminForm
    # ── List view ────────────────────────────────────────────────────────────
    list_display = (
        "service_name_link",
        "submitter_display",
        "status_badge",
        "maturity_tag_display",
        "service_center",
        "licenses_summary",
        "elixir_badge",
        "submitted_at",
        "key_count",
        "api_key_link",
    )
    list_filter = (
        "status",
        "primary_maturity_tag",
        "register_as_elixir",
        "service_center",
        "service_categories",
        "responsible_pis",
        ("submitted_at", admin.DateFieldListFilter),
    )
    search_fields = (
        "service_name",
        "submitter_first_name",
        "submitter_last_name",
        "submitter_affiliation",
        "host_institute",
        "responsible_pis__last_name",
        "responsible_pis__first_name",
        "licenses__license_id",
        "licenses__name",
        "license_note",
    )
    ordering = ("-submitted_at",)
    date_hierarchy = "submitted_at"
    save_on_top = True
    list_per_page = 30
    list_select_related = ("service_center",)
    # Two-panel filtered selector with search — needed for large option sets
    filter_horizontal = (
        "responsible_pis",
        "edam_topics",
        "edam_operations",
        "licenses",
    )

    # ── Permission gates ──────────────────────────────────────────────────────

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("submissions.view_servicesubmission")

    def has_add_permission(self, request):
        return request.user.has_perm("submissions.add_servicesubmission")

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm("submissions.change_servicesubmission")

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm("submissions.delete_servicesubmission")

    # These two methods are called by @admin.action(permissions=[...]) to
    # decide whether an action is shown in the dropdown for a given user.

    def has_approve_servicesubmission_permission(self, request):
        """Called by bulk actions that approve or reject submissions."""
        return request.user.has_perm("submissions.approve_servicesubmission")

    def has_manage_apikeys_permission(self, request):
        """Called by any admin action that issues, resets, or revokes keys."""
        return request.user.has_perm("submissions.manage_apikeys")

    # ── Permission helper ─────────────────────────────────────────────────────

    def _require_perm(self, request, perm: str, action_label: str) -> bool:
        """
        Return True if *request.user* has *perm* (bare codename, submissions app).

        If the permission is absent, emit an error message and return False so
        the caller can bail out immediately.  Always check this before executing
        any destructive or privileged operation in response_change().
        """
        if request.user.has_perm(f"submissions.{perm}"):
            return True
        self.message_user(
            request,
            f"Permission denied — you need '{perm}' to {action_label}.",
            messages.ERROR,
        )
        return False

    # ── Queryset ──────────────────────────────────────────────────────────────

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("api_keys", "licenses")

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        # Override textarea row counts — Django's default vLargeTextField CSS
        # sets height:26em which makes empty fields enormous. Setting rows here
        # takes precedence once we disable the CSS height override in base_site.html.
        textarea_rows = {
            "service_description": 5,
            "user_knowledge_required": 3,
            "associated_partner_note": 2,
            "publications_pmids": 2,
            "keywords_uncited": 2,
            "keywords_seo": 2,
            "comments": 3,
        }
        for field_name, rows in textarea_rows.items():
            if field_name in form.base_fields:
                form.base_fields[field_name].widget.attrs.update({"rows": rows})
        return form

    def formfield_for_manytomanyfield(self, db_field, request, **kwargs):
        # service_categories has a small fixed list — checkboxes are cleaner
        # than a scrollable multi-select box and need no holding Ctrl to pick.
        if db_field.name == "service_categories":
            kwargs["widget"] = CheckboxSelectMultiple()
        return super().formfield_for_manytomanyfield(db_field, request, **kwargs)

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "primary_maturity_tag":
            # Get the default form field then swap in RadioSelect + relabel empty option.
            # Do NOT pass empty_label — TypedChoiceField does not accept it.
            kwargs.setdefault("widget", forms.RadioSelect())
            field = super().formfield_for_dbfield(db_field, request, **kwargs)
            if field is not None:
                # Replace the default "---------" blank label with something clearer.
                field.choices = [("", "None")] + list(PRIMARY_MATURITY_TAG_CHOICES)
            return field
        if db_field.name == "secondary_maturity_tags":
            # JSONField has no built-in choices support; return a full custom field.
            return forms.TypedMultipleChoiceField(
                choices=SECONDARY_MATURITY_TAG_CHOICES,
                widget=forms.CheckboxSelectMultiple(),
                required=False,
                coerce=str,
                label="Secondary maturity tags",
                help_text="Optional secondary tags (Unstable, etc.). Only assignable to approved services.",
            )
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    class Media:
        # Enhanced filter sidebar assets are loaded on the changelist only
        # (the JS guards on `#changelist-filter.changelist-filter--enhanced`).
        css = {"all": ("admin/css/submissions_filter_sidebar.css",)}
        js = (
            "js/admin_submission_change.js",
            "admin/js/submissions_filter_sidebar.js",
        )

    inlines = [SubmissionAPIKeyInline]

    readonly_fields = (
        "id",
        "submitted_at",
        "updated_at",
        "submission_ip_display",
        "status",
        "status_actions",
        "key_management_panel",
        "logo_preview",
        "last_change_summary_display",
        "data_protection_consent",
        "change_history_display",
    )

    @admin.display(description="Logo preview")
    def logo_preview(self, obj):
        if obj.logo:
            return format_html(
                '<img src="{}" style="max-height:120px;max-width:300px;'
                "border:1px solid var(--border-color);"
                'border-radius:4px;padding:4px;" alt="Service logo">',
                obj.logo.url,
            )
        return "—"

    @admin.display(description="Maturity")
    def maturity_tag_display(self, obj):
        if not obj.primary_maturity_tag:
            return "—"
        primary = obj.get_primary_maturity_tag_display()
        secondary = (
            ", ".join(obj.get_secondary_maturity_tag_display_list())
            if obj.secondary_maturity_tags
            else ""
        )
        if secondary:
            return format_html(
                '<span style="color:var(--link-fg);font-weight:600">{}</span> '
                '<span style="color:var(--body-quiet-color);font-size:.85rem">({})</span>',
                primary,
                secondary,
            )
        return format_html(
            '<span style="color:var(--link-fg);font-weight:600">{}</span>', primary
        )

    @admin.display(description="License(s)")
    def licenses_summary(self, obj):
        ids = list(obj.licenses.values_list("license_id", flat=True))
        if ids:
            shown = ", ".join(ids[:3])
            if len(ids) > 3:
                shown += f", +{len(ids) - 3}"
            return shown
        note = (obj.license_note or "").strip()
        if note:
            truncated = note if len(note) <= 40 else note[:37] + "…"
            return format_html(
                '<span style="color:var(--body-quiet-color);font-style:italic">{}</span>',
                truncated,
            )
        return "—"

    @staticmethod
    def _actor_badge(changed_by: str, font_size: str = ".78rem") -> str:
        """Return a styled HTML badge for the actor who made a change."""
        fs = f"font-size:{font_size}"
        base = f"border-radius:4px;padding:1px 7px;{fs};font-weight:600"
        if changed_by == CHANGELOG_ACTOR_SUBMITTER:
            return f'<span style="background:#eff6ff;color:#1d4ed8;{base}">Submitter</span>'
        if changed_by.startswith(CHANGELOG_ACTOR_API_PREFIX):
            label = escape(changed_by.removeprefix(CHANGELOG_ACTOR_API_PREFIX))
            return f'<span style="background:#fef9c3;color:#854d0e;{base}">API: {label}</span>'
        username = escape(
            changed_by.removeprefix(CHANGELOG_ACTOR_ADMIN_PREFIX)
            if ":" in changed_by
            else changed_by
        )
        return f'<span style="background:#f0fdf4;color:#166534;{base}">Admin: {username}</span>'

    @staticmethod
    def _diff_table_html(
        changes: list, padding: str = "5px 10px", font_size: str = ".82rem"
    ) -> str:
        """Return a styled HTML table of field-level diff rows, or a fallback message."""
        p = f"padding:{padding}"
        b = "border:1px solid var(--border-color)"
        fs = f"font-size:{font_size}"
        if not changes:
            return (
                f'<tr><td colspan="3" style="color:var(--body-quiet-color);'
                f'font-style:italic;{p}">No field differences recorded.</td></tr>'
            )
        rows = []
        for ch in changes:
            label = escape(ch.get("label", ch.get("field", "")))
            old = escape(ch.get("old", "—"))
            new = escape(ch.get("new", "—"))
            rows.append(
                f"<tr>"
                f'<td style="{p};{b};font-weight:600;white-space:nowrap;{fs}">{label}</td>'
                f'<td style="{p};{b};color:#991b1b;{fs};word-break:break-word">{old}</td>'
                f'<td style="{p};{b};color:#166534;{fs};word-break:break-word">{new}</td>'
                f"</tr>"
            )
        th = f"{p};{b};background:{{bg}};text-align:left;{fs}"
        header = (
            f'<th style="{th.format(bg="var(--darkened-bg)")}">Field</th>'
            f'<th style="{th.format(bg="#fef2f2")}">Before</th>'
            f'<th style="{th.format(bg="#f0fdf4")}">After</th>'
        )
        return (
            f'<table style="border-collapse:collapse;width:100%;max-width:700px">'
            f"<thead><tr>{header}</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>"
        )

    @admin.display(description="Last change summary")
    def last_change_summary_display(self, obj):
        """
        Render the last_change_summary JSON as a styled HTML table.

        Shows: who made the change, when, and a field-by-field diff.
        Visible to any admin who opens the submission — covers both
        submitter-initiated edits and admin-initiated edits.
        """
        summary = obj.last_change_summary
        if not summary:
            return mark_safe(
                '<span style="color:var(--body-quiet-color);font-size:.85rem">'
                "No change history recorded yet."
                "</span>"
            )

        changed_by = summary.get("changed_by", "unknown")
        changed_at = summary.get("changed_at", "")
        changes = summary.get("changes", [])

        if changed_at:
            try:
                changed_at = datetime.fromisoformat(changed_at).strftime(
                    "%Y-%m-%d %H:%M UTC"
                )
            except (ValueError, TypeError):
                pass
        else:
            changed_at = "unknown time"

        html = (
            f'<div style="font-size:.85rem">'
            f'<p style="margin:.25rem 0 .6rem">'
            f"Changed by {self._actor_badge(changed_by)} &nbsp;·&nbsp; "
            f'<span style="color:var(--body-quiet-color)">{escape(changed_at)}</span>'
            f"</p>"
            f"{self._diff_table_html(changes)}"
            f"</div>"
        )
        return mark_safe(html)

    @admin.display(description="Change history")
    def change_history_display(self, obj):
        """
        Render the full SubmissionChangeLog for this submission.

        Each entry is a <details>/<summary> row — collapsed by default,
        expandable to show the field-level diff. Most recent entry first.
        Covers admin saves, submitter web-form edits, and API PATCH requests.
        """
        entries = list(obj.change_log.all()[:50])  # cap at 50 for rendering
        if not entries:
            return mark_safe(
                '<span style="color:var(--body-quiet-color);font-size:.85rem">'
                "No change history recorded yet."
                "</span>"
            )

        parts = []
        for entry in entries:
            n = len(entry.changes)
            count_txt = f"{n} field{'s' if n != 1 else ''} changed"
            if entry.changes:
                table = self._diff_table_html(
                    entry.changes, padding="4px 8px", font_size=".8rem"
                )
            else:
                table = (
                    '<p style="margin:.3rem 0;color:var(--body-quiet-color);'
                    'font-size:.8rem;font-style:italic">No field differences recorded.</p>'
                )

            ts = escape(entry.changed_at.strftime("%Y-%m-%d %H:%M UTC"))
            parts.append(
                f'<details style="margin-bottom:.4rem;border:1px solid var(--border-color);'
                f'border-radius:4px;padding:.35rem .7rem">'
                f'<summary style="cursor:pointer;font-size:.83rem;user-select:none;list-style:none">'
                f"<span style='margin-right:.5rem'>▶</span>"
                f"{self._actor_badge(entry.changed_by, font_size='.75rem')} &nbsp; "
                f'<span style="color:var(--body-quiet-color)">{ts}</span>'
                f' &nbsp;·&nbsp; <em style="font-size:.78rem">{count_txt}</em>'
                f"</summary>"
                f"{table}"
                f"</details>"
            )

        total = obj.change_log.count()
        footer = ""
        if total > 50:
            footer = (
                f'<p style="font-size:.78rem;color:var(--body-quiet-color);margin-top:.4rem">'
                f"Showing most recent 50 of {total} entries.</p>"
            )

        return mark_safe(
            f'<div style="font-size:.85rem">{"".join(parts)}{footer}</div>'
        )

    # ── Diff capture — save_model / save_related / response_change ─────────────

    def save_model(self, request, obj, form, change):
        """
        Snapshot the scalar fields BEFORE the model is written to the database.

        obj already has the new form values applied, so we re-fetch the original
        from the database to get a true "before" snapshot.  The snapshot is
        stored on the request object so save_related() can compare it after
        M2M relations are committed.
        """
        if change and obj.pk:
            try:
                original = obj.__class__.objects.get(pk=obj.pk)
                request._diff_before_scalar = snapshot(original)
            except obj.__class__.DoesNotExist:
                request._diff_before_scalar = {}
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        """
        Snapshot M2M BEFORE super().save_related() commits the new values, then
        compute the full diff (scalar + M2M), persist it on the model, and stash
        it on the request so response_change() can display the diff banner.
        """
        if change:
            # M2M snapshot BEFORE save_related commits new values.
            before_m2m = snapshot_m2m(form.instance)

        super().save_related(request, form, formsets, change)

        if not change:
            request._diff_changes = []
            return

        # After super() the new M2M values are in the DB — snapshot them now.
        after_scalar = snapshot(form.instance)
        after_m2m = snapshot_m2m(form.instance)

        before_scalar = getattr(request, "_diff_before_scalar", {})
        changes = build_diff(
            {**before_scalar, **before_m2m},
            {**after_scalar, **after_m2m},
        )
        request._diff_changes = changes

        if changes:
            username = getattr(request.user, "username", "admin")
            changed_by = f"{CHANGELOG_ACTOR_ADMIN_PREFIX}{username}"
            now = timezone.now()
            form.instance.last_change_summary = {
                "changed_by": changed_by,
                "changed_at": now.isoformat(),
                "changes": changes,
            }
            form.instance.save(update_fields=["last_change_summary"])
            SubmissionChangeLog.objects.create(
                submission=form.instance,
                changed_by=changed_by,
                changed_at=now,
                changes=changes,
            )

    def response_change(self, request, obj):
        # ── Privileged POST actions — permission checked before execution ──────
        # Each branch uses _require_perm() which emits an error message and
        # returns False when the permission is absent, so the operation is
        # silently skipped and the user sees only the error banner.  This is the
        # authoritative security gate regardless of what the UI shows.
        if "_revoke_all_keys" in request.POST:
            if self._require_perm(request, "manage_apikeys", "revoke API keys"):
                self._revoke_all_keys(request, obj)
        elif "_reset_key" in request.POST:
            if self._require_perm(request, "manage_apikeys", "reset API keys"):
                self._reset_key(request, obj)
        elif "_issue_new_key" in request.POST:
            if self._require_perm(request, "manage_apikeys", "issue new API keys"):
                self._issue_new_key(request, obj)
        elif "_approve" in request.POST:
            if self._require_perm(
                request, "approve_servicesubmission", "approve submissions"
            ):
                self._change_status(
                    request,
                    obj.__class__.objects.filter(pk=obj.pk),
                    "approved",
                    "Approved",
                )
        elif "_reject" in request.POST:
            if self._require_perm(
                request, "approve_servicesubmission", "reject submissions"
            ):
                self._change_status(
                    request,
                    obj.__class__.objects.filter(pk=obj.pk),
                    "rejected",
                    "Rejected",
                )
        elif "_under_review" in request.POST:
            if self._require_perm(
                request, "change_servicesubmission", "mark submissions as under review"
            ):
                self._change_status(
                    request,
                    obj.__class__.objects.filter(pk=obj.pk),
                    "under_review",
                    "Under Review",
                )
        elif "_deprecate" in request.POST:
            if self._require_perm(
                request, "change_servicesubmission", "deprecate submissions"
            ):
                self._change_status(
                    request,
                    obj.__class__.objects.filter(pk=obj.pk),
                    "deprecated",
                    "Deprecated",
                )
        elif "_undeprecate" in request.POST:
            if self._require_perm(
                request, "change_servicesubmission", "undeprecate submissions"
            ):
                self._change_status(
                    request,
                    obj.__class__.objects.filter(pk=obj.pk),
                    "submitted",
                    "Submitted (undeprecated)",
                )
        else:
            # Regular form save — show a diff banner if fields changed.
            changes = getattr(request, "_diff_changes", [])
            if changes:
                # Build a concise "field: old → new" summary for the banner.
                # All user-controlled values are escaped via format_html().
                lines = [
                    format_html(
                        "<li><strong>{}:</strong> "
                        '<span style="color:#991b1b">{}</span> → '
                        '<span style="color:#166534">{}</span></li>',
                        ch["label"],
                        ch["old"],
                        ch["new"],
                    )
                    for ch in changes
                ]
                self.message_user(
                    request,
                    mark_safe(
                        format_html(
                            "<strong>{} field(s) changed:</strong>"
                            '<ul style="margin:.4rem 0 0 1.2rem;padding:0">{}</ul>',
                            len(changes),
                            mark_safe("".join(lines)),
                        )
                    ),
                    messages.INFO,
                )
                # Also write the diff to LogEntry.change_message for the History tab.
                change_msg = "; ".join(
                    f"{ch['label']}: {ch['old']!r} → {ch['new']!r}" for ch in changes
                )
                self._log(request, obj, change_msg)
            elif hasattr(request, "_diff_changes"):
                # Diff was computed but nothing changed.
                self.message_user(
                    request,
                    "Saved — no field values were changed.",
                    messages.INFO,
                )

        return super().response_change(request, obj)

    def history_view(self, request, object_id, extra_context=None):
        """Sort Django admin history newest-first."""
        from django.contrib.admin.views.main import PAGE_VAR

        response = super().history_view(request, object_id, extra_context)
        if not hasattr(response, "context_data"):
            return response  # redirect (obj not found) — let Django handle it

        # Re-paginate with reversed ordering (base uses oldest-first).
        qs = response.context_data["action_list"].paginator.object_list.order_by(
            "-action_time"
        )
        paginator = self.get_paginator(request, qs, 100)
        page_obj = paginator.get_page(request.GET.get(PAGE_VAR, 1))
        response.context_data["action_list"] = page_obj
        response.context_data["page_range"] = paginator.get_elided_page_range(
            page_obj.number
        )
        return response

    fieldsets = (
        (
            "Status & Metadata",
            {
                "fields": (
                    ("id", "status"),
                    ("submitted_at", "updated_at"),
                    "submission_ip_display",
                    "status_actions",
                    ("primary_maturity_tag", "secondary_maturity_tags"),
                ),
            },
        ),
        (
            "Last Change Summary",
            {
                "fields": ("last_change_summary_display",),
                "description": (
                    "Most recent field-level change — who made it and what was different. "
                    "Expand to review before acting on a status decision."
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "Change History",
            {
                "fields": ("change_history_display",),
                "description": (
                    "Field-level diff log for all edits — by submitter (web form), "
                    "admin (this interface), or API. "
                    "Each entry is collapsed; click to expand. "
                    "The History button (top right) shows the narrower admin-action log."
                ),
                "classes": ("collapse",),
            },
        ),
        (
            "A — General",
            {
                "fields": (
                    ("date_of_entry",),
                    (
                        "submitter_first_name",
                        "submitter_last_name",
                        "submitter_affiliation",
                    ),
                    "register_as_elixir",
                ),
            },
        ),
        (
            "B — Service Master Data",
            {
                "fields": (
                    "service_name",
                    "service_description",
                    ("year_established", "service_categories"),
                    ("is_toolbox", "toolbox_name"),
                    "user_knowledge_required",
                    ("edam_topics", "edam_operations"),
                    "publications_pmids",
                    ("logo", "logo_preview"),
                ),
            },
        ),
        (
            "C — Responsibilities",
            {
                "fields": (
                    "responsible_pis",
                    "associated_partner_note",
                    "host_institute",
                    "service_center",
                    ("public_contact_email",),
                    ("internal_contact_name", "internal_contact_email"),
                ),
            },
        ),
        (
            "D — Websites & Links",
            {
                "fields": (
                    ("website_url", "terms_of_use_url"),
                    ("licenses", "license_note"),
                    ("github_url", "biotools_url"),
                    ("fairsharing_url", "other_registry_url"),
                ),
            },
        ),
        (
            "E — KPIs",
            {
                "fields": (("kpi_monitoring", "kpi_start_year"),),
            },
        ),
        (
            "F — Discoverability & Outreach",
            {
                "fields": (
                    ("keywords_uncited", "keywords_seo"),
                    ("survey_participation",),
                    "comments",
                ),
            },
        ),
        (
            "G — Consent",
            {
                "fields": ("data_protection_consent",),
            },
        ),
        (
            "🔑 API Key Management",
            {
                "fields": ("key_management_panel",),
                "description": (
                    "Use the buttons below to issue, reset, or revoke API keys. "
                    "Plaintext keys are shown exactly once — copy them before dismissing."
                ),
            },
        ),
    )

    # ── Dynamic fieldset filtering ────────────────────────────────────────────

    @staticmethod
    def _strip_fields(fields: tuple, excluded: frozenset) -> tuple:
        """
        Remove *excluded* field names from a fieldset ``fields`` tuple.

        Handles both plain strings and inline row-tuples like ``("f1", "f2")``.
        Single-element row-tuples are unwrapped to plain strings so Django
        does not render an empty grid column.
        """
        result = []
        for item in fields:
            if isinstance(item, str):
                if item not in excluded:
                    result.append(item)
            else:
                # item is a tuple of field names displayed on one row
                filtered = tuple(f for f in item if f not in excluded)
                if filtered:
                    # Unwrap single-element tuples — Django renders them fine
                    # either way but a plain string is cleaner.
                    result.append(filtered if len(filtered) > 1 else filtered[0])
        return tuple(result)

    def get_fieldsets(self, request, obj=None):
        """
        Strip permission-gated display fields from the fieldsets for users
        who lack the corresponding permissions.

        Fields controlled here:
          submission_ip_display — superuser-only (IP address is PII)
          status_actions        — requires change_servicesubmission OR
                                  approve_servicesubmission
          key_management_panel  — requires manage_apikeys
        """
        excluded = set()

        if not request.user.is_superuser:
            excluded.add("submission_ip_display")

        if not (
            request.user.has_perm("submissions.change_servicesubmission")
            or request.user.has_perm("submissions.approve_servicesubmission")
        ):
            excluded.add("status_actions")

        if not request.user.has_perm("submissions.manage_apikeys"):
            excluded.add("key_management_panel")

        if not excluded:
            return self.fieldsets

        frozen = frozenset(excluded)
        result = []
        for title, options in self.fieldsets:
            filtered = self._strip_fields(options["fields"], frozen)
            if filtered:
                result.append((title, {**options, "fields": filtered}))
            # If filtered is empty the entire fieldset is dropped — this only
            # happens to "🔑 API Key Management" when manage_apikeys is absent.
        return result

    @admin.action(
        description="Assign maturity tags to selected submissions",
        permissions=["change"],
    )
    def action_assign_maturity_tags(self, request, queryset):
        """Bulk action: open a modal to assign maturity tags (AJAX only)."""
        approved_queryset = queryset.filter(status=SubmissionStatus.APPROVED)
        non_approved_count = queryset.count() - approved_queryset.count()

        if "_assign_tags" in request.POST:
            primary = request.POST.get("primary_maturity_tag", "").strip() or None
            secondary = request.POST.getlist("secondary_maturity_tags") or []

            if primary:
                valid_primary = dict(PRIMARY_MATURITY_TAG_CHOICES)
                if primary not in valid_primary:
                    return JsonResponse(
                        {"status": "error", "message": "Invalid primary tag selection."}
                    )

            valid_secondary = dict(SECONDARY_MATURITY_TAG_CHOICES)
            for tag in secondary:
                if tag not in valid_secondary:
                    return JsonResponse(
                        {"status": "error", "message": f"Invalid secondary tag: {tag}."}
                    )

            if not approved_queryset.exists():
                return JsonResponse(
                    {
                        "status": "error",
                        "message": "No approved submissions selected. Tags can only be assigned to approved services.",
                    }
                )

            username = getattr(request.user, "username", "admin")
            changed_by = f"{CHANGELOG_ACTOR_ADMIN_PREFIX}{username}"
            now = timezone.now()
            updated = 0

            with transaction.atomic():
                # Re-filter inside the transaction so any race-condition status
                # change between the outer filter and this update is excluded.
                approved_subs = queryset.filter(
                    status=SubmissionStatus.APPROVED
                ).select_for_update()
                primary_choices_dict = dict(PRIMARY_MATURITY_TAG_CHOICES)
                secondary_choices_dict = dict(SECONDARY_MATURITY_TAG_CHOICES)

                for sub in approved_subs:
                    changes = []
                    old_primary = sub.primary_maturity_tag
                    old_secondary = list(sub.secondary_maturity_tags or [])

                    if old_primary != primary:
                        changes.append(
                            {
                                "field": "primary_maturity_tag",
                                "label": "Primary Maturity Tag",
                                "old": str(
                                    primary_choices_dict.get(
                                        old_primary, old_primary or "—"
                                    )
                                ),
                                "new": str(
                                    primary_choices_dict.get(primary, primary or "—")
                                ),
                            }
                        )
                    if old_secondary != secondary:
                        changes.append(
                            {
                                "field": "secondary_maturity_tags",
                                "label": "Secondary Maturity Tags",
                                "old": ", ".join(
                                    str(secondary_choices_dict.get(t, t))
                                    for t in old_secondary
                                )
                                or "—",
                                "new": ", ".join(
                                    str(secondary_choices_dict.get(t, t))
                                    for t in secondary
                                )
                                or "—",
                            }
                        )

                    sub.primary_maturity_tag = primary
                    sub.secondary_maturity_tags = secondary
                    sub.save(
                        update_fields=[
                            "primary_maturity_tag",
                            "secondary_maturity_tags",
                        ]
                    )

                    if changes:
                        sub.last_change_summary = {
                            "changed_by": changed_by,
                            "changed_at": now.isoformat(),
                            "changes": changes,
                        }
                        sub.save(update_fields=["last_change_summary"])
                        SubmissionChangeLog.objects.create(
                            submission=sub,
                            changed_by=changed_by,
                            changed_at=now,
                            changes=changes,
                        )

                    updated += 1

            return JsonResponse(
                {
                    "status": "success",
                    "updated": updated,
                    "message": f"Maturity tags assigned to {updated} approved submission(s).",
                }
            )

        # First POST: validate queryset and return form fragment
        if not approved_queryset.exists():
            return JsonResponse(
                {
                    "status": "error",
                    "message": "No approved submissions selected. Tags can only be assigned to approved services.",
                }
            )

        primary_choices = [("", "None")] + list(PRIMARY_MATURITY_TAG_CHOICES)
        secondary_choices = list(SECONDARY_MATURITY_TAG_CHOICES)
        selected_pks = list(queryset.values_list("pk", flat=True))

        form_html = loader.render_to_string(
            "admin/submissions/assign_maturity_tags_partial.html",
            {
                "primary_choices": primary_choices,
                "secondary_choices": secondary_choices,
                "selected_pks": selected_pks,
            },
            request=request,
        )

        return JsonResponse(
            {
                "status": "form",
                "warning_count": non_approved_count,
                "form_html": form_html,
            }
        )

    actions = [
        "action_approve",
        "action_reject",
        "action_mark_under_review",
        "action_deprecate",
        "action_undeprecate",
        "action_assign_maturity_tags",
        "action_export_csv",
        "action_export_json",
    ]

    # ── List display helpers ──────────────────────────────────────────────────

    @admin.display(description="Submitter", ordering="submitter_last_name")
    def submitter_display(self, obj):
        return f"{obj.submitter_last_name}, {obj.submitter_first_name} — {obj.submitter_affiliation}"

    @admin.display(description="Service", ordering="service_name")
    def service_name_link(self, obj):
        from django.urls import reverse

        url = reverse("admin:submissions_servicesubmission_change", args=[obj.pk])
        return format_html(
            '<strong><a href="{}">{}</a></strong>', url, obj.service_name
        )

    @admin.display(description="Status", ordering="status")
    def status_badge(self, obj):
        colours = {
            "draft": ("#6b7280", "#f3f4f6"),
            "submitted": ("#1d4ed8", "#eff6ff"),
            "under_review": ("#92400e", "#fffbeb"),
            "approved": ("#166534", "#f0fdf4"),
            "rejected": ("#991b1b", "#fef2f2"),
            "deprecated": ("#374151", "#e5e7eb"),
        }
        text_col, bg_col = colours.get(obj.status, ("#6b7280", "#f3f4f6"))
        return format_html(
            '<span style="'
            "display:inline-block;font-size:.68rem;font-weight:700;"
            "letter-spacing:.04em;text-transform:uppercase;"
            "padding:2px 9px;border-radius:20px;"
            "color:{};background:{};white-space:nowrap"
            '">{}</span>',
            text_col,
            bg_col,
            obj.get_status_display(),
        )

    @admin.display(description="ELIXIR")
    def elixir_badge(self, obj):
        if obj.register_as_elixir:
            return mark_safe(
                '<span style="color:#0369a1;font-size:.75rem;font-weight:700">✓ ELIXIR</span>'
            )
        return mark_safe(
            '<span style="color:var(--body-quiet-color);font-size:.75rem">—</span>'
        )

    @admin.display(description="API Keys")
    def key_count(self, obj):
        # Use the prefetched api_keys cache — .filter().count() would hit the DB again
        keys = obj.api_keys.all()
        active = sum(1 for k in keys if k.is_active)
        total = len(keys)
        if active == 0:
            return format_html(
                '<span style="color:var(--body-quiet-color);font-size:.8rem">0 / {}</span>',
                total,
            )
        return format_html(
            '<span style="color:#166534;font-size:.8rem;font-weight:600">{}</span>'
            '<span style="color:var(--body-quiet-color);font-size:.8rem"> / {}</span>',
            active,
            total,
        )

    @admin.display(description="Keys")
    def api_key_link(self, obj):
        from django.urls import reverse

        url = (
            reverse("admin:submissions_submissionapikey_changelist")
            + f"?submission__id__exact={obj.pk}"
        )
        count = len(
            obj.api_keys.all()
        )  # len() uses prefetched cache; .count() does not
        return format_html(
            '<a href="{}" style="font-size:.8rem;white-space:nowrap"'
            ' title="Manage API keys for this submission">🔑 Manage ({})</a>',
            url,
            count,
        )

    @admin.display(description="Submission IP")
    def submission_ip_display(self, obj):
        return obj.submission_ip or "—"

    @admin.display(description="Change Status")
    def status_actions(self, obj):
        if not obj.pk:
            return "Save first."
        current = obj.status
        buttons = []
        status_opts = [
            ("_approve", "Approve", "#166534", "#f0fdf4", "#bbf7d0"),
            ("_reject", "Reject", "#991b1b", "#fef2f2", "#fecaca"),
            ("_under_review", "Mark Under Review", "#92400e", "#fffbeb", "#fde68a"),
            ("_deprecate", "Deprecate", "#374151", "#f9fafb", "#d1d5db"),
            (
                "_undeprecate",
                "Undeprecate → Submitted",
                "#1e40af",
                "#eff6ff",
                "#bfdbfe",
            ),
        ]
        for name, label, color, bg, border in status_opts:
            active = (
                (name == "_approve" and current == "approved")
                or (name == "_reject" and current == "rejected")
                or (name == "_under_review" and current == "under_review")
                or (name == "_deprecate" and current == "deprecated")
            )
            style = (
                f"background:{bg};color:{color};border:1.5px solid {border};"
                f"border-radius:5px;padding:.3rem .8rem;font-size:.8rem;"
                f"font-weight:700;cursor:{'default' if active else 'pointer'};"
                f"opacity:{'1' if active else '.85'};"
                f"{'box-shadow:0 0 0 2px ' + color + ';' if active else ''}"
            )
            check = "✓ " if active else ""
            buttons.append(
                f'<button type="submit" name="{name}" value="1" style="{style}" {"disabled" if active else ""}>'
                f"{check}{label}</button>"
            )
        tag_warning = ""
        if current == "approved" and (
            obj.primary_maturity_tag or obj.secondary_maturity_tags
        ):
            primary_label = obj.get_primary_maturity_tag_display() or ""
            secondary_labels = ", ".join(obj.get_secondary_maturity_tag_display_list())
            tag_summary = primary_label
            if secondary_labels:
                tag_summary += (
                    f" ({secondary_labels})" if primary_label else secondary_labels
                )
            tag_warning = format_html(
                '<p style="margin:.6rem 0 0;font-size:.78rem;color:#92400e;'
                "background:#fffbeb;border:1px solid #fde68a;border-radius:4px;"
                'padding:.3rem .6rem;">'
                "⚠ Unapproving will automatically clear maturity tags: "
                "<strong>{}</strong></p>",
                tag_summary,
            )

        return format_html(
            '<div style="display:flex;gap:.5rem;flex-wrap:wrap">{}</div>{}',
            mark_safe("".join(buttons)),
            tag_warning,
        )

    @admin.display(description="API Key Actions")
    def key_management_panel(self, obj):
        if not obj.pk:
            return "Save the record first."
        return mark_safe(
            """
            <div style="display:flex;gap:.5rem;flex-wrap:wrap;align-items:center">
              <button type="submit" name="_issue_new_key" value="1"
                style="background:#5c9d25;color:#fff;border:none;border-radius:6px;
                       padding:.38rem .85rem;font-size:.82rem;font-weight:600;cursor:pointer">
                Issue new key
              </button>
              <button type="submit" name="_reset_key" value="1"
                style="background:#d97706;color:#fff;border:none;border-radius:6px;
                       padding:.38rem .85rem;font-size:.82rem;font-weight:600;cursor:pointer">
                Reset (revoke all + issue one)
              </button>
              <button type="submit" name="_revoke_all_keys" value="1"
                style="background:#dc3545;color:#fff;border:none;border-radius:6px;
                       padding:.38rem .85rem;font-size:.82rem;font-weight:600;cursor:pointer">
                Revoke all keys
              </button>
            </div>
            <p style="margin:.4rem 0 0;font-size:.78rem;color:var(--body-quiet-color)">
              New key label (optional):
              <input type="text" name="new_key_label"
                     placeholder="e.g. &quot;Admin reset 2026-03&quot;"
                     style="border:1px solid var(--border-color);border-radius:5px;
                            background:var(--body-bg);color:var(--body-fg);
                            padding:.25rem .5rem;font-size:.78rem;width:260px;margin-left:.3rem">
            </p>
            """
        )

    # ── Actions ──────────────────────────────────────────────────────────────

    def _change_status(self, request, queryset, new_status, label):
        updated = 0
        tags_cleared = 0
        for sub in queryset:
            if sub.status == new_status:
                continue
            old = sub.status
            # Capture display values before mutation so the diff shows human labels.
            old_status_display = sub.get_status_display() or old
            old_primary_tag = sub.primary_maturity_tag
            old_secondary_tags = list(sub.secondary_maturity_tags or [])

            sub.status = new_status
            update_fields = ["status"]
            log_parts = [f"Status changed {old} → {new_status}"]
            # Auto-clear maturity tags when moving away from approved — tags are
            # only valid on approved services and should not persist after de-approval.
            if new_status != SubmissionStatus.APPROVED and (
                sub.primary_maturity_tag or sub.secondary_maturity_tags
            ):
                sub.primary_maturity_tag = None
                sub.secondary_maturity_tags = []
                update_fields += ["primary_maturity_tag", "secondary_maturity_tags"]
                log_parts.append("maturity tags cleared")
                tags_cleared += 1

            # Build field-level diff for the audit log (mirrors build_diff format).
            # Done before save() so last_change_summary is written atomically with
            # the status mutation in a single round-trip.
            new_status_display = sub.get_status_display() or new_status
            changes = [
                {
                    "field": "status",
                    "label": "Status",
                    "old": old_status_display,
                    "new": new_status_display,
                }
            ]
            if old_primary_tag:
                old_primary_display = str(
                    dict(PRIMARY_MATURITY_TAG_CHOICES).get(
                        old_primary_tag, old_primary_tag
                    )
                )
                changes.append(
                    {
                        "field": "primary_maturity_tag",
                        "label": "Primary Maturity Tag",
                        "old": old_primary_display,
                        "new": "—",
                    }
                )
            if old_secondary_tags:
                old_secondary_display = ", ".join(
                    str(dict(SECONDARY_MATURITY_TAG_CHOICES).get(t, t))
                    for t in old_secondary_tags
                )
                changes.append(
                    {
                        "field": "secondary_maturity_tags",
                        "label": "Secondary Maturity Tags",
                        "old": old_secondary_display,
                        "new": "—",
                    }
                )

            username = getattr(request.user, "username", "admin")
            changed_by = f"{CHANGELOG_ACTOR_ADMIN_PREFIX}{username}"
            now = timezone.now()
            sub.last_change_summary = {
                "changed_by": changed_by,
                "changed_at": now.isoformat(),
                "changes": changes,
            }
            update_fields.append("last_change_summary")
            sub.save(update_fields=update_fields)
            SubmissionChangeLog.objects.create(
                submission=sub,
                changed_by=changed_by,
                changed_at=now,
                changes=changes,
            )

            send_submission_notification.delay(str(sub.id), event="status_changed")
            self._log(request, sub, "; ".join(log_parts))
            updated += 1
        self.message_user(
            request, f"{updated} submission(s) marked as {label}.", messages.SUCCESS
        )
        if tags_cleared:
            self.message_user(
                request,
                f"⚠ Maturity tags automatically cleared on {tags_cleared} submission(s) "
                "that were moved away from Approved status.",
                messages.WARNING,
            )

    # permissions=["approve_servicesubmission"] causes Django to call
    # self.has_approve_servicesubmission_permission(request) before showing
    # this action in the dropdown.  The body guard handles direct POST attacks.
    @admin.action(
        description="✅ Approve selected",
        permissions=["approve_servicesubmission"],
    )
    def action_approve(self, request, queryset):
        if not self._require_perm(
            request, "approve_servicesubmission", "approve submissions"
        ):
            return
        self._change_status(request, queryset, "approved", "Approved")

    @admin.action(
        description="❌ Reject selected",
        permissions=["approve_servicesubmission"],
    )
    def action_reject(self, request, queryset):
        if not self._require_perm(
            request, "approve_servicesubmission", "reject submissions"
        ):
            return
        self._change_status(request, queryset, "rejected", "Rejected")

    @admin.action(
        description="🔍 Mark as Under Review",
        permissions=["change"],
    )
    def action_mark_under_review(self, request, queryset):
        if not self._require_perm(
            request, "change_servicesubmission", "mark submissions as under review"
        ):
            return
        self._change_status(request, queryset, "under_review", "Under Review")

    @admin.action(
        description="🚫 Deprecate selected",
        permissions=["change"],
    )
    def action_deprecate(self, request, queryset):
        if not self._require_perm(
            request, "change_servicesubmission", "deprecate submissions"
        ):
            return
        self._change_status(request, queryset, "deprecated", "Deprecated")

    @admin.action(
        description="♻️ Undeprecate selected (→ Submitted)",
        permissions=["change"],
    )
    def action_undeprecate(self, request, queryset):
        if not self._require_perm(
            request, "change_servicesubmission", "undeprecate submissions"
        ):
            return
        self._change_status(request, queryset, "submitted", "Submitted (undeprecated)")

    def _export_queryset(self, queryset):
        """Return a fully prefetched queryset suitable for both export actions."""
        return queryset.select_related(
            "service_center", "biotoolsrecord"
        ).prefetch_related(
            "service_categories",
            "responsible_pis",
            "edam_topics",
            "edam_operations",
            "licenses",
            "biotoolsrecord__functions",
        )

    def _logo_url(self, request, submission):
        if submission.logo:
            return request.build_absolute_uri(submission.logo.url)
        return ""

    def _biotools_data(self, submission):
        bt = getattr(submission, "biotoolsrecord", None)
        if bt is None:
            return {
                "biotools_id": "",
                "biotools_name": "",
                "biotools_description": "",
                "biotools_homepage": "",
                "biotools_version": "",
                "biotools_license": "",
                "biotools_maturity": "",
                "biotools_cost": "",
                "biotools_tool_type": [],
                "biotools_operating_system": [],
                "biotools_edam_topic_uris": [],
                "biotools_edam_operation_uris": [],
                "biotools_functions": [],
                "biotools_publications": [],
                "biotools_documentation": [],
                "biotools_download": [],
                "biotools_links": [],
                "biotools_last_synced_at": "",
            }
        ops = []
        functions = []
        for fn in bt.functions.all():
            ops.extend(op["uri"] for op in (fn.operations or []) if op.get("uri"))
            functions.append(
                {
                    "operations": fn.operations or [],
                    "inputs": fn.inputs or [],
                    "outputs": fn.outputs or [],
                    "cmd": fn.cmd,
                    "note": fn.note,
                }
            )
        return {
            "biotools_id": bt.biotools_id,
            "biotools_name": bt.name or "",
            "biotools_description": bt.description or "",
            "biotools_homepage": bt.homepage or "",
            "biotools_version": bt.version or "",
            "biotools_license": bt.license or "",
            "biotools_maturity": bt.maturity or "",
            "biotools_cost": bt.cost or "",
            "biotools_tool_type": bt.tool_type or [],
            "biotools_operating_system": bt.operating_system or [],
            "biotools_edam_topic_uris": bt.edam_topic_uris or [],
            "biotools_edam_operation_uris": ops,
            "biotools_functions": functions,
            "biotools_publications": bt.publications or [],
            "biotools_documentation": bt.documentation or [],
            "biotools_download": bt.download or [],
            "biotools_links": bt.links or [],
            "biotools_last_synced_at": bt.last_synced_at.isoformat()
            if bt.last_synced_at
            else "",
        }

    @admin.action(description="📥 Export selected as CSV", permissions=["view"])
    def action_export_csv(self, request, queryset):
        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="submissions.csv"'
        w = csv.writer(resp)
        w.writerow(
            [
                "id",
                "status",
                "primary_maturity_tag",
                "secondary_maturity_tags",
                "date_of_entry",
                "service_name",
                "service_description",
                "year_established",
                "submitter_first_name",
                "submitter_last_name",
                "submitter_affiliation",
                "host_institute",
                "service_center",
                "public_contact_email",
                "internal_contact_name",
                "internal_contact_email",
                "service_categories",
                "responsible_pis",
                "edam_topics",
                "edam_operations",
                "is_toolbox",
                "toolbox_name",
                "user_knowledge_required",
                "publications_pmids",
                "website_url",
                "terms_of_use_url",
                "licenses",
                "license_note",
                "github_url",
                "biotools_url",
                "fairsharing_url",
                "other_registry_url",
                "kpi_monitoring",
                "kpi_start_year",
                "associated_partner_note",
                "keywords_uncited",
                "keywords_seo",
                "register_as_elixir",
                "survey_participation",
                "comments",
                "logo_url",
                "biotools_id",
                "biotools_name",
                "biotools_description",
                "biotools_homepage",
                "biotools_version",
                "biotools_license",
                "biotools_maturity",
                "biotools_cost",
                "biotools_tool_type",
                "biotools_operating_system",
                "biotools_edam_topic_uris",
                "biotools_edam_operation_uris",
                "biotools_functions",
                "biotools_publications",
                "biotools_documentation",
                "biotools_download",
                "biotools_links",
                "biotools_last_synced_at",
                "submitted_at",
                "updated_at",
            ]
        )
        for s in self._export_queryset(queryset):
            bt = self._biotools_data(s)
            w.writerow(
                [
                    str(s.id),
                    s.status,
                    str(s.get_primary_maturity_tag_display())
                    if s.primary_maturity_tag
                    else "",
                    "; ".join(s.get_secondary_maturity_tag_display_list())
                    if s.secondary_maturity_tags
                    else "",
                    s.date_of_entry.isoformat() if s.date_of_entry else "",
                    s.service_name,
                    s.service_description,
                    s.year_established,
                    s.submitter_first_name,
                    s.submitter_last_name,
                    s.submitter_affiliation,
                    s.host_institute,
                    str(s.service_center),
                    s.public_contact_email,
                    s.internal_contact_name,
                    s.internal_contact_email,
                    "; ".join(c.name for c in s.service_categories.all()),
                    "; ".join(
                        f"{pi.first_name} {pi.last_name}".strip()
                        for pi in s.responsible_pis.all()
                    ),
                    "; ".join(f"{t.label} ({t.uri})" for t in s.edam_topics.all()),
                    "; ".join(f"{t.label} ({t.uri})" for t in s.edam_operations.all()),
                    s.is_toolbox,
                    s.toolbox_name,
                    s.user_knowledge_required,
                    s.publications_pmids,
                    s.website_url,
                    s.terms_of_use_url,
                    "; ".join(lic.license_id for lic in s.licenses.all()),
                    s.license_note,
                    s.github_url,
                    s.biotools_url,
                    s.fairsharing_url,
                    s.other_registry_url,
                    s.kpi_monitoring,
                    s.kpi_start_year,
                    s.associated_partner_note,
                    s.keywords_uncited,
                    s.keywords_seo,
                    s.register_as_elixir,
                    s.survey_participation,
                    s.comments,
                    self._logo_url(request, s),
                    bt["biotools_id"],
                    bt["biotools_name"],
                    bt["biotools_description"],
                    bt["biotools_homepage"],
                    bt["biotools_version"],
                    bt["biotools_license"],
                    bt["biotools_maturity"],
                    bt["biotools_cost"],
                    "; ".join(bt["biotools_tool_type"]),
                    "; ".join(bt["biotools_operating_system"]),
                    "; ".join(bt["biotools_edam_topic_uris"]),
                    "; ".join(bt["biotools_edam_operation_uris"]),
                    json.dumps(bt["biotools_functions"]),
                    json.dumps(bt["biotools_publications"]),
                    json.dumps(bt["biotools_documentation"]),
                    json.dumps(bt["biotools_download"]),
                    json.dumps(bt["biotools_links"]),
                    bt["biotools_last_synced_at"],
                    s.submitted_at.isoformat(),
                    s.updated_at.isoformat(),
                ]
            )
        return resp

    @admin.action(description="📥 Export selected as JSON", permissions=["view"])
    def action_export_json(self, request, queryset):
        resp = HttpResponse(content_type="application/json")
        resp["Content-Disposition"] = 'attachment; filename="submissions.json"'
        data = []
        for s in self._export_queryset(queryset):
            bt = self._biotools_data(s)
            data.append(
                {
                    "id": str(s.id),
                    "status": s.status,
                    "primary_maturity_tag": s.primary_maturity_tag,
                    "secondary_maturity_tags": s.secondary_maturity_tags,
                    "date_of_entry": s.date_of_entry.isoformat()
                    if s.date_of_entry
                    else "",
                    "service_name": s.service_name,
                    "service_description": s.service_description,
                    "year_established": s.year_established,
                    "submitter": {
                        "first_name": s.submitter_first_name,
                        "last_name": s.submitter_last_name,
                        "affiliation": s.submitter_affiliation,
                    },
                    "host_institute": s.host_institute,
                    "service_center": str(s.service_center),
                    "public_contact_email": s.public_contact_email,
                    "internal_contact_name": s.internal_contact_name,
                    "internal_contact_email": s.internal_contact_email,
                    "service_categories": [c.name for c in s.service_categories.all()],
                    "responsible_pis": [
                        f"{pi.first_name} {pi.last_name}".strip()
                        for pi in s.responsible_pis.all()
                    ],
                    "edam_topics": [
                        {"label": t.label, "uri": t.uri} for t in s.edam_topics.all()
                    ],
                    "edam_operations": [
                        {"label": t.label, "uri": t.uri}
                        for t in s.edam_operations.all()
                    ],
                    "is_toolbox": s.is_toolbox,
                    "toolbox_name": s.toolbox_name,
                    "user_knowledge_required": s.user_knowledge_required,
                    "publications_pmids": s.publications_pmids,
                    "website_url": s.website_url,
                    "terms_of_use_url": s.terms_of_use_url,
                    "licenses": [lic.license_id for lic in s.licenses.all()],
                    "license_note": s.license_note,
                    "github_url": s.github_url,
                    "biotools_url": s.biotools_url,
                    "fairsharing_url": s.fairsharing_url,
                    "other_registry_url": s.other_registry_url,
                    "kpi_monitoring": s.kpi_monitoring,
                    "kpi_start_year": s.kpi_start_year,
                    "associated_partner_note": s.associated_partner_note,
                    "keywords_uncited": s.keywords_uncited,
                    "keywords_seo": s.keywords_seo,
                    "register_as_elixir": s.register_as_elixir,
                    "survey_participation": s.survey_participation,
                    "comments": s.comments,
                    "logo_url": self._logo_url(request, s),
                    "biotools": bt,
                    "submitted_at": s.submitted_at.isoformat(),
                    "updated_at": s.updated_at.isoformat(),
                }
            )
        json.dump(data, resp, indent=2)
        return resp

    def _revoke_all_keys(self, request, sub):
        n = SubmissionAPIKey.objects.filter(submission=sub, is_active=True).update(
            is_active=False
        )
        self._log(request, sub, f"Revoked {n} active key(s).")
        self.message_user(
            request,
            f"Revoked {n} active key(s) for '{sub.service_name}'.",
            messages.WARNING,
        )

    def _reset_key(self, request, sub):
        SubmissionAPIKey.objects.filter(submission=sub, is_active=True).update(
            is_active=False
        )
        label = f"Admin reset {timezone.now().strftime('%Y-%m-%d')} by {request.user.username}"
        key_obj, plaintext = SubmissionAPIKey.create_for_submission(
            submission=sub,
            label=label,
            created_by=request.user.username,
        )
        self._log(request, sub, f"Reset API key. New prefix: {key_obj.key_hash[:16]}")
        self.message_user(
            request,
            format_html(
                "All previous keys revoked. New API key "
                "(<strong>shown once only — copy now</strong>):"
                "<br><code>{}</code>",
                plaintext,
            ),
            messages.WARNING,
        )

    def _issue_new_key(self, request, sub):
        label = request.POST.get("new_key_label", "").strip() or (
            f"Admin key {timezone.now().strftime('%Y-%m-%d')} by {request.user.username}"
        )
        scope = request.POST.get("new_key_scope", "write")
        if scope not in ("read", "write"):
            scope = "write"
        key_obj, plaintext = SubmissionAPIKey.create_for_submission(
            submission=sub,
            label=label,
            created_by=request.user.username,
            scope=scope,
        )
        self._log(
            request, sub, f"Issued new key '{label}'. Prefix: {key_obj.key_hash[:16]}"
        )
        self.message_user(
            request,
            format_html(
                "New API key issued — label: <em>{}</em>. "
                "<strong>Copy now — shown once only:</strong>"
                "<br><code>{}</code>",
                label,
                plaintext,
            ),
            messages.WARNING,
        )

    # ── Logging ───────────────────────────────────────────────────────────────

    def _log(self, request, obj, message: str):
        LogEntry.objects.log_actions(
            user_id=request.user.pk,
            queryset=obj.__class__.objects.filter(pk=obj.pk),
            action_flag=CHANGE,
            change_message=message,
        )

    # ── Deletion — audit trail + confirmation warning ─────────────────────────

    def _write_deletion_audit(self, request, obj):
        """Snapshot the submission and its changelog before cascade-deletion."""
        changelog_entries = list(
            obj.change_log.values("changed_by", "changed_at", "changes")
        )
        # Convert datetime objects to ISO strings for JSON serialisation.
        for entry in changelog_entries:
            if hasattr(entry.get("changed_at"), "isoformat"):
                entry["changed_at"] = entry["changed_at"].isoformat()
        SubmissionDeletionAudit.objects.create(
            submission_id=obj.pk,
            service_name=obj.service_name,
            status=obj.status,
            submitter_first_name=obj.submitter_first_name,
            submitter_last_name=obj.submitter_last_name,
            submitter_affiliation=obj.submitter_affiliation,
            public_contact_email=obj.public_contact_email,
            deleted_by=f"admin:{request.user.username}",
            changelog_count=len(changelog_entries),
            changelog_snapshot=changelog_entries,
        )

    def delete_model(self, request, obj):
        self._write_deletion_audit(request, obj)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset.prefetch_related("change_log"):
            self._write_deletion_audit(request, obj)
        super().delete_queryset(request, queryset)

    def delete_view(self, request, object_id, extra_context=None):
        from django.urls import reverse

        obj = self.get_object(request, object_id)
        extra = extra_context or {}
        if obj is not None:
            extra["changelog_count"] = obj.change_log.count()
            extra["deprecated_url"] = reverse(
                "admin:submissions_servicesubmission_change", args=[obj.pk]
            )
        return super().delete_view(request, object_id, extra_context=extra)


@admin.register(SubmissionAPIKey)
class SubmissionAPIKeyAdmin(admin.ModelAdmin):
    """
    Change view for a single API key — shows the key details plus a full
    key-management panel covering ALL keys for the same submission.
    This mirrors the panel on ServiceSubmissionAdmin so admins can manage
    keys from either place.
    """

    list_display = (
        "label",
        "submission_link",
        "scope_badge",
        "status_badge",
        "created_by",
        "created_at",
        "last_used_at",
    )
    list_display_links = ("label",)
    list_filter = ("is_active", "submission__status")
    search_fields = ("submission__service_name", "label", "created_by")
    readonly_fields = (
        "id",
        "key_hash",
        "submission_link",
        "created_at",
        "last_used_at",
        "sibling_key_panel",
    )
    fieldsets = (
        (
            "This Key",
            {
                "fields": (("label", "is_active"), ("submission", "created_by")),
            },
        ),
        (
            "🔑 All Keys for This Submission",
            {
                "fields": ("sibling_key_panel",),
                "description": (
                    "Issue, reset, or revoke keys for the submission this key belongs to. "
                    "Plaintext keys are shown exactly once — copy before dismissing."
                ),
            },
        ),
        (
            "Audit",
            {
                "fields": (("id", "key_hash"), ("created_at", "last_used_at")),
                "classes": ("collapse",),
            },
        ),
    )
    ordering = ("-created_at",)
    save_on_top = True
    list_select_related = ("submission",)

    def get_fieldsets(self, request, obj=None):
        if obj is None:
            # Simplified fieldset for the Add form — sibling_key_panel requires
            # an existing pk and is meaningless before the key is created.
            return [
                (
                    "New API Key",
                    {
                        "description": (
                            "The plaintext key will be displayed once after saving. "
                            "Copy it immediately — it cannot be retrieved later."
                        ),
                        "fields": ("submission", "label", "scope", "created_by"),
                    },
                ),
            ]
        return super().get_fieldsets(request, obj)

    def save_model(self, request, obj, form, change):
        if not change:
            # The standard save path leaves key_hash="" (the field is non-editable
            # so the form never sets it), which violates the unique constraint on
            # the second key created.  Route through create_for_submission() so the
            # plaintext is generated and only its SHA-256 hash is persisted.
            key_obj, plaintext = SubmissionAPIKey.create_for_submission(
                submission=obj.submission,
                label=obj.label,
                created_by=obj.created_by or request.user.username,
                scope=obj.scope,
            )
            # Sync the in-memory instance so Django's post-save steps
            # (LogEntry creation, response_add redirect) see the real object.
            obj.pk = key_obj.pk
            obj.key_hash = key_obj.key_hash
            obj.created_at = key_obj.created_at
            # Stash the plaintext so response_add can surface it to the admin.
            request._new_api_key_plaintext = plaintext
        else:
            super().save_model(request, obj, form, change)

    def response_add(self, request, obj, post_url_continue=None):
        plaintext = getattr(request, "_new_api_key_plaintext", None)
        if plaintext:
            self.message_user(
                request,
                format_html(
                    "API key created. <strong>Copy this key now — it will not be shown again:</strong>"
                    "<br><code style='font-size:1.1em;user-select:all'>{}</code>",
                    plaintext,
                ),
                messages.WARNING,
            )
        return super().response_add(request, obj, post_url_continue)

    # ── List helpers ─────────────────────────────────────────────────────────

    @admin.display(description="Submission", ordering="submission__service_name")
    def submission_link(self, obj):
        from django.urls import reverse

        url = reverse(
            "admin:submissions_servicesubmission_change", args=[obj.submission_id]
        )
        return format_html(
            '<a href="{}" title="Open this submission">{}</a>',
            url,
            obj.submission,
        )

    @admin.display(description="Status")
    def status_badge(self, obj):
        if obj.is_active:
            return mark_safe(
                '<span style="color:var(--link-fg);font-weight:700;font-size:.8rem">● Active</span>'
            )
        return mark_safe(
            '<span style="color:var(--body-quiet-color);font-size:.8rem">○ Revoked</span>'
        )

    @admin.display(description="Scope")
    def scope_badge(self, obj):
        if obj.scope == "write":
            return mark_safe(
                '<span style="background:#dbeafe;color:#1e40af;border-radius:4px;'
                'padding:2px 7px;font-size:.7rem;font-weight:700">✏ read-write</span>'
            )
        return mark_safe(
            '<span style="background:#f0fdf4;color:#166534;border-radius:4px;'
            'padding:2px 7px;font-size:.7rem;font-weight:700">👁 read-only</span>'
        )

    # ── Sibling key panel (all keys for same submission) ─────────────────────

    @admin.display(description="Key Management")
    def sibling_key_panel(self, obj):
        if not obj.pk or not obj.submission_id:
            return "Save this key first."

        sub = obj.submission
        siblings = SubmissionAPIKey.objects.filter(submission=sub).order_by(
            "-created_at"
        )

        rows = []
        for k in siblings:
            active_style = (
                "color:var(--link-fg);font-weight:700"
                if k.is_active
                else "color:var(--body-quiet-color)"
            )
            status_html = "● Active" if k.is_active else "○ Revoked"
            this_marker = " ◀ this key" if k.pk == obj.pk else ""
            highlight = (
                "background:var(--darkened-bg);outline:2px solid var(--link-fg);"
                if k.pk == obj.pk
                else ""
            )
            scope_html = (
                '<span style="background:#1e40af;color:#fff;border-radius:3px;padding:1px 5px;font-size:.7rem;font-weight:700">✏ rw</span>'
                if k.scope == "write"
                else '<span style="background:#166534;color:#fff;border-radius:3px;padding:1px 5px;font-size:.7rem;font-weight:700">👁 ro</span>'
            )
            rows.append(
                format_html(
                    '<tr style="{highlight}">'
                    '<td style="padding:.3rem .6rem;font-family:monospace;font-size:.75rem;color:var(--body-fg)">{hash}…</td>'
                    '<td style="padding:.3rem .6rem;font-size:.8rem;color:var(--body-fg)">{label}{marker}</td>'
                    '<td style="padding:.3rem .6rem;font-size:.8rem;color:var(--body-fg)">{created_by}</td>'
                    '<td style="padding:.3rem .6rem;font-size:.8rem;color:var(--body-fg)">{created_at}</td>'
                    '<td style="padding:.3rem .6rem;font-size:.8rem;{active_style}">{status}</td>'
                    '<td style="padding:.3rem .6rem">{scope}</td>'
                    "</tr>",
                    highlight=highlight,
                    hash=k.key_hash[:16],
                    label=k.label,
                    marker=this_marker,
                    created_by=k.created_by,
                    created_at=k.created_at.strftime("%Y-%m-%d %H:%M"),
                    active_style=active_style,
                    status=status_html,
                    scope=mark_safe(scope_html),
                )
            )

        table_html = mark_safe(
            '<table style="width:100%;border-collapse:collapse;margin-bottom:.8rem;'
            'border:1px solid var(--border-color);border-radius:6px;overflow:hidden">'
            '<thead><tr style="background:var(--darkened-bg)">'
            '<th style="padding:.3rem .6rem;font-size:.7rem;font-weight:700;text-transform:uppercase;'
            'letter-spacing:.05em;color:var(--body-quiet-color);text-align:left">Hash prefix</th>'
            '<th style="padding:.3rem .6rem;font-size:.7rem;font-weight:700;text-transform:uppercase;'
            'letter-spacing:.05em;color:var(--body-quiet-color);text-align:left">Label</th>'
            '<th style="padding:.3rem .6rem;font-size:.7rem;font-weight:700;text-transform:uppercase;'
            'letter-spacing:.05em;color:var(--body-quiet-color);text-align:left">Created by</th>'
            '<th style="padding:.3rem .6rem;font-size:.7rem;font-weight:700;text-transform:uppercase;'
            'letter-spacing:.05em;color:var(--body-quiet-color);text-align:left">Created at</th>'
            '<th style="padding:.3rem .6rem;font-size:.7rem;font-weight:700;text-transform:uppercase;'
            'letter-spacing:.05em;color:var(--body-quiet-color);text-align:left">Status</th>'
            '<th style="padding:.3rem .6rem;font-size:.7rem;font-weight:700;text-transform:uppercase;'
            'letter-spacing:.05em;color:var(--body-quiet-color);text-align:left">Scope</th>'
            "</tr></thead>"
            "<tbody>" + "".join(rows) + "</tbody>"
            "</table>"
        )

        buttons_html = (
            '<div style="display:flex;gap:.5rem;flex-wrap:wrap;align-items:center;margin-bottom:.5rem">'
            '<button type="submit" name="_issue_new_key" value="1" '
            'style="background:#5c9d25;color:#fff;border:none;border-radius:6px;'
            'padding:.38rem .85rem;font-size:.82rem;font-weight:600;cursor:pointer">'
            "Issue new key</button>"
            '<button type="submit" name="_reset_key" value="1" '
            'style="background:#d97706;color:#fff;border:none;border-radius:6px;'
            'padding:.38rem .85rem;font-size:.82rem;font-weight:600;cursor:pointer">'
            "Reset (revoke all + issue one)</button>"
            '<button type="submit" name="_revoke_all_keys" value="1" '
            'style="background:#dc3545;color:#fff;border:none;border-radius:6px;'
            'padding:.38rem .85rem;font-size:.82rem;font-weight:600;cursor:pointer">'
            "Revoke all keys</button>"
            "</div>"
            '<p style="margin:.3rem 0 0;font-size:.78rem;color:var(--body-quiet-color);'
            'display:flex;gap:.6rem;align-items:center;flex-wrap:wrap">'
            '<span>Label (optional): <input type="text" name="new_key_label" '
            'placeholder="e.g. CI pipeline 2026" '
            'style="border:1px solid var(--border-color);border-radius:5px;padding:.25rem .5rem;'
            "background:var(--body-bg);color:var(--body-fg);"
            'font-size:.78rem;width:180px;margin-left:.4rem"></span>'
            '<span>Scope: <select name="new_key_scope" '
            'style="border:1px solid var(--border-color);border-radius:5px;padding:.25rem .4rem;'
            'background:var(--body-bg);color:var(--body-fg);font-size:.78rem;margin-left:.3rem">'
            '<option value="write" selected>read-write</option>'
            '<option value="read">read-only</option>'
            "</select></span>"
            "</p>"
        )

        return mark_safe(table_html + buttons_html)

    # ── Key actions delegated to ServiceSubmissionAdmin helpers ──────────────

    def response_change(self, request, obj):
        sub = obj.submission
        # All key-management operations require manage_apikeys.  Check here as
        # the authoritative security gate — do not rely on UI visibility alone.
        _key_ops = ("_revoke_all_keys", "_reset_key", "_issue_new_key")
        if any(op in request.POST for op in _key_ops):
            if not request.user.has_perm("submissions.manage_apikeys"):
                self.message_user(
                    request,
                    "Permission denied — you need 'manage_apikeys' to manage API keys.",
                    messages.ERROR,
                )
                return super().response_change(request, obj)

        if "_revoke_all_keys" in request.POST:
            n = SubmissionAPIKey.objects.filter(submission=sub, is_active=True).update(
                is_active=False
            )
            self._log_key_action(
                request, obj, f"Revoked {n} active key(s) via key admin."
            )
            self.message_user(
                request,
                f"Revoked {n} active key(s) for '{sub.service_name}'.",
                messages.WARNING,
            )
        elif "_reset_key" in request.POST:
            SubmissionAPIKey.objects.filter(submission=sub, is_active=True).update(
                is_active=False
            )
            label = f"Admin reset {timezone.now().strftime('%Y-%m-%d')} by {request.user.username}"
            key_obj, plaintext = SubmissionAPIKey.create_for_submission(
                submission=sub,
                label=label,
                created_by=request.user.username,
            )
            self._log_key_action(
                request, obj, f"Reset all keys. New prefix: {key_obj.key_hash[:16]}"
            )
            self.message_user(
                request,
                format_html(
                    "All previous keys revoked. New key "
                    "(<strong>shown once — copy now</strong>):<br><code>{}</code>",
                    plaintext,
                ),
                messages.WARNING,
            )
        elif "_issue_new_key" in request.POST:
            label = request.POST.get("new_key_label", "").strip() or (
                f"Admin key {timezone.now().strftime('%Y-%m-%d')} by {request.user.username}"
            )
            scope = request.POST.get("new_key_scope", "write")
            if scope not in ("read", "write"):
                scope = "write"
            key_obj, plaintext = SubmissionAPIKey.create_for_submission(
                submission=sub,
                label=label,
                created_by=request.user.username,
                scope=scope,
            )
            self._log_key_action(
                request,
                obj,
                f"Issued new key '{label}'. Prefix: {key_obj.key_hash[:16]}",
            )
            self.message_user(
                request,
                format_html(
                    "New key issued — label: <em>{}</em>. "
                    "<strong>Copy now — shown once only:</strong><br><code>{}</code>",
                    label,
                    plaintext,
                ),
                messages.WARNING,
            )
        return super().response_change(request, obj)

    def _log_key_action(self, request, key_obj, message):
        LogEntry.objects.log_actions(
            user_id=request.user.pk,
            queryset=key_obj.__class__.objects.filter(pk=key_obj.pk),
            action_flag=CHANGE,
            change_message=message,
        )

    # ── Permission gates ──────────────────────────────────────────────────────
    # All key-management operations — viewing included — are gated on a single
    # semantic permission to keep authorisation logic simple and auditable.

    def has_view_permission(self, request, obj=None):
        # Viewers can see key metadata (label, hash prefix, status);
        # they cannot see plaintext keys (never stored) or perform operations.
        return request.user.has_perm(
            "submissions.view_submissionapikey"
        ) or request.user.has_perm("submissions.manage_apikeys")

    def has_add_permission(self, request):
        return request.user.has_perm("submissions.manage_apikeys")

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm("submissions.manage_apikeys")

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm("submissions.manage_apikeys")


# ---------------------------------------------------------------------------
# SubmissionChangeLog — read-only audit-log admin
# ---------------------------------------------------------------------------


@admin.register(SubmissionChangeLog)
class SubmissionChangeLogAdmin(admin.ModelAdmin):
    """
    Read-only admin view for the field-level change audit log.

    This log is append-only and written automatically by the system —
    no human should ever add, edit, or delete entries.  The admin exposes
    it purely for auditing purposes.

    Access requires view_submissionchangelog permission (held by all three
    standard role groups: Viewer, Editor, Manager).
    """

    list_display = (
        "submission_link",
        "changed_by",
        "changed_at_link",
        "field_count",
    )
    list_display_links = None
    list_filter = ("changed_by", ("changed_at", admin.DateFieldListFilter))
    search_fields = ("submission__service_name", "changed_by")
    ordering = ("-changed_at",)
    date_hierarchy = "changed_at"
    list_per_page = 50
    list_select_related = ("submission",)

    readonly_fields = (
        "submission",
        "changed_by",
        "changed_at",
        "changes_display",
    )

    fieldsets = (
        (
            None,
            {
                "fields": ("submission", "changed_by", "changed_at"),
            },
        ),
        (
            "Changed fields",
            {
                "fields": ("changes_display",),
            },
        ),
    )

    # ── Permission gates — entirely read-only ────────────────────────────────

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # Defer to Django's standard model-permission check so that cascade
        # deletions (when a parent submission is deleted) are not blocked for
        # superusers.  Regular role groups (Viewer/Editor/Manager) do not hold
        # delete_submissionchangelog, so individual entry deletion remains
        # blocked for them.
        return super().has_delete_permission(request, obj)

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("submissions.view_submissionchangelog")

    # ── List helpers ─────────────────────────────────────────────────────────

    @admin.display(description="Submission", ordering="submission__service_name")
    def submission_link(self, obj):
        from django.urls import reverse

        url = reverse("admin:submissions_submissionchangelog_changelist")
        return format_html(
            '<a href="{}?submission__id={}" title="View all change log entries for this submission">{}</a>',
            url,
            obj.submission_id,
            obj.submission,
        )

    @admin.display(description="Changed at", ordering="-changed_at")
    def changed_at_link(self, obj):
        from django.urls import reverse
        from django.utils.formats import localize

        url = reverse("admin:submissions_submissionchangelog_change", args=[obj.pk])
        return format_html(
            '<a href="{}" title="View diff for this change">{}</a>',
            url,
            localize(obj.changed_at),
        )

    @admin.display(description="Fields changed", ordering=None)
    def field_count(self, obj):
        n = len(obj.changes) if obj.changes else 0
        return f"{n} field{'s' if n != 1 else ''}"

    # ── Detail helper ─────────────────────────────────────────────────────────

    @admin.display(description="Changed fields")
    def changes_display(self, obj):
        """Render the JSON diff as a readable HTML table."""
        if not obj.changes:
            return "—"
        rows_html = format_html_join(
            "",
            "<tr>"
            "<td style='padding:4px 10px;font-weight:600'>{}</td>"
            "<td style='padding:4px 10px;color:#991b1b'>{}</td>"
            "<td style='padding:4px 10px;color:#166534'>{}</td>"
            "</tr>",
            (
                (
                    ch.get("label", ch.get("field", "?")),
                    ch.get("old", "—"),
                    ch.get("new", "—"),
                )
                for ch in obj.changes
            ),
        )
        header = mark_safe(
            "<tr style='background:var(--darkened-bg)'>"
            "<th style='padding:4px 10px;text-align:left'>Field</th>"
            "<th style='padding:4px 10px;text-align:left'>Before</th>"
            "<th style='padding:4px 10px;text-align:left'>After</th>"
            "</tr>"
        )
        return format_html(
            '<table style="border-collapse:collapse;font-size:.85rem">{}{}</table>',
            header,
            rows_html,
        )


# ---------------------------------------------------------------------------
# SubmissionDeletionAudit — read-only audit log for hard-deleted submissions
# ---------------------------------------------------------------------------


@admin.register(SubmissionDeletionAudit)
class SubmissionDeletionAuditAdmin(admin.ModelAdmin):
    """
    Read-only view of the deletion audit log.  One record is written for each
    hard-deleted ServiceSubmission, capturing the submission state and the full
    SubmissionChangeLog at the time of deletion.
    """

    list_display = (
        "service_name",
        "status",
        "deleted_by",
        "deleted_at",
        "changelog_count",
    )
    list_display_links = None
    list_filter = ("status", "deleted_by", ("deleted_at", admin.DateFieldListFilter))
    search_fields = ("service_name", "deleted_by", "submitter_last_name")
    ordering = ("-deleted_at",)
    date_hierarchy = "deleted_at"
    list_per_page = 50

    readonly_fields = (
        "submission_id",
        "service_name",
        "status",
        "submitter_first_name",
        "submitter_last_name",
        "submitter_affiliation",
        "public_contact_email",
        "deleted_by",
        "deleted_at",
        "changelog_count",
        "changelog_snapshot_display",
    )

    fieldsets = (
        (
            "Deleted submission",
            {
                "fields": (
                    "submission_id",
                    "service_name",
                    "status",
                    "submitter_first_name",
                    "submitter_last_name",
                    "submitter_affiliation",
                    "public_contact_email",
                ),
            },
        ),
        (
            "Deletion metadata",
            {
                "fields": ("deleted_by", "deleted_at"),
            },
        ),
        (
            "Change log at time of deletion",
            {
                "fields": ("changelog_count", "changelog_snapshot_display"),
            },
        ),
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("submissions.view_submissiondeletionaudit")

    @admin.display(description="Change log snapshot")
    def changelog_snapshot_display(self, obj):
        if not obj.changelog_snapshot:
            return "—"
        rows_html = format_html_join(
            "",
            "<tr>"
            "<td style='padding:4px 10px;font-weight:600'>{}</td>"
            "<td style='padding:4px 10px;color:var(--body-quiet-color);font-size:.8rem'>{}</td>"
            "<td style='padding:4px 10px'>{}</td>"
            "<td style='padding:4px 10px'>{}</td>"
            "</tr>",
            (
                (
                    entry.get("changed_by", ""),
                    entry.get("changed_at", ""),
                    len(entry.get("changes", [])),
                    ", ".join(c.get("field", "") for c in entry.get("changes", [])),
                )
                for entry in obj.changelog_snapshot
            ),
        )
        header = mark_safe(
            "<tr style='background:var(--darkened-bg)'>"
            "<th style='padding:4px 10px;text-align:left'>Changed by</th>"
            "<th style='padding:4px 10px;text-align:left'>Changed at</th>"
            "<th style='padding:4px 10px;text-align:left'># Fields</th>"
            "<th style='padding:4px 10px;text-align:left'>Fields</th>"
            "</tr>"
        )
        return format_html(
            '<table style="border-collapse:collapse;font-size:.85rem">{}{}</table>',
            header,
            rows_html,
        )
