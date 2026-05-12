"""
Model Tests
===========
Tests for ServiceSubmission, SubmissionAPIKey, and registry models.

Coverage areas:
  - API key generation: entropy, hash storage, no plaintext persistence
  - API key verification: valid, invalid, revoked, timing-safe
  - Multi-key behaviour: independence, scoping
  - Model validation: field rules, cross-field, URL schemes, year bounds
  - Sanitisation: null bytes, unicode normalisation, HTML stripping
  - Sensitive field isolation: IP and internal email never in serialiser output
"""

import hashlib
import pytest
from django.core.exceptions import ValidationError

from apps.submissions.models import SubmissionStatus
from tests.factories import (
    APIKeyFactory,
    ServiceSubmissionFactory,
)


# ===========================================================================
# SubmissionAPIKey - security
# ===========================================================================


@pytest.mark.django_db
class TestAPIKeyGeneration:
    def test_plaintext_not_stored_in_hash_field(self):
        key_obj, plaintext = APIKeyFactory.create_with_plaintext()
        assert key_obj.key_hash != plaintext

    def test_key_hash_is_sha256(self):
        key_obj, plaintext = APIKeyFactory.create_with_plaintext()
        expected = hashlib.sha256(plaintext.encode()).hexdigest()
        assert key_obj.key_hash == expected
        assert len(key_obj.key_hash) == 64

    def test_key_entropy_minimum(self):
        _, plaintext = APIKeyFactory.create_with_plaintext()
        assert len(plaintext) >= 48

    def test_two_keys_different_hashes(self):
        sub = ServiceSubmissionFactory()
        _, p1 = APIKeyFactory.create_with_plaintext(submission=sub)
        _, p2 = APIKeyFactory.create_with_plaintext(submission=sub)
        assert p1 != p2
        assert (
            hashlib.sha256(p1.encode()).hexdigest()
            != hashlib.sha256(p2.encode()).hexdigest()
        )

    def test_no_plaintext_field_on_model(self):
        from apps.submissions.models import SubmissionAPIKey

        field_names = [f.name for f in SubmissionAPIKey._meta.get_fields()]
        for bad in ("key", "plaintext", "token", "secret"):
            assert bad not in field_names


@pytest.mark.django_db
class TestAPIKeyVerification:
    def test_valid_key_authenticates(self):
        key_obj, plaintext = APIKeyFactory.create_with_plaintext()
        retrieved, authenticated = key_obj.__class__.verify(plaintext)
        assert authenticated is True
        assert retrieved.pk == key_obj.pk

    def test_invalid_key_returns_false_not_exception(self):
        from apps.submissions.models import SubmissionAPIKey

        result, authenticated = SubmissionAPIKey.verify(
            "this-is-not-a-valid-key-at-all"
        )
        assert authenticated is False
        assert result is None

    def test_revoked_key_returns_false(self):
        key_obj, plaintext = APIKeyFactory.create_with_plaintext()
        key_obj.revoke()
        _, authenticated = key_obj.__class__.verify(plaintext)
        assert authenticated is False

    def test_revoked_indistinguishable_from_invalid(self):
        from apps.submissions.models import SubmissionAPIKey

        key_obj, plaintext = APIKeyFactory.create_with_plaintext()
        key_obj.revoke()
        _, auth_revoked = SubmissionAPIKey.verify(plaintext)
        _, auth_invalid = SubmissionAPIKey.verify("totallywrong")
        assert auth_revoked == auth_invalid == False  # noqa

    def test_key_case_sensitive(self):
        from apps.submissions.models import SubmissionAPIKey

        key_obj, plaintext = APIKeyFactory.create_with_plaintext()
        _, authenticated = SubmissionAPIKey.verify(plaintext.upper())
        assert authenticated is False

    def test_verify_updates_last_used_at(self):
        key_obj, plaintext = APIKeyFactory.create_with_plaintext()
        assert key_obj.last_used_at is None
        key_obj.__class__.verify(plaintext)
        key_obj.refresh_from_db()
        assert key_obj.last_used_at is not None

    def test_revoke_persists_to_db(self):
        key_obj, _ = APIKeyFactory.create_with_plaintext()
        key_obj.revoke()
        key_obj.refresh_from_db()
        assert key_obj.is_active is False

    def test_revoked_key_retained_for_audit(self):
        from apps.submissions.models import SubmissionAPIKey

        key_obj, _ = APIKeyFactory.create_with_plaintext()
        key_id = key_obj.pk
        key_obj.revoke()
        assert SubmissionAPIKey.objects.filter(pk=key_id).exists()


