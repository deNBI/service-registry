"""
Tests for SPDX license sync — covers:
  - Fresh import creates rows
  - Re-run updates in place (no duplicates)
  - Deprecated-upstream flag is honoured
  - Rows missing from upstream are swept to deprecated on next sync
  - Deprecated rows can re-appear (flipped back to active) if upstream reinstates them
  - Dry run performs no writes
  - Malformed JSON raises RuntimeError
  - Management command invocation
"""

import json
import tempfile
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

MINIMAL_SPDX = {
    "licenseListVersion": "3.26",
    "releaseDate": "2025-10-14",
    "licenses": [
        {
            "reference": "https://spdx.org/licenses/MIT.html",
            "isDeprecatedLicenseId": False,
            "name": "MIT License",
            "licenseId": "MIT",
            "seeAlso": ["https://opensource.org/license/mit/"],
            "isOsiApproved": True,
            "isFsfLibre": True,
        },
        {
            "reference": "https://spdx.org/licenses/Apache-2.0.html",
            "isDeprecatedLicenseId": False,
            "name": "Apache License 2.0",
            "licenseId": "Apache-2.0",
            "seeAlso": ["https://www.apache.org/licenses/LICENSE-2.0"],
            "isOsiApproved": True,
            "isFsfLibre": True,
        },
        {
            "reference": "https://spdx.org/licenses/GPL-1.0.html",
            "isDeprecatedLicenseId": True,
            "name": "GNU General Public License v1.0 only",
            "licenseId": "GPL-1.0",
            "seeAlso": [],
            "isOsiApproved": False,
        },
    ],
}


def _write_fixture(payload: dict) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(payload, f)
    f.flush()
    f.close()
    return f.name


# ---------------------------------------------------------------------------
# run_sync() unit tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRunSync:
    def test_fresh_import_creates_all_rows(self):
        from apps.licenses.models import SpdxLicense
        from apps.licenses.sync import run_sync

        path = _write_fixture(MINIMAL_SPDX)
        result = run_sync(url=path)

        assert result["created"] == 3
        assert result["updated"] == 0
        assert result["total"] == 3
        assert result["version"] == "3.26"
        assert SpdxLicense.objects.count() == 3

        mit = SpdxLicense.objects.get(license_id="MIT")
        assert mit.name == "MIT License"
        assert mit.reference_url == "https://spdx.org/licenses/MIT.html"
        assert mit.see_also == ["https://opensource.org/license/mit/"]
        assert mit.is_osi_approved is True
        assert mit.is_fsf_libre is True
        assert mit.is_deprecated is False
        assert mit.spdx_version == "3.26"

        gpl1 = SpdxLicense.objects.get(license_id="GPL-1.0")
        assert gpl1.is_deprecated is True
        assert gpl1.is_osi_approved is False

    def test_rerun_updates_in_place(self):
        from apps.licenses.models import SpdxLicense
        from apps.licenses.sync import run_sync

        path = _write_fixture(MINIMAL_SPDX)
        run_sync(url=path)

        # Second run with a changed name
        bumped = json.loads(json.dumps(MINIMAL_SPDX))
        bumped["licenses"][0]["name"] = "MIT (updated)"
        bumped["licenseListVersion"] = "3.27"
        path2 = _write_fixture(bumped)

        result = run_sync(url=path2)
        assert result["created"] == 0
        assert result["updated"] == 3
        assert SpdxLicense.objects.count() == 3
        assert SpdxLicense.objects.get(license_id="MIT").name == "MIT (updated)"
        assert SpdxLicense.objects.get(license_id="MIT").spdx_version == "3.27"

    def test_removed_upstream_marked_deprecated(self):
        from apps.licenses.models import SpdxLicense
        from apps.licenses.sync import run_sync

        path = _write_fixture(MINIMAL_SPDX)
        run_sync(url=path)

        # Drop MIT from upstream payload
        trimmed = json.loads(json.dumps(MINIMAL_SPDX))
        trimmed["licenses"] = [
            lic for lic in trimmed["licenses"] if lic["licenseId"] != "MIT"
        ]
        path2 = _write_fixture(trimmed)

        result = run_sync(url=path2)
        assert result["deprecated_swept"] == 1
        mit = SpdxLicense.objects.get(license_id="MIT")
        assert mit.is_deprecated is True

    def test_reinstated_license_becomes_active(self):
        """If upstream reinstates a license previously swept to deprecated,
        the next sync flips it back to active."""
        from apps.licenses.models import SpdxLicense
        from apps.licenses.sync import run_sync

        path = _write_fixture(MINIMAL_SPDX)
        run_sync(url=path)
        # Manually mark MIT deprecated to simulate a prior sweep
        SpdxLicense.objects.filter(license_id="MIT").update(is_deprecated=True)

        # Upstream still has MIT (isDeprecatedLicenseId=False) — sync should
        # flip is_deprecated back to False via update_or_create defaults
        run_sync(url=path)
        assert SpdxLicense.objects.get(license_id="MIT").is_deprecated is False

    def test_dry_run_writes_nothing(self):
        from apps.licenses.models import SpdxLicense
        from apps.licenses.sync import run_sync

        path = _write_fixture(MINIMAL_SPDX)
        result = run_sync(url=path, dry_run=True)

        assert result["total"] == 0
        assert SpdxLicense.objects.count() == 0

    def test_missing_licenses_key_raises(self):
        from apps.licenses.sync import run_sync

        path = _write_fixture({"licenseListVersion": "3.26"})
        with pytest.raises(RuntimeError, match="missing top-level 'licenses' array"):
            run_sync(url=path)

    def test_malformed_json_raises(self):
        from apps.licenses.sync import run_sync

        f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        f.write("{not valid json")
        f.flush()
        f.close()

        with pytest.raises(RuntimeError, match="Failed to parse SPDX licenses JSON"):
            run_sync(url=f.name)

    def test_blank_license_id_skipped(self):
        from apps.licenses.models import SpdxLicense
        from apps.licenses.sync import run_sync

        payload = json.loads(json.dumps(MINIMAL_SPDX))
        payload["licenses"].append({"licenseId": "", "name": "Empty"})
        payload["licenses"].append({"name": "No id at all"})
        path = _write_fixture(payload)

        run_sync(url=path)
        assert SpdxLicense.objects.count() == 3  # The two malformed rows ignored


# ---------------------------------------------------------------------------
# Management command
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSyncSpdxLicensesCommand:
    def test_command_dry_run(self):
        from apps.licenses.models import SpdxLicense

        path = _write_fixture(MINIMAL_SPDX)
        out = StringIO()
        call_command("sync_spdx_licenses", "--url", path, "--dry-run", stdout=out)

        assert SpdxLicense.objects.count() == 0
        assert "Dry run" in out.getvalue()

    def test_command_real_run(self):
        from apps.licenses.models import SpdxLicense

        path = _write_fixture(MINIMAL_SPDX)
        out = StringIO()
        call_command("sync_spdx_licenses", "--url", path, stdout=out)

        assert SpdxLicense.objects.count() == 3
        assert "SPDX license sync complete" in out.getvalue()

    def test_command_bad_url_raises_commanderror(self):
        out = StringIO()
        with pytest.raises(CommandError):
            call_command(
                "sync_spdx_licenses", "--url", "/nonexistent/licenses.json", stdout=out
            )
