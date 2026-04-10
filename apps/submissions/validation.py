"""
Shared validation functions used by both ServiceSubmission.clean()
and SubmissionDetailSerializer. Keep business rules in one place.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import timezone


def validate_year_established(value: int) -> None:
    """Raise DjangoValidationError if year is outside the allowed range."""
    current_year = timezone.now().year
    if not (1900 <= value <= current_year):
        raise DjangoValidationError(
            f"Year established must be between 1900 and {current_year}."
        )


def validate_description_length(value: str, min_len: int, max_len: int) -> None:
    """Raise DjangoValidationError if description is outside the allowed length."""
    stripped = (value or "").strip()
    if stripped and len(stripped) < min_len:
        raise DjangoValidationError(
            f"Service description must be at least {min_len} characters."
        )
    if stripped and len(stripped) > max_len:
        raise DjangoValidationError(
            f"Service description must not exceed {max_len} characters."
        )


def validate_toolbox_name(is_toolbox: bool, toolbox_name: str) -> None:
    """Raise DjangoValidationError if toolbox name is missing when required."""
    if is_toolbox and not (toolbox_name or "").strip():
        raise DjangoValidationError(
            {"toolbox_name": "Toolbox name is required when is_toolbox is True."}
        )


def validate_kpi_start_year(kpi_monitoring: str, kpi_start_year: str) -> None:
    """Raise DjangoValidationError if KPI start year missing when monitoring is active."""
    if kpi_monitoring and kpi_monitoring != "planned":
        if not (kpi_start_year or "").strip():
            raise DjangoValidationError(
                {"kpi_start_year": "Please provide the year KPI monitoring started."}
            )
