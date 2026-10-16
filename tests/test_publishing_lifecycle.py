"""The published copy across whole review cycles.

Each journey drives a service through several status changes and edits with
the real entry points (the owner's edit form and API, the admin buttons and
actions) and checks the complete published copy after every step: every
public field, the relations, state, pending flag and revisions.

The sequence tests at the end run every combination of status changes and
edits up to a fixed length against a small model of the expected outcome.
The model follows the transition table: a move the table refuses must be
refused and change nothing. They are marked ``slow``: ``make test-slow`` runs
them.
"""

import itertools
import traceback

import pytest
from django.db import transaction
from django.urls import reverse
from rest_framework.test import APIClient

from apps.catalogue.models import PublishedRevision, PublishedService, WithdrawnService
from apps.catalogue.public_fields import (
    PUBLIC_FK_FIELDS,
    PUBLIC_M2M_FIELDS,
    PUBLIC_SCALAR_FIELDS,
)
from apps.catalogue.publishing import republish
from apps.submissions.lifecycle import (
    TransitionNotAllowed,
    get_no_reset_fields,
    set_status,
)
from apps.submissions.models import (
    ServiceSubmission,
    SubmissionStatus,
    SubmissionStatusEvent,
)
from tests.factories import (
    APIKeyFactory,
    PIFactory,
    ServiceCategoryFactory,
    ServiceSubmissionFactory,
)
from tests.helpers import edit_form_data, edit_form_payload

pytestmark = pytest.mark.django_db

CHANGELIST = "admin:submissions_servicesubmission_changelist"
CHANGE = "admin:submissions_servicesubmission_change"
NEW_TEXT = "A reworded description of the service, long enough to be valid here."
OTHER_TEXT = "Another reworded description of the service, also long enough to pass."


@pytest.fixture(autouse=True)
def exempt(settings):
    """website_url can change without review; everything else is reviewed."""
    settings.SUBMISSION_NO_RESET_FIELDS = ["website_url"]
    settings.ALTCHA_HMAC_KEY = ""
    get_no_reset_fields.cache_clear()
    yield
    get_no_reset_fields.cache_clear()


# ── Reading the two copies ───────────────────────────────────────────────────


def _public_values(obj) -> dict:
    """The public fields of a working copy or a published copy (both models
    use the same field names), read straight from the instance."""
    values = {}
    for name in PUBLIC_SCALAR_FIELDS:
        value = getattr(obj, name)
        values[name] = value.name or "" if name == "logo" else value
    for name in PUBLIC_FK_FIELDS:
        values[name] = str(getattr(obj, f"{name}_id"))
    for name in PUBLIC_M2M_FIELDS:
        values[name] = sorted(
            str(pk) for pk in getattr(obj, name).values_list("pk", flat=True)
        )
    return values


def _working(sub) -> dict:
    return _public_values(ServiceSubmission.objects.get(pk=sub.pk))


def _revision_values(revision) -> dict:
    """Revision content in the form of _public_values."""
    content = dict(revision.content)
    for name in PUBLIC_FK_FIELDS:
        content[name] = str(content[name])
    for name in PUBLIC_M2M_FIELDS:
        content[name] = sorted(str(pk) for pk in content[name])
    return content


def _status(sub) -> str:
    return ServiceSubmission.objects.values_list("status", flat=True).get(pk=sub.pk)


def _revisions(sub):
    return list(PublishedRevision.objects.filter(submission=sub).order_by("number"))


def _check_public(sub, *, content, state, pending, revisions):
    """The published copy shows ``content`` with these flags, and its read
    model is the latest of ``revisions`` revisions, numbered 1 to n."""
    ps = PublishedService.objects.get(pk=sub.pk)
    assert _public_values(ps) == content
    assert (ps.state, ps.pending_update) == (state, pending)
    stored = _revisions(sub)
    assert [r.number for r in stored] == list(range(1, revisions + 1))
    assert ps.revision_id == stored[-1].pk
    assert _revision_values(stored[-1]) == content


def _events(sub) -> list:
    return list(
        SubmissionStatusEvent.objects.filter(submission=sub)
        .order_by("id")
        .values_list("to_status", flat=True)
    )


# ── Entry points ─────────────────────────────────────────────────────────────


def _owner_web(client, sub):
    key_obj, _ = APIKeyFactory.create_with_plaintext(submission=sub)
    session = client.session
    session["edit_grants"] = {str(sub.pk): str(key_obj.pk)}
    session.save()
    return client


def _web_edit(client, sub, **changes):
    sub = ServiceSubmission.objects.get(pk=sub.pk)
    resp = client.post(
        reverse("submissions:edit", args=[sub.pk]), edit_form_data(sub, **changes)
    )
    assert resp.status_code == 302, resp.content[:2000]


