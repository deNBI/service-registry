"""Read-only audit of service_description rows ahead of enabling Markdown.

Flags rows whose visible text would change once descriptions are rendered as
Markdown, so operators can fix them in the admin before flipping the
``markdown_descriptions`` feature flag. Never writes to the database or cache.
"""

import csv
import html
import os

from django.core.management.base import BaseCommand, CommandError

from apps.submissions.csv_utils import csv_safe
from apps.submissions.markdown_render import (
    _WS_RE,
    markdown_to_text,
    render_with_notice,
)
from apps.submissions.models import ServiceSubmission

_PREVIEW_LEN = 200
_PREVIEW_CONTEXT = 60
_FLAGGED_MSG = (
    "Flagged rows found; fix them in the admin before enabling markdown_descriptions."
)


def _collapse(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def _window(text: str, idx: int) -> str:
    """At most _PREVIEW_LEN chars of text around idx (_PREVIEW_CONTEXT chars
    before it), with a leading/trailing ellipsis where the text is cut."""
    if len(text) <= _PREVIEW_LEN:
        return text
    start = max(0, idx - _PREVIEW_CONTEXT)
    lead = "…" if start else ""
    end = start + _PREVIEW_LEN - len(lead) - 1  # reserve room for a trailing "…"
    tail = "…" if end < len(text) else ""
    return lead + text[start:end] + tail


def _preview_pair(today: str, rendered: str) -> tuple[str, str]:
    """Preview both texts around their FIRST differing character, so a change
    deep inside a long description is visible. Identical texts (only possible
    for content_removed rows) are shown from the start."""
    idx = 0
    if today != rendered:
        idx = len(os.path.commonprefix([today, rendered]))
    return _window(today, idx), _window(rendered, idx)


def audit_description(raw: str) -> tuple[list[str], str, str]:
    """Return (reasons, today_text, markdown_text) for one description.

    today_text approximates what readers see now (entities decoded,
    whitespace collapsed); markdown_text is the full, untruncated plain text
    of the Markdown rendering. Rendering is flag-independent and uncached.
    """
    raw = raw or ""
    today = _collapse(html.unescape(raw))
    # The plain text never exceeds the raw length (rendering strips syntax
    # and unescaping only shrinks entities), so len(raw) + 1 never truncates.
    rendered = _collapse(markdown_to_text(raw, limit=len(raw) + 1))
    _, removed = render_with_notice(raw)
    reasons = []
    if rendered != today:
        reasons.append("text_changed")
    if removed:
        reasons.append("content_removed")
    return reasons, today, rendered


class Command(BaseCommand):
    help = (
        "Read-only audit: list service descriptions whose visible text would "
        "change when rendered as Markdown (text_changed), or that lose content "
        "to sanitization such as images, code or blocked links "
        "(content_removed). "
        "Run before enabling the markdown_descriptions feature flag and fix "
        "flagged rows in the admin. Legacy HTML-entity rows (e.g. '&gt;') are "
        "not flagged: Markdown renders them as the intended characters, so "
        "their display improves. Never writes to the database. Exits 0 when "
        "no rows are flagged and non-zero (CommandError) when any are; "
        "--csv writes id,service_name,status,reasons for flagged rows."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv",
            dest="csv_path",
            default=None,
            metavar="PATH",
            help="Also write flagged rows to this CSV file.",
        )

    def handle(self, *args, **options):
        flagged = []
        qs = ServiceSubmission.objects.only(
            "id", "service_name", "status", "service_description"
        ).order_by("id")
        for sub in qs:
            reasons, today, rendered = audit_description(sub.service_description)
            if reasons:
                flagged.append(
                    (sub.id, sub.service_name, sub.status, reasons, today, rendered)
                )

        for rid, name, status, reasons, today, rendered in flagged:
            self.stdout.write(f"[{status}] {name} (id={rid}): {', '.join(reasons)}")
            today_preview, rendered_preview = _preview_pair(today, rendered)
            self.stdout.write(f"    today:    {today_preview}")
            self.stdout.write(f"    markdown: {rendered_preview}")

        if options["csv_path"]:
            with open(options["csv_path"], "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(["id", "service_name", "status", "reasons"])
                for rid, name, status, reasons, *_ in flagged:
                    writer.writerow([rid, csv_safe(name), status, ";".join(reasons)])

        self.stdout.write(f"{len(flagged)} row(s) flagged.")
        if flagged:
            raise CommandError(_FLAGGED_MSG)
