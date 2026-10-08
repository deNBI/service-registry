"""Tests for the GitHub-style Write | Preview editor on the public register/edit forms.

The editor (markdown_editor.html + shared markdown_editor_box.html) wraps the
service_description textarea only when the markdown_descriptions feature flag
is on. Enhancement controls ship hidden; static/js/markdown-editor.js drives
them, so the editor markup itself carries no inline script.
"""

import copy
import re
import unicodedata

import pytest
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.safestring import mark_safe

from apps.submissions.forms import SubmissionForm
from apps.submissions.models import DESCRIPTION_MAX_LENGTH, DESCRIPTION_MIN_LENGTH
from tests.factories import APIKeyFactory, ServiceSubmissionFactory
from tests.helpers import base_form_data

pytestmark = pytest.mark.django_db
FID = "id_service_description"


def _set_flag(settings, enabled: bool) -> None:
    cfg = copy.deepcopy(getattr(settings, "SITE_CONFIG", {}) or {})
    cfg.setdefault("features", {})["markdown_descriptions"] = enabled
    settings.SITE_CONFIG = cfg


@pytest.fixture
def md_on(settings):
    _set_flag(settings, True)


@pytest.fixture
def md_off(settings):
    _set_flag(settings, False)


@pytest.fixture
def edit_url(client):
    sub = ServiceSubmissionFactory(biotools_url="")
    key_obj, _ = APIKeyFactory.create_with_plaintext(submission=sub)
    session = client.session
    session["edit_grants"] = {str(sub.pk): str(key_obj.pk)}
    session.save()
    return reverse("submissions:edit", args=[sub.pk])


def _tag(html, id_):
    match = re.search(rf'<[a-z]+[^>]*id="{re.escape(id_)}"[^>]*>', html)
    assert match, f"#{id_} not rendered"
    return match.group(0)


def _assert_editor(html):
    assert "data-md-editor" in html
    assert 'role="tablist"' in html
    for tab in ("write", "preview"):
        assert f'id="{FID}-tab-{tab}"' in html
        assert f'aria-controls="{FID}-panel-{tab}"' in html
        assert f'id="{FID}-panel-{tab}"' in html
        assert f'aria-labelledby="{FID}-tab-{tab}"' in html
    assert re.search(rf'id="{FID}-tab-write"[^>]*aria-selected="true"', html)
    assert re.search(rf'id="{FID}-tab-preview"[^>]*tabindex="-1"', html)
    preview = _tag(html, f"{FID}-panel-preview")
    assert "hidden" in preview
    assert 'tabindex="0"' in preview
    assert "aria-live" not in preview
    assert f'data-min="{DESCRIPTION_MIN_LENGTH}"' in html
    assert f'data-max="{DESCRIPTION_MAX_LENGTH}"' in html
    assert f'aria-controls="{FID}-help"' in html
    assert f'id="{FID}-help"' in html and "Formatting help" in html
    assert 'name="service_description"' in html
    assert 'hx-target="#field-errors-service_description"' in html
    assert 'id="field-errors-service_description"' in html
    assert html.count("js/markdown-editor.js") == 1
    assert "markdown_preview_controls" not in html and "data-md-preview " not in html
    assert 'id="md-preview-' not in html


def _assert_label(html):
    label = re.search(rf'<label for="{FID}".*?</label>', html, re.S)
    assert label, "service_description label not rendered"
    assert "required-star" in label.group(0)
    assert "tooltip-icon" in label.group(0)


def test_register_has_editor(client, md_on):
    html = client.get(reverse("submissions:register")).content.decode()
    _assert_editor(html)
    _assert_label(html)


def test_edit_has_editor(client, md_on, edit_url):
    resp = client.get(edit_url)
    assert resp.status_code == 200
    html = resp.content.decode()
    _assert_editor(html)
    _assert_label(html)


def test_enhancement_controls_ship_hidden(client, md_on):
    html = client.get(reverse("submissions:register")).content.decode()
    tablist = re.search(r'<div[^>]*role="tablist"[^>]*>', html).group(0)
    assert "data-md-enhance" in tablist and "hidden" in tablist
    footer = re.search(r'<div class="md-editor__footer"[^>]*>', html).group(0)
    assert "data-md-enhance" in footer and "hidden" in footer
    assert "hidden" in _tag(html, f"{FID}-help")
    # The write panel (holding the textarea) is visible without JS.
    assert "hidden" not in _tag(html, f"{FID}-panel-write")


def test_flag_off_has_no_editor(client, md_off):
    html = client.get(reverse("submissions:register")).content.decode()
    assert "data-md-editor" not in html and "markdown-editor.js" not in html
    assert 'name="service_description"' in html


