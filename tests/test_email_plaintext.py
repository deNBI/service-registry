"""Plain-text email bodies must not be HTML-escaped; HTML bodies must be.

The .txt notification templates are rendered with render_to_string, which
autoescapes by default. Plain text is not HTML, so entities such as &gt; or
&#x27; would show up literally in the recipient's mail client.
"""

import pytest
from django.core import mail

from tests.factories import ServiceSubmissionFactory

pytestmark = pytest.mark.django_db

RAW_DESC = "x > 5 & Bob's **bold**"


@pytest.fixture(autouse=True)
def celery_eager(settings):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True


def _send(sub, event="created"):
    from apps.submissions.tasks import send_submission_notification

    send_submission_notification(str(sub.id), event=event)


def _admin_mail(settings, sub):
    # Override address: only the admin notification is sent.
    settings.SUBMISSION_NOTIFY_OVERRIDE = "admin@example.com"
    _send(sub)
    assert len(mail.outbox) == 1
    return mail.outbox[0]


def test_email_template_shows_raw_markdown(settings):
    sub = ServiceSubmissionFactory(
        status="submitted", biotools_url="", service_description=RAW_DESC
    )
    msg = _admin_mail(settings, sub)
    lines = msg.body.splitlines()
    assert RAW_DESC in lines
    for bad in ("&gt;", "&amp;", "&#x27;", "&#39;", "<strong>"):
        assert bad not in msg.body


def test_html_notification_still_escapes_description(settings):
    # notification.html has no description row; a description edit reaches it
    # through the change table (ch.old / ch.new), which must stay escaped.
    from apps.submissions.tasks import send_submission_notification

    settings.SUBMISSION_NOTIFY_OVERRIDE = "admin@example.com"
    sub = ServiceSubmissionFactory(status="submitted", biotools_url="")
    changes = [
        {
            "field": "service_description",
            "label": "Service Description",
            "old": "old text",
            "new": "<script>alert(1)</script> & more",
        }
    ]
    send_submission_notification(str(sub.id), event="updated", changes=changes)
    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    html_body, mimetype = msg.alternatives[0]
    assert mimetype == "text/html"
    assert "<script>alert(1)</script>" not in html_body
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; more" in html_body


def test_submitter_text_email_not_escaped(settings):
    sub = ServiceSubmissionFactory(
        status="submitted",
        biotools_url="",
        service_name="Bob's Tool & Co",
        internal_contact_email="pi@example.com",
    )
    _send(sub)
    submitter = next(m for m in mail.outbox if "pi@example.com" in m.to)
    assert "Bob's Tool & Co" in submitter.body
    assert "&amp;" not in submitter.body
    assert "&#x27;" not in submitter.body
