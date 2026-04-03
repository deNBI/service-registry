"""
Migration: add custom permissions to ServiceSubmission
=======================================================
Adds two semantic permissions that are used by the group-based access
control system (see: manage.py setup_groups):

  approve_servicesubmission
      Gates the approve / reject status transitions in the admin.
      Separate from change_servicesubmission so editors can fix data
      without having final-decision authority.

  manage_apikeys
      Gates issue / reset / revoke of SubmissionAPIKey objects.
      Separate so auditors can view key metadata without creating
      credentials that grant submitters write access.

After applying this migration run:
    python manage.py setup_groups
to create (or refresh) the standard role groups.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0010_kpi_start_year_blank"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="servicesubmission",
            options={
                "ordering": ["-submitted_at"],
                "permissions": [
                    (
                        "approve_servicesubmission",
                        "Can approve or reject service submissions",
                    ),
                    (
                        "manage_apikeys",
                        "Can issue, reset, and revoke submission API keys",
                    ),
                ],
                "verbose_name": "Service Submission",
                "verbose_name_plural": "Service Submissions",
            },
        ),
    ]
