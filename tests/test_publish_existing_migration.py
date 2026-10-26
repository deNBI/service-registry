"""Migration that creates the public copy of services approved before the
publishing layer existed.

The migration holds a frozen copy of the content building and of the
publish_backfill rules; these tests pin both to the application code. The
migration writes revisions of schema version 1, so content is compared on the
fields it was written for: a later change to the public fields (a new schema
version) leaves this migration and these comparisons as they are.
"""

import importlib
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection
from django.db.models import JSONField, Value
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader

from apps.catalogue import publishing
from apps.catalogue.models import PublishedRevision, PublishedService
from apps.catalogue.public_fields import ALL_PUBLIC_FIELDS, PUBLIC_M2M_FIELDS
from apps.edam.models import EdamTerm
from apps.licenses.models import SpdxLicense
from apps.submissions.lifecycle import set_status
from apps.submissions.models import (
    ServiceSubmission,
    SubmissionChangeLog,
    SubmissionStatusEvent,
)
from tests.factories import (
    PIFactory,
    ServiceCategoryFactory,
    ServiceSubmissionFactory,
)

pytestmark = pytest.mark.django_db
NAME = "0002_publish_existing_services"
MIGRATION = importlib.import_module(f"apps.catalogue.migrations.{NAME}")
WRITTEN_FIELDS = MIGRATION.SCALAR_FIELDS + MIGRATION.FK_FIELDS + MIGRATION.M2M_FIELDS


def _as_written(content):
    """Content restricted to the fields this migration writes."""
    return {name: content[name] for name in WRITTEN_FIELDS}


@pytest.fixture(scope="module")
def historical_apps():
    """The models as the migration sees them."""
    return (
        MigrationLoader(None, ignore_no_migrations=True)
        .project_state(("catalogue", NAME))
        .apps
    )


@pytest.fixture
def migrate(historical_apps, capsys):
    """Run the migration; returns what it printed."""

    def run():
        capsys.readouterr()
        MIGRATION.publish_existing(historical_apps, None)
        return capsys.readouterr().out

    return run


def _legacy_approved(**kw):
    """Approved before the publishing layer existed: no public copy yet."""
    return ServiceSubmissionFactory(status="approved", biotools_url="", **kw)


def _published():
    sub = ServiceSubmissionFactory(biotools_url="")
    set_status(sub, "approved", actor="admin:jane")
    return sub


def _unpublish(sub):
    PublishedService.objects.filter(pk=sub.pk).delete()
    PublishedRevision.objects.filter(submission=sub).delete()


def _deprecated_after_approval(synthetic=False):
    sub = _legacy_approved()
    SubmissionStatusEvent.objects.filter(submission=sub).update(is_synthetic=synthetic)
    set_status(sub, "deprecated", actor="admin:jane")
    return sub


def _log_after_approval(sub, field, minutes=5):
    approval = SubmissionStatusEvent.objects.filter(
        submission=sub, to_status="approved"
    ).latest("id")
    SubmissionChangeLog.objects.create(
        submission=sub,
        changed_by="submitter",
        changed_at=approval.changed_at + timedelta(minutes=minutes),
        changes=[{"field": field, "label": field, "old": "a", "new": "b"}],
    )


def _waiting(status="submitted"):
    sub = _published()
    _unpublish(sub)
    set_status(sub, status, actor="submitter", owner_edit=True)
    return sub


def _full():
    """An approved service with every kind of public value filled in."""
    topic = EdamTerm.objects.create(
        uri="http://edamontology.org/topic_9001",
        accession="topic_9001",
        branch="topic",
        label="Example topic",
    )
    operation = EdamTerm.objects.create(
        uri="http://edamontology.org/operation_9002",
        accession="operation_9002",
        branch="operation",
        label="Example operation",
    )
    sub = _legacy_approved(
        primary_maturity_tag="mature",
        is_toolbox=True,
        toolbox_name="Example Toolbox",
        publications_pmids="12345678, 23456789",
        keywords_seo="example, keywords",
    )
    ServiceSubmission.objects.filter(pk=sub.pk).update(logo="logos/example.png")
    sub.refresh_from_db()
    sub.service_categories.add(ServiceCategoryFactory(), ServiceCategoryFactory())
    sub.responsible_pis.add(PIFactory(), PIFactory())
    sub.edam_topics.add(topic)
    sub.edam_operations.add(operation)
    sub.licenses.add(
        SpdxLicense.objects.create(license_id="MIT", name="Example MIT"),
        SpdxLicense.objects.create(license_id="Apache-2.0", name="Example Apache"),
    )
    return sub