def _web_deprecate(client, sub):
    resp = client.post(reverse("submissions:edit", args=[sub.pk]), {"_deprecate": "1"})
    assert resp.status_code == 302


def _owner_api(sub):
    _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
    return api


def _api_edit(api, sub, **changes):
    resp = api.patch(f"/api/v1/submissions/{sub.pk}/", changes, format="json")
    assert resp.status_code == 200, resp.content
    return resp


def _action(admin, sub, name):
    resp = admin.post(
        reverse(CHANGELIST), {"action": name, "_selected_action": [str(sub.pk)]}
    )
    assert resp.status_code == 302, resp.content[:2000]


def _button(admin, sub, button, **changes):
    """Press a status button on the admin change form; the form fields are
    saved in the same request, as the browser sends them."""
    sub = ServiceSubmission.objects.get(pk=sub.pk)
    resp = admin.post(
        reverse(CHANGE, args=[sub.pk]),
        edit_form_payload(sub, **{button: "1", **changes}),
    )
    assert resp.status_code == 302, resp.content[:2000]


def _assign_tag(admin, sub, primary):
    resp = admin.post(
        reverse(CHANGELIST),
        {
            "action": "action_assign_maturity_tags",
            "_selected_action": [str(sub.pk)],
            "_assign_tags": "1",
            "primary_maturity_tag": primary,
        },
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )
    assert resp.json()["status"] == "success"


def _restore(admin, sub):
    """Run the restore action and press Confirm on its confirmation page."""
    from html.parser import HTMLParser

    page = admin.post(
        reverse(CHANGELIST),
        {"action": "action_restore_published", "_selected_action": [str(sub.pk)]},
    )
    assert page.status_code == 200

    class Hidden(HTMLParser):
        forms = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "form":
                self.forms.append({})
            elif tag == "input" and attrs.get("type") == "hidden" and self.forms:
                self.forms[-1].setdefault(attrs["name"], []).append(
                    attrs.get("value", "")
                )

    parser = Hidden()
    parser.forms = []
    parser.feed(page.content.decode())
    (fields,) = [f for f in parser.forms if f.get("post") == ["yes"]]
    resp = admin.post(reverse(CHANGELIST), fields)
    assert resp.status_code == 302


def _approved_service(admin, **kw):
    """A service approved by an admin, with two categories and a PI."""
    sub = ServiceSubmissionFactory(**kw)
    _action(admin, sub, "action_approve")
    return ServiceSubmission.objects.get(pk=sub.pk)


# ── Journeys ─────────────────────────────────────────────────────────────────


class TestReviewedEditIsPublishedOnApproval:
    """An approved service gets an edit that needs review. The last approved
    version stays public, marked as waiting, through every review state; the
    approval publishes the edit completely and clears the mark."""

    @pytest.mark.parametrize("via", ["web", "api"])
    def test_edit_then_under_review_then_approved(self, client, superuser_client, via):
        sub = _approved_service(superuser_client)
        v1 = _working(sub)
        _check_public(sub, content=v1, state="active", pending=False, revisions=1)

        new_category = ServiceCategoryFactory()
        new_pi = PIFactory()
        if via == "web":
            _web_edit(
                _owner_web(client, sub),
                sub,
                service_description=NEW_TEXT,
                service_categories=[new_category.pk],
                responsible_pis=[new_pi.pk],
                host_institute="Renamed Institute",
            )
        else:
            _api_edit(
                _owner_api(sub),
                sub,
                service_description=NEW_TEXT,
                service_category_ids=[new_category.pk],
                responsible_pi_ids=[new_pi.pk],
                host_institute="Renamed Institute",
            )
        assert _status(sub) == SubmissionStatus.SUBMITTED
        v2 = _working(sub)
        assert v2 != v1
        _check_public(sub, content=v1, state="active", pending=True, revisions=1)

        _action(superuser_client, sub, "action_mark_under_review")
        _check_public(sub, content=v1, state="active", pending=True, revisions=1)

        _action(superuser_client, sub, "action_approve")
        _check_public(sub, content=v2, state="active", pending=False, revisions=2)
        ps = PublishedService.objects.get(pk=sub.pk)
        # The relations were replaced, not added to.
        assert list(ps.service_categories.values_list("pk", flat=True)) == [
            new_category.pk
        ]
        assert list(ps.responsible_pis.values_list("pk", flat=True)) == [new_pi.pk]
        assert ps.revision.reason == "approval"
        assert ps.revision.actor == "admin:testadmin"
        # The first version is kept unchanged.
        assert _revision_values(_revisions(sub)[0]) == v1

    def test_approval_publishes_the_latest_of_several_edits_under_review(
        self, client, superuser_client
    ):
        sub = _approved_service(superuser_client)
        v1 = _working(sub)
        owner = _owner_web(client, sub)
        _web_edit(owner, sub, service_description=NEW_TEXT)
        _action(superuser_client, sub, "action_mark_under_review")
        # Edits while waiting do not reset or publish anything.
        _web_edit(owner, sub, service_description=OTHER_TEXT)
        _web_edit(owner, sub, website_url="https://example.org/moved")
        assert _status(sub) == SubmissionStatus.UNDER_REVIEW
        _check_public(sub, content=v1, state="active", pending=True, revisions=1)

        _action(superuser_client, sub, "action_approve")
        latest = _working(sub)
        assert latest["service_description"] == OTHER_TEXT
        assert latest["website_url"] == "https://example.org/moved"
        _check_public(sub, content=latest, state="active", pending=False, revisions=2)

    def test_admin_corrections_saved_with_the_approve_button_are_published(
        self, client, superuser_client
    ):
        sub = _approved_service(superuser_client)
        _web_edit(_owner_web(client, sub), sub, service_description=NEW_TEXT)
        _button(superuser_client, sub, "_approve", service_description=OTHER_TEXT)
        assert _status(sub) == SubmissionStatus.APPROVED
        v2 = _working(sub)
        assert v2["service_description"] == OTHER_TEXT
        _check_public(sub, content=v2, state="active", pending=False, revisions=2)

    def test_repeated_review_cycles_publish_one_revision_each(
        self, client, superuser_client
    ):
        sub = _approved_service(superuser_client)
        owner = _owner_web(client, sub)
        versions = [_working(sub)]
        for n in range(3):
            _web_edit(owner, sub, service_description=f"{NEW_TEXT} Round {n}.")
            _check_public(
                sub,
                content=versions[-1],
                state="active",
                pending=True,
                revisions=len(versions),
            )
            _action(superuser_client, sub, "action_approve")
            versions.append(_working(sub))
            _check_public(
                sub,
                content=versions[-1],
                state="active",
                pending=False,
                revisions=len(versions),
            )
        assert [_revision_values(r) for r in _revisions(sub)] == versions


