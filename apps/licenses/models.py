"""
SPDX License Models
===================
Stores the SPDX License List locally so that:
  - The submission form can offer a fast, searchable multi-select
    without network calls
  - Licenses attached to submissions remain stable even if the SPDX
    list evolves (new releases, rename, deprecations)
  - The API can expose license associations using canonical SPDX
    identifiers (e.g. 'MIT', 'Apache-2.0', 'GPL-3.0-only')

Seeding
-------
The canonical SPDX list is published as JSON at:
  https://raw.githubusercontent.com/spdx/license-list-data/main/json/licenses.json

The management command `manage.py sync_spdx_licenses` downloads this file,
parses it, and upserts all entries into this table. It is run automatically
on fresh deployments via a post_migrate signal and on a 15-day Celery beat
schedule.

See: https://spdx.org/licenses/  and
     https://github.com/spdx/license-list-data
"""

from django.db import models


class SpdxLicense(models.Model):
    """
    A single license entry from the SPDX License List.

    The ``license_id`` is the canonical SPDX short identifier
    (e.g. ``Apache-2.0``) and serves as the natural primary key for
    external references. It is used as the wire value in API payloads.
    """

    license_id = models.CharField(
        max_length=80,
        unique=True,
        db_index=True,
        help_text="Canonical SPDX short identifier, e.g. 'Apache-2.0'.",
    )
    name = models.CharField(
        max_length=200,
        db_index=True,
        help_text="Full human-readable license name, e.g. 'Apache License 2.0'.",
    )
    reference_url = models.URLField(
        max_length=500,
        blank=True,
        help_text="Canonical SPDX reference page for this license.",
    )
    see_also = models.JSONField(
        default=list,
        blank=True,
        help_text="Additional authoritative URLs for the license text.",
    )
    is_osi_approved = models.BooleanField(
        default=False,
        db_index=True,
        help_text="True when the license is approved by the Open Source Initiative.",
    )
    is_fsf_libre = models.BooleanField(
        default=False,
        help_text="True when the Free Software Foundation considers the license libre.",
    )
    is_deprecated = models.BooleanField(
        default=False,
        db_index=True,
        help_text=(
            "Deprecated licenses are hidden from new selections but retained "
            "for historical submission references."
        ),
    )
    spdx_version = models.CharField(
        max_length=20,
        blank=True,
        help_text="SPDX License List release version this row was last updated from, e.g. '3.26'.",
    )

    class Meta:
        verbose_name = "SPDX License"
        verbose_name_plural = "SPDX Licenses"
        ordering = ["name"]
        indexes = [
            models.Index(fields=["is_deprecated", "name"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.license_id})"

    @property
    def display_label(self) -> str:
        """Label suitable for pill-picker option display."""
        return f"{self.name} ({self.license_id})"