@pytest.mark.django_db
class TestAPIKeyMultiKey:
    def test_two_keys_independent_revocation(self):
        sub = ServiceSubmissionFactory()
        key1, p1 = APIKeyFactory.create_with_plaintext(submission=sub, label="Key 1")
        key2, p2 = APIKeyFactory.create_with_plaintext(submission=sub, label="Key 2")
        key1.revoke()
        _, auth1 = key1.__class__.verify(p1)
        _, auth2 = key1.__class__.verify(p2)
        assert auth1 is False
        assert auth2 is True

    def test_key_scoped_to_correct_submission(self):
        sub_a = ServiceSubmissionFactory(service_name="Service A")
        sub_b = ServiceSubmissionFactory(service_name="Service B")
        key_a, p_a = APIKeyFactory.create_with_plaintext(submission=sub_a)
        retrieved, _ = key_a.__class__.verify(p_a)
        assert str(retrieved.submission_id) == str(sub_a.pk)
        assert str(retrieved.submission_id) != str(sub_b.pk)


# ===========================================================================
# ServiceSubmission - field validation
# ===========================================================================


@pytest.mark.django_db
class TestSubmissionValidation:
    def test_https_required_for_website_url(self):
        from apps.submissions.models import _validate_https_url

        for bad in (
            "http://example.com",
            "ftp://example.com",
            "javascript:alert(1)",
            "data:text/html,x",
        ):
            with pytest.raises(ValidationError):
                _validate_https_url(bad)
        _validate_https_url("https://example.com")

    def test_github_url_prefix_enforced(self):
        from apps.submissions.models import _validate_github_url

        with pytest.raises(ValidationError):
            _validate_github_url("https://gitlab.com/org/repo")
        _validate_github_url("https://github.com/denbi/tool")

    def test_biotools_url_prefix_enforced(self):
        from apps.submissions.models import _validate_biotools_url

        with pytest.raises(ValidationError):
            _validate_biotools_url("https://bioinformatics.tools/xyz")
        _validate_biotools_url("https://bio.tools/myservice")

    def test_publications_valid_pmid(self):
        from apps.submissions.models import _validate_publications

        _validate_publications("12345678")
        _validate_publications("1234, 5678")

    def test_publications_valid_doi(self):
        from apps.submissions.models import _validate_publications

        _validate_publications("10.1016/0022-2836(70)90057-4")

    def test_publications_rejects_garbage(self):
        from apps.submissions.models import _validate_publications

        with pytest.raises(ValidationError):
            _validate_publications("not-a-pmid-or-doi")

    def test_publications_max_50_entries(self):
        from apps.submissions.models import _validate_publications

        too_many = ", ".join(str(i) for i in range(1, 52))
        with pytest.raises(ValidationError):
            _validate_publications(too_many)

    def test_year_established_lower_bound(self):
        sub = ServiceSubmissionFactory.build(year_established=1899)
        with pytest.raises(ValidationError):
            sub.clean()

    def test_year_established_future_rejected(self):
        from django.utils import timezone

        sub = ServiceSubmissionFactory.build(year_established=timezone.now().year + 1)
        with pytest.raises(ValidationError):
            sub.clean()

    def test_data_protection_consent_not_validated_at_model_level(self):
        """Consent is a form-level concern — the model clean() does not raise for False."""
        sub = ServiceSubmissionFactory.build(data_protection_consent=False)
        sub.clean()  # must not raise — admin saves on existing records must work

    def test_data_protection_consent_validated_at_form_level(self):
        """The registration form still requires consent via clean_data_protection_consent."""
        from apps.submissions.forms import SubmissionForm

        form = SubmissionForm(data={})  # empty data triggers required-field errors
        form.is_valid()
        assert "data_protection_consent" in form.errors

    def test_toolbox_name_required_when_is_toolbox_true(self):
        sub = ServiceSubmissionFactory.build(is_toolbox=True, toolbox_name="")
        with pytest.raises(ValidationError) as exc:
            sub.clean()
        assert "toolbox_name" in str(exc.value)

    def test_toolbox_name_not_required_when_not_toolbox(self):
        sub = ServiceSubmissionFactory.build(is_toolbox=False, toolbox_name="")
        sub.clean()  # must not raise

    def test_description_minimum_length_enforced(self):
        sub = ServiceSubmissionFactory.build(service_description="Too short")
        with pytest.raises(ValidationError):
            sub.clean()

    def test_orcid_format_validation(self):
        from apps.registry.models import _validate_orcid

        # Bad format — must be rejected before checksum check
        for bad in ("not-an-orcid", "0000-0000-0000-000", "1234567890"):
            with pytest.raises(ValidationError):
                _validate_orcid(bad)

        # Wrong checksum (correct format, wrong last digit) — must be rejected
        with pytest.raises(ValidationError):
            _validate_orcid("0000-0002-1825-0098")  # check digit should be 7

        # Valid ORCIDs — must all pass
        # Standard case (total % 11 != 0)
        _validate_orcid("0000-0002-1825-0097")
        # Previously failing: total % 11 == 0 → check digit must be 1
        # The old code computed 12 - 0 = 12 instead of (12 - 0) % 11 = 1
        _validate_orcid("0000-0002-1379-9451")  # Schwudke
        _validate_orcid("0000-0003-2563-7561")  # Klamt
        _validate_orcid("0000-0002-2177-8781")  # Beier
        _validate_orcid("0000-0003-0921-8041")  # Usadel
        _validate_orcid("0000-0001-5809-2321")  # Müller
        _validate_orcid("0000-0002-5016-5191")  # Stadler
        # Edge case: check digit X (total % 11 == 2 → check == 10 → 'X')
        _validate_orcid("0000-0002-3960-224X")


