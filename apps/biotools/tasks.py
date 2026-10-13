"""
bio.tools Celery Tasks
======================
Periodic and on-demand tasks for syncing bio.tools data.

Tasks:
  sync_biotools_record(submission_id)
      One-off task, called when a submission with a bio.tools URL is saved.

  sync_all_biotools_records()
      Periodic task (Celery beat, every 24 hours) that syncs every submission
      with a bio.tools URL, using the URL it has now.
"""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=120,
    autoretry_for=(Exception,),
    name="biotools.sync_record",
)
def sync_biotools_record(self, submission_id: str) -> dict:
    """
    Sync the bio.tools record for a single submission.

    Triggered automatically when a ServiceSubmission with a biotools_url
    is saved (via post_save signal in apps/biotools/signals.py).

    Also callable directly:
        sync_biotools_record.delay(str(submission.id))

    Returns a dict with keys: ok, biotools_id, created, error.
    """
    from apps.submissions.models import ServiceSubmission
    from .sync import biotools_id_from_url, sync_tool

    try:
        submission = ServiceSubmission.objects.get(pk=submission_id)
    except ServiceSubmission.DoesNotExist:
        logger.error("sync_biotools_record: submission %s not found", submission_id)
        return {"ok": False, "error": f"Submission {submission_id} not found"}

    if not submission.biotools_url:
        return {"ok": False, "error": "Submission has no bio.tools URL"}

    # Extract the bio.tools ID from the URL: https://bio.tools/<id>
    biotools_id = biotools_id_from_url(submission.biotools_url)
    if not biotools_id:
        return {"ok": False, "error": "Could not extract bio.tools ID from URL"}

    logger.info(
        "Syncing bio.tools record: %s for submission %s", biotools_id, submission_id
    )
    result = sync_tool(biotools_id=biotools_id, submission_id=submission_id)
    return result._asdict()


@shared_task(
    name="biotools.sync_all",
    ignore_result=True,
)
def sync_all_biotools_records() -> None:
    """
    Sync every submission that has a bio.tools URL.

    Scheduled every 24 hours (CELERY_BEAT_SCHEDULE in config/settings.py).
    The bio.tools ID is taken from the submission's current URL, so a changed
    URL replaces the old tool's data and a submission whose first sync failed
    gets its record. Records of submissions without a URL are removed.
    Errors are logged and stored on the record but do not stop the loop.
    """
    from apps.submissions.models import ServiceSubmission
    from .sync import biotools_id_from_url, remove_records_without_url, sync_tool

    removed = remove_records_without_url()
    if removed:
        logger.info("Removed %d bio.tools record(s) without a bio.tools URL", removed)

    submissions = (
        ServiceSubmission.objects.exclude(biotools_url="")
        .only("id", "biotools_url")
        .order_by("pk")
    )
    total = submissions.count()
    logger.info("Starting bio.tools bulk sync: %d submissions", total)

    ok_count = 0
    err_count = 0

    for submission in submissions:
        biotools_id = biotools_id_from_url(submission.biotools_url)
        if not biotools_id:
            continue
        result = sync_tool(biotools_id=biotools_id, submission_id=str(submission.pk))
        if result.ok:
            ok_count += 1
        else:
            err_count += 1
            logger.warning(
                "bio.tools sync failed for %s: %s", biotools_id, result.error
            )

    logger.info(
        "bio.tools bulk sync complete. OK: %d / %d, Errors: %d",
        ok_count,
        total,
        err_count,
    )