class TestRejection:
    def test_rejected_edit_keeps_the_last_approved_version(
        self, client, superuser_client
    ):
        sub = _approved_service(superuser_client)
        v1 = _working(sub)
        _web_edit(_owner_web(client, sub), sub, service_description=NEW_TEXT)
        _action(superuser_client, sub, "action_mark_under_review")
        _action(superuser_client, sub, "action_reject")
        _check_public(sub, content=v1, state="active", pending=False, revisions=1)

    def test_an_owner_edit_of_a_rejected_service_sends_it_back_to_review(
        self, client, superuser_client
    ):
        """The edit answers the rejection: the service is submitted again, the
        last approved version stays public as waiting, and the approval that
        follows publishes whatever the working copy holds then."""
        sub = _approved_service(superuser_client)
        v1 = _working(sub)
        owner = _owner_web(client, sub)
        _web_edit(owner, sub, service_description=NEW_TEXT)
        _action(superuser_client, sub, "action_reject")
        _check_public(sub, content=v1, state="active", pending=False, revisions=1)

        _web_edit(owner, sub, service_description=OTHER_TEXT)
        assert _status(sub) == SubmissionStatus.SUBMITTED
        _check_public(sub, content=v1, state="active", pending=True, revisions=1)
        assert _events(sub)[-2:] == ["rejected", "submitted"]

        _action(superuser_client, sub, "action_approve")
        v2 = _working(sub)
        assert v2["service_description"] == OTHER_TEXT
        _check_public(sub, content=v2, state="active", pending=False, revisions=2)

    def test_an_edit_that_changes_nothing_leaves_a_rejected_service_rejected(
        self, client, superuser_client
    ):
        sub = _approved_service(superuser_client)
        v1 = _working(sub)
        owner = _owner_web(client, sub)
        _web_edit(owner, sub, service_description=NEW_TEXT)
        _action(superuser_client, sub, "action_reject")
        _web_edit(owner, sub)
        assert _status(sub) == SubmissionStatus.REJECTED
        _check_public(sub, content=v1, state="active", pending=False, revisions=1)

    def test_restoring_before_approval_brings_back_the_published_content(
        self, client, superuser_client
    ):
        sub = _approved_service(superuser_client)
        v1 = _working(sub)
        _web_edit(
            _owner_web(client, sub),
            sub,
            service_description=NEW_TEXT,
            service_categories=[ServiceCategoryFactory().pk],
        )
        _action(superuser_client, sub, "action_reject")
        _restore(superuser_client, sub)
        assert _working(sub) == v1
        assert _status(sub) == SubmissionStatus.REJECTED
        _action(superuser_client, sub, "action_approve")
        # Nothing public changed, so no new revision.
        _check_public(sub, content=v1, state="active", pending=False, revisions=1)


