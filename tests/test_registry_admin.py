"""
Registry Admin — Deletion Guard Tests
======================================
Tests for the _SubmissionGuardMixin applied to ServiceCategoryAdmin,
ServiceCenterAdmin, and PrincipalInvestigatorAdmin.

Scenarios covered per model
----------------------------
  1. Single delete: blocked when record is linked to ≥1 submission
  2. Single delete: allowed (proceeds) when record has no submissions
  3. Bulk delete: entire batch blocked when ≥1 record is in use
  4. Bulk delete: entire batch blocked when ALL records are in use
  5. Bulk delete: mixed selection (some in use, some not) — batch aborted
  6. Bulk delete: allowed when NO selected record is in use
  7. Linked-submissions count column returns the correct annotation value

For single-delete tests we hit the detail delete view (GET), which should
redirect to the changelist with an ERROR message when the record is in use,
and return the confirmation page (200) when the record is safe.

For bulk-delete tests we POST to the changelist with action=guarded_delete_selected.
"""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse

from apps.registry.models import PrincipalInvestigator, ServiceCategory, ServiceCenter
from tests.factories import (
    PIFactory,
    ServiceCategoryFactory,
    ServiceCenterFactory,
    ServiceSubmissionFactory,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(db):
    User = get_user_model()
    user = User.objects.create_superuser(
        username="registryadmin",
        password="adminpass123",
        email="registryadmin@example.com",
    )
    c = Client()
    c.force_login(user)
    return c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _delete_url(model_label: str, pk) -> str:
    """Return the admin delete URL for a registry model record."""
    return reverse(f"admin:registry_{model_label}_delete", args=[pk])


def _changelist_url(model_label: str) -> str:
    return reverse(f"admin:registry_{model_label}_changelist")


def _run_bulk_action(admin_client, model_label: str, *pks):
    """POST the guarded_delete_selected action for the given PKs."""
    return admin_client.post(
        _changelist_url(model_label),
        {
            "action": "guarded_delete_selected",
            "_selected_action": [str(pk) for pk in pks],
        },
    )


def _error_messages(response) -> list[str]:
    return [
        str(m) for m in get_messages(response.wsgi_request) if m.level_tag == "error"
    ]


def _success_messages(response) -> list[str]:
    return [
        str(m) for m in get_messages(response.wsgi_request) if m.level_tag == "success"
    ]


# ===========================================================================
# ServiceCategory
# ===========================================================================


@pytest.mark.django_db
class TestServiceCategoryDeletionGuard:
    # ------------------------------------------------------------------
    # Single delete
    # ------------------------------------------------------------------

    def test_single_delete_blocked_when_in_use(self, admin_client):
        """GET delete view redirects to changelist with error when category is in use."""
        cat = ServiceCategoryFactory()
        ServiceSubmissionFactory(service_categories=[cat])

        resp = admin_client.get(_delete_url("servicecategory", cat.pk))

        assert resp.status_code == 302
        assert resp["Location"].endswith(_changelist_url("servicecategory"))
        assert ServiceCategory.objects.filter(pk=cat.pk).exists(), (
            "Category must not be deleted"
        )
        errors = _error_messages(resp)
        assert errors, "An error message must be shown"
        assert "1\xa0submission" in errors[0]

    def test_single_delete_shows_confirmation_when_not_in_use(self, admin_client):
        """GET delete view returns 200 (confirmation page) when category is safe."""
        cat = ServiceCategoryFactory()

        resp = admin_client.get(_delete_url("servicecategory", cat.pk))

        assert resp.status_code == 200

    def test_single_delete_proceeds_when_not_in_use(self, admin_client):
        """POST delete confirmation deletes the category when it has no submissions."""
        cat = ServiceCategoryFactory()
        pk = cat.pk

        resp = admin_client.post(_delete_url("servicecategory", pk), {"post": "yes"})

        assert resp.status_code == 302
        assert not ServiceCategory.objects.filter(pk=pk).exists()

    def test_single_delete_blocked_multiple_submissions(self, admin_client):
        """Error message reports the correct submission count (>1)."""
        cat = ServiceCategoryFactory()
        for _ in range(3):
            ServiceSubmissionFactory(service_categories=[cat])

        resp = admin_client.get(_delete_url("servicecategory", cat.pk))

        assert resp.status_code == 302
        errors = _error_messages(resp)
        assert "3\xa0submissions" in errors[0]

    # ------------------------------------------------------------------
    # Bulk delete
    # ------------------------------------------------------------------

    def test_bulk_delete_blocked_when_one_in_use(self, admin_client):
        """Batch aborted when a single selected category is in use."""
        cat_in_use = ServiceCategoryFactory()
        ServiceSubmissionFactory(service_categories=[cat_in_use])
        cat_safe = ServiceCategoryFactory()

        resp = _run_bulk_action(
            admin_client, "servicecategory", cat_in_use.pk, cat_safe.pk
        )

        assert resp.status_code == 302
        # Neither record should be deleted
        assert ServiceCategory.objects.filter(pk=cat_in_use.pk).exists()
        assert ServiceCategory.objects.filter(pk=cat_safe.pk).exists()
        errors = _error_messages(resp)
        assert errors
        assert "Deletion blocked" in errors[0]

    def test_bulk_delete_blocked_when_all_in_use(self, admin_client):
        """Batch aborted when every selected category is in use."""
        cats = [ServiceCategoryFactory() for _ in range(2)]
        for cat in cats:
            ServiceSubmissionFactory(service_categories=[cat])

        resp = _run_bulk_action(admin_client, "servicecategory", *[c.pk for c in cats])

        assert resp.status_code == 302
        for cat in cats:
            assert ServiceCategory.objects.filter(pk=cat.pk).exists()
        errors = _error_messages(resp)
        assert "2\xa0records are still referenced" in errors[0]

    def test_bulk_delete_allowed_when_none_in_use(self, admin_client):
        """Batch succeeds when all selected categories have no submissions."""
        cats = [ServiceCategoryFactory() for _ in range(3)]
        pks = [c.pk for c in cats]

        resp = _run_bulk_action(admin_client, "servicecategory", *pks)

        assert resp.status_code == 302
        assert not ServiceCategory.objects.filter(pk__in=pks).exists()
        success = _success_messages(resp)
        assert success
        assert "3" in success[0]

    def test_bulk_delete_mixed_selection_aborts_entire_batch(self, admin_client):
        """If one record in a mixed selection is in use, nothing is deleted."""
        cat_in_use = ServiceCategoryFactory()
        ServiceSubmissionFactory(service_categories=[cat_in_use])
        safe_cats = [ServiceCategoryFactory() for _ in range(2)]
        all_pks = [cat_in_use.pk] + [c.pk for c in safe_cats]

        resp = _run_bulk_action(admin_client, "servicecategory", *all_pks)

        assert resp.status_code == 302
        # Safe ones must NOT have been deleted (batch aborted)
        for cat in safe_cats:
            assert ServiceCategory.objects.filter(pk=cat.pk).exists()

    # ------------------------------------------------------------------
    # Linked submissions column annotation
    # ------------------------------------------------------------------

    def test_linked_submissions_annotation_correct(self, admin_client, db):
        """The list queryset annotation counts submissions per category correctly."""
        from apps.registry.admin import ServiceCategoryAdmin
        from django.test import RequestFactory
        from django.contrib.auth import get_user_model

        cat0 = ServiceCategoryFactory()
        cat2 = ServiceCategoryFactory()
        ServiceSubmissionFactory(service_categories=[cat2])
        ServiceSubmissionFactory(service_categories=[cat2])

        User = get_user_model()
        user = User.objects.get(username="registryadmin")
        request = RequestFactory().get("/")
        request.user = user

        ma = ServiceCategoryAdmin(ServiceCategory, None)
        qs = ma.get_queryset(request)

        counts = {obj.pk: obj._submission_count for obj in qs}
        assert counts[cat0.pk] == 0
        assert counts[cat2.pk] == 2


# ===========================================================================
# ServiceCenter
# ===========================================================================


@pytest.mark.django_db
class TestServiceCenterDeletionGuard:
    def test_single_delete_blocked_when_in_use(self, admin_client):
        center = ServiceCenterFactory()
        ServiceSubmissionFactory(service_center=center)

        resp = admin_client.get(_delete_url("servicecenter", center.pk))

        assert resp.status_code == 302
        assert resp["Location"].endswith(_changelist_url("servicecenter"))
        assert ServiceCenter.objects.filter(pk=center.pk).exists()
        errors = _error_messages(resp)
        assert errors
        assert "1\xa0submission" in errors[0]

    def test_single_delete_shows_confirmation_when_not_in_use(self, admin_client):
        center = ServiceCenterFactory()

        resp = admin_client.get(_delete_url("servicecenter", center.pk))

        assert resp.status_code == 200

    def test_single_delete_proceeds_when_not_in_use(self, admin_client):
        center = ServiceCenterFactory()
        pk = center.pk

        resp = admin_client.post(_delete_url("servicecenter", pk), {"post": "yes"})

        assert resp.status_code == 302
        assert not ServiceCenter.objects.filter(pk=pk).exists()

    def test_bulk_delete_blocked_when_one_in_use(self, admin_client):
        center_in_use = ServiceCenterFactory()
        ServiceSubmissionFactory(service_center=center_in_use)
        center_safe = ServiceCenterFactory()

        resp = _run_bulk_action(
            admin_client, "servicecenter", center_in_use.pk, center_safe.pk
        )

        assert resp.status_code == 302
        assert ServiceCenter.objects.filter(pk=center_in_use.pk).exists()
        assert ServiceCenter.objects.filter(pk=center_safe.pk).exists()
        errors = _error_messages(resp)
        assert errors
        assert "Deletion blocked" in errors[0]

    def test_bulk_delete_allowed_when_none_in_use(self, admin_client):
        centers = [ServiceCenterFactory() for _ in range(2)]
        pks = [c.pk for c in centers]

        resp = _run_bulk_action(admin_client, "servicecenter", *pks)

        assert resp.status_code == 302
        assert not ServiceCenter.objects.filter(pk__in=pks).exists()
        success = _success_messages(resp)
        assert success

    def test_bulk_delete_mixed_selection_aborts_entire_batch(self, admin_client):
        center_in_use = ServiceCenterFactory()
        ServiceSubmissionFactory(service_center=center_in_use)
        safe_center = ServiceCenterFactory()

        resp = _run_bulk_action(
            admin_client, "servicecenter", center_in_use.pk, safe_center.pk
        )

        assert resp.status_code == 302
        assert ServiceCenter.objects.filter(pk=safe_center.pk).exists()
        errors = _error_messages(resp)
        assert errors

    def test_linked_submissions_annotation_correct(self, admin_client, db):
        from apps.registry.admin import ServiceCenterAdmin
        from django.test import RequestFactory
        from django.contrib.auth import get_user_model

        center0 = ServiceCenterFactory()
        center3 = ServiceCenterFactory()
        for _ in range(3):
            ServiceSubmissionFactory(service_center=center3)

        User = get_user_model()
        user = User.objects.get(username="registryadmin")
        request = RequestFactory().get("/")
        request.user = user

        ma = ServiceCenterAdmin(ServiceCenter, None)
        qs = ma.get_queryset(request)

        counts = {obj.pk: obj._submission_count for obj in qs}
        assert counts[center0.pk] == 0
        assert counts[center3.pk] == 3


# ===========================================================================
# PrincipalInvestigator
# ===========================================================================


@pytest.mark.django_db
class TestPrincipalInvestigatorDeletionGuard:
    def test_single_delete_blocked_when_in_use(self, admin_client):
        pi = PIFactory()
        ServiceSubmissionFactory(responsible_pis=[pi])

        resp = admin_client.get(_delete_url("principalinvestigator", pi.pk))

        assert resp.status_code == 302
        assert resp["Location"].endswith(_changelist_url("principalinvestigator"))
        assert PrincipalInvestigator.objects.filter(pk=pi.pk).exists()
        errors = _error_messages(resp)
        assert errors
        assert "1\xa0submission" in errors[0]

    def test_single_delete_shows_confirmation_when_not_in_use(self, admin_client):
        pi = PIFactory()

        resp = admin_client.get(_delete_url("principalinvestigator", pi.pk))

        assert resp.status_code == 200

    def test_single_delete_proceeds_when_not_in_use(self, admin_client):
        pi = PIFactory()
        pk = pi.pk

        resp = admin_client.post(
            _delete_url("principalinvestigator", pk), {"post": "yes"}
        )

        assert resp.status_code == 302
        assert not PrincipalInvestigator.objects.filter(pk=pk).exists()

    def test_bulk_delete_blocked_when_one_in_use(self, admin_client):
        pi_in_use = PIFactory()
        ServiceSubmissionFactory(responsible_pis=[pi_in_use])
        pi_safe = PIFactory()

        resp = _run_bulk_action(
            admin_client, "principalinvestigator", pi_in_use.pk, pi_safe.pk
        )

        assert resp.status_code == 302
        assert PrincipalInvestigator.objects.filter(pk=pi_in_use.pk).exists()
        assert PrincipalInvestigator.objects.filter(pk=pi_safe.pk).exists()
        errors = _error_messages(resp)
        assert errors
        assert "Deletion blocked" in errors[0]

    def test_bulk_delete_allowed_when_none_in_use(self, admin_client):
        pis = [PIFactory() for _ in range(2)]
        pks = [p.pk for p in pis]

        resp = _run_bulk_action(admin_client, "principalinvestigator", *pks)

        assert resp.status_code == 302
        assert not PrincipalInvestigator.objects.filter(pk__in=pks).exists()
        success = _success_messages(resp)
        assert success

    def test_bulk_delete_mixed_selection_aborts_entire_batch(self, admin_client):
        pi_in_use = PIFactory()
        ServiceSubmissionFactory(responsible_pis=[pi_in_use])
        safe_pi = PIFactory()

        resp = _run_bulk_action(
            admin_client, "principalinvestigator", pi_in_use.pk, safe_pi.pk
        )

        assert resp.status_code == 302
        assert PrincipalInvestigator.objects.filter(pk=safe_pi.pk).exists()
        errors = _error_messages(resp)
        assert errors

    def test_bulk_delete_all_in_use_reports_correct_count(self, admin_client):
        pis = [PIFactory() for _ in range(2)]
        for pi in pis:
            ServiceSubmissionFactory(responsible_pis=[pi])

        resp = _run_bulk_action(
            admin_client, "principalinvestigator", *[p.pk for p in pis]
        )

        errors = _error_messages(resp)
        assert "2\xa0records are still referenced" in errors[0]

    def test_linked_submissions_annotation_correct(self, admin_client, db):
        from apps.registry.admin import PrincipalInvestigatorAdmin
        from django.test import RequestFactory
        from django.contrib.auth import get_user_model

        pi0 = PIFactory()
        pi2 = PIFactory()
        ServiceSubmissionFactory(responsible_pis=[pi2])
        ServiceSubmissionFactory(responsible_pis=[pi2])

        User = get_user_model()
        user = User.objects.get(username="registryadmin")
        request = RequestFactory().get("/")
        request.user = user

        ma = PrincipalInvestigatorAdmin(PrincipalInvestigator, None)
        qs = ma.get_queryset(request)

        counts = {obj.pk: obj._submission_count for obj in qs}
        assert counts[pi0.pk] == 0
        assert counts[pi2.pk] == 2

    # ------------------------------------------------------------------
    # Cross-status: submissions in any status are counted
    # ------------------------------------------------------------------

    def test_single_delete_blocked_regardless_of_submission_status(self, admin_client):
        """A PI used only by a rejected submission must still be guarded."""
        pi = PIFactory()
        ServiceSubmissionFactory(responsible_pis=[pi], status="rejected")

        resp = admin_client.get(_delete_url("principalinvestigator", pi.pk))

        assert resp.status_code == 302
        assert PrincipalInvestigator.objects.filter(pk=pi.pk).exists()

    def test_single_delete_blocked_for_draft_submission(self, admin_client):
        pi = PIFactory()
        ServiceSubmissionFactory(responsible_pis=[pi], status="draft")

        resp = admin_client.get(_delete_url("principalinvestigator", pi.pk))

        assert resp.status_code == 302
        assert PrincipalInvestigator.objects.filter(pk=pi.pk).exists()
