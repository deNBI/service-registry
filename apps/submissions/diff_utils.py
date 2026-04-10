"""
Diff Utilities
==============
Snapshot and diff helpers for ServiceSubmission fields.

Used by:
  - EditView.post()           — captures submitter-initiated changes
  - ServiceSubmissionAdmin    — captures admin-initiated changes

The diff is threaded through to email notifications and persisted
in ServiceSubmission.last_change_summary so it is always visible
in the admin change view, regardless of who made the edit.

All public functions are pure and dependency-free (no Django model
imports at module level) so they can be unit-tested without a database.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from apps.submissions.models import ServiceSubmission

# ---------------------------------------------------------------------------
# YAML-based label lookup for license field
# ---------------------------------------------------------------------------
_FORM_TEXTS_PATH = Path(__file__).resolve().parent / "form_texts.yaml"
try:
    _ft = yaml.safe_load(_FORM_TEXTS_PATH.read_text(encoding="utf-8")) or {}
except FileNotFoundError:
    _ft = {}

# Fields whose display labels are defined in form_texts.yaml choices dicts.
# Maps field name → {slug: label} for lookup in snapshot().
_YAML_CHOICE_FIELDS: dict[str, dict[str, str]] = {
    "license": dict((_ft.get("license", {}).get("choices") or {}).items()),
}
# _ft is a temporary variable used only to build _YAML_CHOICE_FIELDS above.
# Delete it to avoid it leaking into the module namespace.
del _ft

# ---------------------------------------------------------------------------
# Field definitions
# ---------------------------------------------------------------------------

# Scalar fields included in the diff.
# Each entry is (model_field_name, human_readable_label).
# Choice fields: get_FOO_display() is used automatically in snapshot().
# FK fields: str(value) is used for display; _id suffix is compared for equality.
DIFFABLE_FIELDS: list[tuple[str, str]] = [
    ("submitter_first_name", "Submitter First Name"),
    ("submitter_last_name", "Submitter Last Name"),
    ("submitter_affiliation", "Submitter Affiliation"),
    ("status", "Status"),
    ("date_of_entry", "Date of Entry"),
    ("service_name", "Service Name"),
    ("service_description", "Service Description"),
    ("year_established", "Year Established"),
    ("is_toolbox", "Is Toolbox"),
    ("toolbox_name", "Toolbox Name"),
    ("user_knowledge_required", "User Knowledge Required"),
    ("publications_pmids", "Publications (PMIDs/DOIs)"),
    ("logo", "Service Logo"),
    ("associated_partner_note", "Associated Partner Note"),
    ("host_institute", "Host Institute"),
    ("service_center", "Service Centre"),
    ("public_contact_email", "Public Contact Email"),
    ("internal_contact_name", "Internal Contact Name"),
    ("internal_contact_email", "Internal Contact Email"),
    ("website_url", "Website URL"),
    ("terms_of_use_url", "Terms of Use URL"),
    ("license", "License"),
    ("github_url", "GitHub URL"),
    ("biotools_url", "bio.tools URL"),
    ("fairsharing_url", "FAIRsharing URL"),
    ("other_registry_url", "Other Registry URL"),
    ("kpi_monitoring", "KPI Monitoring"),
    ("kpi_start_year", "KPI Start Year"),
    ("keywords_uncited", "Keywords (uncited)"),
    ("keywords_seo", "Keywords (SEO)"),
    ("survey_participation", "Survey Participation"),
    ("register_as_elixir", "Register as ELIXIR"),
    ("comments", "Comments"),
    ("primary_maturity_tag", "Primary Maturity Tag"),
    ("secondary_maturity_tags", "Secondary Maturity Tags"),
]

# M2M fields included in the diff.
# Snapshotted as sorted lists of str(item) values.
# IMPORTANT: snapshot_m2m() MUST be called before save_m2m() / save_related().
DIFFABLE_M2M: list[tuple[str, str]] = [
    ("service_categories", "Service Categories"),
    ("responsible_pis", "Responsible PIs"),
    ("edam_topics", "EDAM Topics"),
    ("edam_operations", "EDAM Operations"),
]

# Fields that have a get_FOO_display() method (choice fields).
_CHOICE_FIELDS = {"status", "kpi_monitoring", "primary_maturity_tag"}

# Fields that are FKs — compare via _id attribute, display via str().
_FK_FIELDS = {"service_center"}

# Fields whose raw value is boolean — display as Yes/No.
_BOOL_FIELDS = {"is_toolbox", "survey_participation", "register_as_elixir"}

# File/image fields — display just the basename of the stored file, or "—".
_FILE_FIELDS = {"logo"}

# JSON list fields stored in a JSONField (not M2M).
# Each field in this set MUST have a corresponding get_FOO_display_list() model
# method that returns human-readable labels, matching the _CHOICE_FIELDS pattern.
_LIST_FIELDS = {"secondary_maturity_tags"}

# snapshot() checks _CHOICE_FIELDS before _YAML_CHOICE_FIELDS in its elif chain.
# A field present in both would silently use the wrong branch. Guard against this.
_overlap = _CHOICE_FIELDS & set(_YAML_CHOICE_FIELDS)
assert not _overlap, (
    f"Fields {_overlap} appear in both _CHOICE_FIELDS and _YAML_CHOICE_FIELDS. "
    "Remove them from _CHOICE_FIELDS so the YAML label lookup takes precedence."
)
del _overlap


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def snapshot(instance: "ServiceSubmission") -> dict:
    """
    Return a JSON-serialisable snapshot of all diffable scalar fields.

    Choice fields use their human-readable display value.
    FK fields use str(related_object).
    Boolean fields use "Yes" / "No".
    All other fields are cast to str (empty string for None/falsy).

    Call this BEFORE form.save() / save_model().
    """
    data: dict = {}
    for field, _label in DIFFABLE_FIELDS:
        if field in _CHOICE_FIELDS:
            display_method = f"get_{field}_display"
            value = getattr(instance, display_method, lambda: "")() or ""
        elif field in _YAML_CHOICE_FIELDS:
            slug = getattr(instance, field, "") or ""
            value = _YAML_CHOICE_FIELDS[field].get(slug, slug)
        elif field in _FK_FIELDS:
            related = getattr(instance, field, None)
            value = str(related) if related is not None else ""
        elif field in _BOOL_FIELDS:
            value = "Yes" if getattr(instance, field, False) else "No"
        elif field in _FILE_FIELDS:
            file_field = getattr(instance, field, None)
            name = getattr(file_field, "name", None)
            value = name.split("/")[-1] if name else ""
        elif field in _LIST_FIELDS:
            display_method = f"get_{field}_display_list"
            if hasattr(instance, display_method):
                value = sorted(getattr(instance, display_method)())
            else:
                raw = getattr(instance, field, None)
                value = sorted(str(v) for v in raw) if raw else []
        else:
            raw = getattr(instance, field, None)
            value = str(raw).strip() if raw is not None else ""
        data[field] = value
    return data


def snapshot_m2m(instance: "ServiceSubmission") -> dict:
    """
    Return a JSON-serialisable snapshot of all diffable M2M fields.

    Each entry is a sorted list of str(item) values.

    IMPORTANT: call this BEFORE save_m2m() or save_related() — after those
    calls the new values are already committed and you cannot see the old ones.
    """
    data: dict = {}
    for field, _label in DIFFABLE_M2M:
        manager = getattr(instance, field, None)
        if manager is None:
            data[field] = []
        else:
            data[field] = sorted(str(item) for item in manager.all())
    return data


def build_diff(
    before: dict,
    after: dict,
) -> list[dict]:
    """
    Compare two snapshots (from snapshot() and/or snapshot_m2m()) and return
    a list of changed fields.

    Returns:
        [
            {
                "field":  "service_name",   # model attribute name
                "label":  "Service Name",   # human-readable label
                "old":    "Old Name",       # previous value
                "new":    "New Name",       # new value
            },
            ...
        ]

    Only fields that actually changed are included.  Fields present in one
    snapshot but not the other are silently skipped (graceful degradation for
    partial snapshots).
    """
    # Build a combined label lookup from both field lists.
    label_map: dict[str, str] = {f: lbl for f, lbl in DIFFABLE_FIELDS}
    label_map.update({f: lbl for f, lbl in DIFFABLE_M2M})

    changes: list[dict] = []
    all_keys = set(before) | set(after)

    for key in sorted(all_keys):
        old_val = before.get(key)
        new_val = after.get(key)
        if old_val is None or new_val is None:
            continue
        # Normalise list comparisons (M2M snapshots are lists)
        old_cmp = old_val if not isinstance(old_val, list) else tuple(old_val)
        new_cmp = new_val if not isinstance(new_val, list) else tuple(new_val)
        if old_cmp == new_cmp:
            continue
        changes.append(
            {
                "field": key,
                "label": label_map.get(key, key.replace("_", " ").title()),
                "old": _display(old_val),
                "new": _display(new_val),
            }
        )
    return changes


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _display(value) -> str:
    """Convert a snapshot value to a printable string."""
    if isinstance(value, list):
        return ", ".join(value) if value else "—"
    if value == "" or value is None:
        return "—"
    return str(value)
