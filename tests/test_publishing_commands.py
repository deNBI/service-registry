"""Operations commands for the publishing layer."""

import uuid
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from apps.catalogue import publishing
from apps.catalogue.models import PublishedRevision, PublishedService
from apps.submissions.lifecycle import set_status
from apps.submissions.models import (
    ServiceSubmission,
    SubmissionChangeLog,
    SubmissionStatusEvent,
)
from tests.factories import ServiceCategoryFactory, ServiceSubmissionFactory

pytestmark = pytest.mark.django_db


def _run(name, *args):
    out = StringIO()
    call_command(name, *args, stdout=out)
    return out.getvalue()


def _legacy_approved(**kw):
    """Approved before the publishing layer existed: no public copy yet."""
    return ServiceSubmissionFactory(status="approved", biotools_url="", **kw)


def _published(**kw):
    sub = ServiceSubmissionFactory(biotools_url="", **kw)
    set_status(sub, "approved", actor="admin:jane")
    return sub


def _deprecated_after_approval(synthetic=False):
    sub = ServiceSubmissionFactory(status="approved", biotools_url="")
    SubmissionStatusEvent.objects.filter(submission=sub).update(is_synthetic=synthetic)
    set_status(sub, "deprecated", actor="admin:jane")
    return sub


def _log(sub, *fields, minutes):
    """A change-log entry ``minutes`` after the service's approval event."""
    approval = SubmissionStatusEvent.objects.filter(
        submission=sub, to_status="approved"
    ).latest("id")
    SubmissionChangeLog.objects.create(
        submission=sub,
        changed_by="submitter",
        changed_at=approval.changed_at + timedelta(minutes=minutes),
        changes=[{"field": f, "label": f, "old": "a", "new": "b"} for f in fields],
    )


class TestPublishBackfillDeprecatedContent:
    """A deprecated service is published only when its working copy still
    holds the content of its last approval."""

    def test_deprecated_after_an_edit_under_review_is_listed(self):
        sub = ServiceSubmissionFactory(status="approved", biotools_url="")
        set_status(sub, "submitted", actor="submitter")
        set_status(sub, "deprecated", actor="submitter")
        out = _run("publish_backfill")
        assert not PublishedService.objects.filter(pk=sub.pk).exists()
        assert "content not confirmed as approved (not published): 1" in out
        assert str(sub.pk) in out

    def test_deprecated_and_edited_afterwards_is_listed(self):
        sub = _deprecated_after_approval()
        _log(sub, "service_description", minutes=5)
        _run("publish_backfill")
        assert not PublishedService.objects.filter(pk=sub.pk).exists()

    def test_edits_before_the_approval_do_not_block(self):
        sub = _deprecated_after_approval()
        _log(sub, "service_description", minutes=-5)
        _run("publish_backfill")
        assert PublishedService.objects.get(pk=sub.pk).state == "deprecated"

    def test_status_and_tag_entries_after_the_approval_do_not_block(self):
        sub = _deprecated_after_approval()
        _log(
            sub, "status", "primary_maturity_tag", "secondary_maturity_tags", minutes=5
        )
        _run("publish_backfill")
        assert PublishedService.objects.get(pk=sub.pk).state == "deprecated"

    @pytest.mark.allow_status_drift
    def test_deprecated_without_a_deprecation_event_is_listed(self):
        sub = ServiceSubmissionFactory(status="approved", biotools_url="")
        ServiceSubmission.objects.filter(pk=sub.pk).update(status="deprecated")
        out = _run("publish_backfill")
        assert not PublishedService.objects.filter(pk=sub.pk).exists()
        assert "content not confirmed as approved (not published): 1" in out

    def test_the_last_deprecation_after_an_approval_decides(self):
        """History: created deprecated, approved, deprecated again."""
        sub = ServiceSubmissionFactory(status="deprecated", biotools_url="")
        SubmissionStatusEvent.objects.create(
            submission=sub,
            from_status="deprecated",
            to_status="approved",
            changed_at=sub.updated_at,
            changed_by="system",
            is_synthetic=True,
        )
        SubmissionStatusEvent.objects.create(
            submission=sub,
            from_status="approved",
            to_status="deprecated",
            changed_at=sub.updated_at,
            changed_by="system",
        )
        out = _run("publish_backfill")
        assert PublishedService.objects.get(pk=sub.pk).state == "deprecated"
        assert "content not confirmed as approved (not published): 0" in out

    @pytest.mark.parametrize(
        "changes",
        [None, "text", {"field": "service_description"}, ["text"], [None]],
        ids=["json_null", "text", "dict_not_list", "list_of_text", "list_of_null"],
    )
    def test_an_unreadable_log_entry_is_listed_not_published(self, changes):
        """Such an entry may hide a content edit; the run must not stop on it."""
        from django.db.models import JSONField, Value

        sub = _deprecated_after_approval()
        _log(sub, "service_description", minutes=5)
        SubmissionChangeLog.objects.filter(submission=sub).update(
            changes=Value(changes, JSONField())
        )
        out = _run("publish_backfill")
        assert not PublishedService.objects.filter(pk=sub.pk).exists()
        assert "content not confirmed as approved (not published): 1" in out

    def test_an_empty_log_entry_does_not_block(self):
        sub = _deprecated_after_approval()
        _log(sub, minutes=5)
        _run("publish_backfill")
        assert PublishedService.objects.get(pk=sub.pk).state == "deprecated"

    def test_dry_run_lists_the_same_ids(self):
        sub = ServiceSubmissionFactory(status="approved", biotools_url="")
        set_status(sub, "submitted", actor="submitter")
        set_status(sub, "deprecated", actor="submitter")
        out = _run("publish_backfill", "--dry-run")
        assert "[dry run] published: 0" in out and str(sub.pk) in out


