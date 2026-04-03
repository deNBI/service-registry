"""
Migration: add last_change_summary JSONField to ServiceSubmission.

This field persists the most recent field-level diff so the admin can
always see what changed last, regardless of whether the edit came from
the submitter's edit form or the Django admin backend.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0007_add_deprecated_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="servicesubmission",
            name="last_change_summary",
            field=models.JSONField(
                blank=True,
                null=True,
                help_text=(
                    "Structured record of the most recent field-level change. "
                    "Written by EditView (submitter edits) and ServiceSubmissionAdmin "
                    "(admin edits). Schema: "
                    '{"changed_by": "submitter|admin:<username>", '
                    '"changed_at": "<ISO-8601>", '
                    '"changes": [{"field":…, "label":…, "old":…, "new":…}]}'
                ),
            ),
        ),
    ]
