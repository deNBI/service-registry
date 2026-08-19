"""
Security Tests
==============
Tests verifying security controls: authentication, logging scrubbing,
middleware behaviour, and serialiser field isolation.

Note: Full header tests (CSP, HSTS, X-Frame-Options) require Nginx in the
loop and are covered by integration tests. These unit tests cover the
Django/DRF layer.
"""

from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from tests.factories import APIKeyFactory, ServiceSubmissionFactory


# ===========================================================================
# Authentication — API key scheme
# ===========================================================================


@pytest.mark.django_db
class TestAPIKeyAuthentication:
    def test_missing_authorization_header_returns_401_or_403(self):
        from rest_framework.test import APIClient

        client = APIClient()
        sub = ServiceSubmissionFactory()
        resp = client.get(f"/api/v1/submissions/{sub.pk}/")
        assert resp.status_code in (401, 403)

    def test_wrong_scheme_prefix_returns_401_or_403(self):
        from rest_framework.test import APIClient

        client = APIClient()
        sub = ServiceSubmissionFactory()
        key_obj, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {plaintext}")
        resp = client.get(f"/api/v1/submissions/{sub.pk}/")
        assert resp.status_code in (401, 403)

    def test_empty_key_value_returns_401(self):
        # An empty ApiKey header triggers AuthenticationFailed → 401 (not authenticated)
        from rest_framework.test import APIClient

        client = APIClient()
        sub = ServiceSubmissionFactory()
        client.credentials(HTTP_AUTHORIZATION="ApiKey ")
        resp = client.get(f"/api/v1/submissions/{sub.pk}/")
        assert resp.status_code == 401

    def test_auth_failure_response_body_is_generic(self):
        """Auth failure responses must not reveal whether a key exists or is revoked."""
        from rest_framework.test import APIClient

        client = APIClient()
        sub = ServiceSubmissionFactory()
        key_obj, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        key_obj.revoke()

        # Revoked key
        client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp_revoked = client.get(f"/api/v1/submissions/{sub.pk}/")

        # Totally wrong key
        client.credentials(HTTP_AUTHORIZATION="ApiKey completely-wrong-key-value-1234")
        resp_invalid = client.get(f"/api/v1/submissions/{sub.pk}/")

        # Both must return the same status (401 — AuthenticationFailed).
        # Identical responses prevent inferring whether a key exists or is revoked.
        assert resp_revoked.status_code == resp_invalid.status_code
        assert resp_revoked.status_code in (401, 403)


# ===========================================================================
# Logging scrubber
# ===========================================================================


class TestLoggingScrubber:
    def test_authorization_header_redacted(self):
        from apps.submissions.logging_filters import ScrubSensitiveFilter
        import logging

        f = ScrubSensitiveFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Authorization: ApiKey supersecretkey123",
            args=(),
            exc_info=None,
        )
        f.filter(record)
        assert "supersecretkey123" not in record.msg
        assert "[REDACTED]" in record.msg

    def test_cookie_header_redacted(self):
        from apps.submissions.logging_filters import ScrubSensitiveFilter
        import logging

        f = ScrubSensitiveFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Cookie: sessionid=abc123def456",
            args=(),
            exc_info=None,
        )
        f.filter(record)
        assert "abc123def456" not in record.msg
        assert "[REDACTED]" in record.msg

    def test_non_sensitive_log_unchanged(self):
        from apps.submissions.logging_filters import ScrubSensitiveFilter
        import logging

        f = ScrubSensitiveFilter()
        msg = "User submitted service 'Galaxy Europe'"
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=msg,
            args=(),
            exc_info=None,
        )
        f.filter(record)
        assert record.msg == msg


# ===========================================================================
# Request ID middleware
# ===========================================================================


@pytest.mark.django_db
class TestRequestIDMiddleware:
    def test_response_has_x_request_id_header(self):
        client = Client(enforce_csrf_checks=False)
        resp = client.get("/health/live/")
        assert "X-Request-ID" in resp
        # UUID format: 8-4-4-4-12 hex chars
        request_id = resp["X-Request-ID"]
        parts = request_id.split("-")
        assert len(parts) == 5


# ===========================================================================
# CSRF protection
# ===========================================================================


