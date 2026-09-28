"""Shared Markdown rendering + sanitization for service_description.

Single source of truth: every surface (catalogue, API, editor preview, admin)
renders through render_markdown() so output is identical everywhere.

The parser is markdown-it-py (CommonMark). It runs in linear time and caps
nesting depth, so no input within the description length limit can pin a
worker or exhaust the stack (Python-Markdown took minutes on 5,000 backticks
and raised RecursionError on deeply nested lists).
"""

import hashlib
import html as _html
import importlib.metadata
from html.parser import HTMLParser
import re
from pathlib import Path

import bleach
from django.conf import settings
from django.core.cache import cache
from django.utils.safestring import SafeString, mark_safe
from markdown_it import MarkdownIt

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
_OL_START_RE = re.compile(r"\d{1,9}")


def _allowed_a_attr(tag: str, name: str, value: str) -> bool:
    """bleach attribute filter for <a>: keep title; keep href only with an
    explicit http/https/mailto scheme. bleach's protocol check alone lets
    scheme-less hrefs ('/x', '//host', '#frag') through."""
    if name == "title":
        return True
    if name == "href":
        return bool(_ALLOWED_HREF_RE.match(value))
    return False


def _allowed_ol_attr(tag: str, name: str, value: str) -> bool:
    """bleach attribute filter for <ol>: keep a numeric start, so a list
    written from 2024 ('2024. Launched') keeps its number."""
    return name == "start" and bool(_OL_START_RE.fullmatch(value))


ALLOWED_ATTRS = {"a": _allowed_a_attr, "ol": _allowed_ol_attr}


def _render_fingerprint() -> str:
    """Cache-key part that changes whenever anything shaping the output does:
    this module's source (all render/sanitize rules live here) and the
    installed parser and sanitizer versions.

    No manual version bump to forget: a rule change or a library upgrade (for
    example a bleach security fix) can never serve HTML cached under the old
    rules, and during a rolling deploy old and new workers use disjoint keys.
    Keys also carry a finite TTL so orphaned entries expire on their own.
    """
    digest = hashlib.sha256(Path(__file__).read_bytes())
    for dist in ("markdown-it-py", "mdurl", "bleach"):
        digest.update(f"{dist}={importlib.metadata.version(dist)};".encode())
    return digest.hexdigest()[:16]


RENDER_FINGERPRINT = _render_fingerprint()
MD_CACHE_TTL = 60 * 60 * 24

# Maximum block/inline nesting depth. A list level costs two (list + item),
# so real descriptions stay far below it. Deeper content is not rendered and
# render_with_notice() reports it as removed.
MAX_NESTING = 20
# Container tokens whose content markdown-it tokenizes one level deeper; if
# one opens at MAX_NESTING - 1, the parser skips that content.
_CONTAINER_OPEN = ("list_item_open", "blockquote_open")

# A whole anchor as serialized by bleach: every attribute value is
# double-quoted (a literal '"' inside is entity-encoded), so quoted values may
# safely contain '>' or 'href=' text. Markdown never nests anchors.
_ANCHOR_RE = re.compile(r'<a((?:\s+[\w-]+="[^"]*")*)\s*>(.*?)</a>', re.DOTALL)
_ATTR_RE = re.compile(r'\s+([\w-]+)="([^"]*)"')
# Any opening <a> tag (Markdown-generated in the raw HTML, sanitized after).
_ANCHOR_OPEN_RE = re.compile(r"<a\b")
# A paragraph left empty after sanitization (e.g. it only held an image).
_EMPTY_P_RE = re.compile(r"<p>\s*</p>\n?")
_REL = "nofollow noopener noreferrer"


def markdown_enabled() -> bool:
    """True when the markdown_descriptions feature flag is on."""
    return bool(
        getattr(settings, "SITE_CONFIG", {})
        .get("features", {})
        .get("markdown_descriptions", False)
    )


def decode_legacy_entities(text: str) -> str:
    """Decode HTML entities once, for surfaces that show the description as
    plain text (flag-off catalogue and API, plain-text emails).

    Descriptions saved before Markdown support hold entities because the old
    web form HTML-escaped its input (x &gt; 5); newer rows are raw. Markdown
    rendering decodes entities itself, so decoding here makes every display
    surface agree in both flag states. The stored value is never changed.
    """
    return _html.unescape(text or "")


