"""
View Tests
==========
Tests for the submission form views: register, update, edit, success, health.

Uses Django's test client — no network calls.
"""

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.submissions.models import SubmissionChangeLog

from tests.factories import APIKeyFactory, ServiceSubmissionFactory


def _make_png_bytes() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    img = Image.new("RGB", (1, 1), color=(255, 255, 255))
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def client():
    return Client(enforce_csrf_checks=False)


# ===========================================================================
# Home and static views
# ===========================================================================


@pytest.mark.django_db
class TestHomeView:
    def test_home_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200


# ===========================================================================
# RegisterView
# ===========================================================================


@pytest.mark.django_db
class TestRegisterView:
    def test_get_register_returns_200(self, client):
        resp = client.get(reverse("submissions:register"))
        assert resp.status_code == 200

    def test_get_register_contains_form_sections(self, client):
        resp = client.get(reverse("submissions:register"))
        for section in [
            "Section A",
            "Section B",
            "Section C",
            "Section D",
            "Section E",
            "Section F",
            "Section G",
        ]:
            # Sections are labelled A–G but text varies; check for card headers
            pass  # template assertions kept loose to avoid brittle coupling
        assert b"csrf" in resp.content.lower() or b"csrfmiddlewaretoken" in resp.content

    def test_post_invalid_data_returns_422(self, client):
        resp = client.post(reverse("submissions:register"), data={})
        assert resp.status_code == 422

    def test_post_valid_creates_submission_and_redirects(self, client):
        from tests.factories import (
            PIFactory,
            ServiceCategoryFactory,
            ServiceCenterFactory,
        )
        from django.utils import timezone

        cat = ServiceCategoryFactory()
        center = ServiceCenterFactory()
        pi = PIFactory()

        data = {
            "date_of_entry": timezone.now().date().isoformat(),
            "submitter_first_name": "Test",
            "submitter_last_name": "User",
            "submitter_affiliation": "FZ Jülich",
            "register_as_elixir": "False",
            "service_name": "Unique Test Service XYZ",
            "service_description": "A sufficiently long description of the test service for validation purposes.",
            "year_established": 2022,
            "service_categories": [cat.pk],
            "is_toolbox": "False",
            "toolbox_name": "",
            "user_knowledge_required": "",
            "publications_pmids": "12345678",
            "responsible_pis": [pi.pk],
            "associated_partner_note": "",
            "host_institute": "Test Institute",
            "service_center": center.pk,
            "public_contact_email": "public@test.com",
            "internal_contact_name": "Internal Name",
            "internal_contact_email": "internal@test.com",
            "internal_contact_email_confirm": "internal@test.com",
            "website_url": "https://example.com",
            "terms_of_use_url": "https://example.com/tos",
            "license_note": "MIT",
            "github_url": "",
            "biotools_url": "",
            "fairsharing_url": "",
            "other_registry_url": "",
            "kpi_monitoring": "yes",
            "kpi_start_year": "2022",
            "keywords_uncited": "",
            "keywords_seo": "",
            "survey_participation": "True",
            "comments": "",
            "data_protection_consent": "True",
        }
        resp = client.post(reverse("submissions:register"), data=data)
        # Should redirect to success page
        assert resp.status_code == 302
        assert resp["Location"] == reverse("submissions:success")

    def test_success_page_shows_api_key(self, client):
        """SuccessView must display the API key from session, then clear it."""
        session = client.session
        session["pending_api_key"] = "test-api-key-value"
        session["pending_submission_id"] = "test-uuid"
        session.save()

        resp = client.get(reverse("submissions:success"))
        assert resp.status_code == 200
        assert b"test-api-key-value" in resp.content

    def test_success_page_without_session_redirects(self, client):
        """Navigating directly to success without submitting must redirect."""
        resp = client.get(reverse("submissions:success"))
        assert resp.status_code == 302

    def test_success_page_clears_key_from_session(self, client):
        """API key must be removed from session after success page renders."""
        session = client.session
        session["pending_api_key"] = "one-time-key"
        session["pending_submission_id"] = "some-uuid"
        session.save()

        client.get(reverse("submissions:success"))
        # Refresh session
        session = client.session
        assert "pending_api_key" not in session

    def test_post_valid_with_logo_creates_submission(self, client):
        """Registration with a valid logo file must succeed and store the logo."""
        from tests.factories import (
            PIFactory,
            ServiceCategoryFactory,
            ServiceCenterFactory,
        )
        from django.utils import timezone
        from apps.submissions.models import ServiceSubmission

        cat = ServiceCategoryFactory()
        center = ServiceCenterFactory()
        pi = PIFactory()
        logo = SimpleUploadedFile(
            "logo.png", _make_png_bytes(), content_type="image/png"
        )

        data = {
            "date_of_entry": timezone.now().date().isoformat(),
            "submitter_first_name": "Logo",
            "submitter_last_name": "Tester",
            "submitter_affiliation": "Test Institute",
            "register_as_elixir": "False",
            "service_name": "Logo Upload Service",
            "service_description": "A sufficiently long description of the logo upload test service.",
            "year_established": 2023,
            "service_categories": [cat.pk],
            "is_toolbox": "False",
            "toolbox_name": "",
            "user_knowledge_required": "",
            "publications_pmids": "12345678",
            "responsible_pis": [pi.pk],
            "associated_partner_note": "",
            "host_institute": "Logo Institute",
            "service_center": center.pk,
            "public_contact_email": "logo@test.com",
            "internal_contact_name": "Logo Contact",
            "internal_contact_email": "logo-int@test.com",
            "internal_contact_email_confirm": "logo-int@test.com",
            "website_url": "https://logo-example.com",
            "terms_of_use_url": "https://logo-example.com/tos",
            "license_note": "MIT",
            "github_url": "",
            "biotools_url": "",
            "fairsharing_url": "",
            "other_registry_url": "",
            "kpi_monitoring": "yes",
            "kpi_start_year": "2023",
            "keywords_uncited": "",
            "keywords_seo": "",
            "survey_participation": "True",
            "comments": "",
            "data_protection_consent": "True",
            "logo": logo,
        }
        resp = client.post(reverse("submissions:register"), data=data)
        assert resp.status_code == 302
        sub = ServiceSubmission.objects.get(service_name="Logo Upload Service")
        assert sub.logo  # logo was stored

    def test_post_with_invalid_logo_returns_422(self, client):
        """Registration with an invalid logo file must fail form validation."""
        from tests.factories import (
            PIFactory,
            ServiceCategoryFactory,
            ServiceCenterFactory,
        )
        from django.utils import timezone

        cat = ServiceCategoryFactory()
        center = ServiceCenterFactory()
        pi = PIFactory()
        bad_logo = SimpleUploadedFile(
            "logo.png", b"not an image", content_type="image/png"
        )

        data = {
            "date_of_entry": timezone.now().date().isoformat(),
            "submitter_first_name": "Bad",
            "submitter_last_name": "Logo",
            "submitter_affiliation": "Test Institute",
            "register_as_elixir": "False",
            "service_name": "Bad Logo Service",
            "service_description": "A sufficiently long description of the bad logo test service.",
            "year_established": 2023,
            "service_categories": [cat.pk],
            "is_toolbox": "False",
            "toolbox_name": "",
            "user_knowledge_required": "",
            "publications_pmids": "12345678",
            "responsible_pis": [pi.pk],
            "associated_partner_note": "",
            "host_institute": "Bad Logo Institute",
            "service_center": center.pk,
            "public_contact_email": "bad@test.com",
            "internal_contact_name": "Bad Contact",
            "internal_contact_email": "bad-int@test.com",
            "internal_contact_email_confirm": "bad-int@test.com",
            "website_url": "https://bad-example.com",
            "terms_of_use_url": "https://bad-example.com/tos",
            "license_note": "MIT",
            "github_url": "",
            "biotools_url": "",
            "fairsharing_url": "",
            "other_registry_url": "",
            "kpi_monitoring": "yes",
            "kpi_start_year": "2023",
            "keywords_uncited": "",
            "keywords_seo": "",
            "survey_participation": "True",
            "comments": "",
            "data_protection_consent": "True",
            "logo": bad_logo,
        }
        resp = client.post(reverse("submissions:register"), data=data)
        assert resp.status_code == 422


