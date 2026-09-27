"""Shared Markdown rendering + sanitization for service_description.

Single source of truth: every surface (catalogue, API, HTMX preview, admin)
renders through render_markdown() so output is identical everywhere.
"""

import re

import bleach
import markdown as md
from django.conf import settings
from django.utils.safestring import SafeString, mark_safe

ALLOWED_TAGS = ["p", "br", "strong", "em", "ul", "ol", "li", "a", "blockquote"]
ALLOWED_ATTRS = {"a": ["href", "title"]}
ALLOWED_PROTOCOLS = ["http", "https", "mailto"]

# Matches an opening <a ...> tag (bleach has already validated href/protocol).
_ANCHOR_OPEN_RE = re.compile(r"<a\b([^>]*)>")


def markdown_enabled() -> bool:
    """True when the markdown_descriptions feature flag is on."""
    return bool(
        getattr(settings, "SITE_CONFIG", {})
        .get("features", {})
        .get("markdown_descriptions", False)
    )


def _harden_anchors(html: str) -> str:
    """Add rel/target to existing anchors only. No autolinking of bare text."""

    def repl(m):
        attrs = m.group(1)
        # Drop any pre-existing rel/target, then set ours.
        attrs = re.sub(r'\s+(rel|target)="[^"]*"', "", attrs)
        return f'<a{attrs} rel="nofollow noopener noreferrer" target="_blank">'

    return _ANCHOR_OPEN_RE.sub(repl, html)


def _escape_lt(text: str) -> str:
    """Escape only '<' before Markdown so literal <tag>-like content (e.g.
    'List<String>', '<select>') survives as visible text and cannot form an HTML
    tag. This does NOT touch any Markdown syntax (*, _, [, ], >, #, ...), so
    emphasis/lists/links/blockquotes/headings all still work. '>' is deliberately
    left alone so blockquote syntax keeps working. Verified at runtime."""
    return text.replace("<", "&lt;")


def _md_to_html(text: str) -> str:
    """Markdown -> raw HTML with the '<' pre-escape applied (shared by
    render_markdown and render_with_notice so their outputs stay consistent)."""
    return md.markdown(_escape_lt(text), extensions=["sane_lists", "nl2br"])


def render_markdown(text: str) -> SafeString:
    """Convert stored Markdown to sanitized, safe HTML.

    Order: escape '<' -> markdown.convert -> bleach.clean -> add rel/target -> mark_safe.
    Sanitize on output. The only pre-escape is '<' (see _escape_lt); we never
    escape '&'/quotes/markdown chars (that would break Markdown).
    """
    if not text:
        return mark_safe("")
    clean = bleach.clean(
        _md_to_html(text),
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
    )
    return mark_safe(_harden_anchors(clean))