@pytest.mark.django_db
class TestSubmissionSanitisation:
    def test_null_bytes_stripped(self):
        sub = ServiceSubmissionFactory(service_name="Test\x00Service")
        sub.refresh_from_db()
        assert "\x00" not in sub.service_name

    def test_unicode_normalised_nfc(self):
        import unicodedata

        # "é" in NFD form (e + combining accent)
        decomposed = "te\u0301st"
        # submitter_affiliation is a sanitised text field — should be NFC-normalised on save
        sub = ServiceSubmissionFactory(submitter_affiliation=decomposed + " Institute")
        sub.refresh_from_db()
        assert unicodedata.is_normalized("NFC", sub.submitter_affiliation)

    def test_whitespace_stripped(self):
        sub = ServiceSubmissionFactory(service_name="  Padded Name  ")
        sub.refresh_from_db()
        assert sub.service_name == "Padded Name"


# ===========================================================================
# Sensitive field isolation
# ===========================================================================


@pytest.mark.django_db
class TestSensitiveFieldIsolation:
    def test_internal_contact_email_not_in_detail_serialiser(self):
        sub = ServiceSubmissionFactory()
        from apps.api.serializers import SubmissionDetailSerializer

        data = SubmissionDetailSerializer(sub).data
        assert "internal_contact_email" not in data
        assert sub.internal_contact_email not in str(data)

    def test_internal_contact_name_not_in_detail_serialiser(self):
        sub = ServiceSubmissionFactory()
        from apps.api.serializers import SubmissionDetailSerializer

        data = SubmissionDetailSerializer(sub).data
        assert "internal_contact_name" not in data

    def test_submission_ip_not_in_list_serialiser(self):
        sub = ServiceSubmissionFactory()
        sub.submission_ip = "10.0.0.1"
        sub.save()
        from apps.api.serializers import SubmissionListSerializer

        data = SubmissionListSerializer(sub).data
        assert "submission_ip" not in data
        assert "10.0.0.1" not in str(data)

    def test_user_agent_hash_not_in_any_serialiser(self):
        sub = ServiceSubmissionFactory()
        sub.user_agent_hash = "a" * 64
        sub.save()
        from apps.api.serializers import (
            SubmissionDetailSerializer,
            SubmissionListSerializer,
        )

        for Ser in (SubmissionDetailSerializer, SubmissionListSerializer):
            data = Ser(sub).data
            assert "user_agent_hash" not in data

    def test_key_hash_not_exposed_via_api(self):
        sub = ServiceSubmissionFactory()
        key_obj, _ = APIKeyFactory.create_with_plaintext(submission=sub)
        from apps.api.serializers import SubmissionDetailSerializer

        data = SubmissionDetailSerializer(sub).data
        assert "key_hash" not in str(data)
        assert key_obj.key_hash not in str(data)


