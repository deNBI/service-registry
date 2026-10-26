"""Template filter for text fields that older rows store HTML-escaped."""

from django import template

from apps.submissions.markdown_render import decode_legacy_entities

register = template.Library()


@register.filter
def legacy_decoded(value):
    """Decode legacy HTML entities once (R&amp;D becomes R&D).

    Returns a plain str, never marked safe: an HTML template escapes the
    result exactly once and a plain-text template shows it as is.
    """
    return decode_legacy_entities(str(value)) if value is not None else ""