# Only ever applied to bleach output, where every '<' in text is escaped, so
# '<[^>]*>' matches real tags only.
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
    with no VISIBLE text is unwrapped too, so no text-less link remains.
    Visible text means tags stripped, entities decoded, whitespace including
    U+00A0 trimmed and zero-width characters such as U+200B removed; e.g. a
    README badge whose image was stripped, even inside **...**, or a lone
    &nbsp;.

    Kept anchors get rel="nofollow noopener noreferrer"; only http(s) links
    also get target="_blank" (opening an empty tab for a mail client is bad
    UX).
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


def _shift_headings(state) -> None:
    """Core rule: render '#'..'######' as h4..h6 so description headings never
    compete with the page's own headings (h1->h4, h2->h5, h3 and deeper->h6)."""
    for tok in state.tokens:
        if tok.type in ("heading_open", "heading_close"):
            tok.tag = f"h{min(int(tok.tag[1]) + 3, 6)}"


def _build_parser() -> MarkdownIt:
    """CommonMark with raw HTML off: literal tag-like text ('List<String>',
    '<select>') is escaped as text, never parsed as HTML."""
    parser = MarkdownIt(
        "commonmark",
        {"html": False, "breaks": True, "maxNesting": MAX_NESTING, "xhtmlOut": False},
    )
    # A bare '<url>' or '<email>' is never turned into a link the user did not
    # explicitly write with '[label](url)' syntax.
    parser.disable("autolink")
    # Let every destination through the parser: the bleach href filter is the
    # single place that decides. markdown-it's own check would leave a blocked
    # link as literal '[x](javascript:...)' text instead of unwrapping it to
    # 'x' with the removed-content notice.
    parser.validateLink = lambda url: True
    parser.core.ruler.push("shift_headings", _shift_headings)
    return parser


# Built once: rendering keeps all state per call, so the instance is shared.
_PARSER = _build_parser()


def _md_to_html(text: str) -> tuple[str, bool]:
    """Markdown -> unsanitized HTML (only Markdown-generated tags, since raw
    HTML is off), plus whether the nesting cap skipped content."""
    tokens = _PARSER.parse(text)
    truncated = any(
        tok.type in _CONTAINER_OPEN and tok.level >= MAX_NESTING - 1 for tok in tokens
    )
    return _PARSER.renderer.render(tokens, _PARSER.options, {}), truncated


def _sanitize(raw_html: str) -> str:
    """bleach allowlist, then anchor hardening, then drop emptied paragraphs."""
    clean = bleach.clean(
        raw_html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
    )
    return _EMPTY_P_RE.sub("", _harden_anchors(clean)).strip()


def render_markdown(text: str) -> SafeString:
    """Convert stored Markdown to sanitized, safe HTML.

    Order: markdown-it (raw HTML off) -> bleach.clean -> unwrap href-less or
    empty anchors and add rel/target -> drop emptied paragraphs -> mark_safe.
    Sanitize on output. The parser escapes '<'/'>'/'&' in text content itself;
    we never pre-escape.
    """
    if not text:
        return mark_safe("")
    return mark_safe(_sanitize(_md_to_html(text)[0]))


def render_with_notice(text: str) -> tuple[SafeString, bool]:
    """Return (safe_html, removed) for the description editor's Preview tab.

    removed is True when sanitization dropped content the user wrote: a link
    lost its href (blocked javascript:/data: protocol or no explicit
    http/https/mailto scheme; such anchors are unwrapped to text), a
    Markdown-generated disallowed tag (code, image, horizontal rule) was
    stripped, or content nested deeper than MAX_NESTING was skipped. The
    baseline is _md_to_html, whose output contains only Markdown-generated
    tags because raw HTML is off, so literal tag-like text such as '<select>'
    is escaped in both baseline and output and never triggers the notice.
    """
    if not text:
        return mark_safe(""), False
    raw_html, truncated = _md_to_html(text)
    safe = _sanitize(raw_html)
    # Href-less and emptied anchors are unwrapped, so fewer anchors than
    # Markdown generated means a link lost its href or all of its content.
    href_dropped = len(_ANCHOR_OPEN_RE.findall(raw_html)) > len(
        _ANCHOR_OPEN_RE.findall(safe)
    )
    # Fewer '<' after cleaning means a Markdown-generated disallowed tag was
    # stripped (rel/target hardening adds attributes, never tags; an emptied
    # paragraph only disappears after its content was stripped).
    tag_stripped = raw_html.count("<") > safe.count("<")
    return mark_safe(safe), (href_dropped or tag_stripped or truncated)


