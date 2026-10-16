"""apply_edit: the one place that stores an edit of a service.

It stores content and links in one transaction, works out what changed, resets
the status for an owner edit, publishes again while the service stays
approved, and writes the change log.
"""

import pytest

from apps.catalogue.models import PublishedService
from apps.submissions.edits import EditResult, apply_edit, capture
from apps.submissions.lifecycle import get_no_reset_fields, set_status
from apps.submissions.models import (
    EditConflict,
    ServiceSubmission,
    SubmissionChangeLog,
)
from tests.factories import ServiceCategoryFactory, ServiceSubmissionFactory

pytestmark = pytest.mark.django_db

OWNER = "submitter"
ADMIN = "admin:jane"


@pytest.fixture
def exempt(settings):
    settings.SUBMISSION_NO_RESET_FIELDS = ["github_url"]
    get_no_reset_fields.cache_clear()
    yield
    get_no_reset_fields.cache_clear()


@pytest.fixture
def no_exempt(settings):
    settings.SUBMISSION_NO_RESET_FIELDS = []
    get_no_reset_fields.cache_clear()
    yield
    get_no_reset_fields.cache_clear()


def _service(status="submitted"):
    sub = ServiceSubmissionFactory(biotools_url="", internal_contact_email="a@b.org")
    if status != "submitted":
        set_status(sub, status, actor=ADMIN)
    sub.refresh_from_db()
    return sub


def _edit(sub, *, by_owner=True, actor=OWNER, refine=None, **fields):
    """Apply ``fields`` to ``sub`` the way a view would: capture, then store."""
    before = capture(sub)

    def store():
        for name, value in fields.items():
            setattr(sub, name, value)
        sub.save()

    return apply_edit(
        sub,
        before=before,
        store=store,
        actor=actor,
        by_owner=by_owner,
        refine_changes=refine,
    )


def _transitions(sub):
    return [(e.from_status, e.to_status) for e in sub.status_events.order_by("id")]


class TestResult:
    def test_reports_the_changes(self, no_exempt):
        sub = _service()
        result = _edit(sub, comments="new comment")
        assert isinstance(result, EditResult)
        assert [c["field"] for c in result.changes] == ["comments"]
        assert result.status_reset is False

    def test_no_change_writes_nothing(self, no_exempt):
        sub = _service("approved")
        result = _edit(sub, comments=sub.comments)
        assert result.changes == []
        assert result.status_reset is False
        assert not SubmissionChangeLog.objects.filter(submission=sub).exists()
        sub.refresh_from_db()
        assert sub.last_change_summary is None
        assert sub.status == "approved"


class TestOwnerReset:
    def test_approved_goes_back_to_submitted_for_a_real_change(self, no_exempt):
        sub = _service("approved")
        result = _edit(sub, comments="new comment")
        sub.refresh_from_db()
        assert result.status_reset is True
        assert sub.status == "submitted"
        assert _transitions(sub)[-1] == ("approved", "submitted")
        assert sub.status_events.order_by("id").last().changed_by == OWNER

    def test_the_reset_is_part_of_the_logged_changes(self, no_exempt):
        sub = _service("approved")
        result = _edit(sub, comments="new comment")
        assert {c["field"] for c in result.changes} == {"comments", "status"}
        log = SubmissionChangeLog.objects.get(submission=sub)
        assert log.changes == result.changes
        assert log.changed_by == OWNER
        sub.refresh_from_db()
        assert sub.last_change_summary["changes"] == result.changes
        assert sub.last_change_summary["changed_by"] == OWNER

    def test_a_change_to_an_exempt_field_keeps_it_approved(self, exempt):
        sub = _service("approved")
        result = _edit(sub, github_url="https://github.com/example/new")
        sub.refresh_from_db()
        assert result.status_reset is False
        assert sub.status == "approved"

    def test_a_change_to_another_field_with_an_exempt_list_resets(self, exempt):
        sub = _service("approved")
        result = _edit(sub, comments="new comment")
        assert result.status_reset is True

    def test_rejected_goes_back_to_submitted_even_for_an_exempt_field(self, exempt):
        sub = _service("rejected")
        result = _edit(sub, github_url="https://github.com/example/new")
        sub.refresh_from_db()
        assert result.status_reset is True
        assert sub.status == "submitted"

    def test_other_statuses_are_left_alone(self, no_exempt):
        for status in ("submitted", "under_review", "deprecated"):
            sub = _service(status)
            result = _edit(sub, comments=f"comment {status}")
            sub.refresh_from_db()
            assert result.status_reset is False
            assert sub.status == status

    def test_a_link_change_counts(self, no_exempt):
        sub = _service("approved")
        category = ServiceCategoryFactory()
        before = capture(sub)

        def store():
            sub.save()
            sub.service_categories.add(category)

        result = apply_edit(sub, before=before, store=store, actor=OWNER, by_owner=True)
        assert result.status_reset is True
        assert "service_categories" in {c["field"] for c in result.changes}


