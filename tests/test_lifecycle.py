"""
Tests for apps/submissions/lifecycle.py

Covers:
  - get_no_reset_fields() returns empty frozenset when setting is empty
  - Valid field names are accepted
  - Unknown field names are logged and ignored
  - System-controlled fields are logged and ignored
  - Cache is populated and can be cleared between tests
"""

import unittest.mock as mock

import pytest

from apps.submissions.lifecycle import get_no_reset_fields


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear the lru_cache before and after every test."""
    get_no_reset_fields.cache_clear()
    yield
    get_no_reset_fields.cache_clear()


class TestGetNoResetFields:
    def test_empty_setting_returns_empty_frozenset(self, settings):
        settings.SUBMISSION_NO_RESET_FIELDS = []
        result = get_no_reset_fields()
        assert result == frozenset()

    def test_valid_scalar_fields_are_accepted(self, settings):
        settings.SUBMISSION_NO_RESET_FIELDS = ["logo", "github_url", "biotools_url"]
        result = get_no_reset_fields()
        assert "logo" in result
        assert "github_url" in result
        assert "biotools_url" in result

    def test_valid_m2m_fields_are_accepted(self, settings):
        settings.SUBMISSION_NO_RESET_FIELDS = ["edam_topics", "edam_operations"]
        result = get_no_reset_fields()
        assert "edam_topics" in result
        assert "edam_operations" in result

    def test_unknown_field_is_excluded(self, settings):
        settings.SUBMISSION_NO_RESET_FIELDS = ["not_a_real_field"]
        result = get_no_reset_fields()
        assert "not_a_real_field" not in result
        assert len(result) == 0

    def test_unknown_field_logged_as_warning(self, settings):
        settings.SUBMISSION_NO_RESET_FIELDS = ["typo_field"]
        import apps.submissions.lifecycle as lc

        with mock.patch.object(lc.logger, "warning") as mock_warn:
            get_no_reset_fields()
        mock_warn.assert_called()
        full_message = " ".join(
            str(a) for call in mock_warn.call_args_list for a in call.args
        )
        assert "typo_field" in full_message

    def test_system_controlled_field_is_excluded(self, settings):
        settings.SUBMISSION_NO_RESET_FIELDS = ["status"]
        result = get_no_reset_fields()
        assert "status" not in result

    def test_system_controlled_field_logged_as_warning(self, settings):
        settings.SUBMISSION_NO_RESET_FIELDS = ["primary_maturity_tag"]
        import apps.submissions.lifecycle as lc

        with mock.patch.object(lc.logger, "warning") as mock_warn:
            get_no_reset_fields()
        mock_warn.assert_called()
        full_message = " ".join(
            str(a) for call in mock_warn.call_args_list for a in call.args
        )
        assert "primary_maturity_tag" in full_message

    def test_mixed_valid_and_invalid_fields(self, settings):
        settings.SUBMISSION_NO_RESET_FIELDS = [
            "github_url",
            "not_a_real_field",
            "logo",
        ]
        result = get_no_reset_fields()
        assert "github_url" in result
        assert "logo" in result
        assert "not_a_real_field" not in result

    def test_returns_frozenset(self, settings):
        settings.SUBMISSION_NO_RESET_FIELDS = ["logo"]
        result = get_no_reset_fields()
        assert isinstance(result, frozenset)

    def test_result_is_cached(self, settings):
        settings.SUBMISSION_NO_RESET_FIELDS = ["logo"]
        r1 = get_no_reset_fields()
        r2 = get_no_reset_fields()
        assert r1 is r2  # same object — cache hit

    def test_cache_clear_reloads_settings(self, settings):
        settings.SUBMISSION_NO_RESET_FIELDS = ["logo"]
        r1 = get_no_reset_fields()
        assert "logo" in r1

        get_no_reset_fields.cache_clear()
        settings.SUBMISSION_NO_RESET_FIELDS = ["github_url"]
        r2 = get_no_reset_fields()
        assert "logo" not in r2
        assert "github_url" in r2
