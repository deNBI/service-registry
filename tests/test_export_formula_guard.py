"""CSV export: cells that start like a spreadsheet formula get a leading '."""

import ast
import csv
import io
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.submissions.csv_utils import CSV_FORMULA_TRIGGERS, csv_safe
from tests.factories import ServiceSubmissionFactory


def test_prefixes_formula_chars():
    for ch in "=+-@":
        assert csv_safe(ch + "cmd") == "'" + ch + "cmd"


def test_prefixes_leading_tab_and_carriage_return():
    assert csv_safe("\tcmd") == "'\tcmd"
    assert csv_safe("\rcmd") == "'\rcmd"


def test_leaves_normal_text():
    assert csv_safe("normal") == "normal"
    assert csv_safe("") == ""
    assert csv_safe("a-b=c") == "a-b=c"


def test_handles_non_string():
    assert csv_safe(123) == 123
    assert csv_safe(-5) == -5
    assert csv_safe(True) is True
    assert csv_safe(None) is None


def test_triggers_constant():
    assert CSV_FORMULA_TRIGGERS == ("=", "+", "-", "@", "\t", "\r")


def _imported_modules(source: str) -> set[str]:
    """Dotted module names imported anywhere in `source` (module level or
    inside functions). `from pkg import name` yields both `pkg` and
    `pkg.name`, since `name` may itself be a submodule."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def test_imported_modules_sees_every_import_form():
    src = (
        "import apps.submissions.admin\n"
        "def f():\n"
        "    from apps.submissions import admin\n"
        "    from apps.submissions.admin import csv_safe\n"
    )
    assert _imported_modules(src) >= {
        "apps.submissions.admin",
        "apps.submissions",
        "apps.submissions.admin.csv_safe",
    }


def test_admin_and_audit_command_share_csv_utils():
    """The audit command gets csv_safe from csv_utils, not via the admin
    module (importing the admin from a management command drags in the whole
    admin site)."""
    from apps.submissions import admin
    from apps.submissions.management.commands import audit_markdown_descriptions

    assert admin.csv_safe is csv_safe
    assert audit_markdown_descriptions.csv_safe is csv_safe
    source = Path(audit_markdown_descriptions.__file__).read_text(encoding="utf-8")
    assert "apps.submissions.admin" not in _imported_modules(source)


@pytest.mark.django_db
def test_admin_csv_export_neutralises_formula_cells(client):
    user = get_user_model().objects.create_superuser(
        username="csvadmin", password="adminpass123", email="csv@example.com"
    )
    client.force_login(user)
    sub = ServiceSubmissionFactory(
        status="approved",
        service_name="=HYPERLINK(1)",
        service_description="- first bullet\n- second bullet",
        biotools_url="",
    )
    resp = client.post(
        reverse("admin:submissions_servicesubmission_changelist"),
        {"action": "action_export_csv", "_selected_action": [str(sub.pk)]},
    )
    assert resp.status_code == 200
    rows = list(csv.DictReader(io.StringIO(resp.content.decode("utf-8-sig"))))
    assert len(rows) == 1
    assert rows[0]["service_name"] == "'=HYPERLINK(1)"
    assert rows[0]["service_description"] == "'- first bullet\n- second bullet"
    assert rows[0]["status"] == "approved"
