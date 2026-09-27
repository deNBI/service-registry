"""Shared Markdown rendering + sanitization for service_description.

Single source of truth: every surface (catalogue, API, editor preview, admin)
renders through render_markdown() so output is identical everywhere.
"""

import html as _html
import re

import bleach
import markdown as md
from django.conf import settings
from django.core.cache import cache
from django.utils.safestring import SafeString, mark_safe
from markdown.extensions import Extension
from markdown.treeprocessors import Treeprocessor

ALLOWED_TAGS = [
    "p",
    "br",
    "strong",
    "em",
    "ul",
    "ol",
    "li",
    "a",
    "blockquote",
    "h4",
    "h5",
    "h6",
]
ALLOWED_PROTOCOLS = ["http", "https", "mailto"]
_ALLOWED_HREF_RE = re.compile(r"(https?:|mailto:)", re.IGNORECASE)


def _allowed_a_attr(tag: str, name: str, value: str) -> bool:
    """bleach attribute filter for <a>: keep title; keep href only with an
    explicit http/https/mailto scheme. bleach's protocol check alone lets
    scheme-less hrefs ('/x', '//host', '#frag') through."""
    if name == "title":
        return True
    if name == "href":
        return bool(_ALLOWED_HREF_RE.match(value))
    return False


ALLOWED_ATTRS = {"a": _allowed_a_attr}

# Bump whenever render/sanitize rules change so cached output from a previous
# deploy is never served. Keys also carry a finite TTL so orphaned entries
# (old versions, old updated_at values) expire from Redis on their own.
RENDER_VERSION = 4
MD_CACHE_TTL = 60 * 60 * 24

# A whole anchor as serialized by bleach: every attribute value is
# double-quoted (a literal '"' inside is entity-encoded), so quoted values may
# safely contain '>' or 'href=' text. Markdown never nests anchors.
_ANCHOR_RE = re.compile(r'<a((?:\s+[\w-]+="[^"]*")*)\s*>(.*?)</a>', re.DOTALL)
_ATTR_RE = re.compile(r'\s+([\w-]+)="([^"]*)"')
# Any opening <a> tag (Markdown-generated in the raw HTML, sanitized after).
_ANCHOR_OPEN_RE = re.compile(r"<a\b")
_REL = "nofollow noopener noreferrer"


def markdown_enabled() -> bool:
    """True when the markdown_descriptions feature flag is on."""
    return bool(
        getattr(settings, "SITE_CONFIG", {})
        .get("features", {})
        .get("markdown_descriptions", False)
    )


_TAG_RE = re.compile(r"<[^>]*>")
# Invisible format characters str.strip() keeps: zero-width space/non-joiner/
# joiner, word joiner and BOM (zero-width no-break space).
_ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")


def _visible_text(fragment: str) -> str:
    """Text a reader would see in sanitized inner HTML: tags stripped,
    entities decoded, zero-width characters removed and whitespace trimmed
    (str.strip() also trims U+00A0)."""
    text = _html.unescape(_TAG_RE.sub("", fragment))
    return _ZERO_WIDTH_RE.sub("", text).strip()


def _harden_anchors(html: str) -> str:
    """Finish anchors bleach has already filtered. No autolinking of bare text.

    An anchor whose href bleach dropped (blocked or scheme-less link) is
    unwrapped to its inner text rather than left as a dead <a>. An anchor
    with no VISIBLE text (tags stripped, entities decoded, whitespace
    including U+00A0 trimmed, zero-width characters such as U+200B removed;
    e.g. a README badge whose image was stripped,
    even inside **...**, or a lone &nbsp;) is unwrapped too, so no text-less
    link remains. Kept anchors
    get rel="nofollow noopener noreferrer"; only http(s) links also get
    target="_blank" (opening an empty tab for a mail client is bad UX).
    """

    def repl(m):
        attrs = [
            (k, v)
            for k, v in _ATTR_RE.findall(m.group(1))
            if k not in ("rel", "target")
        ]
        href = dict(attrs).get("href")
        if href is None or not _visible_text(m.group(2)):
            return m.group(2)
        extra = f' rel="{_REL}"'
        if not href.lower().startswith("mailto:"):
            extra += ' target="_blank"'
        attr_str = "".join(f' {k}="{v}"' for k, v in attrs)
        return f"<a{attr_str}{extra}>{m.group(2)}</a>"

    return _ANCHOR_RE.sub(repl, html)


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
    user did not explicitly write with '[label](url)' syntax. It also
    registers _ShiftHeadings so '#' headings render scaled down (h4-h6).
    """

    def extendMarkdown(self, md):
        md.preprocessors.deregister("html_block")
        md.inlinePatterns.deregister("html")
        md.inlinePatterns.deregister("autolink")
        md.inlinePatterns.deregister("automail")
        md.treeprocessors.register(_ShiftHeadings(md), "shift_headings", 5)


class _ShiftHeadings(Treeprocessor):
    """Render '#'..'######' as h4..h6 so description headings never compete
    with the page's own headings (h1->h4, h2->h5, h3 and deeper->h6)."""

    def run(self, root):
        for el in root.iter():
            if len(el.tag) == 2 and el.tag[0] == "h" and el.tag[1] in "123456":
                el.tag = f"h{min(int(el.tag[1]) + 3, 6)}"


