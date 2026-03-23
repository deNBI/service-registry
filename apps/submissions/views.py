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

import logging

from django.conf import settings
from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django_ratelimit.decorators import ratelimit

from .forms import SubmissionForm, UpdateKeyForm
from .models import ServiceSubmission, SubmissionAPIKey, SubmissionStatus
from .tasks import send_submission_notification, send_update_notification

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_client_ip(request: HttpRequest) -> str:
    """
    Extract the real client IP when Django sits behind a reverse proxy.

    Priority order (matches AXES_IPWARE_META_PRECEDENCE_ORDER in settings):
      1. X-Real-IP     — set by nginx to $remote_addr; single value, not spoofable by clients
      2. X-Forwarded-For — leftmost entry is the original client; may have multiple hops
      3. REMOTE_ADDR   — the connecting IP (nginx server's IP in a two-server setup)
    """
    real_ip = request.META.get("HTTP_X_REAL_IP", "").strip()
    if real_ip:
        return real_ip
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "").strip()
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _hash_user_agent(request: HttpRequest) -> str:
    """Return SHA-256 of the User-Agent header for abuse pattern detection."""
    import hashlib

    ua = request.META.get("HTTP_USER_AGENT", "")
    return hashlib.sha256(ua.encode("utf-8")).hexdigest()


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

    def get(self, request: HttpRequest) -> HttpResponse:
        form = SubmissionForm()
        return render(request, self.template_name, {"form": form})

    def post(self, request: HttpRequest) -> HttpResponse:
        form = SubmissionForm(request.POST, request.FILES)

        if not form.is_valid():
            return render(request, self.template_name, {"form": form}, status=422)

        # Save submission
        submission: ServiceSubmission = form.save(commit=False)
        submission.status = "submitted"
        submission.submission_ip = _get_client_ip(request)
        submission.user_agent_hash = _hash_user_agent(request)
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
            return key.submission
        except SubmissionAPIKey.DoesNotExist:
            return None

    def get(self, request: HttpRequest) -> HttpResponse:
        submission = self._get_submission(request)
        if not submission:
            messages.error(
                request, "Your session has expired. Please enter your API key again."
            )
            return redirect("submissions:update")

        form = SubmissionForm(instance=submission)
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "submission": submission,
            },
        )

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

        form = SubmissionForm(request.POST, request.FILES, instance=submission)

        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "submission": submission,
                },
                status=422,
            )

        updated = form.save(commit=False)

        # Reset status to submitted if previously approved (configurable)
        if updated.status == "approved":
            updated.status = "submitted"

        updated.save()
        form.save_m2m()

        logger.info(
            "Submission updated",
            extra={"submission_id": str(submission.id)},
        )

        # Send async notification
        send_update_notification.delay(str(submission.id))

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
