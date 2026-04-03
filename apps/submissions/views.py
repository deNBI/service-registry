"""
Submission Views
================
Handles the public-facing web form for service registration and editing.

Views:
  - RegisterView   : GET shows form, POST creates a new submission
  - UpdateView     : GET shows API key prompt, POST looks up submission
  - EditView       : GET/POST for editing an existing submission (after key lookup)
  - SuccessView    : Shows confirmation with one-time API key display
  - validate_field : HTMX endpoint for per-field inline validation
"""

import base64
import datetime
import json
import logging

from django.conf import settings
from django.contrib import messages
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.utils.timezone import now
from django.views import View
from django_ratelimit.decorators import ratelimit

from .diff_utils import build_diff, snapshot, snapshot_m2m
from .forms import SubmissionForm, UpdateKeyForm
from .http_utils import get_client_ip, hash_user_agent
from .models import (
    ServiceSubmission,
    SubmissionAPIKey,
    SubmissionChangeLog,
    SubmissionStatus,
)
from .tasks import send_submission_notification, send_update_notification

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _verify_altcha(request: HttpRequest) -> bool:
    """
    Verify the ALTCHA proof-of-work payload submitted with the form.

    The widget writes a Base64-encoded JSON string into a hidden field named
    ``altcha``.  This function decodes it and calls ``altcha.verify_solution``
    with expiry checking enabled.

    Returns True (bypassed) when ``ALTCHA_HMAC_KEY`` is not configured — this
    keeps local development and tests working without any extra setup.
    """
    hmac_key = settings.ALTCHA_HMAC_KEY
    if not hmac_key:
        return True  # ALTCHA disabled — no key configured

    from altcha import verify_solution

    payload_b64 = request.POST.get("altcha", "")
    if not payload_b64:
        return False
    try:
        payload = json.loads(base64.b64decode(payload_b64))
    except Exception:
        return False
    ok, _ = verify_solution(payload, hmac_key, check_expires=True)
    return ok


# ---------------------------------------------------------------------------
# AltchaChallengeView — serve fresh proof-of-work challenges
# ---------------------------------------------------------------------------


