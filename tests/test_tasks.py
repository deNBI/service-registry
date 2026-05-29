"""
Task Tests
==========
Tests for async Celery tasks — primarily email dispatch.
Tasks are executed synchronously in tests (CELERY_TASK_ALWAYS_EAGER=True).
"""

import pytest
from django.core import mail

from tests.factories import ServiceSubmissionFactory


@pytest.fixture(autouse=True)
def celery_eager(settings):
    """Run all Celery tasks synchronously in tests."""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True


# ---------------------------------------------------------------------------
# send_submission_notification — admin email
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSubmissionNotificationTask:
    def test_notification_sent_on_create(self):
        sub = ServiceSubmissionFactory(internal_contact_email="admin@example.com")
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(str(sub.id), event="created")
        # Admin email + submitter confirmation = 2 emails.
        assert len(mail.outbox) == 2

    def test_submitter_not_in_cc_on_admin_email(self):
        """Submitter must never appear in CC on the admin notification."""
        sub = ServiceSubmissionFactory(internal_contact_email="submitter@example.com")
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(str(sub.id), event="created")
        admin_email = next(
            m for m in mail.outbox if "submitter@example.com" not in m.to
        )
        assert "submitter@example.com" not in admin_email.cc

    def test_notification_subject_contains_service_name(self):
        sub = ServiceSubmissionFactory(service_name="Galaxy Europe")
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(str(sub.id), event="created")
        assert any("Galaxy Europe" in m.subject for m in mail.outbox)

    def test_notification_does_not_contain_api_key(self):
        """Email body must never contain any API key or key hash."""
        sub = ServiceSubmissionFactory()
        from apps.submissions.models import SubmissionAPIKey

        key_obj, plaintext = SubmissionAPIKey.create_for_submission(sub)

        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(str(sub.id), event="created")

        for msg in mail.outbox:
            assert plaintext not in msg.body
            assert key_obj.key_hash not in msg.body

    def test_created_sends_submitter_confirmation(self):
        """created event: submitter receives a separate confirmation email."""
        sub = ServiceSubmissionFactory(
            service_name="MyTool",
            internal_contact_email="pi@example.com",
        )
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(str(sub.id), event="created")
        assert len(mail.outbox) == 2
        recipients = [m.to[0] for m in mail.outbox]
        assert "pi@example.com" in recipients

    def test_created_submitter_email_subject_contains_service_name(self):
        sub = ServiceSubmissionFactory(
            service_name="MyTool",
            internal_contact_email="pi@example.com",
        )
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(str(sub.id), event="created")
        submitter_email = next(m for m in mail.outbox if "pi@example.com" in m.to)
        assert "MyTool" in submitter_email.subject

    def test_created_submitter_email_has_no_admin_url(self, settings):
        """Submitter confirmation must never contain the admin portal URL."""
        settings.SITE_CONFIG = {"site": {"url": "https://registry.example.com"}}
        sub = ServiceSubmissionFactory(internal_contact_email="pi@example.com")
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(str(sub.id), event="created")
        submitter_email = next(m for m in mail.outbox if "pi@example.com" in m.to)
        assert "registry.example.com" not in submitter_email.body

    def test_created_no_submitter_email_when_internal_contact_missing(self):
        """No submitter email when internal_contact_email is blank."""
        sub = ServiceSubmissionFactory(internal_contact_email="")
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(str(sub.id), event="created")
        # Only the admin email.
        assert len(mail.outbox) == 1

    def test_status_changed_sends_two_emails(self):
        """status_changed: one admin email + one submitter status email."""
        sub = ServiceSubmissionFactory(
            service_name="MetaProFi",
            status="approved",
            internal_contact_email="pi@example.com",
        )
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(str(sub.id), event="status_changed")
        assert len(mail.outbox) == 2

        recipients = [m.to[0] for m in mail.outbox]
        assert "pi@example.com" in recipients  # submitter email

    def test_status_changed_email_has_correct_subject(self):
        sub = ServiceSubmissionFactory(service_name="MetaProFi", status="approved")
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(str(sub.id), event="status_changed")
        assert "MetaProFi" in mail.outbox[0].subject

    def test_event_label_no_underscore_in_admin_email_body(self):
        """Admin notification email body must not contain 'Status_Changed' (underscore)."""
        sub = ServiceSubmissionFactory(service_name="MyTool", status="approved")
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(str(sub.id), event="status_changed")
        admin_email = mail.outbox[0]
        body_text = admin_email.body
        # Check plain-text body
        assert "Status_Changed" not in body_text
        assert "STATUS_CHANGED" not in body_text
        # Check HTML alternative body
        html_body = next(
            (
                content
                for content, mime in admin_email.alternatives
                if mime == "text/html"
            ),
            "",
        )
        assert "Status_Changed" not in html_body
        assert "Status Changed" in html_body  # human-readable label present

    def test_nonexistent_submission_does_not_raise(self):
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(
            "00000000-0000-0000-0000-000000000000", event="created"
        )

    def test_email_override_skips_submitter_email(self, settings):
        """With SUBMISSION_NOTIFY_OVERRIDE set, submitter email is suppressed."""
        settings.SUBMISSION_NOTIFY_OVERRIDE = "override@test.com"
        sub = ServiceSubmissionFactory(
            internal_contact_email="real@example.com", status="approved"
        )
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(str(sub.id), event="status_changed")
        # Only the admin override email; no submitter email.
        assert len(mail.outbox) == 1
        assert "override@test.com" in mail.outbox[0].to
        assert "real@example.com" not in mail.outbox[0].to

    def test_email_override_skips_submitter_created_email(self, settings):
        """With SUBMISSION_NOTIFY_OVERRIDE set, created confirmation is suppressed."""
        settings.SUBMISSION_NOTIFY_OVERRIDE = "override@test.com"
        sub = ServiceSubmissionFactory(internal_contact_email="real@example.com")
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(str(sub.id), event="created")
        assert len(mail.outbox) == 1
        assert "override@test.com" in mail.outbox[0].to
        assert "real@example.com" not in mail.outbox[0].to


