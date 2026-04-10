"""
Async Tasks
===========
Celery tasks for background processing — primarily email notifications.

Tasks:
  - send_submission_notification : Email admin on new/updated/status-changed submission.
                                   Admin email never CC's the submitter (they get a
                                   dedicated separate email for each relevant event).
  - send_update_notification     : Email admin + submitter when a submitter edits.
  - cleanup_stale_drafts         : Periodic task to remove expired draft sessions.

Email path design
-----------------
Admin notifications (to=[admin], cc=SUBMISSION_NOTIFY_CC):
  - Contain the full internal report.
  - For "updated" events: include the field diff table and a direct admin portal link.
  - The submitter is NEVER added to CC — they receive a separate dedicated email.

Submitter notifications (to=[internal_contact_email]):
  - "created"        → notification_created_submitter.html/txt  (receipt confirmation)
  - "status_changed" → status_update_submitter.html/txt  (plain-language status msg)
  - "updated"        → notification_update_submitter.html/txt  (diff of what changed)
"""

import logging
from datetime import timedelta
from pathlib import Path

import yaml
from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Email texts — loaded once from YAML at module import time
# ---------------------------------------------------------------------------
_EMAIL_TEXTS_PATH = Path(__file__).resolve().parent / "email_texts.yaml"
_EMAIL_TEXTS: dict = {}
try:
    with open(_EMAIL_TEXTS_PATH, encoding="utf-8") as f:
        _EMAIL_TEXTS = yaml.safe_load(f) or {}
except FileNotFoundError:
    logger.warning(
        "Email texts file not found at %s; falling back to hardcoded defaults.",
        _EMAIL_TEXTS_PATH,
    )


def _email_subject(key: str, **kwargs) -> str:
    """Return an email subject line from the YAML, with placeholder substitution."""
    subjects = _EMAIL_TEXTS.get("subjects", {})
    template = subjects.get(key, "")
    if not template:
        return f"[de.NBI Registry] {kwargs.get('service_name', 'Notification')}"
    return template.format(**kwargs)


def _status_message(status: str) -> str:
    """Return the submitter-facing message for a given status."""
    messages = _EMAIL_TEXTS.get("status_messages", {})
    return messages.get(status, messages.get("default", ""))


def _site_email_context() -> dict:
    """
    Return the site-level context variables used in email templates.

    Django context processors (which normally inject CONTACT_ORG, CONTACT_EMAIL,
    etc.) are not invoked when render_to_string() is called from a Celery task
    because there is no request object.  This helper replicates the relevant
    subset of apps.submissions.context_processors.site_context() for email use.
    """
    sc: dict = getattr(settings, "SITE_CONFIG", {})
    cont = sc.get("contact", {})
    links = sc.get("links", {})
    return {
        "CONTACT_EMAIL": cont.get("email", "servicecoordination@denbi.de"),
        "CONTACT_ORG": cont.get(
            "organisation", "German Network for Bioinformatics Infrastructure"
        ),
        "WEBSITE_URL": links.get("website", "https://www.denbi.de"),
    }


def _build_admin_url(submission_id) -> str:
    """
    Return the absolute admin change-view URL for a submission.

    Returns an empty string if SITE_CONFIG is absent or misconfigured —
    the templates treat an empty string as "omit the link".
    """
    site_url = (
        getattr(settings, "SITE_CONFIG", {}).get("site", {}).get("url", "").rstrip("/")
    )
    if not site_url:
        return ""
    try:
        path = reverse(
            "admin:submissions_servicesubmission_change", args=[submission_id]
        )
        return f"{site_url}{path}"
    except NoReverseMatch:
        logger.warning("Could not build admin URL for submission %s", submission_id)
        return ""


# ---------------------------------------------------------------------------
# Main notification task
# ---------------------------------------------------------------------------


@shared_task(
    bind=True,
    name="submissions.send_submission_notification",
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    time_limit=600,
    soft_time_limit=540,
)
def send_submission_notification(
    self,
    submission_id: str,
    event: str = "created",
    changes: list | None = None,
) -> None:
    """
    Send an admin notification email for a submission event.

    Args:
        submission_id: UUID string of the ServiceSubmission.
        event:         "created" | "updated" | "status_changed"
        changes:       List of change dicts from diff_utils.build_diff()
                       (only meaningful for event="updated").

    Email routing:
        - Admin email → registry coordination address + SUBMISSION_NOTIFY_CC
        - Submitter CC is intentionally NOT added here; submitter emails are
          sent as separate dedicated messages via the helpers below.
    """
    from apps.submissions.models import ServiceSubmission

    try:
        submission = (
            ServiceSubmission.objects.select_related("service_center")
            .prefetch_related("service_categories", "responsible_pis")
            .get(id=submission_id)
        )
    except ServiceSubmission.DoesNotExist:
        logger.error(
            "send_submission_notification: submission %s not found", submission_id
        )
        return

    admin_email = (
        getattr(settings, "SITE_CONFIG", {}).get("contact", {}).get("email", "")
    )
    override = getattr(settings, "SUBMISSION_NOTIFY_OVERRIDE", "")
    recipient = override or admin_email or settings.DEFAULT_FROM_EMAIL

    # SUBMISSION_NOTIFY_CC only — submitter is NOT added here.
    cc_list = list(getattr(settings, "SUBMISSION_NOTIFY_CC", []))

    subject = _email_subject(
        event,
        service_name=submission.service_name,
        status=submission.get_status_display(),
    )

    # Build admin portal URL (included in admin email only).
    admin_url = "" if override else _build_admin_url(submission_id)

    context = {
        **_site_email_context(),
        "submission": submission,
        "event": event,
        "categories": list(
            submission.service_categories.values_list("name", flat=True)
        ),
        "pis": list(submission.responsible_pis.all()),
        "changes": changes or [],
        "admin_url": admin_url,
    }

    text_body = render_to_string("submissions/email/notification.txt", context)
    html_body = render_to_string("submissions/email/notification.html", context)

    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
        cc=cc_list,
        reply_to=[settings.DEFAULT_FROM_EMAIL],
    )
    msg.attach_alternative(html_body, "text/html")

    try:
        msg.send(fail_silently=False)
        logger.info(
            "Notification sent for submission %s (event=%s)",
            submission_id,
            event,
        )
    except Exception as exc:
        logger.error("Failed to send notification for %s: %s", submission_id, exc)
        raise self.retry(exc=exc)

    # Submitter-facing emails — sent as completely separate messages.
    if override:
        # When an override address is set (test/staging), skip submitter emails
        # to avoid accidentally emailing real submitters from test environments.
        return

    if event == "created":
        _send_submitter_created_email(submission)
    elif event == "status_changed":
        _send_submitter_status_email(submission)
    elif event == "updated" and changes:
        _send_submitter_updated_email(submission, changes)


