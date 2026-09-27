"""Tests for the read-only audit_markdown_descriptions management command."""

import csv
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.submissions.models import ServiceSubmission
from tests.factories import ServiceSubmissionFactory

pytestmark = pytest.mark.django_db


def _run(**kwargs):
    out = StringIO()
    call_command("audit_markdown_descriptions", stdout=out, **kwargs)
    return out.getvalue()


def _run_flagged(**kwargs):
    out = StringIO()
    with pytest.raises(CommandError) as exc:
        call_command("audit_markdown_descriptions", stdout=out, **kwargs)
    return out.getvalue(), str(exc.value)


def _make(desc, name="Svc", status="approved"):
    return ServiceSubmissionFactory(
        status=status,
        biotools_url="",
        service_name=name,
        service_description=desc,
    )


@pytest.mark.parametrize(
    "desc,reason",
    [
        ("Intro text long enough.\n\n- item one\n- item two", "text_changed"),
        ("Uses *emphasis* here", "text_changed"),
        ("See [site](https://example.org) now", "text_changed"),
        ("[bad](javascript:alert(1)) and more text", "content_removed"),
        # Headings are rendered (scaled down), so nothing is removed, but the
        # '#' disappears and the display text changes.
        ("# Heading\n\nBody", "text_changed"),
        # Python-Markdown needs no space after '#': this IS a heading and
        # the '#' disappears, so the display genuinely changes.
        ("#1 tool for alignment", "text_changed"),
        ("Logo ![i](https://e.org/x.png) here", "content_removed"),
        ("Run `make build` first", "content_removed"),
    ],
)
def test_flags_rows_whose_display_changes(desc, reason):
    sub = _make(desc, name="Risky Tool")
    out, err = _run_flagged()
    assert f"[approved] Risky Tool (id={sub.pk}):" in out
    assert reason in out
    if reason == "text_changed":
        assert "content_removed" not in out
    assert "today:" in out and "markdown:" in out
    assert "1 row(s) flagged." in out
    assert err == "1 row(s) flagged."


@pytest.mark.parametrize(
    "desc",
    [
        "Plain description with no markdown at all.",
        "gene_name and length*width values",
        "x &gt; 5 &amp; y",
        "Tool #1 for alignment",
        "Line one\nLine two",
        "List<String> and <select>",
        "",
    ],
)
def test_does_not_flag_unchanged_display(desc):
    _make(desc)
    out = _run()
    assert "0 row(s) flagged." in out


def test_clean_db_returns_normally():
    out = _run()
    assert "0 row(s) flagged." in out


def test_only_flagged_rows_reported_and_counted():
    _make("Plain prose.", name="Clean One")
    _make("Uses *emphasis* here", name="Flagged One", status="submitted")
    _make("# Heading\n\nBody", name="Flagged Two")
    out, err = _run_flagged()
    assert "Clean One" not in out
    assert "[submitted] Flagged One" in out
    assert "Flagged Two" in out
    assert err == "2 row(s) flagged."


def test_csv_written_with_header_and_flagged_rows(tmp_path):
    _make("Plain prose.", name="Clean One")
    bad = _make("Logo ![i](https://e.org/x.png) here", name="Flagged, Two")
    path = tmp_path / "audit.csv"
    _run_flagged(csv=str(path))
    with open(path, newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["id", "service_name", "status", "reasons"]
    assert len(rows) == 2
    assert rows[1][0] == str(bad.pk)
    assert rows[1][1] == "Flagged, Two"
    assert rows[1][2] == "approved"
    assert "content_removed" in rows[1][3].split(";")


def test_csv_written_with_header_only_when_clean(tmp_path):
    _make("Plain prose.")
    path = tmp_path / "audit.csv"
    _run(csv=str(path))
    with open(path, newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows == [["id", "service_name", "status", "reasons"]]


def test_long_text_is_truncated_in_output():
    _make("*" + "a" * 500 + "*")
    out, _ = _run_flagged()
    assert "today:" in out and "markdown:" in out
    for line in out.splitlines():
        if line.strip().startswith(("today:", "markdown:")):
            assert len(line) < 260


def test_read_only_single_query(django_assert_num_queries):
    sub = _make("# Heading\n\nBody")
    before = ServiceSubmission.objects.values("updated_at", "service_description").get(
        pk=sub.pk
    )
    with django_assert_num_queries(1):
        _run_flagged()
    after = ServiceSubmission.objects.values("updated_at", "service_description").get(
        pk=sub.pk
    )
    assert before == after


def test_csv_neutralises_formula_in_service_name(tmp_path):
    _make("# Heading\n\nBody", name="=HYPERLINK(1)")
    path = tmp_path / "audit.csv"
    _run_flagged(csv=str(path))
    with open(path, newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows[1][1] == "'=HYPERLINK(1)"


def test_help_text_does_not_list_headings_as_removed_content():
    """Headings are supported (rendered h4-h6), so the help must not cite
    them as content lost to sanitization."""
    from apps.submissions.management.commands.audit_markdown_descriptions import (
        Command,
    )

    assert "headings" not in Command.help
    assert "images, code or blocked links (content_removed)" in Command.help
