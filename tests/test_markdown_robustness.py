"""Robustness of the Markdown pipeline against hostile input.

The preview endpoint is public, and stored descriptions are rendered on
public pages, so no input within the description length limit may take long
to render or raise. Python-Markdown (the previous engine) needed minutes for
5,000 backticks and raised RecursionError on deeply nested lists; with 4 sync
gunicorn workers and a 60 s timeout, four preview requests could stall the
whole site. markdown-it-py is linear-time and caps nesting (MAX_NESTING).
"""

import copy
import time

import pytest
from django.urls import reverse

from apps.submissions.markdown_render import (
    MAX_NESTING,
    _allowed_ol_attr,
    markdown_to_text,
    render_markdown,
    render_with_notice,
)
from apps.submissions.models import DESCRIPTION_MAX_LENGTH

MAX = DESCRIPTION_MAX_LENGTH

# Absolute wall-clock times depend on the machine, its load and coverage
# instrumentation (several-fold slower), so the complexity check compares
# SCALING instead: rendering 4x more input must cost about 4x (linear), never
# ~16x (quadratic) or ~64x (cubic, the old engine on backticks). Measured
# ratios are 1-6 with and without coverage; MAX_SCALING leaves headroom for
# timer noise. HANG_CAP_S only catches outright hangs (the old engine took
# minutes) on any machine.
MAX_SCALING = 10.0
HANG_CAP_S = 20.0


def _render_cost(text: str) -> float:
    """Best of two wall-clock timings (the minimum filters load spikes)."""
    best = float("inf")
    for _ in range(2):
        start = time.perf_counter()
        render_with_notice(text)
        markdown_to_text(text)
        best = min(best, time.perf_counter() - start)
    return best