@pytest.mark.django_db
class TestCSRFProtection:
    def test_post_without_csrf_token_fails_in_strict_mode(self):
        """With enforce_csrf_checks=True, POST without token returns 403."""
        client = Client(enforce_csrf_checks=True)
        resp = client.post("/register/", {})
        assert resp.status_code == 403

    def test_csrf_cookie_set_on_get(self):
        """Django must set the csrftoken cookie on GET requests."""
        client = Client()
        resp = client.get("/register/")
        assert "csrftoken" in resp.cookies

    # -- csrftoken cookie guaranteed on every form GET (defense in depth) ----

    def test_csrf_cookie_set_on_update_get(self):
        resp = Client().get(reverse("submissions:update"))
        assert "csrftoken" in resp.cookies

    def test_csrf_cookie_set_on_edit_get(self):
        client, sub = self._edit_client()
        resp = client.get(reverse("submissions:edit", args=[sub.pk]))
        assert resp.status_code == 200
        assert "csrftoken" in resp.cookies

    # -- client-side token refresh script is delivered on all form pages -----
    #
    # The four native-POST forms (register, update, edit, deprecate) embed a
    # render-time CSRF token that can drift out of sync with the browser's
    # csrftoken cookie (concurrent first-visit / multi-tab race, amplified by
    # SameSite=Strict on external-link entry). The shared refresh script
    # rewrites the hidden csrfmiddlewaretoken from the live cookie at submit
    # time, mirroring the admin AJAX path and base.html's HTMX header. These
    # tests assert the script is actually delivered to each form page.

    SCRIPT = b"csrf-token-refresh.js"

    def test_register_page_includes_csrf_refresh_script(self):
        resp = Client().get(reverse("submissions:register"))
        assert self.SCRIPT in resp.content

    def test_update_page_includes_csrf_refresh_script(self):
        resp = Client().get(reverse("submissions:update"))
        assert self.SCRIPT in resp.content

    def test_edit_page_includes_csrf_refresh_script(self):
        client, sub = self._edit_client()
        resp = client.get(reverse("submissions:edit", args=[sub.pk]))
        assert resp.status_code == 200
        assert self.SCRIPT in resp.content

    # -- CSRF protection itself must remain intact (the fix must not weaken) --

    def test_token_equal_to_cookie_secret_is_accepted(self):
        """
        The refresh script writes the raw csrftoken cookie value into the
        hidden field. Django must accept that value as a valid token, i.e. the
        request must NOT be rejected with 403 (it fails later validation with
        400/422 instead). This locks in the mechanism the fix relies on.
        """
        client = Client(enforce_csrf_checks=True)
        client.get(reverse("submissions:register"))
        cookie_secret = client.cookies["csrftoken"].value

        resp = client.post(
            reverse("submissions:register"),
            {"csrfmiddlewaretoken": cookie_secret},
        )
        assert resp.status_code != 403

    def test_forged_token_is_rejected_on_register(self):
        client = Client(enforce_csrf_checks=True)
        client.get(reverse("submissions:register"))
        resp = client.post(
            reverse("submissions:register"),
            {"csrfmiddlewaretoken": "forged" + "a" * 58},
        )
        assert resp.status_code == 403

    def test_forged_token_is_rejected_on_update(self):
        client = Client(enforce_csrf_checks=True)
        client.get(reverse("submissions:update"))
        resp = client.post(
            reverse("submissions:update"),
            {"csrfmiddlewaretoken": "forged" + "a" * 58},
        )
        assert resp.status_code == 403

    def test_forged_token_is_rejected_on_edit(self):
        """CSRF is enforced at the middleware layer, before the view's session
        check — a forged token is rejected even with a valid edit session."""
        client, sub = self._edit_client(enforce_csrf_checks=True)
        resp = client.post(
            reverse("submissions:edit", args=[sub.pk]),
            {"csrfmiddlewaretoken": "forged" + "a" * 58},
        )
        assert resp.status_code == 403

    def test_base_hx_headers_uses_anchored_cookie_regex(self):
        """
        The inline body hx-headers setup in base.html reads the csrftoken
        cookie for HTMX requests. It must use an anchored regex so a decoy
        cookie whose name *ends with* ``csrftoken`` (e.g. ``xcsrftoken``)
        cannot shadow the real token and feed a wrong X-CSRFToken header.
        """
        content = Client().get(reverse("submissions:register")).content
        # Anchored form present ...
        assert b"(?:^|;\\s*)csrftoken=" in content
        # ... and the naive unanchored opener gone.
        assert b"match(/csrftoken=" not in content

    # -- htmx requests must carry the live cookie token, not a stale one ------
    #
    # htmx-csrf-refresh.js refreshes the CSRF token on every htmx request from
    # the live cookie, so inline validation (/register/validate/) can't 403 on a
    # stale render-time token. The old form-level hx-headers token is removed.

    HTMX_SCRIPT = b"htmx-csrf-refresh.js"

    # The render-time form attribute: hx-headers='{"X-CSRFToken": "<token>"}'.
    STATIC_HX_TOKEN = b'hx-headers=\'{"X-CSRFToken"'

    def test_register_page_includes_htmx_csrf_refresh_script(self):
        resp = Client().get(reverse("submissions:register"))
        assert self.HTMX_SCRIPT in resp.content

    def test_update_page_includes_htmx_csrf_refresh_script(self):
        resp = Client().get(reverse("submissions:update"))
        assert self.HTMX_SCRIPT in resp.content

    def test_edit_page_includes_htmx_csrf_refresh_script(self):
        client, sub = self._edit_client()
        resp = client.get(reverse("submissions:edit", args=[sub.pk]))
        assert resp.status_code == 200
        assert self.HTMX_SCRIPT in resp.content

    def test_register_form_omits_render_time_csrf_hx_headers(self):
        """No render-time CSRF token baked into hx-headers — it goes stale on
        cookie rotation. The live-cookie listener handles it instead."""
        resp = Client().get(reverse("submissions:register"))
        assert self.STATIC_HX_TOKEN not in resp.content

    def test_edit_form_omits_render_time_csrf_hx_headers(self):
        client, sub = self._edit_client()
        resp = client.get(reverse("submissions:edit", args=[sub.pk]))
        assert resp.status_code == 200
        assert self.STATIC_HX_TOKEN not in resp.content

    def test_htmx_csrf_refresh_script_uses_configrequest_and_anchored_regex(self):
        """Hooks htmx:configRequest, sets X-CSRFToken, and reads the cookie with
        an anchored regex so a decoy cookie (e.g. ``xcsrftoken``) can't shadow
        the real one."""
        js = (
            Path(__file__).resolve().parent.parent
            / "static"
            / "js"
            / "htmx-csrf-refresh.js"
        )
        assert js.exists(), f"missing {js}"
        src = js.read_text()
        assert "htmx:configRequest" in src
        assert "X-CSRFToken" in src
        assert "(?:^|;\\s*)csrftoken=" in src
        assert "match(/csrftoken=" not in src

    def test_htmx_csrf_refresh_script_restamps_csrfmiddlewaretoken_param(self):
        """Django checks the POSTed csrfmiddlewaretoken before the header, and
        htmx sends the form's field, so the listener must refresh the param too.
        A header-only fix is ignored by Django and would not stop the 403."""
        js = (
            Path(__file__).resolve().parent.parent
            / "static"
            / "js"
            / "htmx-csrf-refresh.js"
        )
        src = js.read_text()
        assert "csrfmiddlewaretoken" in src
        # via event.detail.parameters, the object configRequest exposes.
        assert "parameters" in src

    @staticmethod
    def _edit_client(enforce_csrf_checks=False):
        """A Client with a valid EditView session grant, plus its submission."""
        sub = ServiceSubmissionFactory(biotools_url="")
        key_obj, _ = APIKeyFactory.create_with_plaintext(submission=sub)
        client = Client(enforce_csrf_checks=enforce_csrf_checks)
        session = client.session
        session["edit_grants"] = {str(sub.pk): str(key_obj.pk)}
        session.save()
        return client, sub


