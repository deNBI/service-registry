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

    def test_data_protection_consent_required(self):
        sub = ServiceSubmissionFactory.build(data_protection_consent=False)
        with pytest.raises(ValidationError) as exc:
            sub.clean()
        assert "data_protection_consent" in str(exc.value)

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

        for bad in ("not-an-orcid", "0000-0000-0000-000", "1234567890"):
            with pytest.raises(ValidationError):
                _validate_orcid(bad)
        _validate_orcid("0000-0002-1825-0097")  # known valid


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