# ===========================================================================
# SubmissionStatus choices
# ===========================================================================


class TestSubmissionStatusChoices:
    def test_deprecated_is_a_valid_status_choice(self):
        assert "deprecated" in SubmissionStatus.values

    def test_all_expected_statuses_present(self):
        expected = {
            "draft",
            "submitted",
            "under_review",
            "approved",
            "rejected",
            "deprecated",
        }
        assert expected == set(SubmissionStatus.values)


# ===========================================================================
# ServiceSubmission - Maturity Tags
# ===========================================================================


@pytest.mark.django_db
class TestServiceMaturityTags:
    """Test primary + secondary maturity tag validation."""

    def test_approved_service_can_have_primary_tag(self):
        sub = ServiceSubmissionFactory(status="approved", primary_maturity_tag="mature")
        sub.clean()
        assert sub.primary_maturity_tag == "mature"

    def test_approved_service_can_have_secondary_tags(self):
        sub = ServiceSubmissionFactory(
            status="approved", secondary_maturity_tags=["unstable"]
        )
        sub.clean()
        assert "unstable" in sub.secondary_maturity_tags

    def test_non_approved_cannot_have_tags(self):
        for status in ["draft", "submitted", "under_review", "deprecated"]:
            sub = ServiceSubmissionFactory(status=status, primary_maturity_tag="mature")
            with pytest.raises(ValidationError) as exc:
                sub.clean()
            assert "Maturity tags can only be assigned to approved services" in str(
                exc.value
            )

    def test_rejected_cannot_have_tags(self):
        sub = ServiceSubmissionFactory(
            status="rejected", primary_maturity_tag="emerging"
        )
        with pytest.raises(ValidationError) as exc:
            sub.clean()
        assert "Maturity tags can only be assigned to approved services" in str(
            exc.value
        )

    def test_invalid_primary_tag_raises_error(self):
        sub = ServiceSubmissionFactory(status="approved")
        sub.primary_maturity_tag = "invalid_tag"
        with pytest.raises(ValidationError) as exc:
            sub.clean()
        assert "Invalid primary maturity tag" in str(exc.value)

    def test_invalid_secondary_tag_raises_error(self):
        sub = ServiceSubmissionFactory(status="approved")
        sub.secondary_maturity_tags = ["invalid_secondary"]
        with pytest.raises(ValidationError) as exc:
            sub.clean()
        assert "not a valid secondary maturity tag" in str(exc.value)

    def test_approved_with_no_tags_is_valid(self):
        sub = ServiceSubmissionFactory(
            status="approved", primary_maturity_tag=None, secondary_maturity_tags=[]
        )
        sub.clean()
        assert sub.primary_maturity_tag is None
        assert sub.secondary_maturity_tags == []


# ===========================================================================
# SPDX License M2M field
# ===========================================================================


class TestSpdxLicenseM2M:
    def test_licenses_field_exists(self):
        """The licenses M2M field must exist on ServiceSubmission."""
        from apps.submissions.models import ServiceSubmission

        field = ServiceSubmission._meta.get_field("licenses")
        assert field.related_model.__name__ == "SpdxLicense"

    def test_license_note_field_exists(self):
        """The license_note CharField must exist on ServiceSubmission."""
        from apps.submissions.models import ServiceSubmission

        field = ServiceSubmission._meta.get_field("license_note")
        assert field.max_length == 200


# ===========================================================================
# _sanitise_text — edge cases
# ===========================================================================


class TestSanitiseText:
    """Unit tests for the _sanitise_text helper applied on ServiceSubmission.save()."""

    def _make(self, **kwargs):
        """Create a minimal submission with overridden fields."""
        from tests.factories import ServiceSubmissionFactory

        sub = ServiceSubmissionFactory(**kwargs)
        sub.refresh_from_db()
        return sub

    @pytest.mark.django_db
    def test_null_bytes_stripped_from_service_name(self):
        sub = self._make(service_name="Hello\x00World")
        assert "\x00" not in sub.service_name
        assert sub.service_name == "HelloWorld"

    @pytest.mark.django_db
    def test_nfc_normalisation_applied(self):
        import unicodedata

        # café — NFD form (e + combining acute) vs NFC (é as single codepoint)
        nfd = "cafe\u0301"  # NFD: e + combining accent
        nfc = unicodedata.normalize("NFC", nfd)
        sub = self._make(service_name=nfd)
        assert sub.service_name == nfc

    @pytest.mark.django_db
    def test_leading_trailing_whitespace_stripped(self):
        sub = self._make(service_name="  My Service  ")
        assert sub.service_name == "My Service"