class TestPublishBackfill:
    def test_publishes_approved(self):
        sub = _legacy_approved()
        out = _run("publish_backfill")
        ps = PublishedService.objects.get(pk=sub.pk)
        assert ps.state == "active" and ps.pending_update is False
        assert ps.revision.reason == "backfill"
        assert ps.revision.actor == "system"
        assert "published: 1" in out

    def test_published_copy_matches_the_working_copy(self):
        _legacy_approved()
        _run("publish_backfill")
        assert "0 problems" in _run("verify_published")

    @pytest.mark.parametrize("synthetic", [False, True])
    def test_deprecated_with_an_approval_is_published_as_deprecated(self, synthetic):
        sub = _deprecated_after_approval(synthetic=synthetic)
        _run("publish_backfill")
        ps = PublishedService.objects.get(pk=sub.pk)
        assert (ps.state, ps.pending_update) == ("deprecated", False)

    def test_deprecated_without_a_recorded_approval_is_listed_not_published(self):
        sub = ServiceSubmissionFactory(status="deprecated", biotools_url="")
        out = _run("publish_backfill")
        assert not PublishedService.objects.filter(pk=sub.pk).exists()
        assert str(sub.pk) in out
        assert "deprecated, no recorded approval (not published): 1" in out

    @pytest.mark.parametrize(
        "status", ["draft", "submitted", "under_review", "rejected"]
    )
    def test_other_statuses_are_never_published(self, status):
        sub = ServiceSubmissionFactory(status=status, biotools_url="")
        out = _run("publish_backfill")
        assert not PublishedService.objects.filter(pk=sub.pk).exists()
        assert str(sub.pk) not in out

    def test_service_approved_once_but_now_waiting_for_review_stays_private(self):
        sub = _published()
        PublishedService.objects.filter(pk=sub.pk).delete()
        PublishedRevision.objects.filter(submission=sub).delete()
        set_status(sub, "submitted", actor="submitter")
        _run("publish_backfill")
        assert not PublishedService.objects.filter(pk=sub.pk).exists()

    def test_counts_services_approved_once_but_now_waiting_for_review(self):
        waiting = []
        for status in ("submitted", "under_review"):
            sub = _published()
            PublishedService.objects.filter(pk=sub.pk).delete()
            PublishedRevision.objects.filter(submission=sub).delete()
            set_status(sub, status, actor="submitter")
            waiting.append(sub)
        ServiceSubmissionFactory(biotools_url="")  # never approved: not counted
        out = _run("publish_backfill")
        assert "approved before, now waiting for review (not published): 2" in out
        for sub in waiting:
            assert str(sub.pk) not in out

    def test_the_waiting_count_is_also_shown_in_a_dry_run(self):
        sub = _published()
        PublishedService.objects.filter(pk=sub.pk).delete()
        PublishedRevision.objects.filter(submission=sub).delete()
        set_status(sub, "under_review", actor="submitter")
        out = _run("publish_backfill", "--dry-run")
        assert (
            "[dry run] approved before, now waiting for review (not published): 1"
            in out
        )

    @pytest.mark.parametrize("status", ["rejected", "draft", "deprecated"])
    def test_other_statuses_are_not_counted_as_waiting(self, status):
        sub = _published()
        PublishedService.objects.filter(pk=sub.pk).delete()
        PublishedRevision.objects.filter(submission=sub).delete()
        set_status(sub, status, actor="admin:jane")
        out = _run("publish_backfill")
        assert "approved before, now waiting for review (not published): 0" in out

    def test_a_synthetic_approval_counts_as_approved_before(self):
        sub = _published()
        PublishedService.objects.filter(pk=sub.pk).delete()
        PublishedRevision.objects.filter(submission=sub).delete()
        set_status(sub, "under_review", actor="submitter")
        SubmissionStatusEvent.objects.filter(
            submission=sub, to_status="approved"
        ).update(is_synthetic=True, source="backfill")
        out = _run("publish_backfill")
        assert "approved before, now waiting for review (not published): 1" in out

    def test_waiting_services_that_are_already_published_are_not_counted(self):
        sub = _published()
        set_status(sub, "submitted", actor="submitter")
        out = _run("publish_backfill")
        assert "approved before, now waiting for review (not published): 0" in out

    def test_public_modified_at_is_the_last_change_of_the_service(self):
        sub = _legacy_approved()
        earlier = sub.updated_at - timedelta(days=30)
        ServiceSubmission.objects.filter(pk=sub.pk).update(updated_at=earlier)
        _run("publish_backfill")
        assert PublishedService.objects.get(pk=sub.pk).public_modified_at == earlier

    def test_deprecated_copy_keeps_the_last_change_of_the_service(self):
        sub = _deprecated_after_approval()
        earlier = sub.updated_at - timedelta(days=30)
        ServiceSubmission.objects.filter(pk=sub.pk).update(updated_at=earlier)
        _run("publish_backfill")
        ps = PublishedService.objects.get(pk=sub.pk)
        assert (ps.state, ps.public_modified_at) == ("deprecated", earlier)

    @staticmethod
    def _run_with_change(monkeypatch, change):
        """Run the command; ``change(pk)`` happens after the candidates were
        chosen and before a service is looked at again for publishing."""
        from apps.catalogue.management.commands import publish_backfill as cmd

        real = cmd.Command._publish_one

        def changed_meanwhile(self, pk):
            change(pk)
            return real(self, pk)

        monkeypatch.setattr(cmd.Command, "_publish_one", changed_meanwhile)
        return _run("publish_backfill")

    @staticmethod
    def _reset_with_new_text(pk):
        ServiceSubmission.objects.filter(pk=pk).update(
            service_description="An unreviewed description."
        )
        set_status(ServiceSubmission.objects.get(pk=pk), "submitted", actor="submitter")

    def test_a_reset_during_the_run_is_not_published(self, monkeypatch):
        """Another request resets the service after the candidates were chosen."""
        sub = _legacy_approved()
        out = self._run_with_change(monkeypatch, self._reset_with_new_text)
        assert not PublishedService.objects.filter(pk=sub.pk).exists()
        assert not PublishedRevision.objects.filter(submission=sub).exists()
        assert "published: 0" in out

    def test_an_undeprecate_during_the_run_is_not_published(self, monkeypatch):
        sub = _deprecated_after_approval()
        out = self._run_with_change(monkeypatch, self._reset_with_new_text)
        assert not PublishedService.objects.filter(pk=sub.pk).exists()
        assert "published: 0" in out

    def test_a_deprecated_service_edited_during_the_run_is_not_published(
        self, monkeypatch
    ):
        sub = _deprecated_after_approval()
        out = self._run_with_change(
            monkeypatch, lambda pk: _log(sub, "service_description", minutes=5)
        )
        assert not PublishedService.objects.filter(pk=sub.pk).exists()
        assert "published: 0" in out

    def test_a_service_approved_during_the_run_is_published_once(self, monkeypatch):
        """Approval publishes the service itself; the backfill must not add to it."""
        sub = _legacy_approved()

        def reapproved(pk):
            row = ServiceSubmission.objects.get(pk=pk)
            set_status(row, "submitted", actor="submitter")
            set_status(row, "approved", actor="admin:jane")

        out = self._run_with_change(monkeypatch, reapproved)
        revisions = PublishedRevision.objects.filter(submission=sub)
        assert [r.reason for r in revisions] == ["approval"]
        assert "published: 0" in out

    def test_a_service_deleted_during_the_run_is_skipped(self, monkeypatch):
        sub = _legacy_approved()
        out = self._run_with_change(
            monkeypatch, lambda pk: ServiceSubmission.objects.filter(pk=pk).delete()
        )
        assert not PublishedService.objects.filter(pk=sub.pk).exists()
        assert "published: 0" in out

    def test_each_service_is_locked_before_it_is_checked(
        self, locked_models, monkeypatch
    ):
        """The check itself holds the lock, not only the publish that follows."""
        _legacy_approved()
        monkeypatch.setattr(publishing, "publish", lambda *a, **kw: None)
        _run("publish_backfill")
        assert locked_models == ["ServiceSubmission"]

    def test_a_missing_read_model_is_rebuilt_from_the_existing_revision(self):
        sub = _published()
        revisions = PublishedRevision.objects.filter(submission=sub).count()
        PublishedService.objects.filter(pk=sub.pk).delete()
        _run("publish_backfill")
        assert PublishedService.objects.filter(pk=sub.pk).exists()
        assert PublishedRevision.objects.filter(submission=sub).count() == revisions
        assert "0 problems" in _run("verify_published")

    def test_dry_run_writes_nothing(self):
        _legacy_approved()
        out = _run("publish_backfill", "--dry-run")
        assert "[dry run] published: 1" in out
        assert PublishedService.objects.count() == 0
        assert PublishedRevision.objects.count() == 0

    def test_second_run_publishes_nothing(self):
        _legacy_approved()
        _run("publish_backfill")
        revisions = PublishedRevision.objects.count()
        assert "published: 0" in _run("publish_backfill")
        assert PublishedRevision.objects.count() == revisions

    def test_already_published_services_are_left_alone(self):
        sub = _published()
        set_status(sub, "submitted", actor="submitter")
        ServiceSubmission.objects.filter(pk=sub.pk).update(service_name="Edited later")
        before = PublishedService.objects.get(pk=sub.pk).service_name
        _run("publish_backfill")
        assert PublishedService.objects.get(pk=sub.pk).service_name == before

    def test_listing_is_stable_over_repeated_runs(self):
        sub = ServiceSubmissionFactory(status="deprecated", biotools_url="")
        first = _run("publish_backfill")
        second = _run("publish_backfill")
        assert first == second and str(sub.pk) in second

    def test_output_contains_counts_and_ids_only(self):
        a = _legacy_approved(service_name="Example Distinctive Name")
        b = ServiceSubmissionFactory(
            status="deprecated", biotools_url="", service_name="Example Other Name"
        )
        out = _run("publish_backfill")
        assert "Example Distinctive Name" not in out
        assert "Example Other Name" not in out
        assert str(b.pk) in out
        assert str(a.pk) not in out

    def test_many_services(self):
        for _ in range(12):
            _legacy_approved()
        assert "published: 12" in _run("publish_backfill")
        assert PublishedService.objects.count() == 12

    def test_backfilled_first_published_at_follows_the_approval_dates(self):
        real = _published()
        PublishedService.objects.filter(pk=real.pk).delete()
        PublishedRevision.objects.filter(submission=real).delete()
        synthetic = _legacy_approved()
        SubmissionStatusEvent.objects.filter(submission=synthetic).update(
            is_synthetic=True
        )
        _run("publish_backfill")
        assert PublishedService.objects.get(pk=real.pk).first_published_at is not None
        assert PublishedService.objects.get(pk=synthetic.pk).first_published_at is None


