"""Data migration that clears maturity tags on deprecated services."""

import importlib

import pytest
from django.apps import apps as django_apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.submissions.models import ServiceSubmission, SubmissionChangeLog
from tests.factories import ServiceSubmissionFactory

pytestmark = [pytest.mark.django_db, pytest.mark.allow_status_drift]
MIGRATION = importlib.import_module(
    "apps.submissions.migrations.0023_clear_tags_on_deprecated"
)


def _tagged(status):
    """A service holding tags whatever its status (as legacy data can)."""
    sub = ServiceSubmissionFactory(status=status, biotools_url="")
    ServiceSubmission.objects.filter(pk=sub.pk).update(
        primary_maturity_tag="mature", secondary_maturity_tags=["unstable"]
    )
    return ServiceSubmission.objects.get(pk=sub.pk)


def _run():
    MIGRATION.clear_tags(django_apps, None)


def test_deprecated_services_lose_their_tags():
    sub = _tagged("deprecated")
    _run()
    sub.refresh_from_db()
    assert sub.primary_maturity_tag is None
    assert sub.secondary_maturity_tags == []


@pytest.mark.parametrize(
    "status", ["draft", "submitted", "under_review", "approved", "rejected"]
)
def test_other_statuses_keep_their_tags(status):
    sub = _tagged(status)
    _run()
    sub.refresh_from_db()
    assert sub.primary_maturity_tag == "mature"
    assert sub.secondary_maturity_tags == ["unstable"]


def test_a_changed_row_gets_a_new_version():
    sub = _tagged("deprecated")
    _run()
    assert ServiceSubmission.objects.get(pk=sub.pk).version == sub.version + 1


def test_untagged_deprecated_rows_are_not_touched():
    sub = ServiceSubmissionFactory(status="deprecated", biotools_url="")
    stored = ServiceSubmission.objects.get(pk=sub.pk)
    _run()
    after = ServiceSubmission.objects.get(pk=sub.pk)
    assert (after.version, after.updated_at) == (stored.version, stored.updated_at)


def test_running_twice_changes_nothing_more():
    sub = _tagged("deprecated")
    _run()
    version = ServiceSubmission.objects.get(pk=sub.pk).version
    _run()
    assert ServiceSubmission.objects.get(pk=sub.pk).version == version


@pytest.mark.django_db(transaction=True)
def test_runs_through_the_migration_executor_and_reverses():
    sub = _tagged("deprecated")
    before = [("submissions", "0022_backfill_status_events")]
    after = [("submissions", "0023_clear_tags_on_deprecated")]
    try:
        MigrationExecutor(connection).migrate(before)
        MigrationExecutor(connection).migrate(after)
        assert ServiceSubmission.objects.get(pk=sub.pk).primary_maturity_tag is None
        MigrationExecutor(connection).migrate(before)
    finally:
        MigrationExecutor(connection).migrate(
            MigrationExecutor(connection).loader.graph.leaf_nodes()
        )


class TestSurfacesWithoutTagFields:
    """The owner's web form and the API have no tag fields, so a deprecated
    service that still holds tags must be reported as a plain error, never a 500."""

    def _deprecated_tagged(self):
        return _tagged("deprecated")

    def test_owner_web_edit_does_not_fail(self, client):
        from django.urls import reverse

        from tests.factories import APIKeyFactory
        from tests.helpers import edit_form_data

        sub = self._deprecated_tagged()
        key_obj, _ = APIKeyFactory.create_with_plaintext(submission=sub)
        session = client.session
        session["edit_grants"] = {str(sub.pk): str(key_obj.pk)}
        session.save()
        resp = client.post(
            reverse("submissions:edit", args=[sub.pk]),
            data=edit_form_data(
                sub,
                service_description="A changed description that is long enough to be valid.",
            ),
        )
        assert resp.status_code == 422
        assert "A deprecated service cannot have maturity tags" in resp.content.decode()
        assert ServiceSubmission.objects.get(pk=sub.pk).status == "deprecated"

    def test_api_patch_does_not_fail(self):
        from rest_framework.test import APIClient

        from tests.factories import APIKeyFactory

        sub = self._deprecated_tagged()
        _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        resp = client.patch(
            f"/api/v1/submissions/{sub.pk}/",
            {
                "service_description": "A changed description that is long enough to be valid."
            },
            format="json",
        )
        assert resp.status_code < 500, resp.content

    def test_the_model_error_is_not_tied_to_a_field(self):
        from django.core.exceptions import NON_FIELD_ERRORS, ValidationError

        sub = self._deprecated_tagged()
        with pytest.raises(ValidationError) as caught:
            sub.clean()
        assert set(caught.value.message_dict) == {NON_FIELD_ERRORS}


class TestChangeHistory:
    def test_one_entry_per_changed_service_in_the_admin_format(self):
        sub = _tagged("deprecated")
        _run()
        (entry,) = SubmissionChangeLog.objects.filter(submission=sub)
        assert entry.changed_by == "system"
        assert entry.changes == [
            {
                "field": "primary_maturity_tag",
                "label": "Primary Maturity Tag",
                "old": "Mature",
                "new": "—",
            },
            {
                "field": "secondary_maturity_tags",
                "label": "Secondary Maturity Tags",
                "old": "Unstable",
                "new": "—",
            },
        ]

    def test_only_the_tag_field_that_was_set_is_listed(self):
        sub = _tagged("deprecated")
        ServiceSubmission.objects.filter(pk=sub.pk).update(primary_maturity_tag=None)
        _run()
        (entry,) = SubmissionChangeLog.objects.filter(submission=sub)
        assert [c["field"] for c in entry.changes] == ["secondary_maturity_tags"]

    def test_untagged_and_other_status_services_get_no_entry(self):
        ServiceSubmissionFactory(status="deprecated", biotools_url="")
        _tagged("approved")
        _run()
        assert SubmissionChangeLog.objects.count() == 0

    def test_running_twice_adds_no_second_entry(self):
        sub = _tagged("deprecated")
        _run()
        _run()
        assert SubmissionChangeLog.objects.filter(submission=sub).count() == 1