def _deprecated_after_tagged_approval():
    """Approved with a maturity tag, then deprecated: deprecation clears it."""
    sub = _legacy_approved(primary_maturity_tag="mature")
    set_status(sub, "deprecated", actor="admin:jane")
    return sub


def _deprecated_edited_after_approval():
    sub = _deprecated_after_approval()
    _log_after_approval(sub, "service_description")
    return sub


def _deprecated_after_review():
    sub = _legacy_approved()
    set_status(sub, "submitted", actor="submitter", owner_edit=True)
    set_status(sub, "deprecated", actor="submitter")
    return sub


def _deprecated_with_tag_entry_after_approval():
    sub = _deprecated_after_approval()
    _log_after_approval(sub, "primary_maturity_tag")
    return sub


def _deprecated_after_a_review(synthetic, edit_at_minutes):
    """Submitted and under review at one time, approved and deprecated an hour
    later; a content edit is logged ``edit_at_minutes`` after the start. The
    approval's date is a real one only when it is not synthetic."""
    sub = ServiceSubmissionFactory(status="submitted", biotools_url="")
    set_status(sub, "under_review", actor="admin:jane")
    set_status(sub, "approved", actor="admin:jane")
    set_status(sub, "deprecated", actor="admin:jane")
    start = sub.status_events.earliest("id").changed_at
    for event, hours in zip(sub.status_events.order_by("id"), (0, 0, 1, 1)):
        event.changed_at = start + timedelta(hours=hours)
        event.is_synthetic = synthetic and event.to_status == "approved"
        event.save()
    _unpublish(sub)
    SubmissionChangeLog.objects.create(
        submission=sub,
        changed_by="submitter",
        changed_at=start + timedelta(minutes=edit_at_minutes),
        changes=[
            {"field": "service_description", "label": "d", "old": "a", "new": "b"}
        ],
    )
    return sub


# Change-log shapes that an older tool or a script could have written.
UNREADABLE_CHANGES = {
    "json_null": None,
    "text": "service_description changed",
    "dict_not_list": {"field": "service_description"},
    "list_of_text": ["service_description"],
    "list_of_null": [None],
}


def _deprecated_with_unreadable_log(changes):
    sub = _deprecated_after_approval()
    approval = SubmissionStatusEvent.objects.filter(
        submission=sub, to_status="approved"
    ).latest("id")
    log = SubmissionChangeLog.objects.create(
        submission=sub,
        changed_by="system",
        changed_at=approval.changed_at + timedelta(minutes=5),
        changes=[],
    )
    # A JSON null has to be written as a value: None would mean SQL NULL.
    SubmissionChangeLog.objects.filter(pk=log.pk).update(
        changes=Value(changes, JSONField())
    )
    return sub


def _approved_long_ago():
    sub = _legacy_approved()
    ServiceSubmission.objects.filter(pk=sub.pk).update(
        updated_at=sub.updated_at - timedelta(days=30)
    )
    return sub


def _approved_with_real_event():
    sub = _published()
    _unpublish(sub)
    return sub


def _approved_twice():
    """Approved, sent back to review and approved again: the first approval
    is the first publication."""
    sub = _published()
    set_status(sub, "submitted", actor="submitter", owner_edit=True)
    set_status(sub, "approved", actor="admin:jane")
    _unpublish(sub)
    return sub


def _approved_without_logo():
    """Logo stored as NULL (the column allows it), not as an empty string."""
    sub = _legacy_approved()
    ServiceSubmission.objects.filter(pk=sub.pk).update(logo=None)
    return sub


def _approved_synthetic():
    sub = _legacy_approved()
    SubmissionStatusEvent.objects.filter(submission=sub).update(is_synthetic=True)
    return sub