class TestVerifyPublished:
    def test_an_older_revision_is_not_drift_but_the_copy_is_stale(self):
        """A revision written before a field became public: the copy built
        from it is consistent (no drift), and it lacks the working copy's value
        for the new field, so the approved service is reported as stale until
        it is published again."""
        sub = _published(is_toolbox=True, toolbox_name="Example Toolbox")
        rev = PublishedRevision.objects.get(submission=sub)
        content = {k: v for k, v in rev.content.items() if k != "toolbox_name"}
        PublishedRevision.objects.filter(pk=rev.pk).update(
            content=content, content_hash=publishing.content_hash(content)
        )
        call_command("rebuild_published", "--all", stdout=StringIO())
        out = StringIO()
        with pytest.raises(CommandError, match=r"1 problem\(s\): stale=1$"):
            call_command("verify_published", stdout=out)
        counts = dict(
            line.split(": ")
            for line in out.getvalue().splitlines()
            if not line.startswith(" ") and ": " in line
        )
        assert counts["stale"] == "1"
        assert counts["drift"] == "0"
        assert counts["missing_reference"] == "0"

    def test_empty_database_passes(self):
        assert "0 problems" in _run("verify_published")

    def test_clean_state_passes(self):
        _published()
        assert "0 problems" in _run("verify_published")

    def test_pending_update_is_not_a_problem(self):
        sub = _published()
        set_status(sub, "submitted", actor="submitter")
        ServiceSubmission.objects.filter(pk=sub.pk).update(service_name="Edited")
        assert "0 problems" in _run("verify_published")

    def test_detects_stale_public_copy(self):
        sub = _published()
        ServiceSubmission.objects.filter(pk=sub.pk).update(
            website_url="https://example.org/x"
        )
        with pytest.raises(CommandError, match="stale=1"):
            _run("verify_published")

    def test_approved_with_a_pending_flag_is_reported_once(self):
        sub = _published()
        ServiceSubmission.objects.filter(pk=sub.pk).update(
            website_url="https://example.org/x"
        )
        PublishedService.objects.filter(pk=sub.pk).update(pending_update=True)
        with pytest.raises(CommandError) as exc:
            _run("verify_published")
        assert "state_mismatch=1" in str(exc.value)
        assert "stale" not in str(exc.value)

    def test_private_changes_are_not_stale(self):
        sub = _published()
        ServiceSubmission.objects.filter(pk=sub.pk).update(comments="Private note")
        assert "0 problems" in _run("verify_published")

    def test_detects_unpublished_approved(self):
        _legacy_approved()
        with pytest.raises(CommandError, match="unpublished_approved=1"):
            _run("verify_published")

    def test_detects_drift(self):
        sub = _published()
        PublishedService.objects.filter(pk=sub.pk).update(service_name="Drifted")
        with pytest.raises(CommandError, match="drift=1"):
            _run("verify_published")

    def test_detects_drift_in_a_relation(self):
        sub = _published()
        PublishedService.objects.get(pk=sub.pk).service_categories.clear()
        with pytest.raises(CommandError, match="drift=1"):
            _run("verify_published")

    def test_detects_a_read_model_pointing_at_an_old_revision(self):
        sub = _published()
        old = PublishedService.objects.get(pk=sub.pk).revision
        sub.website_url = "https://example.org/moved"
        sub.save()
        publishing.publish(sub, reason="live_edit", actor="submitter")
        PublishedService.objects.filter(pk=sub.pk).update(revision=old)
        with pytest.raises(CommandError, match="drift=1"):
            _run("verify_published")

    @pytest.mark.allow_status_drift
    def test_detects_missing_approval_event(self):
        sub = _published()
        SubmissionStatusEvent.objects.filter(
            submission=sub, to_status="approved"
        ).delete()
        with pytest.raises(CommandError, match="no_approval_event=1"):
            _run("verify_published")

    @pytest.mark.allow_status_drift
    def test_detects_a_status_written_without_its_history(self):
        sub = ServiceSubmissionFactory(biotools_url="")
        ServiceSubmission.objects.filter(pk=sub.pk).update(status="under_review")
        out = StringIO()
        with pytest.raises(CommandError) as exc:
            call_command("verify_published", stdout=out)
        assert str(exc.value).endswith("status_history_drift=1")
        assert str(sub.pk) in out.getvalue()

    @pytest.mark.allow_status_drift
    def test_detects_a_submission_without_any_history(self):
        sub = ServiceSubmissionFactory(biotools_url="")
        SubmissionStatusEvent.objects.filter(submission=sub).delete()
        with pytest.raises(CommandError, match="status_history_drift=1"):
            _run("verify_published")

    def test_synthetic_approval_event_counts_as_an_approval(self):
        sub = _published()
        SubmissionStatusEvent.objects.filter(
            submission=sub, to_status="approved"
        ).update(is_synthetic=True)
        assert "0 problems" in _run("verify_published")

    def test_detects_missing_reference_rows(self):
        cat = ServiceCategoryFactory()
        sub = ServiceSubmissionFactory(biotools_url="")
        sub.service_categories.add(cat)
        set_status(sub, "approved", actor="admin:jane")
        cat.delete()
        with pytest.raises(CommandError) as exc:
            _run("verify_published")
        assert "missing_reference=1" in str(exc.value)

    def test_a_deleted_reference_row_is_not_reported_as_drift(self):
        cat = ServiceCategoryFactory()
        sub = ServiceSubmissionFactory(biotools_url="")
        sub.service_categories.add(cat)
        set_status(sub, "approved", actor="admin:jane")
        cat.delete()
        with pytest.raises(CommandError) as exc:
            _run("verify_published")
        assert "drift" not in str(exc.value)

    @pytest.mark.parametrize(
        "status,state,pending",
        [
            ("approved", "deprecated", False),
            ("approved", "active", True),
            ("deprecated", "active", False),
            ("deprecated", "deprecated", True),
            ("submitted", "active", False),
            ("submitted", "deprecated", True),
            ("under_review", "active", False),
            ("rejected", "active", True),
            ("rejected", "deprecated", False),
            ("draft", "active", True),
            ("draft", "deprecated", False),
        ],
    )
    def test_detects_flags_that_do_not_match_the_status(self, status, state, pending):
        sub = _published()
        set_status(sub, status, actor="admin:jane")
        PublishedService.objects.filter(pk=sub.pk).update(
            state=state, pending_update=pending
        )
        with pytest.raises(CommandError) as exc:
            _run("verify_published")
        assert str(exc.value) == "1 problem(s): state_mismatch=1"

    @pytest.mark.parametrize(
        "status,state,pending",
        [
            ("approved", "active", False),
            ("deprecated", "deprecated", False),
            ("submitted", "active", True),
            ("under_review", "active", True),
            ("rejected", "active", False),
            ("draft", "active", False),
        ],
    )
    def test_accepts_every_consistent_combination(self, status, state, pending):
        sub = _published()
        set_status(sub, status, actor="admin:jane")
        PublishedService.objects.filter(pk=sub.pk).update(
            state=state, pending_update=pending
        )
        assert "0 problems" in _run("verify_published")

    @pytest.mark.parametrize(
        "path,state,pending",
        [
            (["deprecated", "submitted"], "deprecated", True),
            (["deprecated", "under_review"], "deprecated", True),
            (["deprecated", "rejected"], "deprecated", False),
            (["deprecated", "draft"], "deprecated", False),
            (["deprecated", "submitted", "approved"], "active", False),
        ],
    )
    def test_accepts_the_flags_the_live_flow_leaves_after_a_deprecation(
        self, path, state, pending
    ):
        sub = _published()
        for status in path:
            set_status(sub, status, actor="admin:jane")
        ps = PublishedService.objects.get(pk=sub.pk)
        assert (ps.state, ps.pending_update) == (state, pending)
        assert "0 problems" in _run("verify_published")

    def test_detects_an_undeprecated_copy_turned_active(self):
        sub = _published()
        set_status(sub, "deprecated", actor="admin:jane")
        set_status(sub, "submitted", actor="submitter")
        PublishedService.objects.filter(pk=sub.pk).update(state="active")
        with pytest.raises(CommandError) as exc:
            _run("verify_published")
        assert str(exc.value) == "1 problem(s): state_mismatch=1"

    @pytest.mark.allow_status_drift
    def test_detects_a_gap_in_the_status_history(self):
        sub = _published()
        SubmissionStatusEvent.objects.create(
            submission=sub,
            from_status="under_review",
            to_status="approved",
            changed_at=sub.updated_at,
            changed_by="system",
        )
        out = StringIO()
        with pytest.raises(CommandError) as exc:
            call_command("verify_published", stdout=out)
        assert str(exc.value) == "1 problem(s): status_history_broken=1"
        assert str(sub.pk) in out.getvalue()

    @pytest.mark.allow_status_drift
    def test_detects_a_history_that_does_not_start_with_the_creation(self):
        sub = _published()
        SubmissionStatusEvent.objects.filter(
            submission=sub, from_status__isnull=True
        ).delete()
        with pytest.raises(CommandError) as exc:
            _run("verify_published")
        assert str(exc.value) == "1 problem(s): status_history_broken=1"

    def test_a_continuous_history_with_synthetic_rows_passes(self):
        sub = _published()
        set_status(sub, "deprecated", actor="admin:jane")
        SubmissionStatusEvent.objects.filter(
            submission=sub, to_status="approved"
        ).update(is_synthetic=True, source="backfill")
        assert "0 problems" in _run("verify_published")

    @pytest.mark.allow_status_drift
    def test_detects_a_second_creation_event(self):
        sub = _published()
        SubmissionStatusEvent.objects.create(
            submission=sub,
            from_status=None,
            to_status="approved",
            changed_at=sub.updated_at,
            changed_by="system",
        )
        with pytest.raises(CommandError, match="status_history_broken=1"):
            _run("verify_published")

    @pytest.mark.allow_status_drift
    def test_a_service_with_several_breaks_is_reported_once(self):
        sub = _published()
        for _ in range(2):
            SubmissionStatusEvent.objects.create(
                submission=sub,
                from_status="draft",
                to_status="approved",
                changed_at=sub.updated_at,
                changed_by="system",
            )
        out = StringIO()
        with pytest.raises(CommandError) as exc:
            call_command("verify_published", stdout=out)
        assert str(exc.value) == "1 problem(s): status_history_broken=1"
        assert out.getvalue().count(str(sub.pk)) == 1

    def test_the_history_chain_follows_row_order_not_the_clock(self):
        sub = _published()
        set_status(sub, "deprecated", actor="admin:jane")
        for number, event in enumerate(
            SubmissionStatusEvent.objects.filter(submission=sub).order_by("id")
        ):
            SubmissionStatusEvent.objects.filter(pk=event.pk).update(
                changed_at=event.changed_at - timedelta(days=number)
            )
        assert "0 problems" in _run("verify_published")

    @pytest.mark.allow_status_drift
    def test_one_broken_history_among_good_ones_is_reported_by_id_only(self):
        good = _published()
        broken = _published()
        SubmissionStatusEvent.objects.filter(
            submission=broken, from_status__isnull=True
        ).delete()
        out = StringIO()
        with pytest.raises(CommandError):
            call_command("verify_published", stdout=out)
        assert str(broken.pk) in out.getvalue()
        assert str(good.pk) not in out.getvalue()

    def test_only_the_newest_revision_of_each_service_is_loaded(self):
        from django.db.models.signals import post_init

        subs = [_published(), _published()]
        loaded = []

        def count(sender, **kwargs):
            loaded.append(sender)

        def revisions_loaded():
            del loaded[:]
            post_init.connect(count, sender=PublishedRevision, weak=False)
            try:
                assert "0 problems" in _run("verify_published")
            finally:
                post_init.disconnect(count, sender=PublishedRevision)
            return len(loaded)

        one_each = revisions_loaded()
        for sub in subs:
            for number in range(4):
                sub.website_url = f"https://example.org/{number}"
                sub.save()
                publishing.publish(sub, reason="live_edit", actor="submitter")
        assert PublishedRevision.objects.count() == 10
        assert revisions_loaded() == one_each

    def test_reports_every_problem_class_with_ids(self):
        stale = _published()
        ServiceSubmission.objects.filter(pk=stale.pk).update(
            website_url="https://example.org/x"
        )
        drift = _published()
        PublishedService.objects.filter(pk=drift.pk).update(service_name="Drifted")
        missing = _legacy_approved()
        out = StringIO()
        with pytest.raises(CommandError) as exc:
            call_command("verify_published", stdout=out)
        text = out.getvalue()
        for sub in (stale, drift, missing):
            assert str(sub.pk) in text
        assert "stale=1" in str(exc.value)
        assert "drift=1" in str(exc.value)
        assert "unpublished_approved=1" in str(exc.value)

    def test_output_never_contains_field_values(self):
        sub = _published(service_name="Example Distinctive Name")
        PublishedService.objects.filter(pk=sub.pk).update(service_name="Drifted Value")
        out = StringIO()
        with pytest.raises(CommandError) as exc:
            call_command("verify_published", stdout=out)
        combined = out.getvalue() + str(exc.value)
        assert "Drifted Value" not in combined
        assert "Example Distinctive Name" not in combined
        assert "example.com" not in combined

    def test_verify_does_not_change_anything(self):
        sub = _published()
        PublishedService.objects.filter(pk=sub.pk).update(service_name="Drifted")
        revisions = PublishedRevision.objects.count()
        with pytest.raises(CommandError):
            _run("verify_published")
        assert PublishedService.objects.get(pk=sub.pk).service_name == "Drifted"
        assert PublishedRevision.objects.count() == revisions