# ---------------------------------------------------------------------------
# send_submission_notification — updated event with diff
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestUpdatedEventWithDiff:
    _sample_changes = [
        {
            "field": "service_name",
            "label": "Service Name",
            "old": "Old Name",
            "new": "New Name",
        },
        {
            "field": "github_url",
            "label": "GitHub URL",
            "old": "—",
            "new": "https://github.com/org/repo",
        },
    ]

    def test_updated_with_changes_sends_two_emails(self):
        """Admin email + submitter updated email when changes are non-empty."""
        sub = ServiceSubmissionFactory(internal_contact_email="pi@example.com")
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(
            str(sub.id), event="updated", changes=self._sample_changes
        )
        assert len(mail.outbox) == 2
        recipients = [m.to[0] for m in mail.outbox]
        assert "pi@example.com" in recipients

    def test_updated_without_changes_sends_one_email(self):
        """No submitter updated email if changes list is empty."""
        sub = ServiceSubmissionFactory(internal_contact_email="pi@example.com")
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(str(sub.id), event="updated", changes=[])
        assert len(mail.outbox) == 1

    def test_diff_table_in_admin_email_body(self):
        sub = ServiceSubmissionFactory()
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(
            str(sub.id), event="updated", changes=self._sample_changes
        )
        admin_email = next(
            m for m in mail.outbox if m.to != [sub.internal_contact_email]
        )
        assert "Service Name" in admin_email.body
        assert "Old Name" in admin_email.body
        assert "New Name" in admin_email.body

    def test_diff_table_in_submitter_email_body(self):
        sub = ServiceSubmissionFactory(internal_contact_email="pi@example.com")
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(
            str(sub.id), event="updated", changes=self._sample_changes
        )
        submitter_email = next(m for m in mail.outbox if "pi@example.com" in m.to)
        assert "Service Name" in submitter_email.body
        assert "Old Name" in submitter_email.body
        assert "New Name" in submitter_email.body

    def test_admin_url_in_admin_email(self, settings):
        settings.SITE_CONFIG = {"site": {"url": "https://registry.example.com"}}
        sub = ServiceSubmissionFactory()
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(
            str(sub.id), event="updated", changes=self._sample_changes
        )
        admin_email = mail.outbox[0]
        assert "registry.example.com" in admin_email.body

    def test_admin_url_not_in_submitter_email(self, settings):
        settings.SITE_CONFIG = {"site": {"url": "https://registry.example.com"}}
        sub = ServiceSubmissionFactory(internal_contact_email="pi@example.com")
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(
            str(sub.id), event="updated", changes=self._sample_changes
        )
        submitter_email = next(m for m in mail.outbox if "pi@example.com" in m.to)
        assert "registry.example.com" not in submitter_email.body

    def test_submitter_updated_email_subject(self):
        sub = ServiceSubmissionFactory(
            service_name="My Service",
            internal_contact_email="pi@example.com",
        )
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(
            str(sub.id), event="updated", changes=self._sample_changes
        )
        submitter_email = next(m for m in mail.outbox if "pi@example.com" in m.to)
        assert "My Service" in submitter_email.subject

    def test_no_submitter_email_when_internal_contact_missing(self):
        sub = ServiceSubmissionFactory(internal_contact_email="")
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(
            str(sub.id), event="updated", changes=self._sample_changes
        )
        # Only admin email, no submitter email
        assert len(mail.outbox) == 1


