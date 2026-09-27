import pytest
from django.utils.safestring import SafeString

from apps.submissions.markdown_render import (
    markdown_enabled,
    markdown_to_text,
    render_markdown,
)


def test_markdown_enabled_reads_flag(settings):
    settings.SITE_CONFIG = {"features": {"markdown_descriptions": True}}
    assert markdown_enabled() is True


def test_markdown_enabled_defaults_false(settings):
    settings.SITE_CONFIG = {"features": {}}
    assert markdown_enabled() is False


def test_bold_italic():
    out = render_markdown("**bold** and _italic_")
    assert "<strong>bold</strong>" in out
    assert "<em>italic</em>" in out


def test_link_gets_rel_and_target():
    out = render_markdown("[site](https://example.com)")
    assert 'href="https://example.com"' in out
    assert 'rel="nofollow noopener noreferrer"' in out
    assert 'target="_blank"' in out


def test_bullet_and_numbered_lists():
    out = render_markdown("- a\n- b")
    assert "<ul>" in out and "<li>a</li>" in out
    out2 = render_markdown("1. one\n2. two")
    assert "<ol>" in out2 and "<li>one</li>" in out2


def test_nl2br_preserves_single_newlines():
    out = render_markdown("line one\nline two")
    assert "<br>" in out


def test_returns_safestring():
    assert isinstance(render_markdown("hi"), SafeString)


@pytest.mark.parametrize(
    "payload",
    [
        "<script>alert(1)</script>",
        "<img src=x onerror=alert(1)>",
        "<svg onload=x>y",
    ],
)
def test_xss_tags_neutralised(payload):
    # Under the escape model the payload is shown as INERT escaped text, so the
    # word "onerror" may appear harmlessly. The safety property is: no LIVE tag.
    out = str(render_markdown(payload))
    assert "<script" not in out
    assert "<img" not in out
    assert "<svg" not in out
    assert "&lt;" in out  # dangerous markup is escaped, not live


def test_javascript_protocol_link_dropped():
    out = render_markdown("[x](javascript:alert(1))")
    assert "javascript:" not in out
    assert "x" in out  # link text survives


def test_data_protocol_link_dropped():
    out = render_markdown("[x](data:text/html,<script>1</script>)")
    assert "data:text/html" not in out


def test_disallowed_tags_not_allowed():
    out = render_markdown("# Heading\n\n```\ncode\n```")
    assert "<h1" not in out  # headings dropped
    assert "<pre" not in out  # code blocks dropped
    assert "<code" not in out
    assert "Heading" in out  # inner text survives
    assert "code" in out


def test_empty_input():
    assert render_markdown("") == ""
    assert render_markdown(None) == ""


def test_bare_url_not_autolinked():
    out = str(render_markdown("see https://bare.example.com here"))
    assert "<a" not in out  # bare URLs stay plain text (spec §4.1)


def test_tag_like_content_preserved_not_deleted():
    # Regression: strip=True silently deleted <tag>-like content. Must survive.
    out = str(render_markdown("Supports List<String> and <select> element"))
    assert "List&lt;String&gt;" in out
    assert "&lt;select&gt;" in out


def test_tag_like_content_is_inert():
    out = str(render_markdown("<script>alert(1)</script> and <svg onload=x>y"))
    assert "<script" not in out  # not a live tag
    assert "<svg" not in out
    assert "alert(1)" in out  # shown as escaped text, not executed


def test_code_span_angle_bracket_not_double_escaped():
    out = str(render_markdown("`code <x>`"))
    assert "<p>code &lt;x&gt;</p>" in out
    assert "&amp;lt;" not in out


def test_fenced_code_block_angle_bracket_not_double_escaped():
    out = str(render_markdown("```\nList<String>\n```"))
    assert "List&lt;String&gt;" in out
    assert "&amp;lt;" not in out


def test_indented_code_block_angle_bracket_not_double_escaped():
    out = str(render_markdown("    indented <x>"))
    assert "indented &lt;x&gt;" in out
    assert "&amp;lt;" not in out


def test_autolink_url_not_linked():
    out = str(render_markdown("<https://auto.example>"))
    assert "<a" not in out
    assert "&lt;https://auto.example&gt;" in out


def test_snippet_strips_list_markers():
    # A real Markdown list (blank line before it) renders as <ul>; no markers.
    out = markdown_to_text("Our tool does:\n\n- alignment\n- assembly")
    assert "-" not in out
    assert out == "Our tool does: alignment assembly"


def test_snippet_unspaced_list_matches_list_view():
    # Without a blank line Markdown keeps it as paragraph text, so the list
    # view shows the dash; the card snippet must agree.
    text = "Our tool does:\n- alignment"
    assert "- alignment" in str(render_markdown(text))
    assert markdown_to_text(text) == "Our tool does: - alignment"


def test_snippet_keeps_prose_that_looks_like_markers():
    assert "2024." in markdown_to_text("Founded in\n2024. This project began")
    assert "- 5 degrees" in markdown_to_text(
        "Temperatures of\n- 5 degrees were recorded"
    )
    assert ">90% accuracy" in markdown_to_text("&gt;90% accuracy on the benchmark")


def test_snippet_word_boundary_between_heading_and_paragraph():
    assert markdown_to_text("# Title\n\nBody text") == "Title Body text"


def test_snippet_is_plain_text_not_escaped():
    text = "<script>alert(1)</script> & <config>"
    out = markdown_to_text(text)
    assert out == text
    assert "&lt;" not in out and "&amp;" not in out


def test_snippet_strips_emphasis_and_unescapes():
    out = markdown_to_text("**bold** & <fast>")
    assert "*" not in out
    assert "bold" in out
    assert "&amp;" not in out and "&" in out  # unescaped


def test_snippet_preserves_identifiers():
    # Regression: blanket * _ ` stripping corrupted domain identifiers.
    out = markdown_to_text("Supports gene_name and length*width in the grid step")
    assert "gene_name" in out
    assert "length*width" in out


def test_snippet_truncates():
    out = markdown_to_text("x" * 500, limit=100)
    assert len(out) <= 104  # 100 + ellipsis


def test_snippet_empty():
    assert markdown_to_text("") == ""
