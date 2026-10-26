"""
Tests for the biotools app: client, sync, tasks, signals, and views.

All HTTP calls to bio.tools are mocked — no network access is made.
"""

import json
import urllib.error
import urllib.request
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

from apps.biotools.client import (
    BioToolsClient,
    BioToolsError,
    BioToolsNotFound,
    BioToolsToolEntry,
)
from apps.biotools.sync import SyncResult, sync_tool
from apps.biotools.views import biotools_prefill, biotools_search

from .factories import ServiceSubmissionFactory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_RAW = {
    "biotoolsID": "blast",
    "name": "BLAST",
    "description": "Basic Local Alignment Search Tool",
    "homepage": "https://blast.ncbi.nlm.nih.gov",
}

FULL_RAW = {
    "biotoolsID": "blast",
    "name": "BLAST",
    "description": "Basic Local Alignment Search Tool",
    "homepage": "https://blast.ncbi.nlm.nih.gov",
    "version": ["2.14.0", "2.13.0"],
    "toolType": ["Command-line tool", "Web application"],
    "operatingSystem": ["Linux", "Mac", "Windows"],
    "license": "Public domain",
    "maturity": "Mature",
    "cost": "Free of charge",
    "topic": [
        {"uri": "http://edamontology.org/topic_0080", "term": "Sequence analysis"},
        {
            "uri": "http://edamontology.org/topic_0160",
            "term": "Sequence sites, features and motifs",
        },
    ],
    "function": [
        {
            "operation": [
                {
                    "uri": "http://edamontology.org/operation_0346",
                    "term": "Sequence similarity search",
                }
            ],
            "input": [
                {
                    "data": {
                        "uri": "http://edamontology.org/data_2044",
                        "term": "Sequence",
                    },
                    "format": [
                        {"uri": "http://edamontology.org/format_1929", "term": "FASTA"}
                    ],
                }
            ],
            "output": [
                {
                    "data": {
                        "uri": "http://edamontology.org/data_0857",
                        "term": "Sequence search results",
                    },
                    "format": [],
                }
            ],
            "cmd": "blastp -query input.fa",
            "note": "Protein BLAST",
        }
    ],
    "publication": [
        {
            "pmid": "2231712",
            "doi": "10.1126/science.1990",
            "pmcid": "",
            "type": "Primary",
            "note": "",
        },
        {
            "pmid": "",
            "doi": "10.1093/nar/gkn723",
            "pmcid": "",
            "type": "Other",
            "note": "",
        },
    ],
    "documentation": [
        {"url": "https://blast.ncbi.nlm.nih.gov/doc/blast-help/", "type": "General"}
    ],
    "download": [
        {
            "url": "https://github.com/ncbi/blast/releases",
            "type": "Source code",
            "version": "2.14.0",
        }
    ],
    "link": [{"url": "https://github.com/ncbi/blast", "type": "Repository"}],
}


def _mock_response(data: dict, status: int = 200) -> MagicMock:
    """Return a mock urllib response context manager."""
    body = json.dumps(data).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = body
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://bio.tools/api/tool/xyz",
        code=code,
        msg="Not Found" if code == 404 else "Error",
        hdrs={},
        fp=BytesIO(b""),
    )


# ---------------------------------------------------------------------------
# BioToolsClient._parse_tool
# ---------------------------------------------------------------------------


