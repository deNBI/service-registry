"""
The only writer of PublishedRevision and PublishedService.

Only an approval puts a service into the public set (publish() via
on_status_change()). After that, every write that leaves a published service
approved calls republish(); it does nothing when the public content hash is
unchanged. Status changes that leave the approved state only toggle the
published copy's state and pending-update flag (set_publication_state()).

Every writer here first locks the submission row, the same lock set_status()
and a full save take, so writes to one service run one after the other.
"""

import hashlib
import json

from django.db import transaction
from django.utils import timezone

from apps.catalogue.models import PublishedRevision, PublishedService
from apps.catalogue.public_fields import (
    ALL_PUBLIC_FIELDS,
    PUBLIC_FK_FIELDS,
    PUBLIC_M2M_FIELDS,
    PUBLIC_SCALAR_FIELDS,
)
from apps.submissions.models import (
    ServiceSubmission,
    SubmissionStatus,
    clean_text_fields,
)
from apps.submissions.selectors import first_approved_at, latest_public_decisions


def _plain(value):
    if hasattr(value, "name") and hasattr(value, "storage"):  # FieldFile
        return value.name or ""
    return value


def _ref(pk):
    return pk if isinstance(pk, int) else str(pk)


def _m2m_refs(submission, name) -> list:
    """Sorted ids of the reference rows ``submission`` links under ``name``.

    Reads the prefetched rows when the instance came with ``name`` prefetched
    (a page over many services), otherwise asks the database.
    """
    if name in getattr(submission, "_prefetched_objects_cache", {}):
        pks = [row.pk for row in getattr(submission, name).all()]
    else:
        pks = getattr(submission, name).values_list("pk", flat=True)
    return sorted(_ref(pk) for pk in pks)


def build_content(submission) -> dict:
    """Allowlisted public values of the working copy, JSON-safe."""
    content = {name: _plain(getattr(submission, name)) for name in PUBLIC_SCALAR_FIELDS}
    for name in PUBLIC_FK_FIELDS:
        content[name] = _ref(getattr(submission, f"{name}_id"))
    for name in PUBLIC_M2M_FIELDS:
        content[name] = _m2m_refs(submission, name)
    return content


def content_hash(content: dict) -> str:
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def complete_content(content: dict) -> dict:
    """Revision content with every current public field: a field that became
    public after the revision was written gets the read model's default."""
    complete = dict(content)
    blank = PublishedService()  # field defaults, read the way build_content reads
    for name in PUBLIC_SCALAR_FIELDS:
        if name not in complete:
            complete[name] = _plain(getattr(blank, name))
    for name in PUBLIC_M2M_FIELDS:
        complete.setdefault(name, [])
    return complete


def _existing(model, pks) -> list:
    """The ids in ``pks`` whose reference row still exists, in content order.
    One query (none for an empty list: Django skips an empty ``__in``)."""
    found = model.objects.filter(pk__in=pks).values_list("pk", flat=True)
    return sorted(_ref(pk) for pk in found)


def _canonical_refs(model, values) -> set:
    """The ids in ``values`` in the form ``_existing`` reports them, however
    they are written (for example a UUID in upper case)."""
    pk_field = model._meta.pk
    return {_ref(pk_field.to_python(value)) for value in values}


def existing_references(contents) -> dict:
    """For each many-to-many field, the ids named by any of ``contents`` whose
    reference row still exists. One query per relation however many contents
    there are, for a page that compares many services at once."""
    contents = list(contents)
    found = {}
    for name in PUBLIC_M2M_FIELDS:
        model = ServiceSubmission._meta.get_field(name).related_model
        wanted = set()
        for content in contents:
            wanted |= _canonical_refs(model, content.get(name, ()))
        found[name] = set(_existing(model, wanted))
    return found


def _apply(ps: PublishedService, content: dict) -> None:
    """Copy revision content into the read model (row saved by the caller)."""
    content = complete_content(content)
    for name in PUBLIC_SCALAR_FIELDS:
        setattr(ps, name, content[name])
    for name in PUBLIC_FK_FIELDS:
        setattr(ps, f"{name}_id", content.get(name))


def _apply_m2m(ps: PublishedService, content: dict) -> None:
    """Link the reference rows named in the content; rows deleted since are skipped."""
    for name in PUBLIC_M2M_FIELDS:
        manager = getattr(ps, name)
        manager.set(_existing(manager.model, content.get(name, [])))


def _revision_content(revision: PublishedRevision) -> dict:
    if revision.schema_version > PublishedRevision.SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported revision schema version {revision.schema_version}"
        )
    return revision.content