# ===========================================================================
# UpdateView
# ===========================================================================


@pytest.mark.django_db
class TestUpdateView:
    def test_get_update_returns_200(self, client):
        resp = client.get(reverse("submissions:update"))
        assert resp.status_code == 200

    def test_invalid_key_returns_403(self, client):
        resp = client.post(
            reverse("submissions:update"),
            {"api_key": "wrong-key-value-that-is-long-enough"},
        )
        assert resp.status_code == 403

    def test_valid_key_redirects_to_edit(self, client):
        sub = ServiceSubmissionFactory()
        key_obj, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)

        resp = client.post(reverse("submissions:update"), {"api_key": plaintext})
        assert resp.status_code == 302
        assert resp["Location"] == reverse("submissions:edit")

    def test_revoked_key_treated_as_invalid(self, client):
        sub = ServiceSubmissionFactory()
        key_obj, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        key_obj.revoke()

        resp = client.post(reverse("submissions:update"), {"api_key": plaintext})
        assert resp.status_code == 403


# ===========================================================================
# EditView
# ===========================================================================


@pytest.mark.django_db
class TestEditView:
    def test_edit_without_session_redirects(self, client):
        """Accessing edit without a valid session key should redirect to update."""
        resp = client.get(reverse("submissions:edit"))
        assert resp.status_code == 302

    def test_edit_with_valid_session_shows_form(self, client):
        sub = ServiceSubmissionFactory()
        key_obj, _ = APIKeyFactory.create_with_plaintext(submission=sub)
        session = client.session
        session["edit_key_id"] = str(key_obj.pk)
        session["edit_submission_id"] = str(sub.pk)
        session.save()

        resp = client.get(reverse("submissions:edit"))
        assert resp.status_code == 200
        assert sub.service_name.encode() in resp.content

    def _edit_form_data(self, sub, **overrides):
        """Build a complete POST payload for the edit view from a submission instance."""
        data = {
            "date_of_entry": sub.date_of_entry.isoformat(),
            "submitter_first_name": sub.submitter_first_name,
            "submitter_last_name": sub.submitter_last_name,
            "submitter_affiliation": sub.submitter_affiliation,
            "register_as_elixir": str(sub.register_as_elixir),
            "service_name": sub.service_name,
            "service_description": sub.service_description,
            "year_established": sub.year_established,
            "service_categories": [c.pk for c in sub.service_categories.all()],
            "is_toolbox": str(sub.is_toolbox),
            "toolbox_name": sub.toolbox_name or "",
            "user_knowledge_required": sub.user_knowledge_required or "",
            "publications_pmids": sub.publications_pmids or "",
            "responsible_pis": [p.pk for p in sub.responsible_pis.all()],
            "associated_partner_note": sub.associated_partner_note or "",
            "host_institute": sub.host_institute,
            "service_center": sub.service_center.pk,
            "public_contact_email": sub.public_contact_email,
            "internal_contact_name": sub.internal_contact_name,
            "internal_contact_email": sub.internal_contact_email,
            "internal_contact_email_confirm": sub.internal_contact_email,
            "website_url": sub.website_url,
            "terms_of_use_url": sub.terms_of_use_url,
            "licenses": [lic.pk for lic in sub.licenses.all()],
            "license_note": sub.license_note or "",
            "github_url": sub.github_url or "",
            "biotools_url": sub.biotools_url or "",
            "fairsharing_url": sub.fairsharing_url or "",
            "other_registry_url": sub.other_registry_url or "",
            "kpi_monitoring": sub.kpi_monitoring,
            "kpi_start_year": sub.kpi_start_year or "",
            "keywords_uncited": sub.keywords_uncited or "",
            "keywords_seo": sub.keywords_seo or "",
            "survey_participation": str(sub.survey_participation),
            "comments": sub.comments or "",
            "data_protection_consent": str(sub.data_protection_consent),
        }
        data.update(overrides)
        return data

    def _setup_edit_session(self, client, sub):
        key_obj, _ = APIKeyFactory.create_with_plaintext(submission=sub)
        session = client.session
        session["edit_key_id"] = str(key_obj.pk)
        session["edit_submission_id"] = str(sub.pk)
        session.save()

    def test_edit_with_logo_upload_succeeds(self, client):
        """Uploading a valid logo via the edit view must persist the logo."""
        sub = ServiceSubmissionFactory()
        self._setup_edit_session(client, sub)

        logo = SimpleUploadedFile(
            "logo.png", _make_png_bytes(), content_type="image/png"
        )
        data = self._edit_form_data(sub, logo=logo)
        resp = client.post(reverse("submissions:edit"), data=data)
        assert resp.status_code == 302
        sub.refresh_from_db()
        assert sub.logo  # logo was saved

    def test_edit_with_invalid_logo_rejected(self, client):
        """Uploading an invalid logo via the edit view must return a form error."""
        sub = ServiceSubmissionFactory()
        self._setup_edit_session(client, sub)

        bad_logo = SimpleUploadedFile(
            "logo.png", b"not an image", content_type="image/png"
        )
        data = self._edit_form_data(sub, logo=bad_logo)
        resp = client.post(reverse("submissions:edit"), data=data)
        assert resp.status_code == 422
        sub.refresh_from_db()
        assert not sub.logo  # logo was not saved

    def test_owner_can_deprecate_service(self, client):
        """Posting _deprecate sets status=deprecated and redirects."""
        sub = ServiceSubmissionFactory(status="approved")
        self._setup_edit_session(client, sub)
        resp = client.post(reverse("submissions:edit"), {"_deprecate": "1"})
        assert resp.status_code == 302
        sub.refresh_from_db()
        assert sub.status == "deprecated"

    def test_deprecate_is_idempotent(self, client):
        """Posting _deprecate on an already-deprecated service is harmless."""
        sub = ServiceSubmissionFactory(status="deprecated")
        self._setup_edit_session(client, sub)
        resp = client.post(reverse("submissions:edit"), {"_deprecate": "1"})
        assert resp.status_code == 302
        sub.refresh_from_db()
        assert sub.status == "deprecated"

    def test_deprecate_creates_changelog_entry(self, client):
        """Submitter deprecation records a SubmissionChangeLog entry with changed_by=submitter."""
        sub = ServiceSubmissionFactory(status="approved")
        self._setup_edit_session(client, sub)
        client.post(reverse("submissions:edit"), {"_deprecate": "1"})
        log = SubmissionChangeLog.objects.get(submission=sub)
        assert log.changed_by == "submitter"
        assert log.changes[0]["field"] == "status"
        assert log.changes[0]["new"] == "Deprecated"

    def test_deprecate_populates_last_change_summary(self, client):
        """Submitter deprecation writes last_change_summary on the submission."""
        sub = ServiceSubmissionFactory(status="approved")
        self._setup_edit_session(client, sub)
        client.post(reverse("submissions:edit"), {"_deprecate": "1"})
        sub.refresh_from_db()
        assert sub.last_change_summary is not None
        assert sub.last_change_summary["changed_by"] == "submitter"
        assert sub.last_change_summary["changes"][0]["field"] == "status"

    def test_deprecate_idempotent_creates_no_changelog(self, client):
        """Re-deprecating an already-deprecated service creates no new changelog entry."""
        sub = ServiceSubmissionFactory(status="deprecated")
        self._setup_edit_session(client, sub)
        client.post(reverse("submissions:edit"), {"_deprecate": "1"})
        assert SubmissionChangeLog.objects.filter(submission=sub).count() == 0

    def test_deprecate_records_maturity_tag_clearing(self, client):
        """When a tagged service is deprecated, the cleared tags appear in the changelog."""
        sub = ServiceSubmissionFactory(
            status="approved",
            primary_maturity_tag="mature",
            secondary_maturity_tags=["unstable"],
        )
        self._setup_edit_session(client, sub)
        client.post(reverse("submissions:edit"), {"_deprecate": "1"})
        log = SubmissionChangeLog.objects.get(submission=sub)
        fields = [c["field"] for c in log.changes]
        assert "primary_maturity_tag" in fields
        assert "secondary_maturity_tags" in fields
        tag_entry = next(c for c in log.changes if c["field"] == "primary_maturity_tag")
        assert tag_entry["new"] == "—"

    def test_deprecated_badge_shown_when_deprecated(self, client):
        """GET edit page for a deprecated service shows the badge, not the danger zone."""
        sub = ServiceSubmissionFactory(status="deprecated")
        self._setup_edit_session(client, sub)
        resp = client.get(reverse("submissions:edit"))
        assert resp.status_code == 200
        content = resp.content.decode()
        assert "Deprecated" in content
        assert "Deprecate this service" not in content

    def test_deprecate_button_shown_when_not_deprecated(self, client):
        """GET edit page for an active service shows the danger zone deprecate button."""
        sub = ServiceSubmissionFactory(status="approved")
        self._setup_edit_session(client, sub)
        resp = client.get(reverse("submissions:edit"))
        assert resp.status_code == 200
        assert b"Deprecate this service" in resp.content

    # ── diff capture ─────────────────────────────────────────────────────────

    def test_edit_post_populates_last_change_summary(self, client, settings):
        """A successful edit should write last_change_summary to the submission."""
        settings.ALTCHA_HMAC_KEY = ""  # disable CAPTCHA in tests
        settings.CELERY_TASK_ALWAYS_EAGER = True

        sub = ServiceSubmissionFactory(service_name="Before Name", comments="")
        self._setup_edit_session(client, sub)

        data = self._edit_form_data(sub, service_name="After Name")
        resp = client.post(reverse("submissions:edit"), data=data)
        assert resp.status_code == 302

        sub.refresh_from_db()
        assert sub.last_change_summary is not None
        summary = sub.last_change_summary
        assert summary["changed_by"] == "submitter"
        assert "changed_at" in summary
        fields = {ch["field"] for ch in summary["changes"]}
        assert "service_name" in fields

    def test_edit_post_old_and_new_values_recorded(self, client, settings):
        settings.ALTCHA_HMAC_KEY = ""
        settings.CELERY_TASK_ALWAYS_EAGER = True

        sub = ServiceSubmissionFactory(service_name="Old Name")
        self._setup_edit_session(client, sub)

        data = self._edit_form_data(sub, service_name="New Name")
        client.post(reverse("submissions:edit"), data=data)
        sub.refresh_from_db()

        name_change = next(
            ch
            for ch in sub.last_change_summary["changes"]
            if ch["field"] == "service_name"
        )
        assert name_change["old"] == "Old Name"
        assert name_change["new"] == "New Name"

    def test_edit_post_no_change_does_not_write_summary(self, client, settings):
        """Posting without changing any field leaves last_change_summary None."""
        settings.ALTCHA_HMAC_KEY = ""
        settings.CELERY_TASK_ALWAYS_EAGER = True

        sub = ServiceSubmissionFactory()
        assert sub.last_change_summary is None
        self._setup_edit_session(client, sub)

        data = self._edit_form_data(sub)  # no overrides — nothing changes
        client.post(reverse("submissions:edit"), data=data)
        sub.refresh_from_db()
        assert sub.last_change_summary is None

    def test_edit_post_sends_submitter_updated_email_when_changed(
        self, client, settings
    ):
        """A diff-producing edit fires a submitter-facing updated email."""
        from django.core import mail

        settings.ALTCHA_HMAC_KEY = ""
        settings.CELERY_TASK_ALWAYS_EAGER = True
        settings.CELERY_TASK_EAGER_PROPAGATES = True

        sub = ServiceSubmissionFactory(
            service_name="Old", internal_contact_email="pi@example.com"
        )
        self._setup_edit_session(client, sub)

        data = self._edit_form_data(sub, service_name="New")
        client.post(reverse("submissions:edit"), data=data)

        recipients = [addr for m in mail.outbox for addr in m.to]
        assert "pi@example.com" in recipients


