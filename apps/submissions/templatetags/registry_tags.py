"""
Custom template tags for de.NBI Service Registry.
Provides access to site configuration values in templates that don't
receive the submissions context processor (e.g. admin templates).
"""

import re
from pathlib import Path

from django import template
from django.conf import settings
from django.templatetags.static import static
from django.utils.html import format_html, urlize
from django.utils.safestring import mark_safe

register = template.Library()


@register.simple_tag
def site_logo_url():
    """Return the logo URL from site.toml, or empty string."""
    return getattr(settings, "SITE_CONFIG", {}).get("site", {}).get("logo_url", "")


@register.simple_tag
def site_favicon_url():
    """
    Return the favicon URL.
    Priority: site.toml [site] favicon_url → auto-detect static/img/favicon.* → empty string.
    """
    url = getattr(settings, "SITE_CONFIG", {}).get("site", {}).get("favicon_url", "")
    if url:
        return url
    static_img = Path(settings.BASE_DIR) / "static" / "img"
    for fname in ("favicon.ico", "favicon.png", "favicon.svg"):
        if (static_img / fname).exists():
            return static(f"img/{fname}")
    return ""


@register.simple_tag
def site_setting(section, key, default=""):
    """Generic accessor: {% site_setting 'site' 'name' %}"""
    return getattr(settings, "SITE_CONFIG", {}).get(section, {}).get(key, default)


# Matches [link text](https://...) markdown-style links.
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")


def _linkify_segment(text: str) -> str:
    """Linkify one plain-text segment: [text](url) and bare URLs."""
    parts = []
    last = 0
    for m in _MD_LINK_RE.finditer(text):
        before = text[last : m.start()]
        if before:
            parts.append(urlize(before, autoescape=True))
        parts.append(format_html('<a href="{}">{}</a>', m.group(2), m.group(1)))
        last = m.end()
    remaining = text[last:]
    if remaining:
        parts.append(urlize(remaining, autoescape=True))
    return "".join(str(p) for p in parts)


@register.filter(is_safe=True)
def linkify_description(value: str) -> str:
    """
    Render a section description from form_texts.yaml as safe HTML.

    Supported syntax (plain text only — no raw HTML):
      - [link text](https://example.com)  → clickable named link
      - https://example.com               → auto-linked bare URL
      - Blank line (two newlines)         → paragraph break
      - Single newline                    → line break (<br>)
    """
    if not value:
        return ""
    paragraphs = value.split("\n\n")
    rendered = []
    for para in paragraphs:
        lines = [_linkify_segment(line) for line in para.split("\n")]
        rendered.append(mark_safe("<br>".join(lines)))  # nosec B308 B703
    return mark_safe("".join(f"<p>{p}</p>" for p in rendered))  # nosec B308 B703
