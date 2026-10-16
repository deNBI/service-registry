"""Tombstones: remember deleted published services (never their names)."""

from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.utils import timezone

from apps.catalogue.models import PublishedRevision, WithdrawnService
from apps.submissions.models import ServiceSubmission


@receiver(
    pre_delete, sender=ServiceSubmission, dispatch_uid="published_service_tombstone"
)
def write_tombstone(sender, instance, **kwargs):
    """Runs for every way a submission can be deleted (single, queryset, admin,
    shell), before its published copy is removed by the cascade."""
    # Every published copy has revisions; the read model itself may be missing.
    if PublishedRevision.objects.filter(submission_id=instance.pk).exists():
        WithdrawnService.objects.get_or_create(
            id=instance.pk, defaults={"withdrawn_at": timezone.now()}
        )