# ---------------------------------------------------------------------------
# send_update_notification task
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSendUpdateNotification:
    def test_delegates_with_changes(self):
        sub = ServiceSubmissionFactory(internal_contact_email="pi@example.com")
        from apps.submissions.tasks import send_update_notification

        changes = [
            {"field": "comments", "label": "Comments", "old": "—", "new": "updated"}
        ]
        send_update_notification(str(sub.id), changes=changes)
        # Admin + submitter
        assert len(mail.outbox) == 2

    def test_delegates_without_changes(self):
        sub = ServiceSubmissionFactory()
        from apps.submissions.tasks import send_update_notification

        send_update_notification(str(sub.id), changes=[])
        # Admin email only
        assert len(mail.outbox) == 1


# ---------------------------------------------------------------------------
# cleanup_stale_drafts
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCleanupTask:
    def test_cleanup_runs_without_error(self):
        from apps.submissions.tasks import cleanup_stale_drafts

        result = cleanup_stale_drafts()
        assert isinstance(result, int)
        assert result >= 0


# ---------------------------------------------------------------------------
# Site context variables in email templates
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSiteContextInEmails:
    """Verify CONTACT_EMAIL / CONTACT_ORG / WEBSITE_URL reach email bodies.

    Context processors are not called from Celery tasks; _site_email_context()
    must explicitly inject these variables so email footers are populated.
    """

    def test_contact_email_in_admin_notification(self, settings):
        settings.SITE_CONFIG = {
            "contact": {"email": "coord@example.org", "organisation": "Test Org"},
            "links": {"website": "https://example.org"},
        }
        sub = ServiceSubmissionFactory(internal_contact_email="pi@example.com")
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(str(sub.id), event="created")
        admin_email = next(m for m in mail.outbox if "pi@example.com" not in m.to)
        assert "coord@example.org" in admin_email.body

    def test_contact_org_in_admin_notification(self, settings):
        settings.SITE_CONFIG = {
            "contact": {"email": "x@x.com", "organisation": "Unique Registry Org Name"},
            "links": {},
        }
        sub = ServiceSubmissionFactory(internal_contact_email="pi@example.com")
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(str(sub.id), event="created")
        admin_email = next(m for m in mail.outbox if "pi@example.com" not in m.to)
        assert "Unique Registry Org Name" in admin_email.body

    def test_contact_email_in_submitter_created_email(self, settings):
        settings.SITE_CONFIG = {
            "contact": {"email": "coord@example.org", "organisation": "Test Org"},
            "links": {"website": "https://example.org"},
        }
        sub = ServiceSubmissionFactory(internal_contact_email="pi@example.com")
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(str(sub.id), event="created")
        submitter_email = next(m for m in mail.outbox if "pi@example.com" in m.to)
        assert "coord@example.org" in submitter_email.body

    def test_contact_email_in_submitter_status_email(self, settings):
        settings.SITE_CONFIG = {
            "contact": {"email": "coord@example.org", "organisation": "Test Org"},
            "links": {"website": "https://example.org"},
        }
        sub = ServiceSubmissionFactory(
            status="approved", internal_contact_email="pi@example.com"
        )
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(str(sub.id), event="status_changed")
        submitter_email = next(m for m in mail.outbox if "pi@example.com" in m.to)
        assert "coord@example.org" in submitter_email.body

    def test_contact_email_in_submitter_updated_email(self, settings):
        settings.SITE_CONFIG = {
            "contact": {"email": "coord@example.org", "organisation": "Test Org"},
            "links": {"website": "https://example.org"},
        }
        sub = ServiceSubmissionFactory(internal_contact_email="pi@example.com")
        changes = [
            {"field": "comments", "label": "Comments", "old": "—", "new": "hello"}
        ]
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(str(sub.id), event="updated", changes=changes)
        submitter_email = next(m for m in mail.outbox if "pi@example.com" in m.to)
        assert "coord@example.org" in submitter_email.body