# ===========================================================================
# _validate_public_contact
# ===========================================================================


class TestValidatePublicContact:
    """Unit tests for the _validate_public_contact validator."""

    def _call(self, value):
        from apps.submissions.models import _validate_public_contact

        _validate_public_contact(value)

    def test_valid_email_passes(self):
        self._call("user@example.com")  # no exception

    def test_valid_https_url_passes(self):
        self._call("https://support.example.com/helpdesk")  # no exception

    def test_https_url_with_path_and_query_passes(self):
        self._call("https://example.org/support?lang=en")  # no exception

    def test_http_url_rejected(self):
        from django.core.exceptions import ValidationError

        with pytest.raises(ValidationError, match="https://"):
            self._call("http://support.example.com")

    def test_javascript_scheme_rejected(self):
        from django.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            self._call("javascript:alert(1)")

    def test_data_scheme_rejected(self):
        from django.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            self._call("data:text/html,<h1>hi</h1>")

    def test_plain_string_rejected(self):
        from django.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            self._call("not-an-email-or-url")

    def test_empty_string_passes(self):
        self._call("")  # blank — required check is handled by the field, not here

    def test_leading_trailing_whitespace_stripped_before_check(self):
        self._call("  user@example.com  ")  # no exception — whitespace stripped

    def test_malformed_https_url_rejected(self):
        from django.core.exceptions import ValidationError

        with pytest.raises(ValidationError):
            self._call("https://")

    def test_leading_trailing_whitespace_stripped_https_url(self):
        self._call(
            "  https://support.example.com  "
        )  # no exception — whitespace stripped

    def test_rejection_has_correct_error_code(self):
        from django.core.exceptions import ValidationError

        with pytest.raises(ValidationError) as exc_info:
            self._call("http://example.com")
        assert exc_info.value.code == "invalid_public_contact"


@pytest.mark.django_db
class TestPublicContactFieldAcceptsUrl:
    """Integration tests: model field accepts email or https URL, rejects others."""

    def _make(self, public_contact_email):
        from tests.factories import ServiceSubmissionFactory

        return ServiceSubmissionFactory(
            public_contact_email=public_contact_email, biotools_url=""
        )

    def test_email_value_passes_full_clean(self):
        sub = self._make("contact@example.com")
        sub.full_clean()  # no exception

    def test_https_url_passes_full_clean(self):
        sub = self._make("https://support.example.com")
        sub.full_clean()  # no exception

    def test_http_url_fails_full_clean(self):
        from django.core.exceptions import ValidationError

        sub = self._make("http://support.example.com")
        with pytest.raises(ValidationError) as exc_info:
            sub.full_clean()
        assert "public_contact_email" in exc_info.value.message_dict

    def test_plain_string_fails_full_clean(self):
        from django.core.exceptions import ValidationError

        sub = self._make("not-valid")
        with pytest.raises(ValidationError) as exc_info:
            sub.full_clean()
        assert "public_contact_email" in exc_info.value.message_dict


@pytest.mark.django_db
class TestPublicContactIsUrl:
    def test_email_value_returns_false(self):
        from tests.factories import ServiceSubmissionFactory

        sub = ServiceSubmissionFactory(
            public_contact_email="user@example.com", biotools_url=""
        )
        assert sub.public_contact_is_url is False

    def test_https_url_returns_true(self):
        from tests.factories import ServiceSubmissionFactory

        sub = ServiceSubmissionFactory(
            public_contact_email="https://support.example.com", biotools_url=""
        )
        assert sub.public_contact_is_url is True

    def test_audit_email_value_returns_false(self):
        from apps.submissions.models import SubmissionDeletionAudit

        audit = SubmissionDeletionAudit(public_contact_email="user@example.com")
        assert audit.public_contact_is_url is False

    def test_audit_https_url_returns_true(self):
        from apps.submissions.models import SubmissionDeletionAudit

        audit = SubmissionDeletionAudit(
            public_contact_email="https://support.example.com"
        )
        assert audit.public_contact_is_url is True
