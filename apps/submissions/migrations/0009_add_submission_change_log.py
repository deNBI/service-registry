from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0008_add_last_change_summary"),
    ]

    operations = [
        migrations.CreateModel(
            name="SubmissionChangeLog",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "changed_by",
                    models.CharField(
                        max_length=200,
                        help_text=(
                            'Who made this change. Format: "submitter", "admin:<username>", '
                            'or "api:<key_label>".'
                        ),
                    ),
                ),
                ("changed_at", models.DateTimeField()),
                (
                    "changes",
                    models.JSONField(
                        help_text="List of changed fields: [{field, label, old, new}, ...]",
                    ),
                ),
                (
                    "submission",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="change_log",
                        to="submissions.servicesubmission",
                    ),
                ),
            ],
            options={
                "verbose_name": "Change Log Entry",
                "verbose_name_plural": "Change Log",
                "ordering": ["-changed_at"],
            },
        ),
    ]
