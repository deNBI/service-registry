"""Status transition table: which status changes are possible, and the UI."""

from html.parser import HTMLParser
from unittest import mock

import pytest
from django.urls import reverse

from apps.submissions import lifecycle
from apps.submissions.lifecycle import (
    TransitionNotAllowed,
    allowed_transitions,
    set_status,
)
from apps.submissions.models import (
    ServiceSubmission,
    SubmissionChangeLog,
    SubmissionStatus,
)
from tests.factories import APIKeyFactory, ServiceSubmissionFactory
from tests.helpers import edit_form_payload

pytestmark = pytest.mark.django_db

CHANGELIST = "admin:submissions_servicesubmission_changelist"
CHANGE = "admin:submissions_servicesubmission_change"

ALL = ["draft", "submitted", "under_review", "approved", "rejected", "deprecated"]

# Written out independently of the table in lifecycle.py.
ALLOWED = {
    ("submitted", "under_review"),
    ("submitted", "approved"),
    ("submitted", "rejected"),
    ("submitted", "deprecated"),
    ("under_review", "approved"),
    ("under_review", "rejected"),
    ("under_review", "deprecated"),
    ("approved", "under_review"),
    ("approved", "deprecated"),
    ("rejected", "submitted"),
    ("rejected", "under_review"),
    ("rejected", "approved"),
    ("rejected", "deprecated"),
    ("deprecated", "submitted"),
    ("deprecated", "approved"),
    ("draft", "submitted"),
    ("draft", "under_review"),
    ("draft", "approved"),
    ("draft", "rejected"),
    ("draft", "deprecated"),
}
OWNER_EDIT_ONLY = {("approved", "submitted")}

PAIRS = [(a, b) for a in ALL for b in ALL if a != b]


def _make(status):
    return ServiceSubmissionFactory(status=status, biotools_url="")


class TestTable:
    def test_every_status_is_covered_and_draft_is_never_a_target(self):
        assert set(ALL) == set(SubmissionStatus.values)
        assert set(lifecycle.TRANSITIONS) == set(ALL)
        for targets in lifecycle.TRANSITIONS.values():
            assert "draft" not in targets

    @pytest.mark.parametrize("old,new", PAIRS)
    def test_allowed_transitions_matches_the_table(self, old, new):
        assert (new in allowed_transitions(old)) == ((old, new) in ALLOWED)

    @pytest.mark.parametrize("old,new", PAIRS)
    def test_owner_edit_adds_only_the_reset(self, old, new):
        expected = (old, new) in ALLOWED | OWNER_EDIT_ONLY
        assert (new in allowed_transitions(old, owner_edit=True)) == expected

    def test_result_is_a_frozenset_of_status_values(self):
        result = allowed_transitions("approved")
        assert isinstance(result, frozenset)
        assert result == {"under_review", "deprecated"}


