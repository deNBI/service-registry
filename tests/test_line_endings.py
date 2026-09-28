"""Line endings are canonical LF: browsers submit textareas with CRLF, JSON
clients send LF, and the same text must store, measure and diff identically
on every channel (see apps/submissions/validation.normalize_newlines)."""

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.submissions.diff_utils import build_diff, snapshot
from apps.submissions.models import DESCRIPTION_MAX_LENGTH, DESCRIPTION_MIN_LENGTH
from apps.submissions.validation import (
    normalize_newlines,
    validate_description_length,
)
from tests.factories import ServiceSubmissionFactory


@pytest.mark.parametrize(
    "value,expected",
    [
        ("a\r\nb", "a\nb"),
        ("a\rb", "a\nb"),  # old Mac
        ("a\nb", "a\nb"),
        ("a\r\n\r\nb\r", "a\n\nb\n"),
        ("\r\r\n", "\n\n"),  # lone CR, then CRLF
        ("no breaks", "no breaks"),
        ("", ""),
    ],
)
def test_normalize_newlines(value, expected):
    assert normalize_newlines(value) == expected


def test_normalize_newlines_is_idempotent():
    once = normalize_newlines("a\r\nb\rc")
    assert normalize_newlines(once) == once


def test_length_validator_counts_a_line_break_once():
    body = "x" * (DESCRIPTION_MAX_LENGTH - 1)
    validate_description_length(
        body[:10] + "\r\n" + body[10:], DESCRIPTION_MIN_LENGTH, DESCRIPTION_MAX_LENGTH
    )  # exactly the maximum with one CRLF: accepted
    with pytest.raises(ValidationError):
        validate_description_length(
            body + "\r\ny", DESCRIPTION_MIN_LENGTH, DESCRIPTION_MAX_LENGTH
        )
    short = "y" * (DESCRIPTION_MIN_LENGTH - 2) + "\r\n"  # trailing break stripped
    with pytest.raises(ValidationError):
        validate_description_length(
            short, DESCRIPTION_MIN_LENGTH, DESCRIPTION_MAX_LENGTH
        )


@pytest.mark.django_db
def test_snapshot_treats_crlf_and_lf_as_equal():
    sub = ServiceSubmissionFactory(biotools_url="", comments="note\nline two")
    before = snapshot(sub)
    sub.comments = "note\r\nline two"
    sub.service_description = sub.service_description.replace("\n", "\r\n")
    assert build_diff(before, snapshot(sub)) == []
    sub.comments = "note\r\nline 2"
    assert [c["field"] for c in build_diff(before, snapshot(sub))] == ["comments"]


@pytest.mark.django_db
def test_every_text_field_is_stored_with_lf():
    sub = ServiceSubmissionFactory(
        biotools_url="",
        comments="c1\r\nc2",
        user_knowledge_required="k1\rk2",
        associated_partner_note="p1\r\np2",
    )
    sub.refresh_from_db()
    assert sub.comments == "c1\nc2"
    assert sub.user_knowledge_required == "k1\nk2"
    assert sub.associated_partner_note == "p1\np2"


@pytest.mark.django_db
def test_preview_counts_a_crlf_line_break_once(client, settings):
    import copy

    cfg = copy.deepcopy(settings.SITE_CONFIG)
    cfg.setdefault("features", {})["markdown_descriptions"] = True
    settings.SITE_CONFIG = cfg
    url = reverse("submissions:markdown-preview")
    at_max = "x" * 100 + "\r\n" + "y" * (DESCRIPTION_MAX_LENGTH - 101)
    resp = client.post(url, {"service_description": at_max})
    assert b"too long" not in resp.content
    assert b"<br>" in resp.content
    resp = client.post(url, {"service_description": at_max + "z"})
    assert b"Description is too long to preview." in resp.content