SCENARIOS = {
    "approved": _legacy_approved,
    "approved_full": _full,
    "approved_long_ago": _approved_long_ago,
    "approved_synthetic": _approved_synthetic,
    "approved_without_logo": _approved_without_logo,
    "approved_twice": _approved_twice,
    "approved_real_event": _approved_with_real_event,
    "deprecated_after_approval": _deprecated_after_approval,
    "deprecated_after_synthetic_approval": lambda: _deprecated_after_approval(True),
    "deprecated_after_tagged_approval": _deprecated_after_tagged_approval,
    "deprecated_edited_after_approval": _deprecated_edited_after_approval,
    "deprecated_tag_entry_after_approval": _deprecated_with_tag_entry_after_approval,
    "deprecated_after_review": _deprecated_after_review,
    "deprecated_edit_before_real_approval": lambda: _deprecated_after_a_review(
        False, 30
    ),
    "deprecated_edit_before_synthetic_approval": lambda: _deprecated_after_a_review(
        True, 30
    ),
    "deprecated_edit_ahead_of_synthetic_approval": lambda: _deprecated_after_a_review(
        True, -30
    ),
    **{
        f"deprecated_unreadable_log_{name}": (
            lambda changes=changes: _deprecated_with_unreadable_log(changes)
        )
        for name, changes in UNREADABLE_CHANGES.items()
    },
    "deprecated_empty_log_entry": lambda: _deprecated_with_unreadable_log([]),
    "deprecated_never_approved": lambda: ServiceSubmissionFactory(
        status="deprecated", biotools_url=""
    ),
    "waiting_submitted": _waiting,
    "waiting_under_review": lambda: _waiting("under_review"),
    "submitted_never_approved": lambda: ServiceSubmissionFactory(biotools_url=""),
    "rejected": lambda: ServiceSubmissionFactory(status="rejected", biotools_url=""),
    "draft": lambda: ServiceSubmissionFactory(status="draft", biotools_url=""),
}


def _snapshot():
    """Everything the public copy holds, except creation times of revisions."""
    snap = {}
    for ps in PublishedService.objects.select_related("revision"):
        rev = ps.revision
        snap[ps.pk] = {
            "state": ps.state,
            "pending_update": ps.pending_update,
            "public_modified_at": ps.public_modified_at,
            "revision_public_modified_at": ps.revision.public_modified_at,
            "first_published_at": ps.first_published_at,
            "values": _as_written(publishing.build_content(ps)),
            "revisions": [
                (
                    number,
                    reason,
                    actor,
                    schema_version,
                    _as_written(content),
                    digest == publishing.content_hash(content),
                )
                for number, reason, actor, schema_version, content, digest in (
                    PublishedRevision.objects.filter(submission_id=ps.pk)
                    .order_by("number")
                    .values_list(
                        "number",
                        "reason",
                        "actor",
                        "schema_version",
                        "content",
                        "content_hash",
                    )
                )
            ],
            "revision_number": rev.number,
        }
    return snap


def _run_command(*args):
    out = StringIO()
    call_command(*args, stdout=out)
    return out.getvalue()


class TestSameResultAsPublishBackfill:
    @pytest.mark.parametrize("scenario", sorted(SCENARIOS))
    def test_each_case(self, migrate, scenario):
        SCENARIOS[scenario]()
        migrate()
        by_migration = _snapshot()
        PublishedService.objects.all().delete()
        PublishedRevision.objects.all().delete()
        _run_command("publish_backfill")
        assert by_migration == _snapshot()

    def test_all_cases_together_and_the_counts(self, migrate):
        for build in SCENARIOS.values():
            build()
        out = migrate()
        by_migration = _snapshot()
        PublishedService.objects.all().delete()
        PublishedRevision.objects.all().delete()
        command_out = _run_command("publish_backfill")
        assert by_migration == _snapshot()
        assert "published: 14" in command_out
        assert (
            "{'published': 14, 'deprecated_never_approved': 1, "
            "'deprecated_unconfirmed': 8, 'waiting_for_review': 2}"
        ) in out


