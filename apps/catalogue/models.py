"""
Publishing layer models.

The working copy (ServiceSubmission) is what owners edit and admins review.
The published copy is what the public sees: an immutable JSON revision per
publish (PublishedRevision, the source of truth), a derived relational read
model with the same field names as the working copy (PublishedService), and a
nameless tombstone for deleted services (WithdrawnService).

Written only by apps/catalogue/publishing.py and the tombstone receiver in
apps/catalogue/signals.py (tests/test_publishing_guard.py enforces this).
"""

import uuid

from django.db import models

from apps.catalogue.public_fields import PUBLIC_SCALAR_FIELDS
from apps.submissions.models import ServiceSubmission


class PublishedRevision(models.Model):
    """Immutable copy of a service's public fields at one publish."""

    SCHEMA_VERSION = 1
    REASONS = [
        ("approval", "Approval"),
        ("live_edit", "Live edit"),
        ("admin_edit", "Admin edit"),
        ("tag_change", "Maturity tag change"),
        ("backfill", "Backfill"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    submission = models.ForeignKey(
        ServiceSubmission,
        on_delete=models.CASCADE,
        related_name="published_revisions",
    )
    number = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    # The "last public change" date the published copy was given with this
    # revision. It differs from created_at for a copy published for a change
    # made earlier, and lets a rebuilt copy keep its date.
    public_modified_at = models.DateTimeField()
    reason = models.CharField(max_length=20, choices=REASONS)
    actor = models.CharField(max_length=200)
    schema_version = models.PositiveSmallIntegerField(default=SCHEMA_VERSION)
    content = models.JSONField()
    content_hash = models.CharField(max_length=64)

    class Meta:
        ordering = ["submission_id", "-number"]
        constraints = [
            models.UniqueConstraint(
                fields=["submission", "number"], name="published_revision_number_unique"
            )
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError("PublishedRevision is immutable")
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.submission_id} r{self.number} ({self.reason})"


def _mirror(name: str):
    """A fresh copy of the ServiceSubmission field definition."""
    return ServiceSubmission._meta.get_field(name).clone()


class PublishedService(models.Model):
    """Derived read model: the public copy of one service."""

    ACTIVE = "active"
    DEPRECATED = "deprecated"
    STATES = [(ACTIVE, "Active"), (DEPRECATED, "Deprecated")]

    submission = models.OneToOneField(
        ServiceSubmission,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="published",
    )
    # RESTRICT (not PROTECT): deleting the submission cascades to both this
    # row and its revisions; PROTECT would block that delete.
    revision = models.ForeignKey(
        PublishedRevision, on_delete=models.RESTRICT, related_name="+"
    )
    state = models.CharField(max_length=12, choices=STATES, default=ACTIVE)
    # An edit of this service is waiting for review.
    pending_update = models.BooleanField(default=False)
    public_modified_at = models.DateTimeField()
    first_published_at = models.DateTimeField(null=True, blank=True)

    service_center = models.ForeignKey(
        "registry.ServiceCenter",
        on_delete=models.PROTECT,
        related_name="published_services",
    )
    service_categories = models.ManyToManyField(
        "registry.ServiceCategory", related_name="published_services"
    )
    responsible_pis = models.ManyToManyField(
        "registry.PrincipalInvestigator", related_name="published_services"
    )
    edam_topics = models.ManyToManyField(
        "edam.EdamTerm", blank=True, related_name="published_by_topic"
    )
    edam_operations = models.ManyToManyField(
        "edam.EdamTerm", blank=True, related_name="published_by_operation"
    )
    licenses = models.ManyToManyField(
        "licenses.SpdxLicense", blank=True, related_name="published_services"
    )

    class Meta:
        ordering = ["service_name"]
        # Explicit name: unnamed indexes are resolved at class creation, before
        # the mirrored fields below are attached.
        indexes = [
            models.Index(
                fields=["state", "service_name"], name="catalogue_ps_state_name"
            )
        ]

    @property
    def updated_at(self):
        """Lets templates written for ServiceSubmission render this model."""
        return self.public_modified_at

    def __str__(self) -> str:
        return self.service_name


for _name in PUBLIC_SCALAR_FIELDS:
    PublishedService.add_to_class(_name, _mirror(_name))


class WithdrawnService(models.Model):
    """Tombstone for a deleted published service. Never stores a name."""

    id = models.UUIDField(primary_key=True)
    withdrawn_at = models.DateTimeField()

    class Meta:
        ordering = ["-withdrawn_at"]
