"""Plain-text email bodies must not be HTML-escaped; HTML bodies must be.

Plain text is not HTML, so entities such as &gt; or &#x27; would show up
literally in the recipient's mail client. The .txt templates are rendered via
apps.submissions.tasks.render_plaintext (autoescape disabled at the source);
the .html alternatives keep Django's default autoescaping.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
from django.conf import settings as django_settings
from django.core import mail

from tests.factories import ServiceSubmissionFactory

pytestmark = pytest.mark.django_db

RAW_DESC = "x > 5 & Bob's **bold**"
ESCAPED_ENTITIES = ("&gt;", "&lt;", "&amp;", "&#x27;", "&#39;", "&quot;")


@pytest.fixture(autouse=True)
def email_setup(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True


def _assert_unescaped(body):
    for bad in ESCAPED_ENTITIES:
        assert bad not in body, f"{bad!r} found in plain-text body"


def _submission(**kwargs):
    defaults = {
        "status": "submitted",
        "biotools_url": "",
        "service_name": "Bob's Tool & Co",
        "service_description": RAW_DESC,
        "submitter_first_name": "Zoë",
        "submitter_last_name": "O'Brien",
        "internal_contact_email": "pi@example.com",
    }
    defaults.update(kwargs)
    return ServiceSubmissionFactory(**defaults)


def _html(msg):
    html_body, mimetype = msg.alternatives[0]
    assert mimetype == "text/html"
    return html_body


# ---------------------------------------------------------------------------
# render_plaintext unit tests
# ---------------------------------------------------------------------------


def test_render_plaintext_does_not_escape():
    from apps.submissions.tasks import render_plaintext

    out = render_plaintext(
        "submissions/email/notification_update_submitter.txt",
        {
            "submission": SimpleNamespace(
                submitter_first_name="Zoë",
                submitter_last_name="O'Brien",
                service_name="A & B <tool>",
                id="abc",
                updated_at=None,
            ),
            "changes": [{"label": "Desc", "old": "a < b", "new": RAW_DESC}],
            "CONTACT_EMAIL": "x@example.com",
        },
    )
    assert "Dear Zoë O'Brien," in out
    assert "A & B <tool>" in out
    assert "Previous: a < b" in out
    assert f"New:      {RAW_DESC}" in out
    assert "--- FIELDS CHANGED (1) ---" in out  # filters + tags still work
    _assert_unescaped(out)


def test_render_plaintext_matches_render_to_string_for_safe_values():
    """Apart from escaping, output is identical to the default renderer."""
    from django.template.loader import render_to_string

    from apps.submissions.tasks import render_plaintext

    sub = _submission(service_name="Plain Tool", submitter_last_name="Doe")
    ctx = {
        "submission": sub,
        "event": "created",
        "event_label": "New submission",
        "categories": ["Cat A", "Cat B"],
        "pis": [],
        "changes": [],
        "admin_url": "",
        "CONTACT_ORG": "Org",
        "CONTACT_EMAIL": "c@example.com",
        "WEBSITE_URL": "https://example.com",
    }
    sub.service_description = "plain"
    ctx["service_description_text"] = "plain"
    name = "submissions/email/notification.txt"
    assert render_plaintext(name, ctx) == render_to_string(name, ctx)


def _txt_email_templates():
    names = []
    for d in django_settings.TEMPLATES[0]["DIRS"]:
        for p in sorted(Path(d).glob("submissions/email/*.txt")):
            names.append(f"submissions/email/{p.name}")
    return names


def test_txt_template_discovery_finds_templates():
    assert len(_txt_email_templates()) >= 4


@pytest.mark.parametrize("template_name", _txt_email_templates())
def test_every_txt_email_template_renders_unescaped(template_name):
    """Guard: any current or future .txt email template stays unescaped."""
    from apps.submissions.tasks import render_plaintext

    sub = _submission(
        service_name='Bob\'s <Tool> & "Co"',
        submitter_first_name="Zoë",
        submitter_last_name="O'Brien",
    )
    ctx = {
        "submission": sub,
        "event": "updated",
        "event_label": 'Bob\'s & <event> "x"',
        "status_message": 'It\'s approved & live > "now"',
        "categories": ["R&D <core>"],
        "pis": [],
        "changes": [{"label": "L&D", "old": 'it\'s < "a"', "new": "a & b > c"}],
        "status_reset": True,
        "admin_url": "https://example.com/?a=1&b=2",
        "CONTACT_ORG": 'O\'Org & Co <"hq">',
        "CONTACT_EMAIL": "c@example.com",
        "WEBSITE_URL": "https://example.com",
    }
    out = render_plaintext(template_name, ctx)
    assert 'Bob\'s <Tool> & "Co"' in out
    _assert_unescaped(out)


# ---------------------------------------------------------------------------
# Real send path: admin notification task
# ---------------------------------------------------------------------------


def test_admin_text_email_shows_raw_markdown(settings):
    from apps.submissions.tasks import send_submission_notification

    # Override address: only the admin notification is sent.
    settings.SUBMISSION_NOTIFY_OVERRIDE = "admin@example.com"
    sub = _submission()
    send_submission_notification(str(sub.id), event="created")
    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    assert RAW_DESC in msg.body.splitlines()
    assert "Bob's Tool & Co" in msg.body
    assert "<strong>" not in msg.body
    _assert_unescaped(msg.body)


def test_html_change_table_never_turns_legacy_markup_live(settings):
    """The description's change values are entity-decoded once for display,
    then autoescaped once: legacy encoded markup reads as text, never as a
    live tag, and double-encoded text keeps one level of encoding."""
    from apps.submissions.tasks import send_submission_notification

    settings.SUBMISSION_NOTIFY_OVERRIDE = "admin@example.com"
    sub = _submission()
    changes = [
        {
            "field": "service_description",
            "label": "Service Description",
            "old": "&amp;lt;b&amp;gt; kept encoded",
            "new": "&lt;script&gt;alert(1)&lt;/script&gt;",
        }
    ]
    send_submission_notification(str(sub.id), event="updated", changes=changes)
    html_body = _html(mail.outbox[0])
    assert "<script>" not in html_body
    assert '<td class="after">&lt;script&gt;alert(1)&lt;/script&gt;</td>' in html_body
    assert '<td class="before">&amp;lt;b&amp;gt; kept encoded</td>' in html_body


def test_html_notification_still_escapes(settings):
    # notification.html has no description row; a description edit reaches it
    # through the change table (ch.old / ch.new), which is autoescaped once
    # (after the one-time legacy-entity decode, a no-op for this raw value).
    from apps.submissions.tasks import send_submission_notification

    settings.SUBMISSION_NOTIFY_OVERRIDE = "admin@example.com"
    sub = _submission()
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
    html_body = _html(msg)
    assert "<script>alert(1)</script>" not in html_body
    assert "&lt;script&gt;alert(1)&lt;/script&gt; &amp; more" in html_body
    # The text alternative carries the raw value.
    assert "<script>alert(1)</script> & more" in msg.body


# ---------------------------------------------------------------------------
# Real send path: submitter emails
# ---------------------------------------------------------------------------


def test_submitter_created_text_email_not_escaped():
    from apps.submissions.tasks import _send_submitter_created_email

    sub = _submission()
    _send_submitter_created_email(sub)
    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    assert msg.to == ["pi@example.com"]
    assert "Dear Zoë O'Brien," in msg.body
    assert "Bob's Tool & Co" in msg.body
    _assert_unescaped(msg.body)
    assert "O&#x27;Brien" in _html(msg)


def test_submitter_status_text_email_not_escaped():
    from apps.submissions.tasks import _send_submitter_status_email

    sub = _submission(status="approved")
    _send_submitter_status_email(sub)
    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    assert "Dear Zoë O'Brien," in msg.body
    assert "Bob's Tool & Co" in msg.body
    _assert_unescaped(msg.body)


def test_submitter_updated_text_email_not_escaped():
    from apps.submissions.tasks import _send_submitter_updated_email

    sub = _submission()
    changes = [
        {
            "field": "service_description",
            "label": "Service Description",
            "old": "old text",
            "new": RAW_DESC,
        },
        {
            "field": "service_name",
            "label": "Service Name",
            "old": "<b>x</b>",
            "new": "Bob's Tool & Co",
        },
    ]
    _send_submitter_updated_email(sub, changes)
    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    assert "Dear Zoë O'Brien," in msg.body
    assert f"New:      {RAW_DESC}" in msg.body
    assert "Previous: <b>x</b>" in msg.body
    _assert_unescaped(msg.body)
    html_body = _html(msg)
    assert "<b>x</b>" not in html_body
    assert "&lt;b&gt;x&lt;/b&gt;" in html_body


def test_submitter_email_via_task_not_escaped():
    from apps.submissions.tasks import send_submission_notification

    sub = _submission()
    send_submission_notification(str(sub.id), event="created")
    submitter = next(m for m in mail.outbox if "pi@example.com" in m.to)
    assert "Dear Zoë O'Brien," in submitter.body
    _assert_unescaped(submitter.body)


# ---------------------------------------------------------------------------
# Legacy HTML entities in the description are decoded once in plain text
# ---------------------------------------------------------------------------

LEGACY_DESC = "x &gt; 5 &amp; y"


def _description_section(body: str) -> str:
    start = body.index("Description:\n") + len("Description:\n")
    return body[start : body.index("\n\nCategories:")]


def test_admin_text_email_decodes_legacy_description_entities(settings):
    """Rows saved by the old escaping web form hold entities; plain text must
    show the intended characters, as the API and Markdown rendering do."""
    from apps.submissions.tasks import send_submission_notification

    settings.SUBMISSION_NOTIFY_OVERRIDE = "admin@example.com"
    sub = _submission(service_description=LEGACY_DESC)
    send_submission_notification(str(sub.id), event="created")
    assert _description_section(mail.outbox[0].body) == "x > 5 & y"


def test_text_email_decodes_description_entities_exactly_once(settings):
    from apps.submissions.tasks import send_submission_notification

    settings.SUBMISSION_NOTIFY_OVERRIDE = "admin@example.com"
    sub = _submission(service_description="&amp;lt;b&amp;gt; &lt;i&gt;")
    send_submission_notification(str(sub.id), event="created")
    assert _description_section(mail.outbox[0].body) == "&lt;b&gt; <i>"


def test_text_email_raw_description_unchanged(settings):
    """New raw rows are unaffected: only entity sequences are decoded."""
    from apps.submissions.tasks import send_submission_notification

    settings.SUBMISSION_NOTIFY_OVERRIDE = "admin@example.com"
    raw = "a < b & c > d **bold**\r\nline 2"
    sub = _submission(service_description=raw)
    send_submission_notification(str(sub.id), event="created")
    # Stored with LF line endings (browsers submit CRLF); nothing else changes.
    assert _description_section(mail.outbox[0].body) == raw.replace("\r\n", "\n")


def test_change_tables_decode_description_entities_only():
    """The description's before/after values in the change tables are
    decoded once in both the .txt and the .html body (the HTML autoescapes
    the decoded value exactly once); other fields stay as stored."""
    from apps.submissions.tasks import send_submission_notification

    sub = _submission(service_description=LEGACY_DESC)
    changes = [
        {
            "field": "service_description",
            "label": "Service Description",
            "old": "a &lt; b",
            "new": LEGACY_DESC,
        },
        {
            "field": "service_name",
            "label": "Service Name",
            "old": "R&amp;D",
            "new": "Bob's Tool & Co",
        },
    ]
    send_submission_notification(str(sub.id), event="updated", changes=changes)
    admin = next(m for m in mail.outbox if "pi@example.com" not in m.to)
    submitter = next(m for m in mail.outbox if "pi@example.com" in m.to)
    assert "    Before: a < b\n    After:  x > 5 & y\n" in admin.body
    assert "    Before: R&amp;D\n" in admin.body
    assert "    Previous: a < b\n    New:      x > 5 & y\n" in submitter.body
    assert "    Previous: R&amp;D\n" in submitter.body
    # HTML alternatives: decoded once, then autoescaped once.
    for msg in (admin, submitter):
        html = _html(msg)
        assert '<td class="before">a &lt; b</td>' in html
        assert '<td class="after">x &gt; 5 &amp; y</td>' in html
        assert '<td class="before">R&amp;amp;D</td>' in html
    # The caller's change dicts are not mutated.
    assert changes[0]["new"] == LEGACY_DESC