class TestRefusedStatusChanges:
    """The table refuses some moves. Each entry point says so and changes
    nothing: not the status, the history or the published copy."""

    def _assert_untouched(self, sub, v1, events):
        assert _status(sub) == SubmissionStatus.APPROVED
        assert _events(sub) == events
        _check_public(sub, content=v1, state="active", pending=False, revisions=1)

    @pytest.mark.parametrize("action", ["action_reject", "action_undeprecate"])
    def test_an_admin_action_leaves_an_approved_service_unchanged(
        self, superuser_client, action
    ):
        sub = _approved_service(superuser_client)
        v1, events = _working(sub), _events(sub)
        resp = superuser_client.post(
            reverse(CHANGELIST),
            {"action": action, "_selected_action": [str(sub.pk)]},
            follow=True,
        )
        assert resp.status_code == 200
        assert "is not allowed from Approved" in " ".join(
            str(m) for m in resp.context["messages"]
        )
        self._assert_untouched(sub, v1, events)

    def test_an_admin_button_is_refused_without_saving_the_form(self, superuser_client):
        sub = _approved_service(superuser_client)
        v1, events = _working(sub), _events(sub)
        sub = ServiceSubmission.objects.get(pk=sub.pk)
        resp = superuser_client.post(
            reverse(CHANGE, args=[sub.pk]),
            edit_form_payload(sub, _reject="1", service_description=NEW_TEXT),
            follow=True,
        )
        assert resp.status_code == 200
        assert "is not allowed from Approved" in " ".join(
            str(m) for m in resp.context["messages"]
        )
        assert _working(sub) == v1
        self._assert_untouched(sub, v1, events)

    def test_the_change_form_offers_no_button_for_a_refused_move(
        self, superuser_client
    ):
        sub = _approved_service(superuser_client)
        resp = superuser_client.get(reverse(CHANGE, args=[sub.pk]))
        page = resp.content.decode()
        assert 'name="_under_review"' in page
        assert 'name="_deprecate"' in page
        assert 'name="_reject"' not in page
        assert 'name="_undeprecate"' not in page

    def test_a_service_in_review_can_still_be_rejected(self, superuser_client):
        sub = _approved_service(superuser_client)
        v1 = _working(sub)
        _action(superuser_client, sub, "action_mark_under_review")
        _action(superuser_client, sub, "action_reject")
        assert _status(sub) == SubmissionStatus.REJECTED
        _check_public(sub, content=v1, state="active", pending=False, revisions=1)


class TestDeprecation:
    @pytest.mark.parametrize("by", ["owner", "admin"])
    def test_deprecated_then_undeprecated_edited_and_approved(
        self, client, superuser_client, by
    ):
        sub = _approved_service(superuser_client)
        v1 = _working(sub)
        owner = _owner_web(client, sub)
        if by == "owner":
            _web_deprecate(owner, sub)
        else:
            _action(superuser_client, sub, "action_deprecate")
        _check_public(sub, content=v1, state="deprecated", pending=False, revisions=1)

        _action(superuser_client, sub, "action_undeprecate")
        assert _status(sub) == SubmissionStatus.SUBMITTED
        _check_public(sub, content=v1, state="deprecated", pending=True, revisions=1)

        _web_edit(owner, sub, service_description=NEW_TEXT)
        _action(superuser_client, sub, "action_mark_under_review")
        _check_public(sub, content=v1, state="deprecated", pending=True, revisions=1)

        _action(superuser_client, sub, "action_approve")
        v2 = _working(sub)
        _check_public(sub, content=v2, state="active", pending=False, revisions=2)

    def test_edits_of_a_deprecated_service_wait_for_its_approval(
        self, client, superuser_client
    ):
        sub = _approved_service(superuser_client)
        v1 = _working(sub)
        owner = _owner_web(client, sub)
        _web_deprecate(owner, sub)
        _web_edit(owner, sub, service_description=NEW_TEXT)
        _api_edit(_owner_api(sub), sub, website_url="https://example.org/moved")
        assert _status(sub) == SubmissionStatus.DEPRECATED
        _check_public(sub, content=v1, state="deprecated", pending=False, revisions=1)

        _action(superuser_client, sub, "action_approve")
        v2 = _working(sub)
        assert v2["service_description"] == NEW_TEXT
        assert v2["website_url"] == "https://example.org/moved"
        _check_public(sub, content=v2, state="active", pending=False, revisions=2)

    def test_rejecting_after_undeprecation_keeps_the_copy_deprecated(
        self, superuser_client
    ):
        sub = _approved_service(superuser_client)
        v1 = _working(sub)
        _action(superuser_client, sub, "action_deprecate")
        _action(superuser_client, sub, "action_undeprecate")
        _action(superuser_client, sub, "action_reject")
        _check_public(sub, content=v1, state="deprecated", pending=False, revisions=1)

    def test_a_service_deprecated_before_any_approval_is_never_published(
        self, client, superuser_client
    ):
        sub = ServiceSubmissionFactory()
        _action(superuser_client, sub, "action_reject")
        _action(superuser_client, sub, "action_mark_under_review")
        _web_deprecate(_owner_web(client, sub), sub)
        _action(superuser_client, sub, "action_undeprecate")
        assert not PublishedService.objects.filter(pk=sub.pk).exists()
        assert not PublishedRevision.objects.filter(submission=sub).exists()

        _action(superuser_client, sub, "action_approve")
        _check_public(
            sub, content=_working(sub), state="active", pending=False, revisions=1
        )
        approval = SubmissionStatusEvent.objects.get(
            submission=sub, to_status="approved"
        )
        assert PublishedService.objects.get(pk=sub.pk).first_published_at == (
            approval.changed_at
        )