def _md_to_html(text: str) -> str:
    """Markdown -> raw HTML with raw-HTML handling disabled (shared by
    render_markdown and render_with_notice so their outputs stay consistent)."""
    return md.markdown(text, extensions=["sane_lists", "nl2br", _NoRawHtml()])


def render_markdown(text: str) -> SafeString:
    """Convert stored Markdown to sanitized, safe HTML.

    Order: markdown.convert (raw HTML disabled) -> bleach.clean -> unwrap
    href-less or empty anchors and add rel/target -> mark_safe. Sanitize on output. Markdown itself escapes
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


def render_with_notice(text: str) -> tuple[SafeString, bool]:
    """Return (safe_html, removed) for the description editor's Preview tab.

    removed is True when sanitization dropped content the user wrote: a link
    lost its href (blocked javascript:/data: protocol or no explicit
    http/https/mailto scheme; such anchors are unwrapped to text) or a
    Markdown-generated disallowed tag (code, image, horizontal rule) was
    stripped. The baseline is
    _md_to_html, whose output contains only Markdown-generated tags because
    raw HTML is disabled, so literal tag-like text such as '<select>' is
    escaped in both baseline and output and never triggers the notice.
    """
    if not text:
        return mark_safe(""), False
    raw_html = _md_to_html(text)
    safe = str(render_markdown(text))
    # Href-less and emptied anchors are unwrapped, so fewer anchors than
    # Markdown generated means a link lost its href or all of its content.
    href_dropped = len(_ANCHOR_OPEN_RE.findall(raw_html)) > len(
        _ANCHOR_OPEN_RE.findall(safe)
    )
    # Fewer '<' after cleaning means a Markdown-generated disallowed tag was
    # stripped (rel/target hardening adds attributes, never tags).
    tag_stripped = raw_html.count("<") > safe.count("<")
    return mark_safe(safe), (href_dropped or tag_stripped)


_BLOCK_TAG_RE = re.compile(r"(</(?:p|li|ul|ol|blockquote|h4|h5|h6)>|<br\s*/?>)")
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


def _cache_key(kind: str, submission) -> str:
    # Integer microseconds: a stable, exact key part (no float repr).
    ts = (
        int(submission.updated_at.timestamp() * 1_000_000)
        if submission.updated_at
        else 0
    )
    return f"md:v{RENDER_VERSION}:{kind}:{submission.pk}:{ts}"


def render_submission_description(submission) -> str:
    """Flag-aware, cached rendering for a submission's description.

    Returns a SafeString only when the flag is on. When the flag is off,
    returns the raw text (a plain str, NOT mark_safe) so the template
    auto-escapes it exactly as today.
    """
    raw = submission.service_description or ""
    if not markdown_enabled():
        return raw
    key = _cache_key("html", submission)
    cached = cache.get(key)
    if cached is not None:
        return mark_safe(cached)
    html = render_markdown(raw)
    cache.set(key, str(html), timeout=MD_CACHE_TTL)
    return html


def submission_description_snippet(submission) -> str:
    """Flag-aware, cached plain-text card snippet for a submission.

    Flag off: raw text. Flag on: markdown_to_text(raw). Always a plain str
    for the template to autoescape.
    """
    raw = submission.service_description or ""
    if not markdown_enabled():
        return raw
    key = _cache_key("text", submission)
    cached = cache.get(key)
    if cached is not None:
        return cached
    text = markdown_to_text(raw)
    cache.set(key, text, timeout=MD_CACHE_TTL)
    return text