def _fill(unit: str) -> str:
    return (unit * (MAX // len(unit) + 1))[:MAX]


PATHOLOGICAL = {
    "backticks": _fill("`"),
    "bracket_backtick": _fill("[`"),
    "backtick_bracket": _fill("`["),
    "image_openers": _fill("!["),
    "emphasis_brackets": _fill("*["),
    "underscore_brackets": _fill("_["),
    "brackets": _fill("["),
    "unclosed_link_dest": _fill("[a]("),
    "angle_link_dest": _fill("[a](<"),
    "nested_ordered": _fill("1. "),
    "nested_bullets": _fill("  - "),
    "nested_quotes": _fill("> "),
    "quote_list_mix": _fill("> - "),
    "emphasis_runs": _fill("*a **b "),
    "entities": _fill("&amp;"),
    "escapes": _fill("\\"),
    "indented_list_staircase": "".join("    " * i + "- a\n" for i in range(MAX // 8))[
        :MAX
    ],
}


@pytest.mark.parametrize("text", PATHOLOGICAL.values(), ids=PATHOLOGICAL.keys())
def test_max_length_pathological_input_scales_linearly(text):
    assert len(text) == MAX
    # Scaling on a 4x step kept below full length to bound the test's cost.
    small = _render_cost(text[: MAX // 8])
    large = _render_cost(text[: MAX // 2])
    # Tiny timings are all noise: only judge scaling above 10 ms.
    if large > 0.01:
        ratio = large / max(small, 1e-4)
        assert ratio < MAX_SCALING, (
            f"4x input cost {ratio:.1f}x ({small * 1000:.1f} ms -> "
            f"{large * 1000:.1f} ms): superlinear rendering"
        )
    # The full-length description still renders, well inside the hang cap.
    start = time.perf_counter()
    html, _ = render_with_notice(text)
    assert isinstance(markdown_to_text(text), str)
    assert time.perf_counter() - start < HANG_CAP_S
    assert isinstance(str(html), str)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "text",
    [PATHOLOGICAL["backticks"], PATHOLOGICAL["nested_ordered"]],
    ids=["backticks", "nested_ordered"],
)
def test_preview_endpoint_survives_pathological_input(client, settings, text):
    cfg = copy.deepcopy(settings.SITE_CONFIG)
    cfg.setdefault("features", {})["markdown_descriptions"] = True
    settings.SITE_CONFIG = cfg
    start = time.perf_counter()
    resp = client.post(
        reverse("submissions:markdown-preview"), {"service_description": text}
    )
    assert time.perf_counter() - start < HANG_CAP_S
    assert resp.status_code == 200


def _nested_list(depth: int) -> str:
    return "\n".join("  " * i + f"- level {i}" for i in range(depth))


# Each list level costs two nesting levels (list + item), so MAX_NESTING = 20
# renders 9 nested lists in full; the documented limit relies on this.
MAX_LIST_DEPTH = (MAX_NESTING - 1) // 2


def test_max_nesting_matches_the_documented_list_depth():
    assert MAX_LIST_DEPTH == 9


def test_nesting_up_to_the_cap_is_rendered_in_full():
    html, removed = render_with_notice(_nested_list(MAX_LIST_DEPTH))
    assert removed is False
    for i in range(MAX_LIST_DEPTH):
        assert f"level {i}" in str(html)
    assert str(html).count("<ul>") == MAX_LIST_DEPTH


def test_nesting_beyond_the_cap_is_skipped_and_reported():
    depth = MAX_LIST_DEPTH + 1
    html, removed = render_with_notice(_nested_list(depth))
    assert removed is True
    assert f"level {depth - 2}" in str(html)
    assert f"level {depth - 1}" not in str(html)


def test_deep_blockquotes_are_capped_and_reported():
    html, removed = render_with_notice("> " * MAX_NESTING + "deep")
    assert removed is True
    assert "deep" not in str(html)
    html, removed = render_with_notice("> " * (MAX_NESTING - 1) + "deep")
    assert removed is False
    assert "deep" in str(html)


def test_render_markdown_never_raises_on_deep_nesting():
    assert str(render_markdown(PATHOLOGICAL["nested_ordered"])).startswith("<ol>")


# ---------------------------------------------------------------------------
# Ordered-list start numbers
# ---------------------------------------------------------------------------


def test_ordered_list_keeps_its_start_number():
    assert str(render_markdown("2024. Launched\n2025. Extended")) == (
        '<ol start="2024">\n<li>Launched</li>\n<li>Extended</li>\n</ol>'
    )


@pytest.mark.parametrize(
    "src,snippet",
    [
        # The list view shows the number as the marker, so the card keeps it.
        ("2024. Launched\n2025. Extended", "2024. Launched 2025. Extended"),
        ("1. first\n2. second", "1. first 2. second"),
        # Bullets are decoration: dropped, numbering of a nested list kept.
        ("- tools\n  1. align\n  2. call\n- data", "tools 1. align 2. call data"),
        ("3. a\n\n   - x\n4. b", "3. a x 4. b"),
        ("x &gt; 5<br>\n**y** &amp; z", "x > 5<br> y & z"),
    ],
)
def test_card_snippet_matches_list_view_numbering(src, snippet):
    assert markdown_to_text(src) == snippet


def test_audit_does_not_flag_a_numbered_line_whose_display_is_unchanged():
    from apps.submissions.management.commands.audit_markdown_descriptions import (
        audit_description,
    )

    reasons, today, rendered = audit_description("1990. Founded as a small group")
    assert reasons == []
    assert today == rendered == "1990. Founded as a small group"


def test_ordered_list_from_one_has_no_start_attribute():
    assert str(render_markdown("1. a\n2. b")) == "<ol>\n<li>a</li>\n<li>b</li>\n</ol>"


@pytest.mark.parametrize(
    "name,value,allowed",
    [
        ("start", "2024", True),
        ("start", "0", True),
        ("start", "123456789", True),
        ("start", "1234567890", False),
        ("start", "-1", False),
        ("start", "1e3", False),
        ("start", "", False),
        ("start", "3 onclick=x", False),
        ("type", "a", False),
        ("reversed", "", False),
    ],
)
def test_ol_attribute_filter(name, value, allowed):
    assert _allowed_ol_attr("ol", name, value) is allowed


# ---------------------------------------------------------------------------
# CommonMark specifics that change what authors see
# ---------------------------------------------------------------------------


def test_hash_without_space_is_not_a_heading():
    assert str(render_markdown("#1 tool for alignment")) == (
        "<p>#1 tool for alignment</p>"
    )


def test_image_only_description_renders_empty():
    html, removed = render_with_notice("![logo](https://e.org/logo.png)")
    assert str(html) == ""
    assert removed is True
    assert markdown_to_text("![logo](https://e.org/logo.png)") == ""


def test_paragraph_keeps_text_around_a_removed_image():
    html, removed = render_with_notice("Before ![i](https://e.org/i.png) after")
    assert str(html) == "<p>Before  after</p>"
    assert removed is True
