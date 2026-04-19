"""Celery tasks for SPDX license sync."""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="licenses.sync", bind=True, max_retries=2, default_retry_delay=300)
def sync_spdx_licenses_task(self, url: str | None = None) -> dict:
    """
    Download and upsert SPDX licenses.

    Triggered by:
    - Admin "Sync Now" button (immediate)
    - Celery beat 15-day schedule
    """
    from apps.licenses.sync import run_sync

    try:
        return run_sync(url=url, log=logger.info)
    except Exception as exc:
        logger.exception("SPDX license sync failed: %s", exc)
        raise self.retry(exc=exc)