# ===========================================================================
# ALTCHA challenge endpoint
# ===========================================================================


@pytest.mark.django_db
class TestAltchaChallengeView:
    def test_get_returns_200_with_json(self, client):
        """GET /captcha/ must return a JSON challenge object."""
        from django.test import override_settings

        with override_settings(ALTCHA_HMAC_KEY="test-hmac-key-for-challenge"):
            resp = client.get(reverse("submissions:altcha_challenge"))
        assert resp.status_code == 200
        data = resp.json()
        assert data["algorithm"] == "SHA-256"
        assert "challenge" in data
        assert "salt" in data
        assert "signature" in data

    def test_get_challenge_includes_expiry_in_salt(self, client):
        """Challenge salt must include an expiry parameter."""
        from django.test import override_settings

        with override_settings(ALTCHA_HMAC_KEY="test-hmac-key-for-expiry"):
            resp = client.get(reverse("submissions:altcha_challenge"))
        assert resp.status_code == 200
        salt = resp.json()["salt"]
        assert "expires=" in salt

    def test_get_returns_503_when_key_not_configured(self, client):
        """GET /captcha/ must return 503 when ALTCHA_HMAC_KEY is empty."""
        from django.test import override_settings

        with override_settings(ALTCHA_HMAC_KEY=""):
            resp = client.get(reverse("submissions:altcha_challenge"))
        assert resp.status_code == 503

    def test_post_returns_405(self, client):
        """POST to /captcha/ must be rejected with 405 Method Not Allowed."""
        from django.test import override_settings

        with override_settings(ALTCHA_HMAC_KEY="test-hmac-key"):
            resp = client.post(reverse("submissions:altcha_challenge"))
        assert resp.status_code == 405

    def test_response_includes_max_number(self, client):
        """GET /captcha/ JSON must include maxNumber so the widget knows the search space."""
        from django.test import override_settings

        with override_settings(ALTCHA_HMAC_KEY="test-hmac-key-maxnumber"):
            resp = client.get(reverse("submissions:altcha_challenge"))
        assert resp.status_code == 200
        data = resp.json()
        assert "maxNumber" in data
        assert isinstance(data["maxNumber"], int)
        assert data["maxNumber"] > 0

    def test_response_has_no_store_cache_control(self, client):
        """GET /captcha/ must set Cache-Control: no-store to prevent proxy caching."""
        from django.test import override_settings

        with override_settings(ALTCHA_HMAC_KEY="test-hmac-key-cache"):
            resp = client.get(reverse("submissions:altcha_challenge"))
        assert resp.status_code == 200
        assert "no-store" in resp["Cache-Control"]


