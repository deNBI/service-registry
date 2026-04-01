"""
Registry Models
===============
Reference data models managed exclusively via the Django admin.
These are the lookup tables that power the submission form dropdowns.

Models:
  - ServiceCategory   : Type tags for services (Database, Tool, Workflow, etc.)
  - ServiceCenter     : de.NBI service centre organisations
  - PrincipalInvestigator : Named PIs responsible for services

All models use soft-delete (is_active flag) so that removing a PI or centre
from the dropdown does not break existing submission foreign keys.
"""

import uuid
import re

from django.core.exceptions import ValidationError
from django.db import models


# ---------------------------------------------------------------------------
# ServiceCategory
# ---------------------------------------------------------------------------


class ServiceCategory(models.Model):
    """
    A tag describing the type of a de.NBI service.

    Examples: Database, Tool/Application, Workflow/Pipeline, Web application.
    Managed via Django admin. New categories can be added without code changes.
    """

    name = models.CharField(
        max_length=100,
        unique=True,
        help_text="Display name shown in the submission form checkbox list.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text=(
            "Inactive categories are hidden from the submission form but "
            "remain associated with existing submissions."
        ),
    )

    class Meta:
        verbose_name = "Service Category"
        verbose_name_plural = "Service Categories"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


# ---------------------------------------------------------------------------
# ServiceCenter
# ---------------------------------------------------------------------------


class ServiceCenter(models.Model):
    """
    A de.NBI or ELIXIR-DE service centre that hosts or is associated with services.

    Example: Heidelberg Center for Human Bioinformatics (HD-HuB).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    short_name = models.CharField(
        max_length=50,
        help_text="Short abbreviation used in lists, e.g. 'HD-HuB', 'BiGi'.",
    )
    full_name = models.CharField(
        max_length=300,
        help_text="Full official name of the service centre.",
    )
    website = models.URLField(
        blank=True,
        help_text="URL of the service centre's public website (optional).",
    )
    is_active = models.BooleanField(
        default=True,
        help_text=(
            "Inactive centres are hidden from the submission form but "
            "remain linked to existing submissions."
        ),
    )

    class Meta:
        verbose_name = "Service Center"
        verbose_name_plural = "Service Centers"
        ordering = ["full_name"]

    def __str__(self) -> str:
        return f"{self.short_name} — {self.full_name}"


# ---------------------------------------------------------------------------
# PrincipalInvestigator
# ---------------------------------------------------------------------------


def _validate_orcid(value: str) -> None:
    """Validate ORCID iD format (0000-0000-0000-000X) including checksum."""
    if not value:
        return
    pattern = r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$"
    if not re.match(pattern, value):
        raise ValidationError(
            "ORCID must be in format 0000-0000-0000-0000 "
            "(last character may be X for the checksum digit)."
        )
    # Verify Luhn checksum (ISO 7064 MOD 11-2)
    digits = value.replace("-", "")
    total = 0
    for char in digits[:-1]:
        total = (total + int(char)) * 2
    check = 12 - (total % 11)
    check %= 11  # Handle cases where check == 11 (becomes 0) or check == 12 (becomes 1)
    expected = "X" if check == 10 else str(check)
    if digits[-1].upper() != expected:
        raise ValidationError("ORCID checksum is invalid.")


class PrincipalInvestigator(models.Model):
    """
    A named PI who can be listed as responsible for a de.NBI service.

    The list is pre-populated with de.NBI network members and managed via the
    admin portal. Researchers not yet in the list can be added by admins.

    The special ``is_associated_partner`` flag marks the generic "Associated
    partner" option shown at the bottom of the dropdown.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    last_name = models.CharField(max_length=100)
    first_name = models.CharField(max_length=100)
    email = models.EmailField(
        blank=True,
        help_text="Contact email for this PI (not publicly visible).",
    )
    institute = models.CharField(
        max_length=200,
        blank=True,
        help_text="Home institution of this PI.",
    )
    orcid = models.CharField(
        max_length=30,
        blank=True,
        validators=[_validate_orcid],
        help_text="ORCID iD in format 0000-0000-0000-0000.",
    )
    is_active = models.BooleanField(
        default=True,
        help_text=(
            "Inactive PIs are hidden from the submission form but remain "
            "linked to existing submissions."
        ),
    )
    is_associated_partner = models.BooleanField(
        default=False,
        help_text=(
            "Mark True for the generic 'Associated partner' entry. "
            "When selected in the form, the submitter must provide further details."
        ),
    )

    class Meta:
        verbose_name = "Principal Investigator"
        verbose_name_plural = "Principal Investigators"
        ordering = ["last_name", "first_name"]

    def __str__(self) -> str:
        if self.is_associated_partner:
            return "Associated partner [please state below]"
        return f"{self.last_name}, {self.first_name}"

    @property
    def display_name(self) -> str:
        """Full name for display in form dropdowns."""
        return str(self)