class TestBioToolsClientParseTool:
    def test_minimal_raw(self):
        entry = BioToolsClient._parse_tool(MINIMAL_RAW)
        assert isinstance(entry, BioToolsToolEntry)
        assert entry.biotools_id == "blast"
        assert entry.name == "BLAST"
        assert entry.description == "Basic Local Alignment Search Tool"
        assert entry.homepage == "https://blast.ncbi.nlm.nih.gov"
        assert entry.version == []
        assert entry.edam_topics == []
        assert entry.functions == []
        assert entry.publications == []

    def test_full_raw(self):
        entry = BioToolsClient._parse_tool(FULL_RAW)
        assert entry.biotools_id == "blast"
        assert entry.version == ["2.14.0", "2.13.0"]
        assert entry.tool_type == ["Command-line tool", "Web application"]
        assert entry.license == "Public domain"
        assert entry.maturity == "Mature"
        assert entry.cost == "Free of charge"

    def test_edam_topics_extracted(self):
        entry = BioToolsClient._parse_tool(FULL_RAW)
        assert len(entry.edam_topics) == 2
        assert entry.edam_topics[0]["uri"] == "http://edamontology.org/topic_0080"
        assert entry.edam_topics[0]["term"] == "Sequence analysis"

    def test_functions_parsed(self):
        entry = BioToolsClient._parse_tool(FULL_RAW)
        assert len(entry.functions) == 1
        func = entry.functions[0]
        assert len(func["operations"]) == 1
        assert func["operations"][0]["term"] == "Sequence similarity search"
        assert func["cmd"] == "blastp -query input.fa"
        assert func["note"] == "Protein BLAST"
        assert len(func["inputs"]) == 1
        assert len(func["outputs"]) == 1

    def test_publications_extracted(self):
        entry = BioToolsClient._parse_tool(FULL_RAW)
        assert len(entry.publications) == 2
        assert entry.publications[0]["pmid"] == "2231712"
        assert entry.publications[1]["doi"] == "10.1093/nar/gkn723"

    def test_links_extracted(self):
        entry = BioToolsClient._parse_tool(FULL_RAW)
        assert len(entry.links) == 1
        assert "github.com" in entry.links[0]["url"]

    def test_download_extracted(self):
        entry = BioToolsClient._parse_tool(FULL_RAW)
        assert len(entry.download) == 1
        assert entry.download[0]["type"] == "Source code"

    def test_topics_without_uri_skipped(self):
        raw = {
            **MINIMAL_RAW,
            "topic": [
                {"uri": "", "term": "Empty"},
                {"uri": "http://edamontology.org/topic_0080", "term": "Valid"},
            ],
        }
        entry = BioToolsClient._parse_tool(raw)
        assert len(entry.edam_topics) == 1

    def test_biotoolsid_falls_back_to_name(self):
        raw = {"name": "mytool", "description": "d", "homepage": "https://example.com"}
        entry = BioToolsClient._parse_tool(raw)
        assert entry.biotools_id == "mytool"


# ---------------------------------------------------------------------------
# BioToolsClient._get / get_tool / search_by_name
# ---------------------------------------------------------------------------