class TestSetStatusAgainstTheTable:
    @pytest.mark.parametrize("old,new", sorted(ALLOWED))
    def test_allowed_pair_changes_status_and_records_one_event(self, old, new):
        sub = _make(old)
        events_before = sub.status_events.count()
        assert set_status(sub, new, actor="admin:jane") is True
        sub.refresh_from_db()
        assert sub.status == new
        assert sub.status_events.count() == events_before + 1
        event = sub.status_events.order_by("id").last()
        assert (event.from_status, event.to_status) == (old, new)

    @pytest.mark.parametrize("old,new", [p for p in PAIRS if p not in ALLOWED])
    def test_refused_pair_changes_nothing(self, old, new):
        sub = _make(old)
        events_before = sub.status_events.count()
        version = sub.version
        with pytest.raises(TransitionNotAllowed):
            set_status(sub, new, actor="admin:jane")
        sub.refresh_from_db()
        assert sub.status == old
        assert sub.version == version
        assert sub.status_events.count() == events_before

    @pytest.mark.parametrize("status", ALL)
    def test_the_status_it_already_has_is_a_no_op_not_a_refusal(self, status):
        sub = _make(status)
        assert set_status(sub, status, actor="admin:jane") is False
        assert sub.status_events.count() == 1

    def test_refusal_is_a_value_error_naming_both_labels(self):
        sub = _make("approved")
        with pytest.raises(ValueError) as exc:
            set_status(sub, "rejected", actor="admin:jane")
        assert isinstance(exc.value, TransitionNotAllowed)
        assert "Approved" in str(exc.value)
        assert "Rejected" in str(exc.value)

    def test_label_of_a_multi_word_status_is_used(self):
        sub = _make("under_review")
        with pytest.raises(TransitionNotAllowed) as exc:
            set_status(sub, "submitted", actor="admin:jane")
        assert "Under Review" in str(exc.value)
        assert "Submitted" in str(exc.value)

    def test_unknown_status_stays_a_plain_value_error(self):
        sub = _make("submitted")
        with pytest.raises(ValueError) as exc:
            set_status(sub, "published", actor="admin:jane")
        assert not isinstance(exc.value, TransitionNotAllowed)

    def test_the_stored_status_decides_not_the_instance(self):
        sub = _make("submitted")
        stale = ServiceSubmission.objects.get(pk=sub.pk)
        set_status(sub, "approved", actor="admin:jane")
        # The stale copy still says submitted, where this would be allowed.
        with pytest.raises(TransitionNotAllowed):
            set_status(stale, "rejected", actor="admin:john")
        sub.refresh_from_db()
        assert sub.status == "approved"

    def test_the_stored_status_allows_what_the_instance_would_not(self):
        sub = _make("approved")
        stale = ServiceSubmission.objects.get(pk=sub.pk)
        set_status(sub, "under_review", actor="admin:jane")
        # The stale copy says approved, where this is refused.
        assert set_status(stale, "approved", actor="admin:john") is True
        assert stale.status == "approved"

    @pytest.mark.allow_status_drift
    def test_a_refusal_also_undoes_history_recorded_in_the_same_call(self):
        sub = _make("approved")
        sub.status_events.all().delete()
        with pytest.raises(TransitionNotAllowed):
            set_status(sub, "rejected", actor="admin:jane")
        assert sub.status_events.count() == 0

    def test_recording_missed_history_is_not_subject_to_the_table(self):
        sub = _make("rejected")
        sub.status_events.all().delete()
        # No event ends in the stored status; it is recorded although there is
        # no allowed way from nothing to it, and the same-status request stays
        # a no-op otherwise.
        assert set_status(sub, "rejected", actor="admin:jane") is True
        event = sub.status_events.get()
        assert (event.from_status, event.to_status) == (None, "rejected")


class TestOwnerEdit:
    def test_approved_to_submitted_needs_owner_edit(self):
        sub = _make("approved")
        with pytest.raises(TransitionNotAllowed):
            set_status(sub, "submitted", actor="submitter")
        sub.refresh_from_db()
        assert sub.status == "approved"

    def test_owner_edit_allows_the_reset(self):
        sub = _make("approved")
        assert set_status(sub, "submitted", actor="submitter", owner_edit=True)
        sub.refresh_from_db()
        assert sub.status == "submitted"
        event = sub.status_events.order_by("id").last()
        assert (event.from_status, event.to_status) == ("approved", "submitted")

    def test_owner_edit_does_not_open_other_ways_to_submitted(self):
        sub = _make("under_review")
        with pytest.raises(TransitionNotAllowed):
            set_status(sub, "submitted", actor="submitter", owner_edit=True)
        sub.refresh_from_db()
        assert sub.status == "under_review"


def _messages(response):
    return [str(m) for m in response.context["messages"]]


def _bulk(client, action, *subs):
    return client.post(
        reverse(CHANGELIST),
        {"action": action, "_selected_action": [str(s.pk) for s in subs]},
        follow=True,
    )