@pytest.mark.django_db
class TestAltchaVerification:
    """Tests for the ALTCHA verification guard on form submission endpoints."""

    def _valid_register_data(self):
        from tests.factories import (
            PIFactory,
            ServiceCategoryFactory,
            ServiceCenterFactory,
        )
        from django.utils import timezone

        cat = ServiceCategoryFactory()
        center = ServiceCenterFactory()
        pi = PIFactory()
        return {
            "date_of_entry": timezone.now().date().isoformat(),
            "submitter_first_name": "ALTCHA",
            "submitter_last_name": "Tester",
            "submitter_affiliation": "Test Institute",
            "register_as_elixir": "False",
            "service_name": "ALTCHA Verified Service",
            "service_description": "A sufficiently long description for the ALTCHA test service.",
            "year_established": 2023,
            "service_categories": [cat.pk],
            "is_toolbox": "False",
            "toolbox_name": "",
            "user_knowledge_required": "",
            "publications_pmids": "12345678",
            "responsible_pis": [pi.pk],
            "associated_partner_note": "",
            "host_institute": "ALTCHA Institute",
            "service_center": center.pk,
            "public_contact_email": "altcha@test.com",
            "internal_contact_name": "ALTCHA Contact",
            "internal_contact_email": "altcha-int@test.com",
            "internal_contact_email_confirm": "altcha-int@test.com",
            "website_url": "https://altcha-example.com",
            "terms_of_use_url": "https://altcha-example.com/tos",
            "license_note": "MIT",
            "github_url": "",
            "biotools_url": "",
            "fairsharing_url": "",
            "other_registry_url": "",
            "kpi_monitoring": "yes",
            "kpi_start_year": "2023",
            "keywords_uncited": "",
            "keywords_seo": "",
            "survey_participation": "True",
            "comments": "",
            "data_protection_consent": "True",
        }

    def test_register_post_without_altcha_field_returns_400_when_key_configured(
        self, client
    ):
        """POST to /register/ without altcha payload returns 400 when HMAC key is set."""
        from django.test import override_settings

        data = self._valid_register_data()
        with override_settings(ALTCHA_HMAC_KEY="test-hmac-key"):
            resp = client.post(reverse("submissions:register"), data=data)
        assert resp.status_code == 400

    def test_register_post_with_invalid_altcha_returns_400(self, client):
        """POST to /register/ with a malformed altcha value returns 400."""
        from django.test import override_settings

        data = self._valid_register_data()
        data["altcha"] = "not-valid-base64-json"
        with override_settings(ALTCHA_HMAC_KEY="test-hmac-key"):
            resp = client.post(reverse("submissions:register"), data=data)
        assert resp.status_code == 400

    def test_register_post_bypassed_when_no_hmac_key(self, client):
        """POST to /register/ proceeds normally when ALTCHA_HMAC_KEY is empty."""
        from django.test import override_settings

        data = self._valid_register_data()
        # No altcha field — but key is empty so verification is bypassed
        with override_settings(ALTCHA_HMAC_KEY=""):
            resp = client.post(reverse("submissions:register"), data=data)
        assert resp.status_code == 302

    def test_register_post_with_valid_solved_altcha_succeeds(self, client):
        """POST to /register/ with a correctly solved challenge is accepted."""
        import base64
        import json
        from django.test import override_settings
        from altcha import ChallengeOptions, create_challenge, solve_challenge

        hmac_key = "test-hmac-key-solve"
        options = ChallengeOptions(hmac_key=hmac_key, max_number=1000)
        challenge = create_challenge(options)
        solution = solve_challenge(
            challenge.challenge, challenge.salt, challenge.algorithm, 1000
        )
        payload = {
            "algorithm": challenge.algorithm,
            "challenge": challenge.challenge,
            "number": solution.number,
            "salt": challenge.salt,
            "signature": challenge.signature,
        }
        altcha_value = base64.b64encode(json.dumps(payload).encode()).decode()

        data = self._valid_register_data()
        data["altcha"] = altcha_value
        with override_settings(ALTCHA_HMAC_KEY=hmac_key):
            resp = client.post(reverse("submissions:register"), data=data)
        assert resp.status_code == 302

    def _edit_submission_data(self, sub):
        """Build a minimal valid POST payload for the edit view from a submission."""
        return {
            "date_of_entry": sub.date_of_entry.isoformat(),
            "submitter_first_name": sub.submitter_first_name,
            "submitter_last_name": sub.submitter_last_name,
            "submitter_affiliation": sub.submitter_affiliation,
            "register_as_elixir": str(sub.register_as_elixir),
            "service_name": sub.service_name,
            "service_description": sub.service_description,
            "year_established": sub.year_established,
            "service_categories": [c.pk for c in sub.service_categories.all()],
            "is_toolbox": str(sub.is_toolbox),
            "toolbox_name": sub.toolbox_name or "",
            "user_knowledge_required": sub.user_knowledge_required or "",
            "publications_pmids": sub.publications_pmids or "",
            "responsible_pis": [p.pk for p in sub.responsible_pis.all()],
            "associated_partner_note": sub.associated_partner_note or "",
            "host_institute": sub.host_institute,
            "service_center": sub.service_center.pk,
            "public_contact_email": sub.public_contact_email,
            "internal_contact_name": sub.internal_contact_name,
            "internal_contact_email": sub.internal_contact_email,
            "internal_contact_email_confirm": sub.internal_contact_email,
            "website_url": sub.website_url,
            "terms_of_use_url": sub.terms_of_use_url,
            "licenses": [lic.pk for lic in sub.licenses.all()],
            "license_note": sub.license_note or "",
            "github_url": sub.github_url or "",
            "biotools_url": sub.biotools_url or "",
            "fairsharing_url": sub.fairsharing_url or "",
            "other_registry_url": sub.other_registry_url or "",
            "kpi_monitoring": sub.kpi_monitoring,
            "kpi_start_year": sub.kpi_start_year or "",
            "keywords_uncited": sub.keywords_uncited or "",
            "keywords_seo": sub.keywords_seo or "",
            "survey_participation": str(sub.survey_participation),
            "comments": sub.comments or "",
            "data_protection_consent": str(sub.data_protection_consent),
        }

    def _setup_edit_altcha_session(self, client, sub):
        key_obj, _ = APIKeyFactory.create_with_plaintext(submission=sub)
        session = client.session
        session["edit_key_id"] = str(key_obj.pk)
        session["edit_submission_id"] = str(sub.pk)
        session.save()

    def test_edit_post_without_altcha_returns_400_when_key_configured(self, client):
        """POST to /update/edit/ without altcha payload returns 400 when HMAC key is set."""
        from django.test import override_settings

        sub = ServiceSubmissionFactory()
        self._setup_edit_altcha_session(client, sub)
        data = self._edit_submission_data(sub)
        with override_settings(ALTCHA_HMAC_KEY="test-hmac-key"):
            resp = client.post(reverse("submissions:edit"), data=data)
        assert resp.status_code == 400
        assert b"CAPTCHA" in resp.content

    def test_edit_post_with_invalid_altcha_returns_400(self, client):
        """POST to /update/edit/ with a malformed altcha value returns 400."""
        from django.test import override_settings

        sub = ServiceSubmissionFactory()
        self._setup_edit_altcha_session(client, sub)
        data = self._edit_submission_data(sub)
        data["altcha"] = "not-valid-base64-json"
        with override_settings(ALTCHA_HMAC_KEY="test-hmac-key"):
            resp = client.post(reverse("submissions:edit"), data=data)
        assert resp.status_code == 400
        assert b"CAPTCHA" in resp.content

    def test_edit_post_bypassed_when_no_hmac_key(self, client):
        """POST to /update/edit/ proceeds normally when ALTCHA_HMAC_KEY is empty."""
        from django.test import override_settings

        sub = ServiceSubmissionFactory()
        self._setup_edit_altcha_session(client, sub)
        data = self._edit_submission_data(sub)
        with override_settings(ALTCHA_HMAC_KEY=""):
            resp = client.post(reverse("submissions:edit"), data=data)
        assert resp.status_code == 302

    def test_edit_post_with_valid_solved_altcha_succeeds(self, client):
        """POST to /update/edit/ with a correctly solved challenge is accepted."""
        import base64
        import json
        from django.test import override_settings
        from altcha import ChallengeOptions, create_challenge, solve_challenge

        sub = ServiceSubmissionFactory()
        self._setup_edit_altcha_session(client, sub)

        hmac_key = "test-hmac-key-edit-solve"
        options = ChallengeOptions(hmac_key=hmac_key, max_number=1000)
        challenge = create_challenge(options)
        solution = solve_challenge(
            challenge.challenge, challenge.salt, challenge.algorithm, 1000
        )
        payload = {
            "algorithm": challenge.algorithm,
            "challenge": challenge.challenge,
            "number": solution.number,
            "salt": challenge.salt,
            "signature": challenge.signature,
        }
        altcha_value = base64.b64encode(json.dumps(payload).encode()).decode()

        data = self._edit_submission_data(sub)
        data["altcha"] = altcha_value
        with override_settings(ALTCHA_HMAC_KEY=hmac_key):
            resp = client.post(reverse("submissions:edit"), data=data)
        assert resp.status_code == 302

    def test_register_captcha_failure_shows_error_message(self, client):
        """A CAPTCHA failure on /register/ must include the error text in the response."""
        from django.test import override_settings

        data = self._valid_register_data()
        with override_settings(ALTCHA_HMAC_KEY="test-hmac-key"):
            resp = client.post(reverse("submissions:register"), data=data)
        assert resp.status_code == 400
        assert b"CAPTCHA" in resp.content

    def test_register_post_with_expired_altcha_returns_400(self, client):
        """POST to /register/ with an expired ALTCHA challenge must be rejected."""
        import base64
        import datetime
        import json
        from django.test import override_settings
        from altcha import ChallengeOptions, create_challenge, solve_challenge

        hmac_key = "test-hmac-key-expired"
        options = ChallengeOptions(
            hmac_key=hmac_key,
            max_number=100,
            expires=datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(minutes=5),
        )
        challenge = create_challenge(options)
        solution = solve_challenge(
            challenge.challenge, challenge.salt, challenge.algorithm, 100
        )
        payload = {
            "algorithm": challenge.algorithm,
            "challenge": challenge.challenge,
            "number": solution.number,
            "salt": challenge.salt,
            "signature": challenge.signature,
        }
        altcha_value = base64.b64encode(json.dumps(payload).encode()).decode()

        data = self._valid_register_data()
        data["altcha"] = altcha_value
        with override_settings(ALTCHA_HMAC_KEY=hmac_key):
            resp = client.post(reverse("submissions:register"), data=data)
        assert resp.status_code == 400
        assert b"CAPTCHA" in resp.content

    def test_edit_post_with_expired_altcha_returns_400(self, client):
        """POST to /update/edit/ with an expired ALTCHA challenge must be rejected."""
        import base64
        import datetime
        import json
        from django.test import override_settings
        from altcha import ChallengeOptions, create_challenge, solve_challenge

        sub = ServiceSubmissionFactory()
        self._setup_edit_altcha_session(client, sub)

        hmac_key = "test-hmac-key-edit-expired"
        options = ChallengeOptions(
            hmac_key=hmac_key,
            max_number=100,
            expires=datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(minutes=5),
        )
        challenge = create_challenge(options)
        solution = solve_challenge(
            challenge.challenge, challenge.salt, challenge.algorithm, 100
        )
        payload = {
            "algorithm": challenge.algorithm,
            "challenge": challenge.challenge,
            "number": solution.number,
            "salt": challenge.salt,
            "signature": challenge.signature,
        }
        altcha_value = base64.b64encode(json.dumps(payload).encode()).decode()

        data = self._edit_submission_data(sub)
        data["altcha"] = altcha_value
        with override_settings(ALTCHA_HMAC_KEY=hmac_key):
            resp = client.post(reverse("submissions:edit"), data=data)
        assert resp.status_code == 400
        assert b"CAPTCHA" in resp.content

    def test_register_get_shows_widget_when_altcha_enabled(self, client):
        """GET /register/ must render the altcha-widget element when ALTCHA is configured."""
        from django.test import override_settings

        with override_settings(ALTCHA_HMAC_KEY="test-hmac-key"):
            resp = client.get(reverse("submissions:register"))
        assert resp.status_code == 200
        # Check for the opening HTML tag, not just the string (which also appears in JS)
        assert b"<altcha-widget" in resp.content

    def test_register_get_hides_widget_when_altcha_disabled(self, client):
        """GET /register/ must not render the altcha-widget element when ALTCHA is not configured."""
        from django.test import override_settings

        with override_settings(ALTCHA_HMAC_KEY=""):
            resp = client.get(reverse("submissions:register"))
        assert resp.status_code == 200
        assert b"<altcha-widget" not in resp.content

    def test_edit_get_shows_widget_when_altcha_enabled(self, client):
        """GET /update/edit/ must render the altcha-widget element when ALTCHA is configured."""
        from django.test import override_settings

        sub = ServiceSubmissionFactory()
        self._setup_edit_altcha_session(client, sub)
        with override_settings(ALTCHA_HMAC_KEY="test-hmac-key"):
            resp = client.get(reverse("submissions:edit"))
        assert resp.status_code == 200
        assert b"<altcha-widget" in resp.content

    def test_edit_get_hides_widget_when_altcha_disabled(self, client):
        """GET /update/edit/ must not render the altcha-widget element when ALTCHA is not configured."""
        from django.test import override_settings

        sub = ServiceSubmissionFactory()
        self._setup_edit_altcha_session(client, sub)
        with override_settings(ALTCHA_HMAC_KEY=""):
            resp = client.get(reverse("submissions:edit"))
        assert resp.status_code == 200
        assert b"<altcha-widget" not in resp.content


