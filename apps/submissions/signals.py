"""Signal receivers for the submissions app."""

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone

from apps.submissions.models import ServiceSubmission, SubmissionStatusEvent


@receiver(
    post_save, sender=ServiceSubmission, dispatch_uid="submission_creation_status_event"
)
def record_creation_event(sender, instance, created, raw=False, **kwargs):
    """Record the initial status of every new submission, whatever created it.

    The actor is always "system": the receiver sees only the saved row, not
    whether the public form, the admin, an import or a script created it.
    """
    if not created or raw:
        return
    SubmissionStatusEvent.objects.create(
        submission=instance,
        from_status=None,
        to_status=instance.status,
        changed_at=timezone.now(),
        changed_by="system",
    )