class TestMaturityTagsAcrossReview:
    def test_tags_stay_through_review_and_publish_with_the_approval(
        self, client, superuser_client
    ):
        """An owner edit sends the service to review and keeps its tags in the
        working copy. The public copy shows the old version, tags included,
        until the approval publishes the working copy."""
        sub = _approved_service(superuser_client)
        _assign_tag(superuser_client, sub, "mature")
        tagged = _working(sub)
        assert tagged["primary_maturity_tag"] == "mature"
        _check_public(sub, content=tagged, state="active", pending=False, revisions=2)

        _web_edit(_owner_web(client, sub), sub, service_description=NEW_TEXT)
        waiting = _working(sub)
        assert waiting["primary_maturity_tag"] == "mature"
        _check_public(sub, content=tagged, state="active", pending=True, revisions=2)

        _action(superuser_client, sub, "action_mark_under_review")
        _action(superuser_client, sub, "action_reject")
        assert _working(sub)["primary_maturity_tag"] == "mature"
        _web_edit(_owner_web(client, sub), sub, service_description=OTHER_TEXT)
        assert _working(sub)["primary_maturity_tag"] == "mature"

        _action(superuser_client, sub, "action_approve")
        approved = _working(sub)
        assert approved["primary_maturity_tag"] == "mature"
        _check_public(sub, content=approved, state="active", pending=False, revisions=3)

        _assign_tag(superuser_client, sub, "emerging")
        _check_public(
            sub, content=_working(sub), state="active", pending=False, revisions=4
        )
        assert PublishedService.objects.get(pk=sub.pk).primary_maturity_tag == (
            "emerging"
        )

    def test_deprecation_clears_the_tags_and_the_next_approval_publishes_that(
        self, superuser_client
    ):
        sub = _approved_service(superuser_client)
        _assign_tag(superuser_client, sub, "mature")
        tagged = _working(sub)

        _action(superuser_client, sub, "action_deprecate")
        assert _working(sub)["primary_maturity_tag"] is None
        # The public copy keeps what was approved until an approval replaces it.
        _check_public(
            sub, content=tagged, state="deprecated", pending=False, revisions=2
        )

        _action(superuser_client, sub, "action_undeprecate")
        _action(superuser_client, sub, "action_approve")
        untagged = _working(sub)
        assert untagged["primary_maturity_tag"] is None
        _check_public(sub, content=untagged, state="active", pending=False, revisions=3)

    def test_the_approve_button_does_not_change_the_tags_of_a_waiting_service(
        self, client, superuser_client
    ):
        """The tag fields can only be edited while a service is approved: a
        value posted for a waiting one is ignored and the kept tag is
        published with the approval."""
        sub = _approved_service(superuser_client)
        _assign_tag(superuser_client, sub, "mature")
        _web_edit(_owner_web(client, sub), sub, service_description=NEW_TEXT)
        sub = ServiceSubmission.objects.get(pk=sub.pk)
        resp = superuser_client.post(
            reverse(CHANGE, args=[sub.pk]),
            edit_form_payload(sub, _approve="1", primary_maturity_tag="emerging"),
        )
        assert resp.status_code == 302
        assert _status(sub) == SubmissionStatus.APPROVED
        approved = _working(sub)
        assert approved["primary_maturity_tag"] == "mature"
        _check_public(sub, content=approved, state="active", pending=False, revisions=3)

    def test_the_change_form_of_a_deprecated_service_ignores_a_posted_tag(
        self, superuser_client
    ):
        sub = _approved_service(superuser_client)
        _action(superuser_client, sub, "action_deprecate")
        v1 = _working(sub)
        sub = ServiceSubmission.objects.get(pk=sub.pk)
        resp = superuser_client.post(
            reverse(CHANGE, args=[sub.pk]),
            edit_form_payload(sub, primary_maturity_tag="mature"),
        )
        assert resp.status_code == 302
        assert _working(sub) == v1
        assert _status(sub) == SubmissionStatus.DEPRECATED
        _check_public(sub, content=v1, state="deprecated", pending=False, revisions=1)


