"""Every model change has a migration (no pending ``makemigrations`` output)."""

import io

import pytest
from django.core.management import call_command


@pytest.mark.django_db
def test_no_missing_migrations():
    out = io.StringIO()
    try:
        call_command("makemigrations", "--check", "--dry-run", stdout=out, stderr=out)
    except SystemExit as exc:  # --check exits non-zero when changes are pending
        pytest.fail(f"Missing migrations (exit {exc.code}):\n{out.getvalue()}")