class TestPublishExisting:
    def test_publishes_an_approved_service(self, migrate):
        sub = _legacy_approved()
        migrate()
        ps = PublishedService.objects.get(pk=sub.pk)
        assert (ps.state, ps.pending_update) == ("active", False)
        rev = ps.revision
        assert (rev.number, rev.reason, rev.actor, rev.schema_version) == (
            1,
            "backfill",
            "system",
            PublishedRevision.SCHEMA_VERSION,
        )

    def test_content_and_hash_equal_the_application_code(self, migrate):
        sub = _full()
        migrate()
        rev = PublishedRevision.objects.get(submission=sub)
        assert rev.schema_version == 1
        assert set(rev.content) == set(WRITTEN_FIELDS)
        assert rev.content == _as_written(publishing.build_content(sub))
        assert rev.content_hash == publishing.content_hash(rev.content)
        ps = PublishedService.objects.get(pk=sub.pk)
        for name in PUBLIC_M2M_FIELDS:
            assert set(getattr(ps, name).values_list("pk", flat=True)) == set(
                getattr(sub, name).values_list("pk", flat=True)
            )
        assert ps.logo.name == "logos/example.png"

    def test_written_fields_are_the_public_fields_of_schema_version_1(self):
        """Changing the public fields needs a new revision schema version and
        a data migration that publishes again; this migration stays as it is."""
        if PublishedRevision.SCHEMA_VERSION == 1:
            assert set(WRITTEN_FIELDS) == set(ALL_PUBLIC_FIELDS)

    @pytest.mark.skipif(
        PublishedRevision.SCHEMA_VERSION != 1,
        reason="revisions of an older schema version are reported as stale",
    )
    def test_verify_published_reports_no_problem_afterwards(self, migrate):
        for build in SCENARIOS.values():
            build()
        migrate()
        assert "0 problems" in _run_command("verify_published")

    def test_a_deprecated_copy_has_no_maturity_tag(self, migrate):
        """Deprecation clears the tag of the working copy; the copy made
        afterwards starts from that."""
        sub = _deprecated_after_tagged_approval()
        migrate()
        ps = PublishedService.objects.get(pk=sub.pk)
        assert (ps.state, ps.primary_maturity_tag) == ("deprecated", None)

    def test_an_approved_copy_keeps_its_maturity_tag(self, migrate):
        sub = _full()
        migrate()
        assert PublishedService.objects.get(pk=sub.pk).primary_maturity_tag == "mature"

    def test_the_migration_runs_after_the_tags_of_deprecated_services_are_cleared(
        self,
    ):
        loader = MigrationLoader(None, ignore_no_migrations=True)
        done_before = loader.graph.forwards_plan(("catalogue", NAME))
        assert ("submissions", "0023_clear_tags_on_deprecated") in done_before

    def test_deprecated_after_approval_is_published_as_deprecated(self, migrate):
        sub = _deprecated_after_approval()
        migrate()
        ps = PublishedService.objects.get(pk=sub.pk)
        assert (ps.state, ps.pending_update) == ("deprecated", False)

    @pytest.mark.parametrize(
        "scenario",
        [
            "deprecated_edited_after_approval",
            "deprecated_after_review",
            "deprecated_never_approved",
            "waiting_submitted",
            "waiting_under_review",
            "rejected",
            "draft",
            "submitted_never_approved",
        ],
    )
    def test_stays_private(self, migrate, scenario):
        sub = SCENARIOS[scenario]()
        migrate()
        assert not PublishedService.objects.filter(pk=sub.pk).exists()
        assert not PublishedRevision.objects.filter(submission=sub).exists()

    @pytest.mark.parametrize("name", sorted(UNREADABLE_CHANGES))
    def test_an_unreadable_log_entry_keeps_a_deprecated_service_private(
        self, migrate, name
    ):
        """An entry that cannot be read may hide a content edit; the migration
        must not fail on it either, as that would stop the deploy."""
        sub = _deprecated_with_unreadable_log(UNREADABLE_CHANGES[name])
        out = migrate()
        assert not PublishedService.objects.filter(pk=sub.pk).exists()
        assert "'deprecated_unconfirmed': 1" in out

    def test_an_edit_before_a_synthetic_approval_keeps_the_service_private(
        self, migrate
    ):
        """The date of a synthetic approval is not a real date: the edit may
        have been made after the real approval."""
        sub = _deprecated_after_a_review(True, 30)
        out = migrate()
        assert not PublishedService.objects.filter(pk=sub.pk).exists()
        assert "'deprecated_unconfirmed': 1" in out

    def test_an_edit_before_a_real_approval_does_not_block(self, migrate):
        sub = _deprecated_after_a_review(False, 30)
        migrate()
        assert PublishedService.objects.get(pk=sub.pk).state == "deprecated"

    def test_an_edit_ahead_of_a_synthetic_approval_does_not_block(self, migrate):
        sub = _deprecated_after_a_review(True, -30)
        migrate()
        assert PublishedService.objects.get(pk=sub.pk).state == "deprecated"

    def test_an_empty_log_entry_does_not_block(self, migrate):
        sub = _deprecated_with_unreadable_log([])
        migrate()
        assert PublishedService.objects.get(pk=sub.pk).state == "deprecated"

    def test_a_service_without_logo_gets_an_empty_logo_value(self, migrate):
        sub = _approved_without_logo()
        migrate()
        rev = PublishedRevision.objects.get(submission=sub)
        assert rev.content["logo"] == ""
        assert rev.content_hash == publishing.content_hash(
            publishing.build_content(sub)
        )

    def test_status_and_tag_entries_after_the_approval_do_not_block(self, migrate):
        sub = _deprecated_with_tag_entry_after_approval()
        migrate()
        assert PublishedService.objects.get(pk=sub.pk).state == "deprecated"

    def test_dates(self, migrate):
        long_ago = _approved_long_ago()
        long_ago.refresh_from_db()
        synthetic = _approved_synthetic()
        real = _published()
        approved_at = (
            SubmissionStatusEvent.objects.filter(submission=real, to_status="approved")
            .get()
            .changed_at
        )
        _unpublish(real)
        twice = _approved_twice()
        first_approval = (
            SubmissionStatusEvent.objects.filter(submission=twice, to_status="approved")
            .earliest("id")
            .changed_at
        )
        migrate()
        ps = PublishedService.objects.get(pk=long_ago.pk)
        assert ps.public_modified_at == long_ago.updated_at
        assert ps.revision.public_modified_at == long_ago.updated_at
        assert PublishedService.objects.get(pk=synthetic.pk).first_published_at is None
        assert PublishedService.objects.get(pk=real.pk).first_published_at == (
            approved_at
        )
        assert PublishedService.objects.get(pk=twice.pk).first_published_at == (
            first_approval
        )

    def test_already_published_services_are_left_alone(self, migrate):
        sub = _published()
        before = _snapshot()
        ServiceSubmission.objects.filter(pk=sub.pk).update(service_name="Edited later")
        migrate()
        assert _snapshot() == before

    def test_a_service_with_revisions_but_no_copy_is_left_alone(self, migrate):
        """rebuild_published recreates such a copy from its own revisions."""
        sub = _published()
        PublishedService.objects.filter(pk=sub.pk).delete()
        migrate()
        assert not PublishedService.objects.filter(pk=sub.pk).exists()
        assert PublishedRevision.objects.filter(submission=sub).count() == 1

    def test_running_again_creates_nothing(self, migrate):
        _legacy_approved()
        _deprecated_after_approval()
        migrate()
        before = _snapshot()
        assert migrate() == ""
        assert _snapshot() == before

    def test_candidate_rows_are_locked(self, migrate, locked_models):
        _legacy_approved()
        migrate()
        assert locked_models == ["ServiceSubmission"]

    def test_prints_counts_only(self, migrate):
        approved = _legacy_approved(service_name="Example Distinctive Name")
        listed = ServiceSubmissionFactory(
            status="deprecated", biotools_url="", service_name="Example Other Name"
        )
        out = migrate()
        assert "Example Distinctive Name" not in out
        assert "Example Other Name" not in out
        assert str(approved.pk) not in out and str(listed.pk) not in out
        assert "'published': 1" in out

    def test_prints_nothing_on_an_empty_database(self, migrate):
        assert migrate() == ""