class TestAdminBulkActions:
    def test_refused_rows_are_skipped_with_one_warning_and_the_rest_change(
        self, superuser_client, django_capture_on_commit_callbacks
    ):
        ok1 = _make("submitted")
        ok2 = _make("under_review")
        bad1 = _make("approved")  # approved -> rejected is refused
        bad2 = _make("approved")
        # Everything but the approved rows can be rejected.
        with mock.patch(
            "apps.submissions.admin.send_submission_notification.delay"
        ) as delay:
            with django_capture_on_commit_callbacks(execute=True):
                resp = _bulk(superuser_client, "action_reject", ok1, ok2, bad1, bad2)
        for sub, expected in [
            (ok1, "rejected"),
            (ok2, "rejected"),
            (bad1, "approved"),
            (bad2, "approved"),
        ]:
            sub.refresh_from_db()
            assert sub.status == expected
        messages = _messages(resp)
        warnings = [m for m in messages if "not allowed" in m]
        assert len(warnings) == 1
        assert "2 submission(s)" in warnings[0]
        assert any("2 submission(s) marked as Rejected" in m for m in messages)
        # Mails and change-log entries only for the changed rows.
        assert delay.call_count == 2
        for sub in (ok1, ok2):
            assert SubmissionChangeLog.objects.filter(submission=sub).count() == 1
        for sub in (bad1, bad2):
            assert SubmissionChangeLog.objects.filter(submission=sub).count() == 0
            assert sub.status_events.count() == 1

    def test_all_rows_refused_changes_nothing_and_does_not_claim_success(
        self, superuser_client
    ):
        sub = _make("approved")
        resp = _bulk(superuser_client, "action_reject", sub)
        sub.refresh_from_db()
        assert sub.status == "approved"
        messages = _messages(resp)
        assert len([m for m in messages if "not allowed" in m]) == 1
        assert not any("marked as" in m for m in messages)

    @pytest.mark.parametrize("status", ["draft", "rejected", "deprecated"])
    def test_back_to_submitted_says_what_it_did(self, superuser_client, status):
        sub = _make(status)
        resp = _bulk(superuser_client, "action_undeprecate", sub)
        sub.refresh_from_db()
        assert sub.status == "submitted"
        assert _messages(resp) == ["1 submission(s) marked as Submitted."]

    def test_back_to_submitted_is_offered_under_that_name(self, superuser_client):
        _make("deprecated")
        html = superuser_client.get(reverse(CHANGELIST)).content.decode()
        assert "Back to Submitted (selected)" in html
        assert "Undeprecate" not in html

    def test_no_warning_when_every_row_is_allowed(self, superuser_client):
        sub = _make("submitted")
        resp = _bulk(superuser_client, "action_reject", sub)
        assert not any("not allowed" in m for m in _messages(resp))

    def test_rows_already_in_the_target_status_are_not_counted_as_refused(
        self, superuser_client
    ):
        sub = _make("approved")
        resp = _bulk(superuser_client, "action_approve", sub)
        assert not any("not allowed" in m for m in _messages(resp))

    @pytest.mark.parametrize(
        "action,target",
        [
            ("action_approve", "approved"),
            ("action_reject", "rejected"),
            ("action_mark_under_review", "under_review"),
            ("action_deprecate", "deprecated"),
            ("action_undeprecate", "submitted"),
        ],
    )
    @pytest.mark.parametrize("start", ALL)
    def test_every_action_follows_the_table(
        self, superuser_client, action, target, start
    ):
        sub = _make(start)
        _bulk(superuser_client, action, sub)
        sub.refresh_from_db()
        if start == target or (start, target) in ALLOWED:
            assert sub.status == target
        else:
            assert sub.status == start


class _ButtonParser(HTMLParser):
    """Collect the label text of every named <button> on a page."""

    def __init__(self):
        super().__init__()
        self.labels = {}
        self._open = None

    def handle_starttag(self, tag, attrs):
        if tag == "button":
            self._open = dict(attrs).get("name")
            if self._open:
                self.labels[self._open] = ""

    def handle_data(self, data):
        if self._open:
            self.labels[self._open] += data

    def handle_endtag(self, tag):
        if tag == "button":
            self._open = None


def _change_form_button_labels(client, sub):
    resp = client.get(reverse(CHANGE, args=[sub.pk]))
    assert resp.status_code == 200
    parser = _ButtonParser()
    parser.feed(resp.content.decode())
    return {
        name: label.strip()
        for name, label in parser.labels.items()
        if name in STATUS_BUTTON_NAMES
    }


STATUS_BUTTON_NAMES = {
    "_approve",
    "_reject",
    "_under_review",
    "_deprecate",
    "_undeprecate",
}


def _change_form_buttons(client, sub):
    return set(_change_form_button_labels(client, sub))


BUTTON_TARGET = {
    "_approve": "approved",
    "_reject": "rejected",
    "_under_review": "under_review",
    "_deprecate": "deprecated",
    "_undeprecate": "submitted",
}