def test_edit_flag_off_has_no_editor(client, md_off, edit_url):
    html = client.get(edit_url).content.decode()
    assert 'name="service_description"' in html
    assert "data-md-editor" not in html and "markdown-editor.js" not in html


def test_help_lists_supported_syntax(client, md_on):
    html = client.get(reverse("submissions:register")).content.decode()
    for s in (
        "**bold**",
        "_italic_",
        "# Heading",
        "## Sub-heading",
        "- item",
        "1. item",
        "[text](https://",
        "&gt; quote",
    ):
        assert s in html


def test_help_note_lists_limitations():
    html = render_to_string("submissions/partials/markdown_help.html")
    for s in (
        "images",
        "code blocks",
        "tables",
        "strikethrough",
        "raw HTML",
        "4 spaces",
        r"<code>\#</code>",
        r"<code>1990\.</code>",
    ):
        assert s in html


def test_no_inline_script_in_editor(client, md_on):
    html = client.get(reverse("submissions:register")).content.decode()
    start = html.index("data-md-editor")
    end = html.index(f'id="{FID}-help"')
    assert "<script" not in html[start:end]


def test_box_partial_accepts_prerendered_widget():
    html = render_to_string(
        "submissions/partials/markdown_editor_box.html",
        {
            "field_id": "id_x",
            "widget_html": mark_safe('<textarea id="id_x"></textarea>'),
        },
    )
    assert 'id="id_x-panel-write"' in html
    assert '<textarea id="id_x"></textarea>' in html
    assert "hx-post" not in html  # no public validation wrapper in admin mode
    assert "<script" not in html


def test_description_length_limits_tag():
    from django.template import engines

    tpl = engines["django"].from_string(
        "{% load registry_tags %}{% description_length_limits as l %}{{ l.min }}-{{ l.max }}"
    )
    assert tpl.render({}) == f"{DESCRIPTION_MIN_LENGTH}-{DESCRIPTION_MAX_LENGTH}"


# ---------------------------------------------------------------------------
# Client-side editor script + counter parity with the server
# ---------------------------------------------------------------------------


def test_markdown_editor_js_exists_and_has_no_placeholder():
    path = finders.find("js/markdown-editor.js")
    assert path
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    assert "window.MarkdownEditor" in src and "role='tab'" in src
    assert "Placeholder" not in src


def _client_count(value: str) -> int:
    """Python port of markdown-editor.js serverLength(): LF line endings (a
    line break counts once), NFC, trimmed."""
    return len(unicodedata.normalize("NFC", re.sub(r"\r\n?", "\n", value)).strip())


@pytest.mark.parametrize(
    ("typed", "valid"),
    [
        # 24 + line break + 25 = 50: browsers submit the break as CRLF, but the
        # server counts it once, like the counter, and accepts it at min 50.
        ("a" * 24 + "\n" + "b" * 25, True),
        ("a" * 24 + "\n" + "b" * 24, False),
        # Decomposed e + U+0301 (NFC -> 1 code point), whitespace trimmed.
        ("  e\u0301" + "x" * 46 + "\n\ny  ", True),
        ("  e\u0301" + "x" * 45 + "\n\ny  ", False),
    ],
)
def test_server_counts_submitted_crlf_like_the_client_counter(typed, valid):
    submitted = typed.replace("\n", "\r\n")  # what the browser POSTs
    form = SubmissionForm(data=base_form_data({"service_description": submitted}))
    form.is_valid()
    assert ("service_description" not in form.errors) is valid
    assert _client_count(typed) == _client_count(submitted)
    assert (_client_count(typed) >= DESCRIPTION_MIN_LENGTH) is valid


def test_help_table_has_explicit_aria_roles():
    """Mobile CSS turns rows into grids, which drops implicit table semantics
    in WebKit, so the roles are stated explicitly."""
    html = render_to_string("submissions/partials/markdown_help.html")
    assert 'role="table" aria-label="Markdown formatting help"' in html
    assert html.count('role="row"') == html.count("<tr")
    assert html.count('role="cell"') == html.count("<td")


def _read_static(path: str) -> str:
    with open(finders.find(path), encoding="utf-8") as fh:
        return fh.read()


def _css_decls(css: str, selector: str) -> dict[str, str]:
    """Declarations of the rule(s) whose selector list contains `selector`,
    tolerant of whitespace, declaration order and grouped selectors. Later
    rules win, as in the cascade."""
    decls: dict[str, str] = {}
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    for sel_list, block in re.findall(r"([^{}]+)\{([^{}]*)\}", body):
        sels = {" ".join(x.split()) for x in sel_list.split(",")}
        if selector in sels:
            for decl in block.split(";"):
                if ":" in decl:
                    k, v = decl.split(":", 1)
                    decls[k.strip()] = " ".join(v.split())
    return decls


