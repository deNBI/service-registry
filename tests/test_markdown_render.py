import datetime as _dt
from types import SimpleNamespace

import pytest
from django.core.cache import cache
from django.utils.safestring import SafeString

from apps.submissions import markdown_render as mr
from apps.submissions.markdown_render import (
    RENDER_VERSION,
    markdown_enabled,
    markdown_to_text,
    render_markdown,
    render_with_notice,
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


def test_headings_rendered_scaled_not_stripped():
    out = str(render_markdown("# Heading\n\nBody"))
    assert "<h1" not in out  # never a page-level heading
    assert "<h4>Heading</h4>" in out


def test_code_blocks_still_stripped():
    out = render_markdown("# Heading\n\n```\ncode\n```")
    assert "<pre" not in out  # code blocks dropped
    assert "<code" not in out
    assert "code" in out  # inner text survives


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
    assert out == "x" * 100 + "…"


def test_snippet_empty():
    assert markdown_to_text("") == ""


# --- render_submission_description / submission_description_snippet ---------

_TS = _dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=_dt.timezone.utc)


def _sub(desc, pk=1, updated_at=_TS):
    return SimpleNamespace(pk=pk, updated_at=updated_at, service_description=desc)


@pytest.fixture
def md_on(settings):
    settings.SITE_CONFIG = {"features": {"markdown_descriptions": True}}
    cache.clear()
    yield
    cache.clear()


def test_submission_description_flag_off_returns_raw_unmarked(settings):
    settings.SITE_CONFIG = {"features": {"markdown_descriptions": False}}
    out = mr.render_submission_description(_sub("**b** <x>"))
    assert out == "**b** <x>"
    assert not isinstance(out, SafeString)


def test_submission_description_flag_on_renders_safe(md_on):
    out = mr.render_submission_description(_sub("**b**"))
    assert isinstance(out, SafeString)
    assert "<strong>b</strong>" in out


def test_submission_description_empty(md_on):
    assert mr.render_submission_description(_sub(None)) == ""


def test_submission_description_is_cached(md_on):
    sub = _sub("**one**")
    assert "<strong>one</strong>" in mr.render_submission_description(sub)
    sub.service_description = "**two**"
    out = mr.render_submission_description(sub)
    assert isinstance(out, SafeString)
    assert "<strong>one</strong>" in out


def test_submission_description_updated_at_busts_cache(md_on):
    mr.render_submission_description(_sub("**one**"))
    later = _TS + _dt.timedelta(seconds=1)
    out = mr.render_submission_description(_sub("**two**", updated_at=later))
    assert "<strong>two</strong>" in out


def test_submission_description_key_and_ttl(md_on, monkeypatch):
    calls = []
    monkeypatch.setattr(
        mr.cache, "set", lambda k, v, timeout: calls.append((k, timeout))
    )
    mr.render_submission_description(_sub("x", pk=7))
    assert calls == [
        (f"md:v{mr.RENDER_VERSION}:html:7:{_TS.timestamp()}", mr.MD_CACHE_TTL)
    ]
    assert mr.MD_CACHE_TTL == 60 * 60 * 24
    assert mr.RENDER_VERSION == 2


def test_render_version_bump_forces_fresh_render(md_on, monkeypatch):
    sub = _sub("**one**")
    mr.render_submission_description(sub)
    sub.service_description = "**two**"
    monkeypatch.setattr(mr, "RENDER_VERSION", mr.RENDER_VERSION + 1)
    assert "<strong>two</strong>" in mr.render_submission_description(sub)


def test_snippet_flag_off_returns_raw(settings):
    settings.SITE_CONFIG = {"features": {"markdown_descriptions": False}}
    assert mr.submission_description_snippet(_sub("**b**")) == "**b**"


def test_snippet_flag_on_plain_text(md_on):
    out = mr.submission_description_snippet(_sub("**b** & _i_"))
    assert out == "b & i"
    assert not isinstance(out, SafeString)


def test_snippet_is_cached(md_on):
    sub = _sub("first")
    assert mr.submission_description_snippet(sub) == "first"
    sub.service_description = "second"
    assert mr.submission_description_snippet(sub) == "first"


def test_snippet_key_and_ttl(md_on, monkeypatch):
    calls = []
    monkeypatch.setattr(
        mr.cache, "set", lambda k, v, timeout: calls.append((k, timeout))
    )
    mr.submission_description_snippet(_sub("x", pk=7))
    assert calls == [
        (f"md:v{mr.RENDER_VERSION}:text:7:{_TS.timestamp()}", mr.MD_CACHE_TTL)
    ]


