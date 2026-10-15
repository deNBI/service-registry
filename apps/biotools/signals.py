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

        Only triggers when the submission has a biotools_url and there is no
        record yet for the tool in that URL (new submission, changed URL, or
        an earlier sync that never created a record). When the URL is empty,
        a record left from an earlier URL is removed so its data is not shown.
        """
        from apps.biotools.models import BioToolsRecord

        from .sync import biotools_id_from_url
        from .tasks import sync_biotools_record

        if not instance.biotools_url:
            removed, _ = BioToolsRecord.objects.filter(submission=instance).delete()
            if removed:
                logger.info(
                    "Removed bio.tools record of submission %s (no bio.tools URL)",
                    instance.pk,
                )
            return

        # post_save runs after the row is written, so the previous URL cannot
        # be read back here. Compare with the record instead: skip only when a
        # record for the tool in the current URL exists, so ordinary saves
        # (status changes, KPI updates) do not trigger a sync.
        if BioToolsRecord.objects.filter(
            submission=instance,
            biotools_id=biotools_id_from_url(instance.biotools_url),
        ).exists():
            return

        logger.info(
            "Scheduling bio.tools sync for submission %s (url: %s)",
            instance.pk,
            instance.biotools_url,
        )
        # Queue the task only once the saving transaction has committed: the
        # task reads the submission from the database. An edit stores content,
        # relations and audit entries in one transaction, which can take longer
        # than any fixed delay.
        submission_id = str(instance.pk)
        transaction.on_commit(
            lambda: sync_biotools_record.apply_async(args=[submission_id])
        )
