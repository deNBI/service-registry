"""Create the public copy of services approved before the publishing layer.

Uses historical models and a frozen copy of the content building and of the
rules of the publish_backfill command, so later changes to the application
code cannot alter this migration (tests/test_publish_existing_migration.py
checks that both still give the same result).

- approved: published as active;
- deprecated: published as deprecated only if it went straight from approved
  to deprecated and no content edit was logged after that approval (after
  the event before it when the approval was reconstructed, because its date
  is not a real one);
- other deprecated services and services approved before but now waiting for
  review stay private and are only counted (publish_backfill lists the ids).

A deprecated service has no maturity tag in the working copy (deprecation
clears it, and migration submissions 0023 cleared older ones), so its copy has
none either.

Services that already have a public copy or a revision are left alone.
Rolling back keeps the copies; applying the migration again adds nothing.
Output is aggregate counts only.
"""

import hashlib
import json

from django.db import migrations

APPROVED = "approved"
DEPRECATED = "deprecated"
WAITING = ("submitted", "under_review")
SYSTEM = "system"

SCALAR_FIELDS = (
    "service_name",
    "service_description",
    "user_knowledge_required",
    "logo",
    "primary_maturity_tag",
    "host_institute",
    "year_established",
    "is_toolbox",
    "toolbox_name",
    "website_url",
    "terms_of_use_url",
    "license_note",
    "public_contact_email",
    "github_url",
    "biotools_url",
    "fairsharing_url",
    "other_registry_url",
    "keywords_seo",
    "publications_pmids",
)
FK_FIELDS = ("service_center",)
M2M_FIELDS = (
    "service_categories",
    "responsible_pis",
    "edam_topics",
    "edam_operations",
    "licenses",
)
# Change-log fields written together with a status change; not content edits.
STATUS_LOG_FIELDS = {"status", "primary_maturity_tag", "secondary_maturity_tags"}


def _plain(value):
    if hasattr(value, "name") and hasattr(value, "storage"):  # FieldFile
        return value.name or ""
    return value


def _ref(pk):
    return pk if isinstance(pk, int) else str(pk)


def _content(sub):
    content = {name: _plain(getattr(sub, name)) for name in SCALAR_FIELDS}
    for name in FK_FIELDS:
        content[name] = _ref(getattr(sub, f"{name}_id"))
    for name in M2M_FIELDS:
        pks = getattr(sub, name).values_list("pk", flat=True)
        content[name] = sorted(_ref(pk) for pk in pks)
    return content


def _hash(content):
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _may_edit_content(changes):
    """True when a change-log entry may hold a content edit. An entry that
    cannot be read counts as one, so the service stays private."""
    if not isinstance(changes, list):
        return True
    return any(
        not isinstance(change, dict) or change.get("field") not in STATUS_LOG_FIELDS
        for change in changes
    )


def _holds_approved_content(Event, ChangeLog, sub):
    """True when a deprecated service went straight from approved to
    deprecated and no content edit was logged after that approval."""
    events = list(
        Event.objects.filter(submission_id=sub.pk)
        .order_by("id")
        .values_list("to_status", "changed_at", "is_synthetic")
    )
    last_deprecation = max(
        (i for i, event in enumerate(events) if event[0] == DEPRECATED),
        default=None,
    )
    if not last_deprecation:  # never deprecated, or created deprecated
        return False
    to_status, approved_at, synthetic = events[last_deprecation - 1]
    if to_status != APPROVED:
        return False
    if synthetic and last_deprecation > 1:
        # The date of a reconstructed approval is not a real one: the service
        # may have been approved, and edited, before it. Edits since the event
        # ahead of it count.
        approved_at = events[last_deprecation - 2][1]
    later_entries = ChangeLog.objects.filter(
        submission_id=sub.pk, changed_at__gt=approved_at
    ).values_list("changes", flat=True)
    return not any(_may_edit_content(changes) for changes in later_entries)


def _decision(Event, ChangeLog, sub):
    if sub.status == APPROVED:
        return "published"
    approved = Event.objects.filter(submission_id=sub.pk, to_status=APPROVED)
    if not approved.exists():
        return "deprecated_never_approved"
    if _holds_approved_content(Event, ChangeLog, sub):
        return "published"
    return "deprecated_unconfirmed"


def _publish(Revision, Published, Event, sub):
    content = _content(sub)
    revision = Revision.objects.create(
        submission_id=sub.pk,
        number=1,
        public_modified_at=sub.updated_at,
        reason="backfill",
        actor=SYSTEM,
        schema_version=1,
        content=content,
        content_hash=_hash(content),
    )
    first_approval = (
        Event.objects.filter(
            submission_id=sub.pk, to_status=APPROVED, is_synthetic=False
        )
        .order_by("changed_at")
        .values_list("changed_at", flat=True)
        .first()
    )
    published = Published(
        submission_id=sub.pk,
        revision=revision,
        state=DEPRECATED if sub.status == DEPRECATED else "active",
        pending_update=False,
        public_modified_at=sub.updated_at,
        first_published_at=first_approval,
    )
    for name in SCALAR_FIELDS:
        setattr(published, name, content[name])
    for name in FK_FIELDS:
        setattr(published, f"{name}_id", content[name])
    published.save()
    for name in M2M_FIELDS:
        getattr(published, name).set(content[name])


def publish_existing(apps, schema_editor):
    Submission = apps.get_model("submissions", "ServiceSubmission")
    ChangeLog = apps.get_model("submissions", "SubmissionChangeLog")
    Event = apps.get_model("submissions", "SubmissionStatusEvent")
    Revision = apps.get_model("catalogue", "PublishedRevision")
    Published = apps.get_model("catalogue", "PublishedService")

    has_revisions = Revision.objects.values("submission_id")
    unpublished = Submission.objects.exclude(pk__in=has_revisions)
    counts = {
        "published": 0,
        "deprecated_never_approved": 0,
        "deprecated_unconfirmed": 0,
        "waiting_for_review": 0,
    }
    candidates = (
        unpublished.select_for_update()
        .filter(status__in=[APPROVED, DEPRECATED])
        .order_by("pk")
    )
    for sub in candidates:
        decision = _decision(Event, ChangeLog, sub)
        counts[decision] += 1
        if decision == "published":
            _publish(Revision, Published, Event, sub)
    counts["waiting_for_review"] = unpublished.filter(
        status__in=WAITING,
        pk__in=Event.objects.filter(to_status=APPROVED).values("submission_id"),
    ).count()
    if any(counts.values()):
        print(f"  publish existing services: {counts}")


class Migration(migrations.Migration):
    dependencies = [
        ("catalogue", "0001_initial"),
        ("submissions", "0023_clear_tags_on_deprecated"),
    ]
    operations = [migrations.RunPython(publish_existing, migrations.RunPython.noop)]
