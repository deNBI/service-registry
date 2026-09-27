"""The public form stores service_description as raw Markdown (no HTML escaping)."""

import pytest

from apps.submissions.forms import SubmissionForm
from tests.factories import ServiceSubmissionFactory
from tests.test_forms import _base_form_data

pytestmark = pytest.mark.django_db


def _data_with_desc(desc):
    data = _base_form_data()
    data["service_description"] = desc
    return data


def test_description_angle_brackets_not_escaped():
    # Must exceed DESCRIPTION_MIN_LENGTH (50 chars, forms.py:490 / models.py:44).
    desc = "Use x > 5 and 3 < 4 in the pipeline to filter reads reliably every run"
    form = SubmissionForm(data=_data_with_desc(desc))
    assert form.is_valid(), form.errors
    assert ">" in form.cleaned_data["service_description"]
    assert "&gt;" not in form.cleaned_data["service_description"]


def test_description_blockquote_survives():
    desc = (
        "> quoted line explaining the tool in full and reproducible operational detail"
    )
    form = SubmissionForm(data=_data_with_desc(desc))
    assert form.is_valid(), form.errors
    assert form.cleaned_data["service_description"].startswith("> quoted")


def test_legacy_escaped_description_round_trips_unchanged():
    # Existing rows hold bleach-escaped entities; posting them back verbatim
    # must not register as a change (so no status reset is triggered).
    stored = "Filter reads where x &gt; 5 to keep only high quality alignments per run"
    sub = ServiceSubmissionFactory(
        status="approved", biotools_url="", service_description=stored
    )
    form = SubmissionForm(data=_data_with_desc(stored), instance=sub)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["service_description"] == stored
    assert "service_description" not in form.changed_data