# ---------------------------------------------------------------------------
# Submitter-facing helpers
# ---------------------------------------------------------------------------


def _send_submitter_email(
    submission,
    *,
    event_key: str,
    txt_template: str,
    html_template: str,
    subject_kwargs: dict | None = None,
    extra_context: dict | None = None,
) -> None:
    """
    Send a submitter-facing email using the given templates.

    Handles the shared boilerplate: recipient guard, subject, site context,
    rendering, sending, and logging. Each public helper below passes only the
    parts that differ between event types.
    """
    recipient = submission.internal_contact_email
    if not recipient:
        logger.warning(
            "No internal_contact_email on submission %s — skipping submitter %s email",
            submission.id,
            event_key,
        )
        return

    subject = _email_subject(
        event_key,
        service_name=submission.service_name,
        **(subject_kwargs or {}),
    )
    context = {
        **_site_email_context(),
        "submission": submission,
        **(extra_context or {}),
    }
    text_body = render_to_string(txt_template, context)
    html_body = render_to_string(html_template, context)

    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
        reply_to=[settings.DEFAULT_FROM_EMAIL],
    )
    msg.attach_alternative(html_body, "text/html")
    try:
        msg.send(fail_silently=False)
        logger.info(
            "Submitter %s email sent for submission %s to %s",
            event_key,
            submission.id,
            recipient,
        )
    except Exception as exc:
        logger.error(
            "Failed to send submitter %s email for %s: %s",
            event_key,
            submission.id,
            exc,
        )


def _send_submitter_created_email(submission) -> None:
    """Receipt confirmation — no admin URL, no timeline language."""
    _send_submitter_email(
        submission,
        event_key="submitter_created",
        txt_template="submissions/email/notification_created_submitter.txt",
        html_template="submissions/email/notification_created_submitter.html",
    )


def _send_submitter_status_email(submission) -> None:
    """Plain-language status update, separate from the admin notification."""
    _send_submitter_email(
        submission,
        event_key="submitter_status",
        txt_template="submissions/email/status_update_submitter.txt",
        html_template="submissions/email/status_update_submitter.html",
        subject_kwargs={"status": submission.get_status_display()},
        extra_context={"status_message": _status_message(submission.status)},
    )


def _send_submitter_updated_email(submission, changes: list) -> None:
    """Notify the submitter about which fields they just changed."""
    _send_submitter_email(
        submission,
        event_key="submitter_updated",
        txt_template="submissions/email/notification_update_submitter.txt",
        html_template="submissions/email/notification_update_submitter.html",
        extra_context={"changes": changes},
    )


# ---------------------------------------------------------------------------
# Update notification task
# ---------------------------------------------------------------------------


@shared_task(
    bind=True,
    name="submissions.send_update_notification",
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    time_limit=600,
    soft_time_limit=540,
)
def send_update_notification(
    self, submission_id: str, changes: list | None = None
) -> None:
    """
    Send notification when a submitter edits their submission via the update form.

    Delegates to send_submission_notification with event="updated" and the
    field-level diff so both the admin email and the submitter email include
    a "what changed" summary.
    """
    send_submission_notification.delay(
        submission_id, event="updated", changes=changes or []
    )


# ---------------------------------------------------------------------------
# Maintenance task
# ---------------------------------------------------------------------------


@shared_task(name="submissions.cleanup_stale_drafts")
def cleanup_stale_drafts() -> int:
    """
    Remove Django session entries used for draft auto-save that have not been
    accessed in more than 24 hours.

    Returns the number of sessions removed.
    """
    from django.contrib.sessions.models import Session

    cutoff = timezone.now() - timedelta(hours=24)
    stale = Session.objects.filter(expire_date__lt=cutoff)
    count = stale.count()
    stale.delete()
    logger.info("cleanup_stale_drafts: removed %d expired sessions", count)
    return count
