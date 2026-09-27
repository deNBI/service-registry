"""Shared Markdown rendering + sanitization for service_description.

Single source of truth: every surface (catalogue, API, HTMX preview, admin)
renders through render_markdown() so output is identical everywhere.
"""

import html as _html
import re

import bleach
import markdown as md
from django.conf import settings
from django.utils.safestring import SafeString, mark_safe
from markdown.extensions import Extension

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


class _NoRawHtml(Extension):
    """Disable Python-Markdown's raw-HTML handling.

    Without this, literal tag-like text (e.g. 'List<String>', '<select>') is
    parsed as raw HTML and passed through to bleach, which then either strips
    it (silently deleting the text) or, worse, lets it through as a live tag.
    Deregistering the html block/inline patterns makes Markdown treat '<' and
    '>' as ordinary text, which it escapes itself during serialization -
    including inside code spans/blocks, so no separate pre-escape step (and
    its double-escaping bug there) is needed. Deregistering autolink/automail
    also means a bare '<url>' or '<email>' is never turned into a link the
    user did not explicitly write with '[label](url)' syntax.
    """

    def extendMarkdown(self, md):
        md.preprocessors.deregister("html_block")
        md.inlinePatterns.deregister("html")
        md.inlinePatterns.deregister("autolink")
        md.inlinePatterns.deregister("automail")


def _md_to_html(text: str) -> str:
    """Markdown -> raw HTML with raw-HTML handling disabled (shared by
    render_markdown and render_with_notice so their outputs stay consistent)."""
    return md.markdown(text, extensions=["sane_lists", "nl2br", _NoRawHtml()])


def render_markdown(text: str) -> SafeString:
    """Convert stored Markdown to sanitized, safe HTML.

    Order: markdown.convert (raw HTML disabled) -> bleach.clean -> add
    rel/target -> mark_safe. Sanitize on output. Markdown itself escapes
    '<'/'>'/'&' in text content (see _NoRawHtml); we never pre-escape.
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


_BLOCK_TAG_RE = re.compile(r"(</(?:p|li|ul|ol|blockquote)>|<br\s*/?>)")
_WS_RE = re.compile(r"\s+")


def markdown_to_text(text: str, limit: int = 300) -> str:
    """Plain-text snippet of Markdown for compact card display.

    Derived from render_markdown() output so the card shows exactly the text
    the list view shows: block boundaries become spaces, all tags are
    stripped, entities are unescaped and whitespace is collapsed. List,
    quote and heading markers only disappear where Markdown itself treats
    them as block syntax (rendered <li>/<blockquote>/headings carry no
    marker characters); prose such as "2024. This..." or "- 5 degrees" on a
    continuation line is kept verbatim, as in the list view. Returns a plain
    str (not SafeString) for the template to autoescape.
    """
    if not text:
        return ""
    html = str(render_markdown(text))
    html = _BLOCK_TAG_RE.sub(" ", html)  # keep word boundaries
    plain = bleach.clean(html, tags=[], strip=True)
    plain = _html.unescape(plain)
    plain = _WS_RE.sub(" ", plain).strip()
    if len(plain) > limit:
        plain = plain[:limit].rstrip() + "…"  # ellipsis
    return plain