class TestLiveEdits:
    def test_live_edit_then_reviewed_edit_then_approval(self, client, superuser_client):
        sub = _approved_service(superuser_client)
        owner = _owner_web(client, sub)
        _web_edit(owner, sub, website_url="https://example.org/moved")
        assert _status(sub) == SubmissionStatus.APPROVED
        live = _working(sub)
        _check_public(sub, content=live, state="active", pending=False, revisions=2)
        assert _revisions(sub)[-1].reason == "live_edit"

        _web_edit(owner, sub, service_description=NEW_TEXT)
        _check_public(sub, content=live, state="active", pending=True, revisions=2)
        _action(superuser_client, sub, "action_approve")
        _check_public(
            sub, content=_working(sub), state="active", pending=False, revisions=3
        )

    def test_admin_edit_of_an_approved_service_goes_live(self, superuser_client):
        sub = _approved_service(superuser_client)
        sub = ServiceSubmission.objects.get(pk=sub.pk)
        resp = superuser_client.post(
            reverse(CHANGE, args=[sub.pk]),
            edit_form_payload(
                sub,
                service_description=NEW_TEXT,
                service_categories=[ServiceCategoryFactory().pk],
            ),
        )
        assert resp.status_code == 302
        assert _status(sub) == SubmissionStatus.APPROVED
        _check_public(
            sub, content=_working(sub), state="active", pending=False, revisions=2
        )
        assert _revisions(sub)[-1].reason == "admin_edit"


class TestNoOpsAndDeletion:
    def test_approving_an_approved_service_again_changes_nothing(
        self, superuser_client
    ):
        sub = _approved_service(superuser_client)
        before = PublishedService.objects.get(pk=sub.pk).public_modified_at
        _action(superuser_client, sub, "action_approve")
        _check_public(
            sub, content=_working(sub), state="active", pending=False, revisions=1
        )
        assert PublishedService.objects.get(pk=sub.pk).public_modified_at == before
        assert _events(sub) == ["submitted", "approved"]

    def test_deleting_a_service_with_an_edit_under_review(
        self, client, superuser_client
    ):
        sub = _approved_service(superuser_client)
        _web_edit(_owner_web(client, sub), sub, service_description=NEW_TEXT)
        ServiceSubmission.objects.get(pk=sub.pk).delete()
        assert not PublishedService.objects.filter(pk=sub.pk).exists()
        assert not PublishedRevision.objects.filter(submission_id=sub.pk).exists()
        assert WithdrawnService.objects.filter(pk=sub.pk).exists()


class TestClockStepsBack:
    """A host clock can step back by a second or two between two requests.
    The status history must stay in time order then, or the first approval,
    found by its date, would be a later event than the one that published the
    service."""

    @pytest.fixture
    def clock(self, monkeypatch):
        from datetime import datetime, timedelta, timezone

        from django.utils import timezone as dj_timezone

        class Clock:
            now = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)

            def set(self, seconds):
                self.now = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc) + (
                    timedelta(seconds=seconds)
                )

        clock = Clock()
        monkeypatch.setattr(dj_timezone, "now", lambda: clock.now)
        return clock

    def test_the_first_publication_is_the_first_approval_event(self, clock):
        sub = ServiceSubmissionFactory()
        clock.set(10)
        set_status(sub, "approved", actor="admin:jane")
        first_approved = SubmissionStatusEvent.objects.get(
            submission=sub, to_status="approved"
        )

        # The clock steps back 2 s below the first approval and a review cycle
        # follows.
        clock.set(8)
        set_status(sub, "under_review", actor="admin:jane")
        clock.set(8)
        set_status(sub, "approved", actor="admin:jane")

        events = list(
            SubmissionStatusEvent.objects.filter(submission=sub).order_by("id")
        )
        ps = PublishedService.objects.get(pk=sub.pk)
        approvals = [e for e in events if e.to_status == "approved"]
        assert len(approvals) == 2
        assert ps.first_published_at == min(e.changed_at for e in approvals)
        assert ps.first_published_at == first_approved.changed_at
        times = [e.changed_at for e in events]
        assert times == sorted(times)

    def test_an_event_is_never_dated_before_the_one_it_follows(self, clock):
        sub = ServiceSubmissionFactory()
        clock.set(10)
        set_status(sub, "under_review", actor="admin:jane")
        clock.set(7)
        set_status(sub, "approved", actor="admin:jane")
        approval = SubmissionStatusEvent.objects.get(
            submission=sub, to_status="approved"
        )
        assert (
            approval.changed_at
            == SubmissionStatusEvent.objects.get(
                submission=sub, to_status="under_review"
            ).changed_at
        )
        assert PublishedService.objects.get(pk=sub.pk).first_published_at == (
            approval.changed_at
        )