class TestBioToolsClientHTTP:
    def test_get_tool_success(self):
        with patch("urllib.request.urlopen", return_value=_mock_response(FULL_RAW)):
            client = BioToolsClient()
            tool = client.get_tool("blast")
        assert tool.name == "BLAST"
        assert tool.biotools_id == "blast"

    def test_get_tool_404_raises_not_found(self):
        with patch("urllib.request.urlopen", side_effect=_http_error(404)):
            client = BioToolsClient()
            with pytest.raises(BioToolsNotFound):
                client.get_tool("nonexistent")

    def test_get_tool_http_error_raises_biotools_error(self):
        with patch("urllib.request.urlopen", side_effect=_http_error(500)):
            client = BioToolsClient()
            with pytest.raises(BioToolsError) as exc_info:
                client.get_tool("blast")
            assert exc_info.value.status_code == 500

    def test_get_tool_network_error(self):
        with patch(
            "urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")
        ):
            client = BioToolsClient()
            with pytest.raises(BioToolsError, match="network error"):
                client.get_tool("blast")

    def test_get_tool_invalid_json(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json {"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            client = BioToolsClient()
            with pytest.raises(BioToolsError, match="invalid JSON"):
                client.get_tool("blast")

    def test_search_by_name_success(self):
        search_response = {"list": [FULL_RAW, MINIMAL_RAW]}
        with patch(
            "urllib.request.urlopen", return_value=_mock_response(search_response)
        ):
            client = BioToolsClient()
            results = client.search_by_name("blast")
        assert len(results) == 2
        assert results[0].name == "BLAST"

    def test_search_by_name_empty_list(self):
        with patch("urllib.request.urlopen", return_value=_mock_response({"list": []})):
            client = BioToolsClient()
            results = client.search_by_name("zzz")
        assert results == []

    def test_search_by_name_skips_bad_entries(self):
        # One bad entry (will raise during parse) + one good
        bad_entry = None  # will cause AttributeError when accessed
        search_response = {"list": [bad_entry, MINIMAL_RAW]}
        with patch(
            "urllib.request.urlopen", return_value=_mock_response(search_response)
        ):
            client = BioToolsClient()
            # Should skip the bad entry without raising
            results = client.search_by_name("blast")
        # Only the good one should be returned
        assert len(results) == 1

    def test_user_agent_set(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["user_agent"] = req.headers.get("User-agent")
            return _mock_response(FULL_RAW)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            client = BioToolsClient(user_agent="test-agent/1.0")
            client.get_tool("blast")

        assert captured["user_agent"] == "test-agent/1.0"

    def test_params_encoded_in_url(self):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            return _mock_response(FULL_RAW)

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            client = BioToolsClient()
            client.get_tool("blast")

        assert "format=json" in captured["url"]
        assert "blast" in captured["url"]


# ---------------------------------------------------------------------------
# sync_tool
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSyncTool:
    def _mock_client(self, tool_raw=None):
        """Patch BioToolsClient.get_tool to return a parsed entry."""
        raw = tool_raw or FULL_RAW
        entry = BioToolsClient._parse_tool(raw)
        mock = MagicMock()
        mock.get_tool.return_value = entry
        return mock

    def test_creates_new_record(self):
        # Create submission WITHOUT biotools_url to avoid signal triggering sync
        submission = ServiceSubmissionFactory(biotools_url="")
        with patch(
            "apps.biotools.sync.BioToolsClient", return_value=self._mock_client()
        ):
            result = sync_tool("blast", submission_id=str(submission.pk))

        assert result.ok is True
        assert result.created is True
        assert result.error == ""
        assert result.biotools_id == "blast"

        from apps.biotools.models import BioToolsRecord

        record = BioToolsRecord.objects.get(submission=submission)
        assert record.name == "BLAST"
        assert record.license == "Public domain"

    def test_updates_existing_record(self):
        from apps.biotools.models import BioToolsRecord

        # Create submission without URL to avoid duplicate record from signal
        submission = ServiceSubmissionFactory(biotools_url="")
        # Create the record first
        with patch(
            "apps.biotools.sync.BioToolsClient", return_value=self._mock_client()
        ):
            sync_tool("blast", submission_id=str(submission.pk))

        # Now call again — should update, not create
        updated_raw = {**FULL_RAW, "name": "BLAST Updated"}
        with patch(
            "apps.biotools.sync.BioToolsClient",
            return_value=self._mock_client(updated_raw),
        ):
            result = sync_tool("blast", submission_id=str(submission.pk))

        assert result.ok is True
        assert result.created is False
        record = BioToolsRecord.objects.get(submission=submission)
        assert record.name == "BLAST Updated"

    def test_not_found_returns_error_result(self):
        submission = ServiceSubmissionFactory(biotools_url="")
        mock = MagicMock()
        mock.get_tool.side_effect = BioToolsNotFound("not found", 404)
        with patch("apps.biotools.sync.BioToolsClient", return_value=mock):
            result = sync_tool("xyz", submission_id=str(submission.pk))

        assert result.ok is False
        assert "not found" in result.error.lower()

    def test_api_error_returns_error_result(self):
        submission = ServiceSubmissionFactory(biotools_url="")
        mock = MagicMock()
        mock.get_tool.side_effect = BioToolsError("server error", 500)
        with patch("apps.biotools.sync.BioToolsClient", return_value=mock):
            result = sync_tool("blast", submission_id=str(submission.pk))

        assert result.ok is False
        assert "server error" in result.error

    def test_no_submission_id_and_no_record_returns_error(self):
        mock = MagicMock()
        mock.get_tool.return_value = BioToolsClient._parse_tool(FULL_RAW)
        with patch("apps.biotools.sync.BioToolsClient", return_value=mock):
            result = sync_tool("blast", submission_id=None)

        assert result.ok is False
        assert "no submission_id" in result.error

    def test_nonexistent_submission_id_returns_error(self):
        import uuid

        fake_id = str(uuid.uuid4())
        mock = MagicMock()
        mock.get_tool.return_value = BioToolsClient._parse_tool(FULL_RAW)
        with patch("apps.biotools.sync.BioToolsClient", return_value=mock):
            result = sync_tool("blast", submission_id=fake_id)

        assert result.ok is False
        assert "not found" in result.error

    def test_functions_created(self):
        from apps.biotools.models import BioToolsFunction

        submission = ServiceSubmissionFactory(biotools_url="")
        with patch(
            "apps.biotools.sync.BioToolsClient", return_value=self._mock_client()
        ):
            result = sync_tool("blast", submission_id=str(submission.pk))

        assert result.ok is True
        functions = BioToolsFunction.objects.filter(record__submission=submission)
        assert functions.count() == 1
        func = functions.first()
        assert func.position == 0
        assert len(func.operations) == 1

    def test_functions_rebuilt_on_update(self):
        from apps.biotools.models import BioToolsFunction

        submission = ServiceSubmissionFactory(biotools_url="")
        with patch(
            "apps.biotools.sync.BioToolsClient", return_value=self._mock_client()
        ):
            sync_tool("blast", submission_id=str(submission.pk))

        # Second sync — functions should be deleted and recreated
        with patch(
            "apps.biotools.sync.BioToolsClient", return_value=self._mock_client()
        ):
            sync_tool("blast", submission_id=str(submission.pk))

        assert (
            BioToolsFunction.objects.filter(record__submission=submission).count() == 1
        )

    def test_sync_marks_success(self):
        from apps.biotools.models import BioToolsRecord

        submission = ServiceSubmissionFactory(biotools_url="")
        with patch(
            "apps.biotools.sync.BioToolsClient", return_value=self._mock_client()
        ):
            sync_tool("blast", submission_id=str(submission.pk))

        record = BioToolsRecord.objects.get(submission=submission)
        assert record.sync_ok is True
        assert record.sync_error == ""
        assert record.last_synced_at is not None

    def test_not_found_marks_sync_error_on_existing_record(self):
        from apps.biotools.models import BioToolsRecord

        submission = ServiceSubmissionFactory(biotools_url="")
        # Create record first
        with patch(
            "apps.biotools.sync.BioToolsClient", return_value=self._mock_client()
        ):
            sync_tool("blast", submission_id=str(submission.pk))

        # Now simulate 404
        mock = MagicMock()
        mock.get_tool.side_effect = BioToolsNotFound("not found", 404)
        with patch("apps.biotools.sync.BioToolsClient", return_value=mock):
            result = sync_tool("blast", submission_id=str(submission.pk))

        assert result.ok is False
        record = BioToolsRecord.objects.get(submission=submission)
        assert record.sync_error != ""


# ---------------------------------------------------------------------------
# Celery tasks
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSyncBioToolsTask:
    def test_submission_not_found(self):
        import uuid
        from apps.biotools.tasks import sync_biotools_record

        result = sync_biotools_record(str(uuid.uuid4()))
        assert result["ok"] is False
        assert "not found" in result["error"]

    def test_no_biotools_url(self):
        from apps.biotools.tasks import sync_biotools_record

        submission = ServiceSubmissionFactory(biotools_url="")
        result = sync_biotools_record(str(submission.pk))
        assert result["ok"] is False
        assert "no bio.tools URL" in result["error"]

    def test_extracts_id_from_url(self):
        from apps.biotools.tasks import sync_biotools_record

        # Use empty URL so signal doesn't pre-run; set it directly on the model
        from apps.submissions.models import ServiceSubmission

        submission = ServiceSubmissionFactory(biotools_url="")
        ServiceSubmission.objects.filter(pk=submission.pk).update(
            biotools_url="https://bio.tools/blast"
        )
        submission.refresh_from_db()

        expected = SyncResult(ok=True, biotools_id="blast", created=True, error="")
        with patch("apps.biotools.sync.sync_tool", return_value=expected) as mock_sync:
            sync_biotools_record(str(submission.pk))

        mock_sync.assert_called_once_with(
            biotools_id="blast",
            submission_id=str(submission.pk),
        )

    def test_trailing_slash_stripped_from_url(self):
        from apps.biotools.tasks import sync_biotools_record
        from apps.submissions.models import ServiceSubmission

        submission = ServiceSubmissionFactory(biotools_url="")
        ServiceSubmission.objects.filter(pk=submission.pk).update(
            biotools_url="https://bio.tools/blast/"
        )
        submission.refresh_from_db()

        expected = SyncResult(ok=True, biotools_id="blast", created=True, error="")
        with patch("apps.biotools.sync.sync_tool", return_value=expected) as mock_sync:
            sync_biotools_record(str(submission.pk))

        mock_sync.assert_called_once_with(
            biotools_id="blast", submission_id=str(submission.pk)
        )

    def test_returns_sync_result_as_dict(self):
        from apps.biotools.tasks import sync_biotools_record
        from apps.submissions.models import ServiceSubmission

        submission = ServiceSubmissionFactory(biotools_url="")
        ServiceSubmission.objects.filter(pk=submission.pk).update(
            biotools_url="https://bio.tools/blast"
        )
        submission.refresh_from_db()

        expected = SyncResult(ok=True, biotools_id="blast", created=True, error="")
        with patch("apps.biotools.sync.sync_tool", return_value=expected):
            result = sync_biotools_record(str(submission.pk))
        assert result == {
            "ok": True,
            "biotools_id": "blast",
            "created": True,
            "error": "",
        }


def _entry_for(biotools_id):
    """A parsed bio.tools entry whose id and name follow the requested id."""
    raw = {**FULL_RAW, "biotoolsID": biotools_id, "name": biotools_id.upper()}
    return BioToolsClient._parse_tool(raw)


@pytest.mark.django_db
class TestSyncAllBioToolsTask:
    def test_empty_records_completes(self):
        from apps.biotools.tasks import sync_all_biotools_records

        # No records — should complete without error
        sync_all_biotools_records()

    def test_syncs_all_records(self):
        from apps.biotools.tasks import sync_all_biotools_records
        from apps.biotools.models import BioToolsRecord

        # Create with an empty URL so the signal does not sync, then set the
        # URLs directly (bypassing the signal).
        sub1 = ServiceSubmissionFactory(biotools_url="")
        sub2 = ServiceSubmissionFactory(biotools_url="")
        for sub, bid in [(sub1, "blast"), (sub2, "interproscan")]:
            BioToolsRecord.objects.create(
                submission=sub, biotools_id=bid, name=bid, raw_json={}
            )
            type(sub).objects.filter(pk=sub.pk).update(
                biotools_url=f"https://bio.tools/{bid}"
            )

        ok_result = SyncResult(ok=True, biotools_id="x", created=False, error="")
        err_result = SyncResult(
            ok=False, biotools_id="x", created=False, error="API error"
        )

        # One failing sync does not stop the loop.
        with patch(
            "apps.biotools.sync.sync_tool", side_effect=[ok_result, err_result]
        ) as mock_sync:
            sync_all_biotools_records()

        synced = sorted(c.kwargs["biotools_id"] for c in mock_sync.call_args_list)
        assert synced == ["blast", "interproscan"]

    def test_follows_changed_url_instead_of_stored_id(self):
        """A record created for one tool is refreshed from the URL the
        submission has now, not from the id stored on the record."""
        from apps.biotools.models import BioToolsRecord
        from apps.biotools.tasks import sync_all_biotools_records

        sub = ServiceSubmissionFactory(biotools_url="")
        BioToolsRecord.objects.create(
            submission=sub, biotools_id="blast", name="BLAST", raw_json={}
        )
        type(sub).objects.filter(pk=sub.pk).update(
            biotools_url="https://bio.tools/interproscan"
        )

        with patch(
            "apps.biotools.client.BioToolsClient.get_tool", side_effect=_entry_for
        ) as mock_get:
            sync_all_biotools_records()

        mock_get.assert_called_once_with("interproscan")
        record = BioToolsRecord.objects.get(submission=sub)
        assert record.biotools_id == "interproscan"

    def test_creates_missing_record_for_submission_with_url(self):
        """A submission whose first sync never produced a record gets one
        from the daily sync."""
        from apps.biotools.models import BioToolsRecord
        from apps.biotools.tasks import sync_all_biotools_records

        sub = ServiceSubmissionFactory(biotools_url="")
        type(sub).objects.filter(pk=sub.pk).update(
            biotools_url="https://bio.tools/blast"
        )
        assert not BioToolsRecord.objects.filter(submission=sub).exists()

        with patch(
            "apps.biotools.client.BioToolsClient.get_tool", side_effect=_entry_for
        ):
            sync_all_biotools_records()

        assert BioToolsRecord.objects.get(submission=sub).biotools_id == "blast"

    def test_skips_url_without_biotools_id(self):
        from apps.biotools.tasks import sync_all_biotools_records

        sub = ServiceSubmissionFactory(biotools_url="")
        type(sub).objects.filter(pk=sub.pk).update(biotools_url="/")

        with patch("apps.biotools.sync.sync_tool") as mock_sync:
            sync_all_biotools_records()

        mock_sync.assert_not_called()

    def test_removes_record_of_submission_without_url(self):
        """A record whose submission no longer has a bio.tools URL (for example
        cleared by a bulk update that bypasses signals) is removed."""
        from apps.biotools.models import BioToolsRecord
        from apps.biotools.tasks import sync_all_biotools_records

        sub = ServiceSubmissionFactory(biotools_url="")
        BioToolsRecord.objects.create(
            submission=sub, biotools_id="blast", name="BLAST", raw_json={}
        )

        with patch("apps.biotools.client.BioToolsClient.get_tool") as mock_get:
            sync_all_biotools_records()

        mock_get.assert_not_called()
        assert not BioToolsRecord.objects.filter(submission=sub).exists()


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://bio.tools/blast", "blast"),
        ("https://bio.tools/blast/", "blast"),
        ("https://bio.tools/interproscan", "interproscan"),
        ("", ""),
        (None, ""),
    ],
)
def test_biotools_id_from_url(url, expected):
    from apps.biotools.sync import biotools_id_from_url

    assert biotools_id_from_url(url) == expected


@pytest.mark.django_db
def test_remove_records_without_url_keeps_records_of_submissions_with_url():
    from apps.biotools.models import BioToolsRecord
    from apps.biotools.sync import remove_records_without_url

    keep = ServiceSubmissionFactory(biotools_url="")
    stale = ServiceSubmissionFactory(biotools_url="")
    for sub, bid in [(keep, "blast"), (stale, "interproscan")]:
        BioToolsRecord.objects.create(
            submission=sub, biotools_id=bid, name=bid, raw_json={}
        )
    type(keep).objects.filter(pk=keep.pk).update(biotools_url="https://bio.tools/blast")

    assert remove_records_without_url() == 1
    assert BioToolsRecord.objects.filter(submission=keep).exists()
    assert not BioToolsRecord.objects.filter(submission=stale).exists()


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBioToolsSignal:
    """
    Tests for the post_save signal that triggers bio.tools sync.

    The sync is queued when the saving transaction commits; tests run inside
    a transaction, so they run the commit callbacks explicitly.
    CELERY_TASK_ALWAYS_EAGER=True in settings_test means apply_async runs
    synchronously. We mock the bio.tools API client to avoid real HTTP calls
    and check the observable side effect (BioToolsRecord creation).
    """

    def test_signal_creates_record_for_new_submission_with_url(
        self, django_capture_on_commit_callbacks
    ):
        """End-to-end: saving a submission with a biotools_url triggers sync."""
        from apps.biotools.models import BioToolsRecord

        entry = BioToolsClient._parse_tool(FULL_RAW)
        with (
            patch("apps.biotools.client.BioToolsClient.get_tool", return_value=entry),
            django_capture_on_commit_callbacks(execute=True),
        ):
            sub = ServiceSubmissionFactory(biotools_url="https://bio.tools/blast")

        assert BioToolsRecord.objects.filter(submission=sub).exists()
        record = BioToolsRecord.objects.get(submission=sub)
        assert record.name == "BLAST"

    def test_signal_skips_when_no_url(self, django_capture_on_commit_callbacks):
        """Submission without biotools_url queues no sync and creates no record."""
        from apps.biotools.models import BioToolsRecord

        with django_capture_on_commit_callbacks(execute=True) as callbacks:
            sub = ServiceSubmissionFactory(biotools_url="")
        assert callbacks == []
        assert not BioToolsRecord.objects.filter(submission=sub).exists()

    def test_signal_skips_update_when_url_unchanged_and_synced(
        self, django_capture_on_commit_callbacks
    ):
        """Resaving a submission with unchanged biotools_url and existing record skips re-sync."""
        from apps.biotools.models import BioToolsRecord

        entry = BioToolsClient._parse_tool(FULL_RAW)
        with (
            patch("apps.biotools.client.BioToolsClient.get_tool", return_value=entry),
            django_capture_on_commit_callbacks(execute=True),
        ):
            sub = ServiceSubmissionFactory(biotools_url="https://bio.tools/blast")

        assert BioToolsRecord.objects.filter(submission=sub).exists()

        # Resave with same URL: nothing is queued (record exists, URL unchanged)
        with (
            patch(
                "apps.biotools.client.BioToolsClient.get_tool", return_value=entry
            ) as mock_get,
            django_capture_on_commit_callbacks(execute=True) as callbacks,
        ):
            sub.save()
        assert callbacks == []
        mock_get.assert_not_called()

    def test_signal_syncs_again_when_the_record_is_missing(
        self, django_capture_on_commit_callbacks
    ):
        """Control for the skip above: with the same unchanged URL but no
        record, a resave does queue a sync."""
        from apps.biotools.models import BioToolsRecord

        entry = BioToolsClient._parse_tool(FULL_RAW)
        with (
            patch("apps.biotools.client.BioToolsClient.get_tool", return_value=entry),
            django_capture_on_commit_callbacks(execute=True),
        ):
            sub = ServiceSubmissionFactory(biotools_url="https://bio.tools/blast")
        BioToolsRecord.objects.filter(submission=sub).delete()

        with (
            patch(
                "apps.biotools.client.BioToolsClient.get_tool", return_value=entry
            ) as mock_get,
            django_capture_on_commit_callbacks(execute=True) as callbacks,
        ):
            sub.save()
        assert len(callbacks) == 1
        mock_get.assert_called_once()
        assert BioToolsRecord.objects.filter(submission=sub).exists()

    def test_sync_is_queued_only_when_the_save_commits(
        self, django_capture_on_commit_callbacks
    ):
        """The task reads the submission from the database, so it must not be
        queued before the saving transaction has committed."""
        with patch("apps.biotools.tasks.sync_biotools_record.apply_async") as queue:
            with django_capture_on_commit_callbacks() as callbacks:
                sub = ServiceSubmissionFactory(biotools_url="https://bio.tools/blast")
                queue.assert_not_called()
            assert len(callbacks) == 1
            callbacks[0]()
        queue.assert_called_once_with(args=[str(sub.pk)])

    def test_sync_is_not_queued_when_the_save_is_rolled_back(
        self, django_capture_on_commit_callbacks
    ):
        from django.db import transaction

        with patch("apps.biotools.tasks.sync_biotools_record.apply_async") as queue:
            with django_capture_on_commit_callbacks(execute=True) as callbacks:
                try:
                    with transaction.atomic():
                        ServiceSubmissionFactory(biotools_url="https://bio.tools/blast")
                        raise RuntimeError("the edit failed")
                except RuntimeError:
                    pass
        assert callbacks == []
        queue.assert_not_called()

    def test_signal_resyncs_when_url_changes_to_another_tool(
        self, django_capture_on_commit_callbacks
    ):
        """Saving a new bio.tools URL replaces the record of the old tool."""
        from apps.biotools.models import BioToolsRecord

        with patch(
            "apps.biotools.client.BioToolsClient.get_tool", side_effect=_entry_for
        ):
            with django_capture_on_commit_callbacks(execute=True):
                sub = ServiceSubmissionFactory(biotools_url="https://bio.tools/blast")
            assert BioToolsRecord.objects.get(submission=sub).biotools_id == "blast"

            sub.biotools_url = "https://bio.tools/interproscan"
            with django_capture_on_commit_callbacks(execute=True):
                sub.save()

        record = BioToolsRecord.objects.get(submission=sub)
        assert record.biotools_id == "interproscan"
        assert record.name == "INTERPROSCAN"

    def test_signal_removes_record_when_url_is_cleared(
        self, django_capture_on_commit_callbacks
    ):
        """Clearing the bio.tools URL removes the old record, so its data is
        no longer shown."""
        from apps.biotools.models import BioToolsRecord

        with (
            patch(
                "apps.biotools.client.BioToolsClient.get_tool", side_effect=_entry_for
            ),
            django_capture_on_commit_callbacks(execute=True),
        ):
            sub = ServiceSubmissionFactory(biotools_url="https://bio.tools/blast")
        assert BioToolsRecord.objects.filter(submission=sub).exists()

        sub.biotools_url = ""
        sub.save()

        assert not BioToolsRecord.objects.filter(submission=sub).exists()

    def test_ordinary_save_does_not_resync(self, django_capture_on_commit_callbacks):
        """A save that leaves the bio.tools URL as it is (e.g. a status change)
        does not call bio.tools again."""
        with patch(
            "apps.biotools.client.BioToolsClient.get_tool", side_effect=_entry_for
        ) as mock_get:
            with django_capture_on_commit_callbacks(execute=True):
                sub = ServiceSubmissionFactory(biotools_url="https://bio.tools/blast")
            assert mock_get.call_count == 1

            sub.status = "under_review"
            with django_capture_on_commit_callbacks(execute=True) as callbacks:
                sub.save(update_fields=["status", "updated_at"])

            assert callbacks == []

            assert mock_get.call_count == 1


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBioToolsPrefillView:
    rf = RequestFactory()

    def _get(self, params=""):
        request = self.rf.get(f"/biotools/prefill/{params}")
        return biotools_prefill(request)

    def test_no_id_returns_400(self):
        response = self._get()
        assert response.status_code == 400
        data = json.loads(response.content)
        assert data["found"] is False

    def test_success_returns_200(self):
        entry = BioToolsClient._parse_tool(FULL_RAW)
        with patch("apps.biotools.views.BioToolsClient") as MockClient:
            MockClient.return_value.get_tool.return_value = entry
            request = self.rf.get("/biotools/prefill/", {"id": "blast"})
            response = biotools_prefill(request)

        assert response.status_code == 200
        data = json.loads(response.content)
        assert data["found"] is True
        assert data["biotools_id"] == "blast"
        assert data["name"] == "BLAST"
        assert "description" in data
        assert "edam_topics" in data
        assert "edam_operations" in data

    def test_url_stripped_to_id(self):
        entry = BioToolsClient._parse_tool(MINIMAL_RAW)
        with patch("apps.biotools.views.BioToolsClient") as MockClient:
            MockClient.return_value.get_tool.return_value = entry
            request = self.rf.get(
                "/biotools/prefill/", {"id": "https://bio.tools/blast"}
            )
            response = biotools_prefill(request)
            # Should have called get_tool with just "blast"
            MockClient.return_value.get_tool.assert_called_once_with("blast")

        assert response.status_code == 200

    def test_not_found_returns_404(self):
        with patch("apps.biotools.views.BioToolsClient") as MockClient:
            MockClient.return_value.get_tool.side_effect = BioToolsNotFound(
                "not found", 404
            )
            request = self.rf.get("/biotools/prefill/", {"id": "xyz"})
            response = biotools_prefill(request)

        assert response.status_code == 404
        data = json.loads(response.content)
        assert data["found"] is False

    def test_api_error_returns_503(self):
        with patch("apps.biotools.views.BioToolsClient") as MockClient:
            MockClient.return_value.get_tool.side_effect = BioToolsError("timeout")
            request = self.rf.get("/biotools/prefill/", {"id": "blast"})
            response = biotools_prefill(request)

        assert response.status_code == 503
        data = json.loads(response.content)
        assert data["found"] is False

    def test_publications_extracted_as_comma_separated(self):
        entry = BioToolsClient._parse_tool(FULL_RAW)
        with patch("apps.biotools.views.BioToolsClient") as MockClient:
            MockClient.return_value.get_tool.return_value = entry
            request = self.rf.get("/biotools/prefill/", {"id": "blast"})
            response = biotools_prefill(request)

        data = json.loads(response.content)
        # First pub has pmid, second has doi (no pmid)
        assert "2231712" in data["publications"]

    def test_github_url_extracted_from_links(self):
        entry = BioToolsClient._parse_tool(FULL_RAW)
        with patch("apps.biotools.views.BioToolsClient") as MockClient:
            MockClient.return_value.get_tool.return_value = entry
            request = self.rf.get("/biotools/prefill/", {"id": "blast"})
            response = biotools_prefill(request)

        data = json.loads(response.content)
        assert "github.com" in data["github_url"]

    def test_post_not_allowed(self):
        request = self.rf.post("/biotools/prefill/")
        response = biotools_prefill(request)
        assert response.status_code == 405


class TestBioToolsSearchView:
    rf = RequestFactory()

    def test_short_query_returns_empty(self):
        request = self.rf.get("/biotools/search/", {"q": "b"})
        response = biotools_search(request)
        data = json.loads(response.content)
        assert data["results"] == []

    def test_no_query_returns_empty(self):
        request = self.rf.get("/biotools/search/")
        response = biotools_search(request)
        data = json.loads(response.content)
        assert data["results"] == []

    def test_success_returns_results(self):
        entries = [
            BioToolsClient._parse_tool(FULL_RAW),
            BioToolsClient._parse_tool(MINIMAL_RAW),
        ]
        with patch("apps.biotools.views.BioToolsClient") as MockClient:
            MockClient.return_value.search_by_name.return_value = entries
            request = self.rf.get("/biotools/search/", {"q": "blast"})
            response = biotools_search(request)

        data = json.loads(response.content)
        assert len(data["results"]) == 2
        assert data["results"][0]["biotools_id"] == "blast"
        assert "name" in data["results"][0]
        assert "description" in data["results"][0]

    def test_api_error_returns_empty(self):
        with patch("apps.biotools.views.BioToolsClient") as MockClient:
            MockClient.return_value.search_by_name.side_effect = BioToolsError(
                "timeout"
            )
            request = self.rf.get("/biotools/search/", {"q": "blast"})
            response = biotools_search(request)

        data = json.loads(response.content)
        assert data["results"] == []

    def test_long_description_truncated(self):
        long_desc = "x" * 300
        raw = {**MINIMAL_RAW, "description": long_desc}
        entry = BioToolsClient._parse_tool(raw)
        with patch("apps.biotools.views.BioToolsClient") as MockClient:
            MockClient.return_value.search_by_name.return_value = [entry]
            request = self.rf.get("/biotools/search/", {"q": "blast"})
            response = biotools_search(request)

        data = json.loads(response.content)
        assert len(data["results"][0]["description"]) <= 160  # 150 + ellipsis


# ---------------------------------------------------------------------------
# BioToolsRecord model helpers
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBioToolsRecordModel:
    def _make_record(self):
        from apps.biotools.models import BioToolsRecord

        # Use empty URL so signal doesn't create a record automatically
        sub = ServiceSubmissionFactory(biotools_url="")
        return BioToolsRecord.objects.create(
            submission=sub,
            biotools_id="blast",
            name="BLAST",
            raw_json={},
        )

    def test_biotools_url_property(self):
        record = self._make_record()
        assert record.biotools_url == "https://bio.tools/blast"

    def test_sync_ok_false_initially(self):
        record = self._make_record()
        assert record.sync_ok is False  # no last_synced_at yet

    def test_mark_sync_success(self):
        record = self._make_record()
        record.mark_sync_success()
        record.refresh_from_db()
        assert record.sync_ok is True
        assert record.sync_error == ""
        assert record.last_synced_at is not None

    def test_mark_sync_error(self):
        record = self._make_record()
        record.mark_sync_error("API returned 500")
        record.refresh_from_db()
        assert record.sync_error == "API returned 500"

    def test_str_repr(self):
        record = self._make_record()
        assert "blast" in str(record)