# ===========================================================================
# Session unlock scoping (kiosk hardening)
# ===========================================================================


@pytest.mark.django_db
class TestSessionExpiry:
    """Entering an API key unlocks the web edit form for the browsing session
    (the grant is session-lived, re-verified every request). The exposure window
    on a shared/kiosk machine is bounded by the session, so these properties
    must hold: the session cookie dies on browser close, and SESSION_COOKIE_AGE
    is the server-side cap. Removing SESSION_EXPIRE_AT_BROWSER_CLOSE would
    silently widen that window — these tests guard against that regression."""

    def test_session_expire_at_browser_close_is_enabled(self, settings):
        assert settings.SESSION_EXPIRE_AT_BROWSER_CLOSE is True
        # The session must still have a finite server-side cap.
        assert settings.SESSION_COOKIE_AGE and settings.SESSION_COOKIE_AGE > 0

    def test_unlock_session_cookie_is_browser_scoped(self, client):
        """After a successful key entry (which records the edit grant), the
        sessionid cookie must be a browser-session cookie — no Max-Age/Expires —
        so the unlock cannot outlive the browser session on a shared machine."""
        sub = ServiceSubmissionFactory(biotools_url="")
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)

        resp = client.post(reverse("submissions:update"), {"api_key": plaintext})
        assert resp.status_code == 302

        cookie = resp.cookies.get("sessionid")
        assert cookie is not None, "key entry must establish a session"
        assert cookie["max-age"] in ("", None)
        assert cookie["expires"] in ("", None)


