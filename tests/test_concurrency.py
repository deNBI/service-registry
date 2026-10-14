"""
Concurrency and data-integrity guards
=====================================
Each class covers one behaviour that must hold when requests overlap:

  TestEmailQueuedAfterCommit  - notifications are queued only once the data is committed

The overlap is staged deterministically: the competing write is made between
the moment the form is loaded and the moment it is posted.
"""

import re
from unittest import mock

import pytest
from django.core import mail
from django.db import transaction
from django.urls import reverse

from apps.submissions.lifecycle import get_no_reset_fields
from tests.factories import (
    APIKeyFactory,
    ServiceSubmissionFactory,
)
from tests.helpers import edit_form_payload

CHANGE = "admin:submissions_servicesubmission_change"
CHANGELIST = "admin:submissions_servicesubmission_changelist"
LONG_TEXT = "Example text that is long enough for the description length rule. "


def _in_atomic_block() -> bool:
    return transaction.get_connection().in_atomic_block


def _hidden_value(response, name):
    """Value of the hidden input ``name`` in a rendered page, or None."""
    html = response.content.decode()
    for tag in re.findall(r"<input[^>]*>", html):
        if f'name="{name}"' in tag and 'type="hidden"' in tag:
            m = re.search(r'value="([^"]*)"', tag)
            return m.group(1) if m else ""
    return None


def _grant(client, sub):
    """Give the test client an edit grant for ``sub`` (as the key lookup does)."""
    key_obj, _ = APIKeyFactory.create_with_plaintext(submission=sub)
    session = client.session
    session["edit_grants"] = {str(sub.pk): str(key_obj.pk)}
    session.save()


@pytest.fixture(autouse=True)
def _empty_cache():
    """One-time tokens live in the cache; start every test without any."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def exempt(settings):
    """Edits to these fields keep an approved service approved."""
    settings.SUBMISSION_NO_RESET_FIELDS = ["github_url", "website_url"]
    get_no_reset_fields.cache_clear()
    yield
    get_no_reset_fields.cache_clear()


# ---------------------------------------------------------------------------
# Notifications are queued after the transaction commits
# ---------------------------------------------------------------------------

STATUS_BUTTONS = [
    ("_approve", "submitted", "approved"),
    ("_reject", "submitted", "rejected"),
    ("_under_review", "submitted", "under_review"),
    ("_deprecate", "approved", "deprecated"),
    ("_undeprecate", "deprecated", "submitted"),
]


@pytest.mark.django_db(transaction=True)
class TestEmailQueuedAfterCommit:
    @pytest.mark.parametrize("button,start,end", STATUS_BUTTONS)
    def test_change_form_status_button_queues_email_after_commit(
        self, superuser_client, button, start, end
    ):
        sub = ServiceSubmissionFactory(status=start, biotools_url="")
        seen = []

        with mock.patch(
            "apps.submissions.admin.send_submission_notification.delay",
            side_effect=lambda *a, **k: seen.append(_in_atomic_block()),
        ):
            resp = superuser_client.post(
                reverse(CHANGE, args=[sub.pk]), edit_form_payload(sub, **{button: "1"})
            )
        assert resp.status_code == 302
        sub.refresh_from_db()
        assert sub.status == end
        assert seen == [False]

    def test_change_form_status_email_reads_committed_status(self, superuser_client):
        """The mail is rendered from the committed row, so it names the new status."""
        sub = ServiceSubmissionFactory(status="submitted", biotools_url="")
        superuser_client.post(
            reverse(CHANGE, args=[sub.pk]), edit_form_payload(sub, _approve="1")
        )
        assert len(mail.outbox) >= 1
        assert any("Approved" in m.subject for m in mail.outbox)

    def test_change_form_status_email_not_sent_when_request_fails(
        self, superuser_client
    ):
        sub = ServiceSubmissionFactory(status="submitted", biotools_url="")
        superuser_client.raise_request_exception = False
        with mock.patch(
            "apps.submissions.admin.ServiceSubmissionAdmin._log",
            side_effect=RuntimeError("write failed"),
        ):
            resp = superuser_client.post(
                reverse(CHANGE, args=[sub.pk]), edit_form_payload(sub, _approve="1")
            )
        assert resp.status_code == 500
        sub.refresh_from_db()
        assert sub.status == "submitted"
        assert mail.outbox == []

    def test_bulk_action_queues_email_after_commit(self, superuser_client):
        sub = ServiceSubmissionFactory(status="submitted", biotools_url="")
        seen = []
        with mock.patch(
            "apps.submissions.admin.send_submission_notification.delay",
            side_effect=lambda *a, **k: seen.append(_in_atomic_block()),
        ):
            superuser_client.post(
                reverse(CHANGELIST),
                {"action": "action_approve", "_selected_action": [str(sub.pk)]},
            )
        assert seen == [False]
        sub.refresh_from_db()
        assert sub.status == "approved"
