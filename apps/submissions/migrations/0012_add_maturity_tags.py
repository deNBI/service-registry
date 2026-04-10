# Generated migration for maturity tags
# Uses JSONField for compatibility across PostgreSQL and SQLite

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0011_servicesubmission_custom_permissions"),
    ]

    operations = [
        migrations.AddField(
            model_name="servicesubmission",
            name="primary_maturity_tag",
            field=models.CharField(
                blank=True,
                choices=[
                    ("mature", "Mature"),
                    ("emerging", "Emerging"),
                    ("legacy", "Legacy"),
                ],
                db_index=True,
                help_text="Primary maturity stage (Mature, Emerging, or Legacy). Only assignable to approved services.",
                max_length=20,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="servicesubmission",
            name="secondary_maturity_tags",
            field=models.JSONField(
                blank=True,
                null=True,
                default=list,
                help_text="Optional secondary tags (Unstable, etc.). Only assignable to approved services.",
            ),
        ),
    ]
