"""Linked rows are told apart by primary key, not by display name.

Two principal investigators (or service centres) can share a display name.
Swapping one for the other is a change: an approved service goes back to
review, the public copy stays as it was, and the change log says so.
"""

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.catalogue.models import PublishedService
from apps.registry.models import PrincipalInvestigator, ServiceCenter
from apps.submissions.edits import apply_edit, capture
from apps.submissions.lifecycle import get_no_reset_fields
from apps.submissions.models import SubmissionChangeLog
from tests.factories import APIKeyFactory, ServiceSubmissionFactory
from tests.helpers import edit_form_data

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def exempt(settings):
    settings.SUBMISSION_NO_RESET_FIELDS = ["github_url"]
    get_no_reset_fields.cache_clear()
    yield
    get_no_reset_fields.cache_clear()


def _doe(institute):
    return PrincipalInvestigator.objects.create(
        last_name="Doe", first_name="John", institute=institute
    )


def _approved_with(pi):
    sub = ServiceSubmissionFactory(status="approved", biotools_url="", published=True)
    sub.responsible_pis.set([pi])
    return sub


def _link_changes(sub, field="responsible_pis"):
    return [
        c
        for log in SubmissionChangeLog.objects.filter(submission=sub)
        for c in log.changes
        if c["field"] == field
    ]


def _assert_sent_back_and_logged(sub, old_pi, new_pi):
    sub.refresh_from_db()
    assert sub.status == "submitted"
    published = PublishedService.objects.get(submission=sub)
    assert published.pending_update is True
    assert [str(p) for p in sub.responsible_pis.all()] == ["Doe, John"]
    assert sub.responsible_pis.get().pk == new_pi.pk
    (change,) = _link_changes(sub)
    assert change["old"] != change["new"]
    assert "Inst A" in change["old"]
    assert "Inst B" in change["new"]


def test_web_edit_swapping_a_same_named_pi_resets_an_approved_service(client):
    inst_a, inst_b = _doe("Inst A"), _doe("Inst B")
    sub = _approved_with(inst_a)
    key_obj, _ = APIKeyFactory.create_with_plaintext(submission=sub)
    session = client.session
    session["edit_grants"] = {str(sub.pk): str(key_obj.pk)}
    session.save()
    resp = client.post(
        reverse("submissions:edit", args=[sub.pk]),
        data=edit_form_data(sub, responsible_pis=[inst_b.pk]),
    )
    assert resp.status_code == 302, resp.content
    _assert_sent_back_and_logged(sub, inst_a, inst_b)


def test_api_patch_swapping_a_same_named_pi_resets_an_approved_service():
    inst_a, inst_b = _doe("Inst A"), _doe("Inst B")
    sub = _approved_with(inst_a)
    _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
    resp = api.patch(
        f"/api/v1/submissions/{sub.pk}/",
        {"responsible_pi_ids": [str(inst_b.pk)]},
        format="json",
    )
    assert resp.status_code == 200, resp.content
    _assert_sent_back_and_logged(sub, inst_a, inst_b)


def test_equal_institutes_are_still_told_apart_in_the_log():
    first, second = _doe("Inst A"), _doe("Inst A")
    sub = _approved_with(first)
    before = capture(sub)
    result = apply_edit(
        sub,
        before=before,
        store=lambda: sub.responsible_pis.set([second]),
        actor="submitter",
        by_owner=True,
    )
    assert result.status_reset is True
    (change,) = [c for c in result.changes if c["field"] == "responsible_pis"]
    assert change["old"] != change["new"]


def test_swapping_a_same_named_service_centre_resets_an_approved_service():
    centre_a = ServiceCenter.objects.create(short_name="HUB", full_name="Hub Centre")
    centre_b = ServiceCenter.objects.create(short_name="HUB", full_name="Hub Centre")
    sub = ServiceSubmissionFactory(
        status="approved", biotools_url="", service_center=centre_a, published=True
    )
    before = capture(sub)

    def store():
        sub.service_center = centre_b
        sub.save()

    result = apply_edit(
        sub, before=before, store=store, actor="submitter", by_owner=True
    )
    assert result.status_reset is True
    (change,) = [c for c in result.changes if c["field"] == "service_center"]
    assert change["old"] != change["new"]


def test_an_unchanged_link_set_is_not_a_change():
    pi = _doe("Inst A")
    sub = _approved_with(pi)
    before = capture(sub)
    result = apply_edit(
        sub,
        before=before,
        store=lambda: sub.responsible_pis.set([pi]),
        actor="submitter",
        by_owner=True,
    )
    assert result.status_reset is False
    assert result.changes == []