class TestAdminSave:
    def test_never_resets_the_status(self, no_exempt):
        for status in ("approved", "rejected"):
            sub = _service(status)
            result = _edit(sub, by_owner=False, actor=ADMIN, comments="admin note")
            sub.refresh_from_db()
            assert result.status_reset is False
            assert sub.status == status

    def test_logs_the_actor(self, no_exempt):
        sub = _service()
        _edit(sub, by_owner=False, actor=ADMIN, comments="admin note")
        assert SubmissionChangeLog.objects.get(submission=sub).changed_by == ADMIN


class TestPublishing:
    def test_an_edit_that_keeps_the_service_approved_goes_live(self, exempt):
        sub = _service("approved")
        _edit(sub, github_url="https://github.com/example/new")
        ps = PublishedService.objects.get(pk=sub.pk)
        assert ps.github_url == "https://github.com/example/new"
        assert ps.revision.reason == "live_edit"
        assert ps.revision.actor == OWNER

    def test_an_admin_edit_is_published_as_an_admin_edit(self, no_exempt):
        sub = _service("approved")
        _edit(sub, by_owner=False, actor=ADMIN, github_url="https://github.com/x/y")
        ps = PublishedService.objects.get(pk=sub.pk)
        assert ps.github_url == "https://github.com/x/y"
        assert ps.revision.reason == "admin_edit"
        assert ps.revision.actor == ADMIN

    def test_a_reset_does_not_replace_the_public_copy(self, no_exempt):
        sub = _service("approved")
        old = PublishedService.objects.get(pk=sub.pk).website_url
        _edit(sub, website_url="https://example.org/moved")
        ps = PublishedService.objects.get(pk=sub.pk)
        assert ps.website_url == old
        assert ps.pending_update is True

    def test_an_unpublished_service_is_not_published(self, no_exempt):
        sub = _service("under_review")
        _edit(sub, by_owner=False, actor=ADMIN, comments="note")
        assert not PublishedService.objects.filter(pk=sub.pk).exists()


class TestRefineChanges:
    def test_the_filter_only_shapes_what_is_logged(self, no_exempt):
        sub = _service("approved")
        result = _edit(
            sub,
            comments="new comment",
            refine=lambda changes: [c for c in changes if c["field"] == "status"],
        )
        assert result.status_reset is True
        assert [c["field"] for c in result.changes] == ["status"]
        assert [c["field"] for c in SubmissionChangeLog.objects.get().changes] == [
            "status"
        ]

    def test_a_filter_that_removes_everything_logs_nothing(self, no_exempt):
        sub = _service()
        result = _edit(sub, comments="new comment", refine=lambda changes: [])
        assert result.changes == []
        assert not SubmissionChangeLog.objects.filter(submission=sub).exists()


class TestAtomicity:
    def test_a_stale_version_stores_nothing(self, no_exempt):
        sub = _service("approved")
        stale = ServiceSubmission.objects.get(pk=sub.pk)
        sub.comments = "someone else"
        sub.save()
        stale.version -= 1
        with pytest.raises(EditConflict):
            _edit(stale, comments="late edit")
        fresh = ServiceSubmission.objects.get(pk=sub.pk)
        assert fresh.comments == "someone else"
        assert fresh.status == "approved"
        assert not SubmissionChangeLog.objects.filter(submission=sub).exists()

    def test_a_failing_log_write_undoes_the_edit_and_the_reset(
        self, no_exempt, monkeypatch
    ):
        sub = _service("approved")

        def boom(*args, **kwargs):
            raise RuntimeError("log write failed")

        monkeypatch.setattr(SubmissionChangeLog.objects, "create", boom)
        with pytest.raises(RuntimeError):
            _edit(sub, comments="new comment")
        fresh = ServiceSubmission.objects.get(pk=sub.pk)
        assert fresh.comments != "new comment"
        assert fresh.status == "approved"


class TestRecordChange:
    """The change-log entry every edit leaves, written by one helper."""

    def test_writes_the_summary_and_one_log_entry(self):
        from apps.submissions.edits import record_change

        sub = ServiceSubmissionFactory(biotools_url="")
        changes = [{"field": "service_name", "old": "A", "new": "B"}]
        record_change(sub, ADMIN, changes)
        sub.refresh_from_db()
        assert sub.last_change_summary["changed_by"] == ADMIN
        assert sub.last_change_summary["changes"] == changes
        entry = SubmissionChangeLog.objects.get(submission=sub)
        assert (entry.changed_by, entry.changes) == (ADMIN, changes)
        assert sub.last_change_summary["changed_at"] == entry.changed_at.isoformat()
