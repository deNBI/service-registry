# Generated migration to remove authtoken dependency
# This removes the authtoken_token table that's no longer needed
# after we standardize on AdminAPIKey for all authentication

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0001_admin_api_key"),
    ]

    operations = [
        # Drop the authtoken.Token table that's being replaced by AdminAPIKey
        # Use database-agnostic SQL that works with SQLite, PostgreSQL, etc.
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS authtoken_token;",
            reverse_sql="",  # Irreversible — we're not going back to Token auth
        ),
    ]