@method_decorator(
    ratelimit(key="ip", rate=settings.RATE_LIMIT_CHALLENGE, method="GET", block=True),
    name="dispatch",
)
class AltchaChallengeView(View):
    """
    GET /captcha/

    Returns a fresh ALTCHA challenge as JSON.  The challenge is signed with
    ``ALTCHA_HMAC_KEY`` and expires after 10 minutes.  The browser widget
    fetches this endpoint automatically when the user focuses the form.
    """

    def get(self, request: HttpRequest) -> JsonResponse:
        if not settings.ALTCHA_HMAC_KEY:
            return JsonResponse({"detail": "ALTCHA not configured"}, status=503)

        from altcha import ChallengeOptions, create_challenge

        options = ChallengeOptions(
            hmac_key=settings.ALTCHA_HMAC_KEY,
            max_number=100_000,
            expires=datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(minutes=10),
        )
        challenge = create_challenge(options)
        response = JsonResponse(challenge.to_dict())
        response["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return response


# ---------------------------------------------------------------------------
# RegisterView — new submission
# ---------------------------------------------------------------------------


@method_decorator(
    ratelimit(key="ip", rate=settings.RATE_LIMIT_SUBMIT, method="POST", block=True),
    name="dispatch",
)
class RegisterView(View):
    """
    GET  /register/  — Display the blank registration form.
    POST /register/  — Validate and create a new ServiceSubmission.

    On success, redirects to SuccessView with the one-time API key passed
    via the session (not in the URL to prevent it appearing in server logs).
    """

    template_name = "submissions/register.html"

    def _context(self, form):
        return {"form": form, "altcha_enabled": bool(settings.ALTCHA_HMAC_KEY)}

    def get(self, request: HttpRequest) -> HttpResponse:
        form = SubmissionForm()
        return render(request, self.template_name, self._context(form))

    def post(self, request: HttpRequest) -> HttpResponse:
        if not _verify_altcha(request):
            form = SubmissionForm(request.POST, request.FILES)
            form.add_error(None, "CAPTCHA verification failed. Please try again.")
            return render(request, self.template_name, self._context(form), status=400)

        form = SubmissionForm(request.POST, request.FILES)

        if not form.is_valid():
            return render(request, self.template_name, self._context(form), status=422)

        # Save submission
        submission: ServiceSubmission = form.save(commit=False)
        submission.status = "submitted"
        submission.submission_ip = get_client_ip(request)
        submission.user_agent_hash = hash_user_agent(request)
        submission.save()
        form.save_m2m()  # Save ManyToMany fields

        # Generate API key — plaintext returned once, hash stored
        _, plaintext_key = SubmissionAPIKey.create_for_submission(
            submission=submission,
            label="Initial key",
            created_by="submitter",
        )

        logger.info(
            "New submission created",
            extra={
                "submission_id": str(submission.id),
                "service_name": submission.service_name,
            },
        )

        # Send async notification email
        send_submission_notification.delay(str(submission.id), event="created")

        # Pass the plaintext key via session for one-time display.
        # It is immediately cleared after the success page renders.
        request.session["pending_api_key"] = plaintext_key
        request.session["pending_submission_id"] = str(submission.id)

        return redirect("submissions:success")


# ---------------------------------------------------------------------------
# SuccessView — one-time API key display
# ---------------------------------------------------------------------------


class SuccessView(View):
    """
    GET /register/success/

    Displays the API key exactly once. The key is read from the session and
    immediately deleted. If the user reloads the page, the key is gone.
    """

    template_name = "submissions/success.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        api_key = request.session.pop("pending_api_key", None)
        submission_id = request.session.pop("pending_submission_id", None)

        if not api_key:
            # User navigated here directly without submitting — redirect to form
            return redirect("submissions:register")

        return render(
            request,
            self.template_name,
            {
                "api_key": api_key,
                "submission_id": submission_id,
            },
        )


# ---------------------------------------------------------------------------
# UpdateView — enter API key to retrieve submission for editing
# ---------------------------------------------------------------------------


@method_decorator(
    ratelimit(key="ip", rate=settings.RATE_LIMIT_UPDATE, method="POST", block=True),
    name="dispatch",
)
class UpdateView(View):
    """
    GET  /update/  — Show the API key entry form.
    POST /update/  — Validate the key and redirect to EditView.
    """

    template_name = "submissions/update.html"

    def get(self, request: HttpRequest) -> HttpResponse:
        form = UpdateKeyForm()
        return render(request, self.template_name, {"form": form})

    def post(self, request: HttpRequest) -> HttpResponse:
        form = UpdateKeyForm(request.POST)

        if not form.is_valid():
            return render(request, self.template_name, {"form": form}, status=422)

        plaintext_key = form.cleaned_data["api_key"]
        key_obj, authenticated = SubmissionAPIKey.verify(plaintext_key)

        if not authenticated:
            # Generic error — do not reveal whether the key exists or is revoked
            form.add_error("api_key", "Invalid API key. Please check and try again.")
            return render(request, self.template_name, {"form": form}, status=403)

        # Store the verified key in session to authenticate the edit view
        request.session["edit_key_id"] = str(key_obj.id)
        request.session["edit_submission_id"] = str(key_obj.submission_id)

        return redirect("submissions:edit")


# ---------------------------------------------------------------------------
# EditView — edit an existing submission (authenticated via session)
# ---------------------------------------------------------------------------


@method_decorator(
    ratelimit(key="ip", rate=settings.RATE_LIMIT_UPDATE, method="POST", block=True),
    name="dispatch",
)
class EditView(View):
    """
    GET  /update/edit/  — Show the pre-populated edit form.
    POST /update/edit/  — Validate and save changes.

    Access requires a valid API key previously verified in UpdateView.
    The session stores the verified key ID and submission ID.
    """

    template_name = "submissions/edit.html"

    def _get_submission(self, request: HttpRequest) -> ServiceSubmission | None:
        """Return the submission from session, or None if session is invalid."""
        submission_id = request.session.get("edit_submission_id")
        key_id = request.session.get("edit_key_id")
        if not submission_id or not key_id:
            return None
        try:
            # Single query: verify key is active and fetch its submission in one JOIN
            key = SubmissionAPIKey.objects.select_related("submission").get(
                id=key_id, is_active=True
            )
        except SubmissionAPIKey.DoesNotExist:
            return None

        # Enforce scope: read-only keys may view the form (GET) but may not
        # submit mutations via the web form — mirrors the API's IsSubmissionOwner check.
        if key.scope == SubmissionAPIKey.SCOPE_READ and request.method not in (
            "GET",
            "HEAD",
            "OPTIONS",
        ):
            return None

        return key.submission

    def _context(self, form, submission):
        return {
            "form": form,
            "submission": submission,
            "altcha_enabled": bool(settings.ALTCHA_HMAC_KEY),
        }

    def get(self, request: HttpRequest) -> HttpResponse:
        submission = self._get_submission(request)
        if not submission:
            messages.error(
                request, "Your session has expired. Please enter your API key again."
            )
            return redirect("submissions:update")

        form = SubmissionForm(instance=submission)
        return render(request, self.template_name, self._context(form, submission))

    def post(self, request: HttpRequest) -> HttpResponse:
        submission = self._get_submission(request)
        if not submission:
            messages.error(
                request, "Your session has expired. Please enter your API key again."
            )
            return redirect("submissions:update")

        # Handle deprecation request (separate action, not a form save)
        if "_deprecate" in request.POST:
            if submission.status != SubmissionStatus.DEPRECATED:
                submission.status = SubmissionStatus.DEPRECATED
                submission.save(update_fields=["status"])
                send_submission_notification.delay(
                    str(submission.id), event="status_changed"
                )
                logger.info(
                    "Submission deprecated by owner",
                    extra={"submission_id": str(submission.id)},
                )
            messages.success(request, "Your service has been marked as deprecated.")
            return redirect("submissions:update")

        if not _verify_altcha(request):
            form = SubmissionForm(request.POST, request.FILES, instance=submission)
            form.add_error(None, "CAPTCHA verification failed. Please try again.")
            return render(
                request,
                self.template_name,
                self._context(form, submission),
                status=400,
            )

        # Snapshot BEFORE the form is validated — Django's _post_clean() applies
        # POST data to the instance during is_valid(), so we must capture the
        # original values before any form processing touches the object.
        before_scalar = snapshot(submission)
        before_m2m = snapshot_m2m(submission)

        form = SubmissionForm(request.POST, request.FILES, instance=submission)

        if not form.is_valid():
            return render(
                request,
                self.template_name,
                self._context(form, submission),
                status=422,
            )

        updated = form.save(commit=False)

        # Reset status to submitted if previously approved (configurable)
        if updated.status == "approved":
            updated.status = "submitted"

        updated.save()
        form.save_m2m()

        # Snapshot AFTER saving and compute the diff.
        after_scalar = snapshot(updated)
        # Re-fetch M2M from DB — form.save_m2m() has committed the new values.
        after_m2m = snapshot_m2m(updated)

        changes = build_diff(
            {**before_scalar, **before_m2m}, {**after_scalar, **after_m2m}
        )

        # Persist the diff on the submission so it's always visible in the admin.
        if changes:
            changed_at = now()
            updated.last_change_summary = {
                "changed_by": "submitter",
                "changed_at": changed_at.isoformat(),
                "changes": changes,
            }
            updated.save(update_fields=["last_change_summary"])
            SubmissionChangeLog.objects.create(
                submission=updated,
                changed_by="submitter",
                changed_at=changed_at,
                changes=changes,
            )

        logger.info(
            "Submission updated (%d field(s) changed)",
            len(changes),
            extra={"submission_id": str(submission.id)},
        )

        # Send async notification (passes diff so both admin and submitter emails show it).
        send_update_notification.delay(str(submission.id), changes=changes)

        # Clear edit session keys
        request.session.pop("edit_key_id", None)
        request.session.pop("edit_submission_id", None)

        messages.success(
            request, "Your service registration has been updated successfully."
        )
        return redirect("submissions:update_success")


# ---------------------------------------------------------------------------
# HTMX inline field validation
# ---------------------------------------------------------------------------


@ratelimit(key="ip", rate=settings.RATE_LIMIT_VALIDATE, method="POST", block=True)
def validate_field(request: HttpRequest) -> HttpResponse:
    """
    POST /register/validate/

    HTMX endpoint: receives a single field name + value and returns an
    HTML fragment with the field widget + any validation errors.
    Used for inline validation on-blur without a full page reload.
    """
    if request.method != "POST":
        return HttpResponse(status=405)

    field_name = request.POST.get("field")
    if not field_name:
        return HttpResponse(status=400)

    # Create form with only this field's data to trigger its validation.
    # request.FILES is included for correctness, but note that HTMX inline
    # validation cannot carry file data (browsers don't serialise file inputs
    # in XHR requests), so FileField validation via this endpoint is a no-op.
    form = SubmissionForm(request.POST, request.FILES)
    form.is_valid()  # Populates form.errors

    field = form.fields.get(field_name)
    if not field:
        return HttpResponse(status=400)

    bound_field = form[field_name]
    return render(
        request,
        "submissions/partials/field_validation.html",
        {
            "field": bound_field,
        },
    )


# ---------------------------------------------------------------------------
# Simple informational views
# ---------------------------------------------------------------------------


def update_success(request: HttpRequest) -> HttpResponse:
    return render(request, "submissions/update_success.html")


def home(request: HttpRequest) -> HttpResponse:
    return render(request, "submissions/home.html")