def _latest_revision(submission_id):
    return (
        PublishedRevision.objects.filter(submission_id=submission_id)
        .order_by("-number")
        .first()
    )


def publish(submission, *, reason: str, actor: str, public_modified_at=None):
    """Write a new revision when the public content changed; return it or None.

    Reads the submission row from the database (not the passed instance) under
    a row lock, so concurrent writers publish one after the other and the
    revision always matches what is stored. ``public_modified_at`` replaces the
    current time as the copy's last change (used when a copy is created for a
    service that was changed earlier); the revision keeps that date, so a
    rebuilt copy has it too.
    """
    with transaction.atomic():
        row = ServiceSubmission.objects.select_for_update().get(pk=submission.pk)
        content = build_content(row)
        digest = content_hash(content)
        latest = _latest_revision(row.pk)
        ps = PublishedService.objects.filter(pk=row.pk).first()
        if latest and latest.content_hash == digest:
            if ps is None:
                rebuild_from_revision(row.pk)
            return None
        modified_at = public_modified_at or timezone.now()
        revision = PublishedRevision.objects.create(
            submission_id=row.pk,
            number=(latest.number + 1) if latest else 1,
            public_modified_at=modified_at,
            reason=reason,
            actor=actor,
            content=content,
            content_hash=digest,
        )
        if ps is None:
            ps = PublishedService(
                submission_id=row.pk, first_published_at=first_approved_at(row.pk)
            )
        ps.revision = revision
        ps.public_modified_at = modified_at
        _apply(ps, content)
        ps.save()
        _apply_m2m(ps, content)
        return revision


def republish(submission, *, reason: str, actor: str):
    """Republish an already published service that is currently approved."""
    with transaction.atomic():
        status = (
            ServiceSubmission.objects.select_for_update()
            .values_list("status", flat=True)
            .get(pk=submission.pk)
        )
        if status != SubmissionStatus.APPROVED:
            return None
        if not PublishedService.objects.filter(pk=submission.pk).exists():
            return None
        return publish(submission, reason=reason, actor=actor)


def set_publication_state(
    submission_id, *, state=None, pending_update=None, public_modified_at=None
) -> None:
    """Update state / pending flag of a published service without new content.

    ``public_modified_at`` only moves when the state changes: to the current
    time, or to ``public_modified_at`` when given.
    """
    with transaction.atomic():
        # Same lock as publish(), which writes the whole row: without it a
        # publish could write back the state it read before this change.
        ServiceSubmission.objects.select_for_update().filter(pk=submission_id).exists()
        ps = PublishedService.objects.filter(pk=submission_id).first()
        if ps is None:
            return
        fields = []
        if state is not None and state != ps.state:
            ps.state = state
            ps.public_modified_at = public_modified_at or timezone.now()
            fields += ["state", "public_modified_at"]
        if pending_update is not None and pending_update != ps.pending_update:
            ps.pending_update = pending_update
            fields.append("pending_update")
        if fields:
            ps.save(update_fields=fields)


def expected_flags(status, latest_decision) -> tuple[str, bool]:
    """(state, pending_update) a published copy has while the working copy is
    in ``status``. ``latest_decision`` is the status of the latest approval or
    deprecation event (see selectors.latest_public_decisions): leaving the
    deprecated state for review or rejection keeps the copy deprecated."""
    state = (
        PublishedService.DEPRECATED
        if latest_decision == SubmissionStatus.DEPRECATED
        else PublishedService.ACTIVE
    )
    pending = status in (SubmissionStatus.SUBMITTED, SubmissionStatus.UNDER_REVIEW)
    return state, pending


def on_status_change(submission, to_status, *, actor: str) -> None:
    """Apply one status transition to the published copy.

    - approved: publish the working copy; the service is active and nothing is
      waiting for review.
    - submitted / under review: the last approved version stays public and an
      update is marked as being reviewed.
    - deprecated: a published service becomes deprecated; one that was never
      published stays private.
    - rejected / draft: the last approved version stays public; nothing is
      waiting for review any more.
    """
    if to_status == SubmissionStatus.APPROVED:
        publish(submission, reason="approval", actor=actor)
        set_publication_state(
            submission.pk, state=PublishedService.ACTIVE, pending_update=False
        )
    elif to_status in (SubmissionStatus.SUBMITTED, SubmissionStatus.UNDER_REVIEW):
        set_publication_state(submission.pk, pending_update=True)
    elif to_status == SubmissionStatus.DEPRECATED:
        set_publication_state(
            submission.pk, state=PublishedService.DEPRECATED, pending_update=False
        )
    elif to_status in (SubmissionStatus.REJECTED, SubmissionStatus.DRAFT):
        set_publication_state(submission.pk, pending_update=False)