# ===========================================================================
# Sensitive-page caching (no-store)
# ===========================================================================


@pytest.mark.django_db
class TestSensitivePageCaching:
    """The success page renders the one-time API key, and the edit page is an
    authenticated form; neither may be cached (disk cache / bfcache could
    re-expose them on a shared machine after the session ends)."""

    def test_success_page_is_not_cacheable(self, client):
        import uuid

        sub_id = str(uuid.uuid4())
        session = client.session
        session["pending_keys"] = {sub_id: "one-time-key"}
        session.save()

        resp = client.get(reverse("submissions:success", args=[sub_id]))
        assert resp.status_code == 200
        assert "no-store" in resp.headers.get("Cache-Control", "")

    def test_edit_page_is_not_cacheable(self):
        sub = ServiceSubmissionFactory(biotools_url="")
        key_obj, _ = APIKeyFactory.create_with_plaintext(submission=sub)
        client = Client()
        session = client.session
        session["edit_grants"] = {str(sub.pk): str(key_obj.pk)}
        session.save()

        resp = client.get(reverse("submissions:edit", args=[sub.pk]))
        assert resp.status_code == 200
        assert "no-store" in resp.headers.get("Cache-Control", "")


# ===========================================================================
# URL scheme validation
# ===========================================================================


class TestURLSchemeValidation:
    """Pure unit tests — no DB needed."""

    @pytest.mark.parametrize(
        "url,should_raise",
        [
            ("https://example.com", False),
            ("http://example.com", True),
            ("ftp://example.com", True),
            ("javascript:alert(1)", True),
            ("data:text/html,<h1>XSS</h1>", True),
            ("//example.com", True),
            ("", False),  # empty is allowed (optional fields)
        ],
    )
    def test_https_url_validator(self, url, should_raise):
        from apps.submissions.models import _validate_https_url
        from django.core.exceptions import ValidationError

        if should_raise:
            with pytest.raises(ValidationError):
                _validate_https_url(url)
        else:
            _validate_https_url(url)  # must not raise


# ===========================================================================
# Admin auth token masking
# ===========================================================================

User = get_user_model()


@pytest.mark.django_db
# ===========================================================================
# ALTCHA challenge endpoint security
# ===========================================================================


@pytest.mark.django_db
class TestAltchaChallengeSecurity:
    """Security properties of the GET /captcha/ challenge endpoint."""

    def test_endpoint_accessible_without_authentication(self):
        """GET /captcha/ must be publicly accessible — no auth required."""
        from django.test import Client, override_settings
        from django.urls import reverse

        client = Client()
        with override_settings(ALTCHA_HMAC_KEY="test-security-key"):
            resp = client.get(reverse("submissions:altcha_challenge"))
        assert resp.status_code == 200

    def test_response_content_type_is_json(self):
        """GET /captcha/ must return application/json."""
        from django.test import Client, override_settings
        from django.urls import reverse

        client = Client()
        with override_settings(ALTCHA_HMAC_KEY="test-security-key"):
            resp = client.get(reverse("submissions:altcha_challenge"))
        assert "application/json" in resp["Content-Type"]

    def test_challenge_payload_does_not_expose_hmac_key(self):
        """The JSON challenge must never include the raw HMAC key."""
        from django.test import Client, override_settings
        from django.urls import reverse

        hmac_key = "super-secret-hmac-key-must-not-leak"
        client = Client()
        with override_settings(ALTCHA_HMAC_KEY=hmac_key):
            resp = client.get(reverse("submissions:altcha_challenge"))
        assert hmac_key not in resp.content.decode()

    def test_challenge_fields_are_not_empty(self):
        """Every required challenge field must be a non-empty string."""
        from django.test import Client, override_settings
        from django.urls import reverse

        client = Client()
        with override_settings(ALTCHA_HMAC_KEY="test-security-key"):
            resp = client.get(reverse("submissions:altcha_challenge"))
        data = resp.json()
        for field in ("algorithm", "challenge", "salt", "signature"):
            assert data.get(field), f"Challenge field '{field}' must not be empty"