def _root_vars(*paths: str) -> dict[str, str]:
    """Custom properties from the light-theme `:root` blocks (first wins)."""
    found: dict[str, str] = {}
    for path in paths:
        css = re.sub(r"/\*.*?\*/", "", _read_static(path), flags=re.DOTALL)
        for sel_list, block in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            # rsplit drops a preceding statement such as `@charset "UTF-8";`.
            sel_list = sel_list.rsplit(";", 1)[-1]
            if ":root" not in {x.strip() for x in sel_list.split(",")}:
                continue
            for k, v in re.findall(r"(--[\w-]+)\s*:\s*([^;]+)", block):
                found.setdefault(k, v.strip())
    return found


def _resolve_rgb(value: str, variables: dict[str, str]) -> tuple[int, int, int]:
    """Resolve var() references, then parse #hex, rgb()/rgba() or a bare
    'r,g,b' triple (Bootstrap's *-rgb variables)."""
    for _ in range(10):
        m = re.search(r"var\(\s*(--[\w-]+)\s*(?:,([^()]*))?\)", value)
        if not m:
            break
        sub = variables.get(m.group(1), m.group(2))
        assert sub is not None, f"undefined custom property {m.group(1)}"
        value = value[: m.start()] + sub.strip() + value[m.end() :]
    value = value.strip()
    hx = re.fullmatch(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", value)
    if hx:
        h = hx.group(1)
        h = "".join(c * 2 for c in h) if len(h) == 3 else h
        return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))
    nums = re.findall(r"[\d.]+", value)
    return tuple(int(float(n)) for n in nums[:3])


def _contrast(fg, bg) -> float:
    def lum(rgb):
        def f(v):
            v /= 255
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

        r, g, b = (f(c) for c in rgb)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    a, b = lum(fg), lum(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def test_css_decls_helper_tolerates_formatting():
    css = """/* x */
    .a,
    .md-editor__empty   {
        font-style : italic ;
        color:   var(--gray-600)
    }
    .b { color: red; }"""
    assert _css_decls(css, ".md-editor__empty")["color"] == "var(--gray-600)"
    assert _css_decls(css, ".missing") == {}


@pytest.mark.parametrize(
    "path,color",
    [
        ("css/registry.css", "var(--gray-600)"),
        ("admin/css/markdown_preview.css", "var(--body-quiet-color)"),
    ],
)
def test_preview_placeholder_contrast_and_no_dead_busy_rule(path, color):
    """The placeholder colour meets WCAG AA (gray-600 on white 7.56:1; admin
    quiet colour 5.74:1 light, 12.15:1 dark). The JS swaps in the placeholder
    before setting aria-busy, so a busy rule on other children is dead, and
    any opacity pulse on the placeholder would drop it below 4.5:1."""
    css = _read_static(path)
    decls = _css_decls(css, ".md-editor__empty")
    assert decls.get("color") == color
    assert "opacity" not in decls
    assert "animation" not in decls
    assert "aria-busy" not in css
    assert "md-editor-pulse" not in css


@pytest.mark.parametrize(
    "selector,background",
    [
        # The counter and footer sit on the white form card.
        (".md-editor__count.is-warn", "#fff"),
        (".md-editor__count.is-error", "#fff"),
        (".md-editor__footer", "#fff"),
        (".md-editor__empty", "#fff"),
        (".md-editor__tab", "var(--gray-50)"),  # tab strip
        (".md-editor__help-toggle", "#fff"),
        # Inside the formatting help panel (--gray-50).
        (".md-editor__help-link", "var(--gray-50)"),
        (".md-editor__help-note", "var(--gray-50)"),
        (".md-editor__help-quote", "var(--gray-50)"),
        (".md-editor__alert", "var(--amber-light)"),
    ],
)
def test_public_editor_text_colours_meet_wcag_aa(selector, background):
    """Every text colour the public editor sets reaches 4.5:1 (WCAG AA) on
    the background it is actually drawn on."""
    variables = _root_vars("css/registry.css", "css/bootstrap.min.css")
    color = _css_decls(_read_static("css/registry.css"), selector)["color"]
    ratio = _contrast(
        _resolve_rgb(color, variables), _resolve_rgb(background, variables)
    )
    assert ratio >= 4.5, f"{selector}: {color} on {background} is {ratio:.2f}:1"