@pytest.mark.django_db(transaction=True)
def test_a_failure_part_way_leaves_nothing_published(monkeypatch):
    """The migration runs in one transaction: an error on a later service
    undoes the services published before it, and the migration is not marked
    as applied, so the deploy stops and can simply be run again."""
    before = [("catalogue", "0001_initial")]
    after = [("catalogue", NAME)]
    real = MIGRATION._publish
    calls = []

    def fail_on_second(*args):
        calls.append(args)
        if len(calls) == 2:
            raise RuntimeError("publish failed")
        return real(*args)

    try:
        MigrationExecutor(connection).migrate(before)
        first, second = _legacy_approved(), _legacy_approved()
        monkeypatch.setattr(MIGRATION, "_publish", fail_on_second)
        with pytest.raises(RuntimeError, match="publish failed"):
            MigrationExecutor(connection).migrate(after)
        assert len(calls) == 2
        assert PublishedService.objects.count() == 0
        assert PublishedRevision.objects.count() == 0
        applied = MigrationExecutor(connection).loader.applied_migrations
        assert ("catalogue", NAME) not in applied
        monkeypatch.setattr(MIGRATION, "_publish", real)
        MigrationExecutor(connection).migrate(after)
        assert set(PublishedService.objects.values_list("pk", flat=True)) == {
            first.pk,
            second.pk,
        }
    finally:
        monkeypatch.undo()
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())


@pytest.mark.django_db(transaction=True)
def test_runs_through_the_executor_and_is_kept_on_rollback():
    """Rolling back to catalogue 0001 keeps the copies; applying the migration
    again adds nothing."""
    before = [("catalogue", "0001_initial")]
    after = [("catalogue", NAME)]
    try:
        MigrationExecutor(connection).migrate(before)
        sub = _legacy_approved()
        MigrationExecutor(connection).migrate(after)
        assert PublishedService.objects.filter(pk=sub.pk).exists()
        MigrationExecutor(connection).migrate(before)
        assert PublishedService.objects.filter(pk=sub.pk).exists()
        MigrationExecutor(connection).migrate(after)
        assert PublishedRevision.objects.filter(submission=sub).count() == 1
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