# ── Every sequence of operations ────────────────────────────────────────────
#
# Operations, written the way the entry points write (the journeys above show
# each entry point uses these calls):
#   a status:   set_status (admin buttons, admin actions, owner deprecation)
#   "edit":     an owner edit of a reviewed field through the API
#   "live":     an owner edit of a field that needs no review, through the API
#   "tag":      the maturity tag action of the admin
#
# The model below is written from the rules alone. Moves the rules refuse are
# part of it: the call is refused and nothing changes.

STATUSES = [
    SubmissionStatus.SUBMITTED,
    SubmissionStatus.UNDER_REVIEW,
    SubmissionStatus.APPROVED,
    SubmissionStatus.REJECTED,
    SubmissionStatus.DEPRECATED,
    SubmissionStatus.DRAFT,
]
OPERATIONS = [*STATUSES, "edit", "live", "tag"]
TAGS = ["emerging", "legacy", "mature"]

# Which status a service may be moved to by an admin or the owner's
# deprecation, written out here and not read from the code under test. An
# approved service is withdrawn by deprecating it; it returns to review only
# by an owner edit (below). Nothing moves into draft.
ALLOWED = {
    "submitted": {"under_review", "approved", "rejected", "deprecated"},
    "under_review": {"approved", "rejected", "deprecated"},
    "approved": {"under_review", "deprecated"},
    "rejected": {"submitted", "under_review", "approved", "deprecated"},
    "deprecated": {"submitted", "approved"},
}


class _Rollback(Exception):
    pass


class _Model:
    """What the working copy, the status history and the published copy must
    look like, worked out from the operations alone, without the publishing
    code. It starts from the stored state of the service."""

    def __init__(self, sub):
        row = ServiceSubmission.objects.get(pk=sub.pk)
        ps = PublishedService.objects.filter(pk=sub.pk).first()
        self.status = row.status
        self.events = _events(sub)
        self.working = _working(sub)
        self.secondary = list(row.secondary_maturity_tags or [])
        self.published = ps is not None
        self.content = _public_values(ps) if ps else None
        self.state = ps.state if ps else None
        self.pending = ps.pending_update if ps else None
        self.revisions = len(_revisions(sub))

    def _enter(self, to):
        """The service is now in ``to``; the published copy follows."""
        self.status = to
        self.events.append(to)
        if to == SubmissionStatus.DEPRECATED:
            # Deprecation is the only status change that clears the tags.
            self.working["primary_maturity_tag"] = None
            self.secondary = []
        if to == SubmissionStatus.APPROVED:
            if not self.published or self.working != self.content:
                self.revisions += 1
            self.published = True
            self.content = dict(self.working)
            self.state, self.pending = "active", False
        elif not self.published:
            return
        elif to in (SubmissionStatus.SUBMITTED, SubmissionStatus.UNDER_REVIEW):
            self.pending = True
        elif to == SubmissionStatus.DEPRECATED:
            self.state, self.pending = "deprecated", False
        else:
            self.pending = False

    def move(self, to):
        """An admin or deprecation move. Returns "same", "refused" or "moved"."""
        if to == self.status:
            return "same"
        if to not in ALLOWED.get(self.status, ()):
            return "refused"
        self._enter(to)
        return "moved"

    def _republish(self):
        if self.working != self.content:
            self.revisions += 1
            self.content = dict(self.working)

    def reviewed_edit(self, changes):
        """An owner edit of a field that needs review."""
        self.working.update(changes)
        if self.status in (SubmissionStatus.APPROVED, SubmissionStatus.REJECTED):
            self._enter(SubmissionStatus.SUBMITTED)

    def live_edit(self, changes):
        """An owner edit of a field that needs no review: an approved service
        stays approved and goes live; a rejected one is answered and returns to
        review; any other status is not affected."""
        self.working.update(changes)
        if self.status == SubmissionStatus.APPROVED:
            self._republish()
        elif self.status == SubmissionStatus.REJECTED:
            self._enter(SubmissionStatus.SUBMITTED)

    def assign_tags(self, primary, secondary):
        """The tag action: only an approved service takes tags. Returns whether
        it did."""
        if self.status != SubmissionStatus.APPROVED:
            return False
        self.working["primary_maturity_tag"] = primary
        self.secondary = secondary
        self._republish()
        return True


