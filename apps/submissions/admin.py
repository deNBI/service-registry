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

from django.contrib import admin, messages
from django.forms.widgets import CheckboxSelectMultiple
from django.contrib.admin.models import CHANGE, LogEntry
from django.http import HttpResponse
from django.utils import timezone
from django.utils.html import format_html, mark_safe

from .models import ServiceSubmission, SubmissionAPIKey
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


# ─────────────────────────────────────────────────────────────────────────────
# ServiceSubmission admin
# ─────────────────────────────────────────────────────────────────────────────


@admin.register(ServiceSubmission)
class ServiceSubmissionAdmin(admin.ModelAdmin):
    # ── List view ────────────────────────────────────────────────────────────
    list_display = (
        "service_name_link",
        "submitter_display",
        "status_badge",
        "service_center",
        "elixir_badge",
        "submitted_at",
        "key_count",
        "api_key_link",
    )
    list_filter = (
        "status",
        "register_as_elixir",
        "service_center",
        "service_categories",
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
    )
    ordering = ("-submitted_at",)
    date_hierarchy = "submitted_at"
    save_on_top = True
    list_per_page = 30
    list_select_related = ("service_center",)
    # Two-panel filtered selector with search — needed for large option sets
    filter_horizontal = ("responsible_pis", "edam_topics", "edam_operations")

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("api_keys")

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

    fieldsets = (
        (
            "Status & Metadata",
            {
                "fields": (
                    ("id", "status"),
                    ("submitted_at", "updated_at"),
                    "submission_ip_display",
                    "status_actions",
                ),
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
                    ("license", "github_url"),
                    ("biotools_url", "fairsharing_url"),
                    "other_registry_url",
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
                    ("outreach_consent", "survey_participation"),
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

    actions = [
        "action_approve",
        "action_reject",
        "action_mark_under_review",
        "action_deprecate",
        "action_undeprecate",
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
            '<a href="{}" style="font-size:.8rem;white-space:nowrap">🔑 Manage ({})</a>',
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
        return mark_safe(
            '<div style="display:flex;gap:.5rem;flex-wrap:wrap">'
            + "".join(buttons)
            + "</div>"
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
        for sub in queryset:
            if sub.status == new_status:
                continue
            old = sub.status
            sub.status = new_status
            sub.save(update_fields=["status"])
            send_submission_notification.delay(str(sub.id), event="status_changed")
            self._log(request, sub, f"Status changed {old} → {new_status}")
            updated += 1
        self.message_user(
            request, f"{updated} submission(s) marked as {label}.", messages.SUCCESS
        )

    @admin.action(description="✅ Approve selected")
    def action_approve(self, request, queryset):
        self._change_status(request, queryset, "approved", "Approved")

    @admin.action(description="❌ Reject selected")
    def action_reject(self, request, queryset):
        self._change_status(request, queryset, "rejected", "Rejected")

    @admin.action(description="🔍 Mark as Under Review")
    def action_mark_under_review(self, request, queryset):
        self._change_status(request, queryset, "under_review", "Under Review")

    @admin.action(description="🚫 Deprecate selected")
    def action_deprecate(self, request, queryset):
        self._change_status(request, queryset, "deprecated", "Deprecated")

    @admin.action(description="♻️ Undeprecate selected (→ Submitted)")
    def action_undeprecate(self, request, queryset):
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
                "biotools_edam_topic_uris": [],
                "biotools_edam_operation_uris": [],
            }
        ops = []
        for fn in bt.functions.all():
            ops.extend(fn.edam_operation_uris or [])
        return {
            "biotools_id": bt.biotools_id,
            "biotools_name": bt.name or "",
            "biotools_edam_topic_uris": bt.edam_topic_uris or [],
            "biotools_edam_operation_uris": ops,
        }

    @admin.action(description="📥 Export selected as CSV")
    def action_export_csv(self, request, queryset):
        resp = HttpResponse(content_type="text/csv")
        resp["Content-Disposition"] = 'attachment; filename="submissions.csv"'
        w = csv.writer(resp)
        w.writerow(
            [
                "id",
                "status",
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
                "license",
                "github_url",
                "biotools_url",
                "fairsharing_url",
                "other_registry_url",
                "kpi_monitoring",
                "kpi_start_year",
                "keywords_uncited",
                "keywords_seo",
                "register_as_elixir",
                "outreach_consent",
                "survey_participation",
                "comments",
                "logo_url",
                "biotools_id",
                "biotools_name",
                "biotools_edam_topic_uris",
                "biotools_edam_operation_uris",
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
                    s.license,
                    s.github_url,
                    s.biotools_url,
                    s.fairsharing_url,
                    s.other_registry_url,
                    s.kpi_monitoring,
                    s.kpi_start_year,
                    s.keywords_uncited,
                    s.keywords_seo,
                    s.register_as_elixir,
                    s.outreach_consent,
                    s.survey_participation,
                    s.comments,
                    self._logo_url(request, s),
                    bt["biotools_id"],
                    bt["biotools_name"],
                    "; ".join(bt["biotools_edam_topic_uris"]),
                    "; ".join(bt["biotools_edam_operation_uris"]),
                    s.submitted_at.isoformat(),
                    s.updated_at.isoformat(),
                ]
            )
        return resp

    @admin.action(description="📥 Export selected as JSON")
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
                    "license": s.license,
                    "github_url": s.github_url,
                    "biotools_url": s.biotools_url,
                    "fairsharing_url": s.fairsharing_url,
                    "other_registry_url": s.other_registry_url,
                    "kpi_monitoring": s.kpi_monitoring,
                    "kpi_start_year": s.kpi_start_year,
                    "keywords_uncited": s.keywords_uncited,
                    "keywords_seo": s.keywords_seo,
                    "register_as_elixir": s.register_as_elixir,
                    "outreach_consent": s.outreach_consent,
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

    # ── Key management via response_change ───────────────────────────────────

    def response_change(self, request, obj):
        if "_revoke_all_keys" in request.POST:
            self._revoke_all_keys(request, obj)
        elif "_reset_key" in request.POST:
            self._reset_key(request, obj)
        elif "_issue_new_key" in request.POST:
            self._issue_new_key(request, obj)
        elif "_approve" in request.POST:
            self._change_status(
                request, obj.__class__.objects.filter(pk=obj.pk), "approved", "Approved"
            )
        elif "_reject" in request.POST:
            self._change_status(
                request, obj.__class__.objects.filter(pk=obj.pk), "rejected", "Rejected"
            )
        elif "_under_review" in request.POST:
            self._change_status(
                request,
                obj.__class__.objects.filter(pk=obj.pk),
                "under_review",
                "Under Review",
            )
        elif "_deprecate" in request.POST:
            self._change_status(
                request,
                obj.__class__.objects.filter(pk=obj.pk),
                "deprecated",
                "Deprecated",
            )
        elif "_undeprecate" in request.POST:
            self._change_status(
                request,
                obj.__class__.objects.filter(pk=obj.pk),
                "submitted",
                "Submitted (undeprecated)",
            )
        return super().response_change(request, obj)

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

    # ── List helpers ─────────────────────────────────────────────────────────

    @admin.display(description="Submission", ordering="submission__service_name")
    def submission_link(self, obj):
        from django.urls import reverse

        url = reverse(
            "admin:submissions_servicesubmission_change", args=[obj.submission_id]
        )
        return format_html(
            '<a href="{}">{}</a>',
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

    def has_add_permission(self, request):
        return True
