"""
Template tags for the enhanced ServiceSubmission admin filter sidebar.

Exposes a single inclusion tag `filter_sidebar_context` that walks the Django
admin `ChangeList.filter_specs` and yields:
  * ``pills``  – one entry per currently active filter (for the pill summary)
  * ``sections`` – one entry per filter spec (for the collapsible sections)

No custom ListFilter subclasses are introduced; the stock spec objects remain
the source of truth for which filter is applied.
"""

from django import template
from django.utils.html import format_html

register = template.Library()


def _section_from_spec(spec, cl):
    """Build section dict for a filter spec. Returns None if spec has no output."""
    if not spec.has_output():
        return None
    choices = list(spec.choices(cl))
    # First choice is conventionally the "All"/default — its query_string clears
    # this filter. `selected` marks the currently-active choice.
    default = choices[0] if choices else None
    options = []
    for choice in choices:
        display = str(choice.get("display", ""))
        options.append(
            {
                "display": display,
                "display_lower": display.lower(),
                "selected": bool(choice.get("selected")),
                "query_string": choice.get("query_string", ""),
            }
        )
    return {
        "field": getattr(spec, "field_path", None) or spec.__class__.__name__,
        "title": spec.title,
        "options": options,
        "default_query_string": default["query_string"] if default else "",
        "has_active": any(o["selected"] for o in options[1:]),
    }


def _pills_from_sections(sections):
    """Derive pill entries from precomputed section dicts."""
    pills = []
    for section in sections:
        if not section["has_active"]:
            continue
        # First selected option beyond the "All" default is the active one.
        for opt in section["options"][1:]:
            if opt["selected"]:
                pills.append(
                    {
                        "title": section["title"],
                        "label": opt["display"],
                        "remove_qs": section["default_query_string"],
                    }
                )
                break
    return pills


@register.inclusion_tag(
    "admin/submissions/servicesubmission/_filter_sidebar.html",
    takes_context=False,
)
def enhanced_filter_sidebar(cl):
    """Render the enhanced filter sidebar for a ChangeList."""
    sections = []
    for spec in cl.filter_specs:
        section = _section_from_spec(spec, cl)
        if section is not None:
            sections.append(section)
    pills = _pills_from_sections(sections)
    return {
        "cl": cl,
        "pills": pills,
        "sections": sections,
        "has_pills": bool(pills),
        "clear_all_qs": cl.clear_all_filters_qs,
    }


@register.simple_tag
def pill_aria_label(title, label):
    """Build aria-label for the pill remove anchor."""
    return format_html("Remove filter {}: {}", title, label)
