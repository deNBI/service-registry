"""
bio.tools Sync Logic
====================
Core function that fetches a tool from bio.tools and upserts the local
BioToolsRecord and BioToolsFunction rows.

Separated from the Celery task and management command so both can call
the same code and it can be unit-tested without mocking Celery.
"""

import logging
from typing import NamedTuple

from .client import BioToolsClient, BioToolsError, BioToolsNotFound, BioToolsToolEntry

logger = logging.getLogger(__name__)


def biotools_id_from_url(url: str) -> str:
    """Return the bio.tools ID from a URL such as https://bio.tools/<id> ("" if none)."""
    return (url or "").rstrip("/").split("/")[-1]


def remove_records_without_url() -> int:
    """Delete records whose submission no longer has a bio.tools URL.

    Returns the number of records removed (their function rows go with them).
    """
    from .models import BioToolsRecord

    _, per_model = BioToolsRecord.objects.filter(submission__biotools_url="").delete()
    return per_model.get(BioToolsRecord._meta.label, 0)


class SyncResult(NamedTuple):
    ok: bool
    biotools_id: str
    created: bool  # True if the record was newly created
    error: str  # Non-empty when ok=False


def sync_tool(biotools_id: str, submission_id: str | None = None) -> SyncResult:
    """
    Fetch one tool from bio.tools and upsert BioToolsRecord + BioToolsFunctions.

    Args:
        biotools_id:    The bio.tools ID to fetch, e.g. 'blast'.
        submission_id:  UUID of the ServiceSubmission to link to.
                        Required when creating a new record.
                        Ignored when updating an existing record (record already knows its submission).

    Returns:
        SyncResult(ok, biotools_id, created, error)
    """
    from apps.biotools.models import BioToolsFunction, BioToolsRecord

    # -----------------------------------------------------------------
    # 1. Find or resolve the submission
    # -----------------------------------------------------------------
    record = None
    if submission_id:
        try:
            record = BioToolsRecord.objects.select_related("submission").get(
                submission_id=submission_id
            )
        except BioToolsRecord.DoesNotExist:
            pass

    if record is None:
        try:
            record = BioToolsRecord.objects.get(biotools_id=biotools_id)
        except BioToolsRecord.DoesNotExist:
            pass

    # -----------------------------------------------------------------
    # 2. Fetch from bio.tools API
    # -----------------------------------------------------------------
    client = BioToolsClient()
    try:
        tool: BioToolsToolEntry = client.get_tool(biotools_id)
    except BioToolsNotFound:
        error_msg = f"bio.tools ID '{biotools_id}' not found (HTTP 404)"
        logger.warning(error_msg)
        if record:
            record.mark_sync_error(error_msg)
        return SyncResult(
            ok=False, biotools_id=biotools_id, created=False, error=error_msg
        )
    except BioToolsError as exc:
        error_msg = str(exc)
        logger.error("bio.tools sync error for '%s': %s", biotools_id, error_msg)
        if record:
            record.mark_sync_error(error_msg)
        return SyncResult(
            ok=False, biotools_id=biotools_id, created=False, error=error_msg
        )

    # -----------------------------------------------------------------
    # 3. Upsert BioToolsRecord
    # -----------------------------------------------------------------
    topic_uris = [t["uri"] for t in tool.edam_topics if t.get("uri")]

    record_defaults = {
        "biotools_id": tool.biotools_id or biotools_id,
        "name": tool.name[:200],
        "description": tool.description[:5000] if tool.description else "",
        "homepage": tool.homepage[:500] if tool.homepage else "",
        "version": ", ".join(tool.version[:5]),
        "license": tool.license[:100],
        "maturity": tool.maturity[:50],
        "cost": tool.cost[:50],
        "tool_type": tool.tool_type,
        "operating_system": tool.operating_system,
        "publications": tool.publications,
        "documentation": tool.documentation,
        "download": tool.download,
        "links": tool.links,
        "edam_topic_uris": topic_uris,
        "raw_json": tool.raw,
        "sync_error": "",
    }

    created = False
    if record is None:
        # Need a submission to create a new record
        if not submission_id:
            error_msg = "Cannot create BioToolsRecord: no submission_id provided"
            logger.error(error_msg)
            return SyncResult(
                ok=False, biotools_id=biotools_id, created=False, error=error_msg
            )

        from apps.submissions.models import ServiceSubmission

        try:
            submission = ServiceSubmission.objects.get(pk=submission_id)
        except ServiceSubmission.DoesNotExist:
            error_msg = f"ServiceSubmission {submission_id} not found"
            return SyncResult(
                ok=False, biotools_id=biotools_id, created=False, error=error_msg
            )

        record = BioToolsRecord.objects.create(
            submission=submission,
            **record_defaults,
        )
        created = True
        logger.info(
            "Created BioToolsRecord for %s (submission %s)", biotools_id, submission_id
        )
    else:
        for attr, value in record_defaults.items():
            setattr(record, attr, value)
        record.save()
        logger.info("Updated BioToolsRecord for %s", biotools_id)

    # -----------------------------------------------------------------
    # 4. Rebuild BioToolsFunction rows (delete + recreate for simplicity)
    # -----------------------------------------------------------------
    BioToolsFunction.objects.filter(record=record).delete()

    for position, func in enumerate(tool.functions):
        BioToolsFunction.objects.create(
            record=record,
            position=position,
            operations=func.get("operations", []),
            inputs=func.get("inputs", []),
            outputs=func.get("outputs", []),
            cmd=func.get("cmd", ""),
            note=func.get("note", ""),
        )

    record.mark_sync_success()
    return SyncResult(ok=True, biotools_id=biotools_id, created=created, error="")