_WS_RE = re.compile(r"\s+")
_BLOCK_TAGS = frozenset(("p", "li", "ul", "ol", "blockquote", "h4", "h5", "h6", "br"))


class _TextExtractor(HTMLParser):
    """Visible text of sanitized description HTML, as the list view shows it.

    Block boundaries become spaces (so words never run together), entities
    are decoded (convert_charrefs) and ordered-list items keep their number
    ("2024. Launched"), because the list view shows that number as the
    marker. Bullets and quote bars are decoration and are dropped.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._lists: list[int | None] = []  # next number per open list

    def handle_starttag(self, tag, attrs):
        if tag in _BLOCK_TAGS:
            self.parts.append(" ")
        if tag == "ol":
            start = dict(attrs).get("start") or "1"
            self._lists.append(int(start))
        elif tag == "ul":
            self._lists.append(None)
        elif tag == "li" and self._lists and self._lists[-1] is not None:
            self.parts.append(f"{self._lists[-1]}. ")
            self._lists[-1] += 1

    def handle_endtag(self, tag):
        if tag in _BLOCK_TAGS:
            self.parts.append(" ")
        if tag in ("ol", "ul") and self._lists:
            self._lists.pop()

    def handle_data(self, data):
        self.parts.append(data)


SNIPPET_LIMIT = 300  # characters of card snippet text


def markdown_to_text(text: str, limit: int = SNIPPET_LIMIT) -> str:
    """Plain-text snippet of Markdown for compact card display.

    Derived from render_markdown() output so the card shows exactly the text
    the list view shows (see _TextExtractor), with whitespace collapsed.
    Bullet, quote and heading markers only disappear where Markdown itself
    treats them as block syntax; prose such as "- 5 degrees" in the middle
    of a paragraph is kept verbatim, as in the list view. Returns a plain
    str (not SafeString) for the template to autoescape.
    """
    if not text:
        return ""
    return _html_to_text(str(render_markdown(text)), limit)


def _html_to_text(html: str, limit: int) -> str:
    """Snippet text of already-sanitized description HTML (see
    markdown_to_text), so a cached rendering can be reused without parsing
    the Markdown again."""
    extractor = _TextExtractor()
    extractor.feed(html)
    extractor.close()
    plain = _WS_RE.sub(" ", "".join(extractor.parts)).strip()
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
    return f"md:{RENDER_FINGERPRINT}:{kind}:{submission.pk}:{ts}"


def render_submission_description(submission) -> str:
    """Flag-aware, cached rendering for a submission's description.

    Returns a SafeString only when the flag is on. When the flag is off,
    returns the plain text with legacy entities decoded
    (decode_legacy_entities), as a plain str, NOT mark_safe, so the template
    autoescapes it exactly once.
    """
    raw = submission.service_description or ""
    if not markdown_enabled():
        return decode_legacy_entities(raw)
    key = _cache_key("html", submission)
    cached = cache.get(key)
    if cached is not None:
        return mark_safe(cached)
    html = render_markdown(raw)
    cache.set(key, str(html), timeout=MD_CACHE_TTL)
    return html


def submission_description_snippet(submission) -> str:
    """Flag-aware, cached plain-text card snippet for a submission.

    Flag off: the plain text with legacy entities decoded. Flag on: the same
    text markdown_to_text(raw) gives, derived from the (cached) rendered HTML
    so the Markdown is parsed once for both the card and the list view.
    Always a plain str for the template to autoescape.
    """
    raw = submission.service_description or ""
    if not markdown_enabled():
        return decode_legacy_entities(raw)
    key = _cache_key("text", submission)
    cached = cache.get(key)
    if cached is not None:
        return cached
    text = _html_to_text(str(render_submission_description(submission)), SNIPPET_LIMIT)
    cache.set(key, text, timeout=MD_CACHE_TTL)
    return text
