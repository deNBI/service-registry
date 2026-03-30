# Generated manually — removes outreach_consent field (no longer required per product feedback)

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0005_add_logo_field"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="servicesubmission",
            name="outreach_consent",
        ),
    ]