def test_snippet_render_version_bump_forces_fresh(md_on, monkeypatch):
    sub = _sub("first")
    mr.submission_description_snippet(sub)
    sub.service_description = "second"
    monkeypatch.setattr(mr, "RENDER_VERSION", mr.RENDER_VERSION + 1)
    assert mr.submission_description_snippet(sub) == "second"


@pytest.mark.parametrize(
    "src,expected",
    [
        ("# One", "<h4>One</h4>"),
        ("## Two", "<h5>Two</h5>"),
        ("### Three", "<h6>Three</h6>"),
        ("#### Four", "<h6>Four</h6>"),
        ("###### Six", "<h6>Six</h6>"),
    ],
)
def test_headings_are_scaled_down(src, expected):
    assert str(render_markdown(src)) == expected


def test_headings_do_not_trigger_removed_notice():
    _, removed = render_with_notice("# Overview\n\n## Features\n\nText.")
    assert removed is False


@pytest.mark.parametrize(
    "src",
    ["[r](/relative)", "[p](//evil.example)", "[f](#frag)", "[d](data:text/html,x)"],
)
def test_schemeless_and_blocked_hrefs_are_dropped(src):
    out = str(render_markdown(src))
    assert "href=" not in out
    assert render_with_notice(src)[1] is True


@pytest.mark.parametrize(
    "src,href",
    [
        ("[ok](https://e.org)", "https://e.org"),
        ("[ok](HTTPS://e.org)", "HTTPS://e.org"),
        ("[m](mailto:a@b.c)", "mailto:a@b.c"),
    ],
)
def test_allowed_schemes_keep_href(src, href):
    assert f'href="{href}"' in str(render_markdown(src))


def test_snippet_word_boundary_after_scaled_heading():
    assert markdown_to_text("# Title\nBody") == "Title Body"


def test_render_version_bumped():
    assert RENDER_VERSION == 2


@pytest.mark.parametrize(
    "src,label",
    [("[x](javascript:alert(1))", "x"), ("[x](/rel)", "x")],
)
def test_hrefless_anchor_unwrapped_to_text_and_flagged(src, label):
    html, removed = render_with_notice(src)
    assert str(html) == f"<p>{label}</p>"
    assert removed is True


def test_multiple_links_one_blocked_is_flagged():
    src = "[a](https://a.org) and [b](/rel)"
    html, removed = render_with_notice(src)
    assert 'href="https://a.org"' in str(html)
    assert "<a" in str(html) and str(html).count("<a") == 1
    assert removed is True


def test_all_links_allowed_not_flagged():
    _, removed = render_with_notice("[a](https://a.org) and [m](mailto:a@b.c)")
    assert removed is False


def test_mailto_link_has_rel_but_no_target():
    out = str(render_markdown("[m](mailto:a@b.c)"))
    assert 'rel="nofollow noopener noreferrer"' in out
    assert "target=" not in out


def test_http_link_opens_in_new_tab():
    out = str(render_markdown("[s](http://e.org)"))
    assert 'rel="nofollow noopener noreferrer"' in out
    assert 'target="_blank"' in out


def test_snippet_of_unwrapped_link_keeps_text():
    assert markdown_to_text("see [docs](/rel) now") == "see docs now"


@pytest.mark.parametrize(
    "src",
    [
        "[![i](https://e.org/x.png)](https://e.org)",
        "[ ![i](https://e.org/x.png) ](https://e.org)",
    ],
)
def test_badge_link_left_empty_by_removed_image_is_unwrapped(src):
    """A README badge loses its image; the now-empty link must not remain as
    a clickable anchor with no text (accessibility defect)."""
    html, removed = render_with_notice(src)
    assert "<a" not in str(html)
    assert str(html).replace(" ", "") == "<p></p>"
    assert removed is True
    assert "<a" not in str(render_markdown(src))


def test_normal_link_next_to_badge_is_kept():
    html, removed = render_with_notice(
        "[![i](https://e.org/x.png)](https://e.org) [docs](https://d.org)"
    )
    assert str(html).count("<a") == 1
    assert '<a href="https://d.org"' in str(html)
    assert ">docs</a>" in str(html)
    assert removed is True


def test_normal_link_unchanged():
    html, removed = render_with_notice("[docs](https://d.org)")
    assert str(html) == (
        '<p><a href="https://d.org" rel="nofollow noopener noreferrer" '
        'target="_blank">docs</a></p>'
    )
    assert removed is False
