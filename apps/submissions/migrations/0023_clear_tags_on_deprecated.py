"""Clear maturity tags on deprecated services.

Deprecation clears tags, but rows deprecated before that rule, or edited
directly in the database, may still carry them. Each cleared service gets one
change-log entry, so the change shows in its Change History. Output is an
aggregate count only.
"""

from django.db import migrations
from django.db.models import F
from django.utils import timezone

# Frozen copies of the tag labels, so later label changes cannot alter the
# entries written here.
PRIMARY_LABELS = {"mature": "Mature", "emerging": "Emerging", "legacy": "Legacy"}
SECONDARY_LABELS = {"unstable": "Unstable"}
EMPTY = "\u2014"  # same placeholder the admin writes
SYSTEM = "system"


def _entries(primary, secondary):
    """Change-log entries in the format the admin writes for a cleared tag."""
    changes = []
    if primary:
        changes.append(
            {
                "field": "primary_maturity_tag",
                "label": "Primary Maturity Tag",
                "old": PRIMARY_LABELS.get(primary, primary),
                "new": EMPTY,
            }
        )
    if secondary:
        changes.append(
            {
                "field": "secondary_maturity_tags",
                "label": "Secondary Maturity Tags",
                "old": ", ".join(SECONDARY_LABELS.get(t, t) for t in secondary),
                "new": EMPTY,
            }
        )
    return changes


def clear_tags(apps, schema_editor):
    """Clear both tag fields of deprecated services that still hold tags.

    The version is raised so that edit forms and ETags issued for the old
    state are refused as stale. Rows without tags are not written.
    """
    Submission = apps.get_model("submissions", "ServiceSubmission")
    ChangeLog = apps.get_model("submissions", "SubmissionChangeLog")
    tagged = [
        (pk, primary, secondary)
        for pk, primary, secondary in Submission.objects.filter(
            status="deprecated"
        ).values_list("pk", "primary_maturity_tag", "secondary_maturity_tags")
        if primary or secondary
    ]
    if tagged:
        now = timezone.now()
        ChangeLog.objects.bulk_create(
            ChangeLog(
                submission_id=pk,
                changed_by=SYSTEM,
                changed_at=now,
                changes=_entries(primary, secondary or []),
            )
            for pk, primary, secondary in tagged
        )
        Submission.objects.filter(pk__in=[pk for pk, _, _ in tagged]).update(
            primary_maturity_tag=None,
            secondary_maturity_tags=[],
            version=F("version") + 1,
        )
        print(f"  cleared maturity tags on {len(tagged)} deprecated service(s)")


class Migration(migrations.Migration):
    dependencies = [("submissions", "0022_backfill_status_events")]
    operations = [migrations.RunPython(clear_tags, migrations.RunPython.noop)]