def apply_caught_up_status(submission_id) -> None:
    """Apply status changes that were recorded late to the published copy.

    Status changes written without an event (for example by an earlier
    release) are recorded as events when the next change finds them. The copy
    follows the latest approval or deprecation decision in the history the way
    it would have on time: approved makes it active, deprecated makes it
    deprecated. Only the state moves. Content is never published here: the
    working copy may already hold an edit that nobody reviewed. Nothing
    happens without a decision in the history or without a published copy.
    """
    decision = latest_public_decisions([submission_id]).get(submission_id)
    if decision == SubmissionStatus.APPROVED:
        set_publication_state(submission_id, state=PublishedService.ACTIVE)
    elif decision == SubmissionStatus.DEPRECATED:
        set_publication_state(submission_id, state=PublishedService.DEPRECATED)


def rebuild_from_revision(submission_id) -> PublishedService:
    """Recreate the read model from the latest revision (never the working copy)."""
    with transaction.atomic():
        # Same lock as publish(): a publish running at the same time cannot
        # leave the read model pointing at an older revision.
        ServiceSubmission.objects.select_for_update().filter(pk=submission_id).exists()
        revision = _latest_revision(submission_id)
        if revision is None:
            raise PublishedRevision.DoesNotExist(str(submission_id))
        content = _revision_content(revision)
        ps = PublishedService.objects.filter(pk=submission_id).first()
        if ps is None:
            # The flags are not part of a revision; derive them from the
            # current status and the status history. The date is the one the
            # copy got with this revision; a later state change moved it, and
            # a rebuild does not restore that.
            status = ServiceSubmission.objects.values_list("status", flat=True).get(
                pk=submission_id
            )
            decision = latest_public_decisions([submission_id]).get(submission_id)
            state, pending = expected_flags(status, decision)
            ps = PublishedService(
                submission_id=submission_id,
                first_published_at=first_approved_at(submission_id),
                public_modified_at=revision.public_modified_at,
                state=state,
                pending_update=pending,
            )
        ps.revision = revision
        _apply(ps, content)
        ps.save()
        _apply_m2m(ps, content)
        return ps


def restorable_content(submission, content: dict, existing=None) -> dict:
    """What restoring ``content`` writes into the working copy of ``submission``.

    The result holds exactly the current public fields, so a version that
    still has a field which is no longer public compares like any other. A
    field the version does not have (it was written before the field became
    public) keeps the value the working copy holds, for every kind of field:
    there is nothing published to restore, and the read model's default
    (empty, NULL) would blank a required value or leave a required relation
    empty. The text fields that a save cleans are cleaned the same way, so
    the result equals what the working copy holds after the restore and its
    save. The maturity tag is only taken from the version on an approved
    service: tags are not valid on any other status, so the working copy keeps
    its own. Links to reference rows deleted since the version was written
    are left out, as the restore leaves them out; ``existing`` is the result
    of ``existing_references`` for this content when the caller has already
    looked the rows up for many versions at once.
    """
    if existing is None:
        existing = existing_references([content])
    restored = {
        name: value
        for name, value in complete_content(content).items()
        if name in ALL_PUBLIC_FIELDS
    }
    for name in PUBLIC_SCALAR_FIELDS:
        if name not in content:
            restored[name] = _plain(getattr(submission, name))
    for name in PUBLIC_FK_FIELDS:
        if name not in content:
            restored[name] = _ref(getattr(submission, f"{name}_id"))
    for name in PUBLIC_M2M_FIELDS:
        if name not in content:
            restored[name] = _m2m_refs(submission, name)
    clean_text_fields(restored)
    for name in PUBLIC_M2M_FIELDS:
        if name in content:
            model = ServiceSubmission._meta.get_field(name).related_model
            kept = _canonical_refs(model, restored[name]) & existing[name]
            restored[name] = sorted(kept)
    if submission.status != SubmissionStatus.APPROVED:
        restored["primary_maturity_tag"] = submission.primary_maturity_tag
    return restored


def apply_content_to_working_copy(submission, content: dict) -> None:
    """Copy revision content back into the working copy (restore action)."""
    restored = restorable_content(submission, content)
    for name in PUBLIC_SCALAR_FIELDS:
        setattr(submission, name, restored[name])
    for name in PUBLIC_FK_FIELDS:
        setattr(submission, f"{name}_id", restored.get(name))
    submission.save()
    for name in PUBLIC_M2M_FIELDS:
        getattr(submission, name).set(restored[name])