def _check_state(sub, model, where):
    """The stored service, its history and its published copy match the model."""
    row = ServiceSubmission.objects.get(pk=sub.pk)
    assert row.status == model.status, where
    assert _events(sub) == model.events, where
    assert _public_values(row) == model.working, where
    assert (row.secondary_maturity_tags or []) == model.secondary, where
    if not model.published:
        assert not PublishedService.objects.filter(pk=sub.pk).exists(), where
        assert not PublishedRevision.objects.filter(submission=sub).exists(), where
        return
    ps = PublishedService.objects.get(pk=sub.pk)
    assert _public_values(ps) == model.content, where
    assert (ps.state, ps.pending_update) == (model.state, model.pending), where
    stored = _revisions(sub)
    assert [r.number for r in stored] == list(range(1, model.revisions + 1)), where
    assert ps.revision_id == stored[-1].pk, where
    assert _revision_values(stored[-1]) == model.content, where
    if model.status == SubmissionStatus.APPROVED:
        assert _public_values(ps) == model.working, where
    # The copy was first published when the first approval happened.
    approval = (
        SubmissionStatusEvent.objects.filter(
            submission=sub, to_status="approved", is_synthetic=False
        )
        .order_by("id")
        .first()
    )
    assert ps.first_published_at == approval.changed_at, where


def _run(sub, sequence, api, admin, categories):
    model = _Model(sub)
    kept = [(r.pk, r.content_hash, r.content) for r in _revisions(sub)]
    for step, op in enumerate(sequence):
        where = f"{sequence} at step {step} ({op})"
        before = ServiceSubmission.objects.get(pk=sub.pk)
        if op in STATUSES:
            outcome = model.move(op)
            if outcome == "refused":
                try:
                    set_status(before, op, actor="admin:jane")
                except TransitionNotAllowed:
                    pass
                else:
                    raise AssertionError(f"{where}: the move was not refused")
                assert (
                    ServiceSubmission.objects.get(pk=sub.pk).version == before.version
                ), where
            else:
                assert set_status(before, op, actor="admin:jane") == (
                    outcome == "moved"
                ), where
        elif op == "edit":
            current = set(model.working["service_categories"])
            other = next(c for c in categories if str(c.pk) not in current)
            text = f"{NEW_TEXT} Step {step}."
            _api_edit(
                api, sub, service_description=text, service_category_ids=[other.pk]
            )
            model.reviewed_edit(
                {"service_description": text, "service_categories": [str(other.pk)]}
            )
        elif op == "live":
            url = f"https://example.org/step-{step}"
            _api_edit(api, sub, website_url=url)
            model.live_edit({"website_url": url})
        elif op == "tag":
            choices = [t for t in TAGS if t != model.working["primary_maturity_tag"]]
            primary = choices[step % len(choices)]
            secondary = ["unstable"] if step % 2 == 0 else []
            resp = admin.post(
                reverse(CHANGELIST),
                {
                    "action": "action_assign_maturity_tags",
                    "_selected_action": [str(sub.pk)],
                    "_assign_tags": "1",
                    "primary_maturity_tag": primary,
                    "secondary_maturity_tags": secondary,
                },
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            )
            took = model.assign_tags(primary, secondary)
            assert resp.json()["status"] == ("success" if took else "error"), where

        _check_state(sub, model, where)
        # Earlier revisions are never changed.
        stored = [(r.pk, r.content_hash, r.content) for r in _revisions(sub)]
        assert stored[: len(kept)] == kept, where
        kept = stored


@pytest.mark.slow
@pytest.mark.parametrize("start", ["new", "published"])
def test_every_sequence_of_three_operations(start, superuser_client):
    """From a new service and from a published one with maturity tags, run
    every sequence of three operations and check the working copy, the status
    history and the published copy after each step against the model. Moves
    the transition table refuses must be refused and change nothing."""
    categories = [ServiceCategoryFactory() for _ in range(3)]
    failures = []
    for sequence in itertools.product(OPERATIONS, repeat=3):
        try:
            with transaction.atomic():
                sub = ServiceSubmissionFactory(service_categories=[categories[0]])
                if start == "published":
                    set_status(sub, "approved", actor="admin:jane")
                    sub = ServiceSubmission.objects.get(pk=sub.pk)
                    sub.primary_maturity_tag = "mature"
                    sub.secondary_maturity_tags = ["unstable"]
                    sub.save(
                        update_fields=[
                            "primary_maturity_tag",
                            "secondary_maturity_tags",
                        ]
                    )
                    republish(sub, reason="tag_change", actor="admin:jane")
                _run(sub, sequence, _owner_api(sub), superuser_client, categories)
                raise _Rollback
        except _Rollback:
            pass
        except AssertionError:
            failures.append(traceback.format_exc()[-2500:])
    assert not failures, f"{len(failures)} sequences failed:\n" + "\n".join(
        failures[:20]
    )
