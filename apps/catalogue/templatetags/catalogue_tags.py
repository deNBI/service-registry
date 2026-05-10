import re
from urllib.parse import urlencode

from django import template
from django.utils.html import escape, mark_safe

register = template.Library()


@register.simple_tag
def catalogue_service_url(service) -> str:
    """
    Return the URL for a service card link.
    Today: external website_url. Future: swap this tag for an internal detail URL.
    """
    return service.website_url or ""


@register.filter(name="as_query_string")
def as_query_string(params) -> str:
    """Render CatalogueQueryParams as a URL-encoded query string (no page key)."""
    return urlencode(params.to_query_string_dict(), doseq=True)


@register.filter(name="highlight")
def highlight(text: str, search_term: str) -> str:
    """Wrap occurrences of search_term in <mark> tags (case-insensitive)."""
    if not search_term or not text:
        return text
    # Escape both inputs before building regex so user content can never
    # inject HTML — search on the already-escaped string, mark is our own tag.
    safe_text = str(escape(str(text)))
    pattern = re.compile(re.escape(str(escape(search_term))), re.IGNORECASE)
    highlighted = pattern.sub(lambda m: f"<mark>{m.group()}</mark>", safe_text)
    return mark_safe(highlighted)