# ---------------------------------------------------------------------------
# Do-not-reply footer in submitter emails
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDoNotReplyFooter:
    """All submitter-facing emails must include the do-not-reply footer line."""

    _changes = [
        {"field": "comments", "label": "Comments", "old": "—", "new": "updated"}
    ]

    def _get_submitter_email(self, sub):
        return next(m for m in mail.outbox if sub.internal_contact_email in m.to)

    def test_created_email_has_do_not_reply(self):
        sub = ServiceSubmissionFactory(internal_contact_email="pi@example.com")
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(str(sub.id), event="created")
        email = self._get_submitter_email(sub)
        assert "do not reply" in email.body.lower()

    def test_status_update_email_has_do_not_reply(self):
        sub = ServiceSubmissionFactory(
            status="approved", internal_contact_email="pi@example.com"
        )
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(str(sub.id), event="status_changed")
        email = self._get_submitter_email(sub)
        assert "do not reply" in email.body.lower()

    def test_updated_email_has_do_not_reply(self):
        sub = ServiceSubmissionFactory(internal_contact_email="pi@example.com")
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(
            str(sub.id), event="updated", changes=self._changes
        )
        email = self._get_submitter_email(sub)
        assert "do not reply" in email.body.lower()


# ---------------------------------------------------------------------------
# Status-reset lifecycle notice in submitter update email
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStatusResetNoticeInEmail:
    """
    When status_reset=True is passed to send_submission_notification the
    submitter update email must include the lifecycle-reset notice.
    When status_reset=False (default) no such notice should appear.
    """

    _changes = [
        {"field": "service_name", "label": "Service Name", "old": "Old", "new": "New"}
    ]

    def _submitter_email(self, sub):
        return next(m for m in mail.outbox if sub.internal_contact_email in m.to)

    def test_status_reset_notice_shown_when_true(self):
        sub = ServiceSubmissionFactory(
            status="submitted", internal_contact_email="pi@example.com"
        )
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(
            str(sub.id), event="updated", changes=self._changes, status_reset=True
        )
        email = self._submitter_email(sub)
        body_lower = email.body.lower()
        assert "reset" in body_lower or "lifecycle" in body_lower

    def test_status_reset_notice_absent_when_false(self):
        sub = ServiceSubmissionFactory(
            status="submitted", internal_contact_email="pi@example.com"
        )
        from apps.submissions.tasks import send_submission_notification

        send_submission_notification(
            str(sub.id), event="updated", changes=self._changes, status_reset=False
        )
        email = self._submitter_email(sub)
        assert "lifecycle" not in email.body.lower()

    def test_send_update_notification_forwards_status_reset(self):
        sub = ServiceSubmissionFactory(
            status="submitted", internal_contact_email="pi@example.com"
        )
        from apps.submissions.tasks import send_update_notification

        send_update_notification(str(sub.id), changes=self._changes, status_reset=True)
        email = self._submitter_email(sub)
        body_lower = email.body.lower()
        assert "reset" in body_lower or "lifecycle" in body_lower
