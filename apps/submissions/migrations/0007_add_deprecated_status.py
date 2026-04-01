from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0006_remove_outreach_consent"),
    ]

    operations = [
        migrations.AlterField(
            model_name="servicesubmission",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("submitted", "Submitted"),
                    ("under_review", "Under Review"),
                    ("approved", "Approved"),
                    ("rejected", "Rejected"),
                    ("deprecated", "Deprecated"),
                ],
                default="submitted",
                max_length=20,
            ),
        ),
    ]