class TestAdminChangeForm:
    @pytest.mark.parametrize("status", ALL)
    def test_only_buttons_for_allowed_transitions_are_shown(
        self, superuser_client, status
    ):
        sub = _make(status)
        shown = _change_form_buttons(superuser_client, sub)
        expected = {
            name
            for name, target in BUTTON_TARGET.items()
            if (status, target) in ALLOWED
            or (status == target and name != "_undeprecate")
        }
        assert shown == expected

    def test_the_current_status_button_is_still_shown_disabled(self, superuser_client):
        sub = _make("approved")
        html = superuser_client.get(reverse(CHANGE, args=[sub.pk])).content.decode()
        assert 'name="_approve" value="1"' in html
        assert "disabled>✓ Approve" in html

    @pytest.mark.parametrize("status", ["draft", "rejected", "deprecated"])
    def test_the_way_back_to_submitted_is_labelled_for_what_it_does(
        self, superuser_client, status
    ):
        labels = _change_form_button_labels(superuser_client, _make(status))
        assert labels["_undeprecate"] == "Back to Submitted"

    def test_refused_button_post_changes_nothing_and_shows_an_error(
        self, superuser_client
    ):
        sub = _make("approved")
        resp = superuser_client.post(
            reverse(CHANGE, args=[sub.pk]),
            edit_form_payload(sub, _reject="1"),
            follow=True,
        )
        assert resp.status_code == 200
        sub.refresh_from_db()
        assert sub.status == "approved"
        assert sub.status_events.count() == 1
        assert SubmissionChangeLog.objects.filter(submission=sub).count() == 0
        errors = [m for m in resp.context["messages"] if m.level_tag == "error"]
        assert len(errors) == 1
        assert "Approved" in str(errors[0]) and "Rejected" in str(errors[0])

    def test_refused_button_post_stores_no_field_edits(self, superuser_client):
        sub = _make("approved")
        version = sub.version
        name = sub.service_name
        resp = superuser_client.post(
            reverse(CHANGE, args=[sub.pk]),
            edit_form_payload(
                sub,
                _reject="1",
                service_name="Edited together with a refused button",
                loaded_version=str(version),
            ),
            follow=True,
        )
        assert resp.status_code == 200
        sub.refresh_from_db()
        assert sub.status == "approved"
        assert sub.service_name == name
        assert sub.version == version
        assert SubmissionChangeLog.objects.filter(submission=sub).count() == 0
        shown = [(m.level_tag, str(m)) for m in resp.context["messages"]]
        assert len(shown) == 1
        level, text = shown[0]
        assert level == "error"
        assert "Approved" in text and "Rejected" in text

    @pytest.mark.parametrize("button", sorted(BUTTON_TARGET))
    @pytest.mark.parametrize("start", ALL)
    def test_button_post_follows_the_table(self, superuser_client, button, start):
        target = BUTTON_TARGET[button]
        sub = _make(start)
        resp = superuser_client.post(
            reverse(CHANGE, args=[sub.pk]),
            edit_form_payload(sub, **{button: "1"}),
        )
        assert resp.status_code == 302
        sub.refresh_from_db()
        if start == target or (start, target) in ALLOWED:
            assert sub.status == target
        else:
            assert sub.status == start


def _owner_session(client, sub):
    key, _ = APIKeyFactory.create_with_plaintext(submission=sub)
    session = client.session
    session["edit_grants"] = {str(sub.pk): str(key.pk)}
    session.save()


class TestOwnerDeprecate:
    @pytest.mark.parametrize(
        "status", ["draft", "submitted", "under_review", "approved", "rejected"]
    )
    def test_deprecate_works_from_every_status_that_allows_it(self, client, status):
        sub = _make(status)
        _owner_session(client, sub)
        resp = client.post(
            reverse("submissions:edit", args=[sub.pk]), {"_deprecate": "1"}
        )
        assert resp.status_code == 302
        sub.refresh_from_db()
        assert sub.status == "deprecated"

    def test_button_is_shown_when_the_transition_is_allowed(self, client):
        sub = _make("approved")
        _owner_session(client, sub)
        resp = client.get(reverse("submissions:edit", args=[sub.pk]))
        assert b"Deprecate this service" in resp.content
        assert b'name="_deprecate"' in resp.content

    def test_button_is_hidden_when_the_transition_is_not_allowed(
        self, client, monkeypatch
    ):
        monkeypatch.setattr(
            lifecycle, "TRANSITIONS", {**lifecycle.TRANSITIONS, "approved": frozenset()}
        )
        sub = _make("approved")
        _owner_session(client, sub)
        resp = client.get(reverse("submissions:edit", args=[sub.pk]))
        assert resp.status_code == 200
        assert b"Deprecate this service" not in resp.content
        assert b'name="_deprecate"' not in resp.content

    def test_post_when_not_allowed_changes_nothing_and_shows_an_error(
        self, client, monkeypatch
    ):
        monkeypatch.setattr(
            lifecycle, "TRANSITIONS", {**lifecycle.TRANSITIONS, "approved": frozenset()}
        )
        sub = _make("approved")
        _owner_session(client, sub)
        resp = client.post(
            reverse("submissions:edit", args=[sub.pk]),
            {"_deprecate": "1"},
            follow=True,
        )
        assert resp.status_code == 200
        sub.refresh_from_db()
        assert sub.status == "approved"
        assert sub.status_events.count() == 1
        assert SubmissionChangeLog.objects.filter(submission=sub).count() == 0
        levels = [m.level_tag for m in resp.context["messages"]]
        assert levels == ["error"]

    def test_already_deprecated_stays_harmless(self, client):
        sub = _make("deprecated")
        _owner_session(client, sub)
        resp = client.post(
            reverse("submissions:edit", args=[sub.pk]), {"_deprecate": "1"}
        )
        assert resp.status_code == 302
        sub.refresh_from_db()
        assert sub.status == "deprecated"
