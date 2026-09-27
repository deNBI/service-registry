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
    """Python port of markdown-editor.js serverLength(): the textarea value
    (LF line breaks), NFC, trimmed, each line break counted as CRLF."""
    return len(re.sub(r"\r?\n", "\r\n", unicodedata.normalize("NFC", value).strip()))


@pytest.mark.parametrize(
    ("typed", "valid"),
    [
        # 24 + line break + 24 = 49 in the textarea (LF), but browsers submit
        # CRLF, so the server counts 50 and accepts it at min 50.
        ("a" * 24 + "\n" + "b" * 24, True),
        ("a" * 23 + "\n" + "b" * 24, False),
        # Decomposed é (NFC -> 1 code point), surrounding whitespace trimmed.
        ("  e\u0301" + "x" * 44 + "\n\ny  ", True),
    ],
)
def test_server_counts_submitted_crlf_like_the_client_counter(typed, valid):
    submitted = typed.replace("\n", "\r\n")  # what the browser POSTs
    form = SubmissionForm(data=base_form_data({"service_description": submitted}))
    form.is_valid()
    assert ("service_description" not in form.errors) is valid
    server_len = len(unicodedata.normalize("NFC", submitted).strip())
    assert server_len == _client_count(typed)
    assert (server_len >= DESCRIPTION_MIN_LENGTH) is valid


def test_help_table_has_explicit_aria_roles():
    """Mobile CSS turns rows into grids, which drops implicit table semantics
    in WebKit, so the roles are stated explicitly."""
    html = render_to_string("submissions/partials/markdown_help.html")
    assert 'role="table" aria-label="Markdown formatting help"' in html
    assert html.count('role="row"') == html.count("<tr")
    assert html.count('role="cell"') == html.count("<td")