class TestRebuildPublished:
    def test_rebuild_all_fixes_drift(self):
        sub = _published()
        PublishedService.objects.filter(pk=sub.pk).update(service_name="Drifted")
        out = _run("rebuild_published", "--all")
        assert "rebuilt: 1" in out
        assert PublishedService.objects.get(pk=sub.pk).service_name == sub.service_name
        assert "0 problems" in _run("verify_published")

    def test_rebuild_one_by_id_leaves_others_alone(self):
        a, b = _published(), _published()
        PublishedService.objects.filter(pk__in=[a.pk, b.pk]).update(
            service_name="Drifted"
        )
        out = _run("rebuild_published", "--id", str(a.pk))
        assert "rebuilt: 1" in out
        assert PublishedService.objects.get(pk=a.pk).service_name == a.service_name
        assert PublishedService.objects.get(pk=b.pk).service_name == "Drifted"

    def test_several_ids(self):
        a, b = _published(), _published()
        out = _run("rebuild_published", "--id", str(a.pk), "--id", str(b.pk))
        assert "rebuilt: 2" in out

    def test_rebuild_recreates_a_missing_read_model(self):
        sub = _published()
        PublishedService.objects.filter(pk=sub.pk).delete()
        _run("rebuild_published", "--all")
        assert PublishedService.objects.filter(pk=sub.pk).exists()

    def test_rebuild_ignores_the_working_copy(self):
        sub = _published()
        ServiceSubmission.objects.filter(pk=sub.pk).update(service_name="Edited later")
        _run("rebuild_published", "--all")
        assert PublishedService.objects.get(pk=sub.pk).service_name != "Edited later"

    def test_rebuild_keeps_the_date_of_a_backfilled_copy(self):
        """A copy the backfill dated by the last change of the service is not
        dated by the time the backfill ran when the read model is rebuilt."""
        sub = _legacy_approved()
        earlier = sub.updated_at - timedelta(days=30)
        ServiceSubmission.objects.filter(pk=sub.pk).update(updated_at=earlier)
        _run("publish_backfill")
        PublishedService.objects.filter(pk=sub.pk).delete()
        _run("rebuild_published", "--all")
        assert PublishedService.objects.get(pk=sub.pk).public_modified_at == earlier

    def test_rebuild_does_not_create_revisions(self):
        _published()
        revisions = PublishedRevision.objects.count()
        _run("rebuild_published", "--all")
        assert PublishedRevision.objects.count() == revisions

    def test_rebuild_counts_a_service_once_however_many_revisions_it_has(self):
        sub = _published()
        sub.website_url = "https://example.org/moved"
        sub.save()
        publishing.publish(sub, reason="live_edit", actor="submitter")
        assert PublishedRevision.objects.filter(submission=sub).count() == 2
        assert "rebuilt: 1" in _run("rebuild_published", "--all")

    def test_rebuild_is_repeatable(self):
        _published()
        _run("rebuild_published", "--all")
        assert "rebuilt: 1" in _run("rebuild_published", "--all")
        assert "0 problems" in _run("verify_published")

    def test_rebuild_all_with_nothing_published(self):
        assert "rebuilt: 0" in _run("rebuild_published", "--all")

    def test_unknown_id_fails(self):
        with pytest.raises(CommandError, match="no revision"):
            _run("rebuild_published", "--id", str(uuid.uuid4()))

    def test_malformed_id_fails(self):
        with pytest.raises(CommandError, match="not a valid id"):
            _run("rebuild_published", "--id", "not-a-uuid")

    def test_an_option_is_required(self):
        with pytest.raises(CommandError):
            _run("rebuild_published")

    def test_all_and_id_are_exclusive(self):
        with pytest.raises(CommandError):
            _run("rebuild_published", "--all", "--id", str(uuid.uuid4()))

    @pytest.mark.parametrize(
        "path,state,pending",
        [
            ([], "active", False),
            (["submitted"], "active", True),
            (["rejected"], "active", False),
            (["deprecated"], "deprecated", False),
            (["deprecated", "submitted"], "deprecated", True),
            (["deprecated", "rejected"], "deprecated", False),
            (["deprecated", "submitted", "approved"], "active", False),
        ],
    )
    def test_a_recreated_read_model_gets_the_flags_of_the_live_flow(
        self, path, state, pending
    ):
        sub = _published()
        for status in path:
            set_status(sub, status, actor="admin:jane")
        PublishedService.objects.filter(pk=sub.pk).delete()
        _run("rebuild_published", "--id", str(sub.pk))
        ps = PublishedService.objects.get(pk=sub.pk)
        assert (ps.state, ps.pending_update) == (state, pending)
        assert "0 problems" in _run("verify_published")

    def test_rebuild_fixes_drift_in_a_relation(self):
        sub = _published()
        PublishedService.objects.get(pk=sub.pk).service_categories.clear()
        _run("rebuild_published", "--all")
        assert "0 problems" in _run("verify_published")
