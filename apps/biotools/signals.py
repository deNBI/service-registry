"""
bio.tools Signals
=================
Connects Django's post_save signal on ServiceSubmission to the Celery
sync task so that adding or changing a bio.tools URL automatically
triggers a background refresh.
"""

import logging

from django.db import transaction
from django.db.models.signals import post_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


def connect_signals():
    """
    Called from BioToolsConfig.ready() to register signal handlers.
    Using a function instead of module-level decorators avoids import
    issues during app startup.
    """
    from apps.submissions.models import ServiceSubmission

    @receiver(
        post_save,
        sender=ServiceSubmission,
        dispatch_uid="biotools_sync_on_save",
        weak=False,
    )
    def trigger_biotools_sync(sender, instance, created, **kwargs):
        """
        When a ServiceSubmission is saved with a bio.tools URL, kick off
        a background sync.

        Only triggers when:
          - The submission has a biotools_url set
          - The biotools_url has changed (or this is a new submission)

        Uses update_fields to avoid triggering on every field save.
        """
        from .tasks import sync_biotools_record

        if not instance.biotools_url:
            return

        # Check if the biotools_url actually changed to avoid unnecessary syncs
        # on every save (e.g. status changes, KPI updates)
        if not created:
            try:
                previous = sender.objects.get(pk=instance.pk)
                if previous.biotools_url == instance.biotools_url:
                    # URL hasn't changed — check if we already have a record
                    from apps.biotools.models import BioToolsRecord

                    if BioToolsRecord.objects.filter(submission=instance).exists():
                        return  # Already synced, URL unchanged — skip
            except sender.DoesNotExist:
                pass

        logger.info(
            "Scheduling bio.tools sync for submission %s (url: %s)",
            instance.pk,
            instance.biotools_url,
        )
        # Queue the task only once the saving transaction has committed: the
        # task reads the submission from the database. An edit stores content,
        # relations, publishing and audit entries in one transaction, which can
        # take longer than any fixed delay.
        submission_id = str(instance.pk)
        transaction.on_commit(
            lambda: sync_biotools_record.apply_async(args=[submission_id])
        )