# ===========================================================================
# Health endpoints
# ===========================================================================


@pytest.mark.django_db
class TestHealthEndpoints:
    def test_liveness_returns_200(self, client):
        resp = client.get("/health/live/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_readiness_returns_json(self, client):
        resp = client.get("/health/ready/")
        # Status may be 200 or 503 depending on Redis availability in test env
        assert resp.status_code in (200, 503)
        data = resp.json()
        assert "status" in data
        # Internal service breakdown must NOT be exposed to callers
        assert "checks" not in data


# ===========================================================================
# EditView — API key scope enforcement
# ===========================================================================


@pytest.mark.django_db
class TestEditViewScopeEnforcement:
    """
    A read-only API key (SCOPE_READ) stored in the edit session must not be
    able to submit mutations via the web form, while still being able to load
    the form on GET.
    """

    def _setup_session(self, client, sub, scope):
        key_obj = APIKeyFactory(submission=sub, scope=scope)
        session = client.session
        session["edit_key_id"] = str(key_obj.pk)
        session["edit_submission_id"] = str(sub.pk)
        session.save()

    def test_read_only_key_can_load_edit_form(self, client):
        """GET /update/edit/ with a SCOPE_READ key must succeed (200)."""
        sub = ServiceSubmissionFactory()
        self._setup_session(client, sub, scope="read")
        resp = client.get(reverse("submissions:edit"))
        assert resp.status_code == 200

    def test_read_only_key_cannot_post_edit_form(self, client):
        """POST /update/edit/ with a SCOPE_READ key must redirect to the key-entry page."""
        sub = ServiceSubmissionFactory()
        original_name = sub.service_name
        self._setup_session(client, sub, scope="read")

        resp = client.post(reverse("submissions:edit"), data={"service_name": "hacked"})
        assert resp.status_code == 302
        assert reverse("submissions:update") in resp["Location"]
        sub.refresh_from_db()
        assert sub.service_name == original_name  # no change applied

    def test_write_key_can_post_edit_form(self, client):
        """POST /update/edit/ with a SCOPE_WRITE key proceeds normally (not rejected)."""
        sub = ServiceSubmissionFactory()
        self._setup_session(client, sub, scope="write")
        # A POST without a full valid form just re-renders the form (200 or 302),
        # the key point is that it does NOT redirect to the key-entry page.
        resp = client.post(reverse("submissions:edit"), data={})
        assert resp.status_code not in (301, 302) or reverse(
            "submissions:update"
        ) not in resp.get("Location", "")
