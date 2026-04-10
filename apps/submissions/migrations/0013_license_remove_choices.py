from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("submissions", "0012_add_maturity_tags"),
    ]

    operations = [
        migrations.AlterField(
            model_name="servicesubmission",
            name="license",
            field=models.CharField(
                help_text="License governing use of this service.",
                max_length=50,
            ),
        ),
    ]
