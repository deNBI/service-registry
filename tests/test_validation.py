"""
Validation Module Tests
=======================
Unit tests for shared validation functions in apps/submissions/validation.py.
"""

import pytest
from django.core.exceptions import ValidationError

from apps.submissions.validation import (
    validate_description_length,
    validate_kpi_start_year,
    validate_toolbox_name,
    validate_year_established,
)


class TestValidateYearEstablished:
    def test_valid_year_passes(self):
        validate_year_established(2020)  # no exception

    def test_year_before_1900_raises(self):
        with pytest.raises(ValidationError):
            validate_year_established(1899)

    def test_future_year_raises(self):
        with pytest.raises(ValidationError):
            validate_year_established(9999)


class TestValidateDescriptionLength:
    def test_valid_length_passes(self):
        validate_description_length("a" * 50, 50, 5000)

    def test_too_short_raises(self):
        with pytest.raises(ValidationError):
            validate_description_length("short", 50, 5000)

    def test_too_long_raises(self):
        with pytest.raises(ValidationError):
            validate_description_length("a" * 5001, 50, 5000)


class TestValidateToolboxName:
    def test_toolbox_without_name_raises(self):
        with pytest.raises(ValidationError):
            validate_toolbox_name(True, "")

    def test_toolbox_with_name_passes(self):
        validate_toolbox_name(True, "My Toolbox")

    def test_non_toolbox_no_name_passes(self):
        validate_toolbox_name(False, "")


class TestValidateKpiStartYear:
    def test_active_monitoring_requires_year(self):
        with pytest.raises(ValidationError):
            validate_kpi_start_year("yes", "")

    def test_planned_does_not_require_year(self):
        validate_kpi_start_year("planned", "")

    def test_active_with_year_passes(self):
        validate_kpi_start_year("yes", "2022")
