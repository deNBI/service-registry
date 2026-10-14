"""
Concurrency and data-integrity guards
=====================================
Each class covers one behaviour that must hold when requests overlap:

  TestEmailQueuedAfterCommit  - notifications are queued only once the data is committed
  TestStaleWebEditForm        - the owner's edit form refuses to overwrite newer changes
  TestStaleAdminChangeForm    - the admin change form (and its status buttons) likewise
  TestDoubleSubmit            - one registration per form token / Idempotency-Key

The overlap is staged deterministically: the competing write is made between
the moment the form is loaded and the moment it is posted.
"""

import re
from unittest import mock

import pytest
from django.core import mail
from django.db import transaction
from django.urls import reverse

from apps.submissions.lifecycle import get_no_reset_fields
from apps.submissions.models import (
    ServiceSubmission,
    SubmissionChangeLog,
)
from tests.factories import (
    APIKeyFactory,
    PIFactory,
    ServiceCategoryFactory,
    ServiceSubmissionFactory,
)
from tests.helpers import edit_form_data, edit_form_payload

CHANGE = "admin:submissions_servicesubmission_change"
CHANGELIST = "admin:submissions_servicesubmission_changelist"
LONG_TEXT = "Example text that is long enough for the description length rule. "


def _in_atomic_block() -> bool:
    return transaction.get_connection().in_atomic_block


def _hidden_value(response, name):
    """Value of the hidden input ``name`` in a rendered page, or None."""
    html = response.content.decode()
    for tag in re.findall(r"<input[^>]*>", html):
        if f'name="{name}"' in tag and 'type="hidden"' in tag:
            m = re.search(r'value="([^"]*)"', tag)
            return m.group(1) if m else ""
    return None


def _grant(client, sub):
    """Give the test client an edit grant for ``sub`` (as the key lookup does)."""
    key_obj, _ = APIKeyFactory.create_with_plaintext(submission=sub)
    session = client.session
    session["edit_grants"] = {str(sub.pk): str(key_obj.pk)}
    session.save()


@pytest.fixture(autouse=True)
def _empty_cache():
    """One-time tokens live in the cache; start every test without any."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def exempt(settings):
    """Edits to these fields keep an approved service approved."""
    settings.SUBMISSION_NO_RESET_FIELDS = ["github_url", "website_url"]
    get_no_reset_fields.cache_clear()
    yield
    get_no_reset_fields.cache_clear()


# ---------------------------------------------------------------------------
# Notifications are queued after the transaction commits
# ---------------------------------------------------------------------------

STATUS_BUTTONS = [
    ("_approve", "submitted", "approved"),
    ("_reject", "submitted", "rejected"),
    ("_under_review", "submitted", "under_review"),
    ("_deprecate", "approved", "deprecated"),
    ("_undeprecate", "deprecated", "submitted"),
]


@pytest.mark.django_db(transaction=True)
class TestEmailQueuedAfterCommit:
    @pytest.mark.parametrize("button,start,end", STATUS_BUTTONS)
    def test_change_form_status_button_queues_email_after_commit(
        self, superuser_client, button, start, end
    ):
        sub = ServiceSubmissionFactory(status=start, biotools_url="")
        seen = []

        with mock.patch(
            "apps.submissions.admin.send_submission_notification.delay",
            side_effect=lambda *a, **k: seen.append(_in_atomic_block()),
        ):
            resp = superuser_client.post(
                reverse(CHANGE, args=[sub.pk]), edit_form_payload(sub, **{button: "1"})
            )
        assert resp.status_code == 302
        sub.refresh_from_db()
        assert sub.status == end
        assert seen == [False]

    def test_change_form_status_email_reads_committed_status(self, superuser_client):
        """The mail is rendered from the committed row, so it names the new status."""
        sub = ServiceSubmissionFactory(status="submitted", biotools_url="")
        superuser_client.post(
            reverse(CHANGE, args=[sub.pk]), edit_form_payload(sub, _approve="1")
        )
        assert len(mail.outbox) >= 1
        assert any("Approved" in m.subject for m in mail.outbox)

    def test_change_form_status_email_not_sent_when_request_fails(
        self, superuser_client
    ):
        sub = ServiceSubmissionFactory(status="submitted", biotools_url="")
        superuser_client.raise_request_exception = False
        with mock.patch(
            "apps.submissions.admin.ServiceSubmissionAdmin._log",
            side_effect=RuntimeError("write failed"),
        ):
            resp = superuser_client.post(
                reverse(CHANGE, args=[sub.pk]), edit_form_payload(sub, _approve="1")
            )
        assert resp.status_code == 500
        sub.refresh_from_db()
        assert sub.status == "submitted"
        assert mail.outbox == []

    def test_bulk_action_queues_email_after_commit(self, superuser_client):
        sub = ServiceSubmissionFactory(status="submitted", biotools_url="")
        seen = []
        with mock.patch(
            "apps.submissions.admin.send_submission_notification.delay",
            side_effect=lambda *a, **k: seen.append(_in_atomic_block()),
        ):
            superuser_client.post(
                reverse(CHANGELIST),
                {"action": "action_approve", "_selected_action": [str(sub.pk)]},
            )
        assert seen == [False]
        sub.refresh_from_db()
        assert sub.status == "approved"


@pytest.mark.django_db
class TestContentFingerprint:
    def _fp(self, sub):
        from apps.submissions.concurrency import content_fingerprint

        return content_fingerprint(ServiceSubmission.objects.get(pk=sub.pk))

    def test_same_for_unchanged_data(self):
        sub = _approved_sub()
        assert self._fp(sub) == self._fp(sub)

    @pytest.mark.parametrize(
        "field,value",
        [
            ("service_description", "Another description. " + LONG_TEXT),
            ("comments", "note"),
            ("website_url", "https://example.org/other"),
            ("status", "rejected"),
            ("survey_participation", False),
        ],
    )
    def test_changes_with_a_tracked_field(self, field, value):
        sub = _approved_sub()
        before = self._fp(sub)
        ServiceSubmission.objects.filter(pk=sub.pk).update(**{field: value})
        assert self._fp(sub) != before

    def test_changes_with_linked_rows(self):
        sub = _approved_sub()
        before = self._fp(sub)
        sub.service_categories.add(ServiceCategoryFactory())
        assert self._fp(sub) != before

    def test_ignores_bookkeeping_columns(self):
        sub = _approved_sub()
        before = self._fp(sub)
        ServiceSubmission.objects.filter(pk=sub.pk).update(
            last_change_summary={"changed_by": "submitter", "changes": []},
            submission_ip="203.0.113.7",
        )
        assert self._fp(sub) == before

    def test_blank_fingerprint_skips_the_check_without_a_query(
        self, django_assert_num_queries
    ):
        from apps.submissions.concurrency import ensure_unchanged

        sub = _approved_sub()
        with django_assert_num_queries(0):
            ensure_unchanged(sub.pk, "")

    def test_deleted_service_counts_as_changed(self):
        import uuid

        from apps.submissions.concurrency import StaleFormError, ensure_unchanged

        with pytest.raises(StaleFormError):
            ensure_unchanged(uuid.uuid4(), "0" * 64)


# ---------------------------------------------------------------------------
# The owner's edit form refuses to overwrite newer changes
# ---------------------------------------------------------------------------


def _approved_sub(**kw):
    kw.setdefault("biotools_url", "")
    sub = ServiceSubmissionFactory(status="approved", **kw)
    return sub


@pytest.mark.django_db
class TestStaleWebEditForm:
    def _open(self, client, sub):
        _grant(client, sub)
        resp = client.get(reverse("submissions:edit", args=[sub.pk]))
        assert resp.status_code == 200
        return resp, _hidden_value(resp, "loaded_fingerprint")

    def _post(self, client, sub, token, **overrides):
        data = edit_form_data(sub, **overrides)
        if token is not None:
            data["loaded_fingerprint"] = token
        return client.post(reverse("submissions:edit", args=[sub.pk]), data)

    def test_edit_page_carries_the_fingerprint(self, client):
        sub = _approved_sub()
        _, token = self._open(client, sub)
        assert token and re.fullmatch(r"[0-9a-f]{64}", token)

    def test_fingerprint_is_the_same_on_every_load_of_unchanged_data(self, client):
        sub = _approved_sub()
        _, first = self._open(client, sub)
        _, second = self._open(client, sub)
        assert first == second

    def test_saved_form_draft_never_restores_the_fingerprint(self, client):
        """The browser's draft restore must not overwrite the hidden value."""
        sub = _approved_sub()
        resp, _ = self._open(client, sub)
        skip = re.search(r"SKIP_NAMES = new Set\(\[([^\]]*)\]", resp.content.decode())
        assert skip and "'loaded_fingerprint'" in skip.group(1)

    def test_unchanged_resave_still_works(self, client):
        sub = _approved_sub()
        _, token = self._open(client, sub)
        resp = self._post(client, sub, token)
        assert resp.status_code == 302
        assert ServiceSubmission.objects.get(pk=sub.pk).status == "approved"

    def test_edit_without_concurrent_change_is_stored(self, client):
        sub = _approved_sub()
        _, token = self._open(client, sub)
        resp = self._post(client, sub, token, comments="Example note")
        assert resp.status_code == 302
        stored = ServiceSubmission.objects.get(pk=sub.pk)
        assert stored.comments == "Example note"

    def test_newer_admin_change_is_not_reverted(self, client, exempt):
        sub = _approved_sub()
        _, token = self._open(client, sub)
        owner_payload = edit_form_data(
            sub, github_url="https://github.com/example/new-home"
        )
        owner_payload["loaded_fingerprint"] = token
        ServiceSubmission.objects.filter(pk=sub.pk).update(
            website_url="https://example.org/fixed"
        )
        resp = client.post(reverse("submissions:edit", args=[sub.pk]), owner_payload)
        assert resp.status_code == 409
        stored = ServiceSubmission.objects.get(pk=sub.pk)
        assert stored.website_url == "https://example.org/fixed"
        assert stored.github_url == ""
        assert stored.status == "approved"
        assert not SubmissionChangeLog.objects.filter(submission=sub).exists()
        assert mail.outbox == []

    def test_conflict_page_explains_and_keeps_the_entered_values(self, client):
        sub = _approved_sub()
        _, token = self._open(client, sub)
        ServiceSubmission.objects.filter(pk=sub.pk).update(comments="Changed by Jane")
        resp = self._post(client, sub, token, keywords_seo="my own words")
        assert resp.status_code == 409
        html = resp.content.decode()
        assert "changed while you were editing" in html
        assert "my own words" in html

    def test_second_tab_save_is_refused_after_first_tab_saved(self, client):
        sub = _approved_sub()
        _, token_a = self._open(client, sub)
        _, token_b = self._open(client, sub)
        assert token_a == token_b
        first = self._post(client, sub, token_a, comments="from tab A")
        assert first.status_code == 302
        sub.refresh_from_db()
        second = self._post(client, sub, token_b, comments="from tab B")
        assert second.status_code == 409
        assert ServiceSubmission.objects.get(pk=sub.pk).comments == "from tab A"

    def test_reloaded_page_after_a_save_can_be_saved_again(self, client):
        sub = _approved_sub()
        _, token = self._open(client, sub)
        self._post(client, sub, token, comments="first")
        sub.refresh_from_db()
        _, fresh = self._open(client, sub)
        assert fresh != token
        resp = self._post(client, sub, fresh, comments="second")
        assert resp.status_code == 302
        assert ServiceSubmission.objects.get(pk=sub.pk).comments == "second"

    def test_changed_categories_count_as_a_change(self, client):
        sub = _approved_sub()
        _, token = self._open(client, sub)
        sub.service_categories.add(ServiceCategoryFactory())
        resp = self._post(client, sub, token, comments="x")
        assert resp.status_code == 409

    def test_changed_pis_count_as_a_change(self, client):
        sub = _approved_sub()
        _, token = self._open(client, sub)
        sub.responsible_pis.add(PIFactory())
        resp = self._post(client, sub, token, comments="x")
        assert resp.status_code == 409

    def test_form_without_fingerprint_is_still_accepted(self, client):
        """Pages rendered before the check existed carry no fingerprint."""
        sub = _approved_sub()
        _grant(client, sub)
        resp = self._post(client, sub, None, comments="older page")
        assert resp.status_code == 302

    def test_validation_error_page_keeps_the_loaded_fingerprint(self, client):
        sub = _approved_sub()
        _, token = self._open(client, sub)
        assert token
        # Another field is edited too: the instance holds that value by now.
        resp = self._post(
            client, sub, token, service_description="too short", comments="my note"
        )
        assert resp.status_code == 422
        assert _hidden_value(resp, "loaded_fingerprint") == token
        # Corrected and posted again with the token the error page carried.
        again = self._post(
            client,
            sub,
            _hidden_value(resp, "loaded_fingerprint"),
            service_description=LONG_TEXT * 2,
            comments="my note",
        )
        assert again.status_code == 302

    def test_legacy_row_with_unnormalised_text_resaves(self, client):
        sub = _approved_sub()
        ServiceSubmission.objects.filter(pk=sub.pk).update(
            keywords_seo="  line one\r\nline two  ", comments="caf\u0065\u0301"
        )
        sub.refresh_from_db()
        _, token = self._open(client, sub)
        resp = self._post(client, sub, token, comments="plain")
        assert resp.status_code == 302

    def test_refused_save_stores_nothing_of_the_edit(self, client):
        sub = _approved_sub()
        _, token = self._open(client, sub)
        ServiceSubmission.objects.filter(pk=sub.pk).update(comments="Changed by Jane")
        before_cats = set(sub.service_categories.values_list("pk", flat=True))
        new_cat = ServiceCategoryFactory()
        resp = self._post(
            client,
            sub,
            token,
            service_categories=[new_cat.pk],
            keywords_seo="new words",
        )
        assert resp.status_code == 409
        stored = ServiceSubmission.objects.get(pk=sub.pk)
        assert stored.keywords_seo != "new words"
        assert set(stored.service_categories.values_list("pk", flat=True)) == (
            before_cats
        )
        assert stored.status == "approved"


# ---------------------------------------------------------------------------
# The admin change form (and its status and key buttons)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestStaleAdminChangeForm:
    def _open(self, admin_client, sub):
        resp = admin_client.get(reverse(CHANGE, args=[sub.pk]))
        assert resp.status_code == 200
        return resp, _hidden_value(resp, "loaded_fingerprint")

    def _post(self, admin_client, sub, token, **overrides):
        payload = edit_form_payload(sub, **overrides)
        if token is not None:
            payload["loaded_fingerprint"] = token
        return admin_client.post(reverse(CHANGE, args=[sub.pk]), payload)

    def test_change_page_carries_the_fingerprint(self, superuser_client):
        sub = _approved_sub()
        _, token = self._open(superuser_client, sub)
        assert token and re.fullmatch(r"[0-9a-f]{64}", token)

    def test_admin_and_owner_form_agree_on_the_fingerprint(
        self, superuser_client, client
    ):
        sub = _approved_sub()
        _, admin_token = self._open(superuser_client, sub)
        _grant(client, sub)
        owner = client.get(reverse("submissions:edit", args=[sub.pk]))
        assert _hidden_value(owner, "loaded_fingerprint") == admin_token

    def test_unchanged_resave_still_works(self, superuser_client):
        sub = _approved_sub()
        _, token = self._open(superuser_client, sub)
        resp = self._post(superuser_client, sub, token)
        assert resp.status_code == 302
        assert resp.url == reverse("admin:submissions_servicesubmission_changelist")

    def test_edit_without_concurrent_change_is_stored(self, superuser_client):
        sub = _approved_sub()
        _, token = self._open(superuser_client, sub)
        resp = self._post(superuser_client, sub, token, comments="Checked by Jane")
        assert resp.status_code == 302
        assert ServiceSubmission.objects.get(pk=sub.pk).comments == "Checked by Jane"

    def test_owner_change_made_after_the_page_loaded_is_not_reverted(
        self, superuser_client, client, exempt
    ):
        sub = _approved_sub()
        _, token = self._open(superuser_client, sub)
        # The owner changes an exempt field; the service stays approved.
        _grant(client, sub)
        owner_payload = edit_form_data(
            sub, github_url="https://github.com/example/new-home"
        )
        owner_payload["loaded_fingerprint"] = _hidden_value(
            client.get(reverse("submissions:edit", args=[sub.pk])),
            "loaded_fingerprint",
        )
        assert (
            client.post(
                reverse("submissions:edit", args=[sub.pk]), owner_payload
            ).status_code
            == 302
        )
        # The admin saves the page loaded earlier.
        resp = self._post(superuser_client, sub, token, comments="Checked by Jane")
        assert resp.status_code == 302
        assert resp.url == reverse(CHANGE, args=[sub.pk])
        stored = ServiceSubmission.objects.get(pk=sub.pk)
        assert stored.github_url == "https://github.com/example/new-home"
        assert stored.comments == ""

    def test_refusal_shows_an_error_message(self, superuser_client):
        sub = _approved_sub()
        _, token = self._open(superuser_client, sub)
        ServiceSubmission.objects.filter(pk=sub.pk).update(comments="Changed by John")
        resp = self._post(superuser_client, sub, token, comments="Checked by Jane")
        page = superuser_client.get(resp.url)
        messages_text = [str(m) for m in page.context["messages"]]
        assert any("changed while you were editing" in m for m in messages_text)

    @pytest.mark.parametrize(
        "button,end",
        [("_approve", "approved"), ("_reject", "rejected")],
    )
    def test_status_button_on_a_stale_page_changes_nothing(
        self, superuser_client, button, end
    ):
        sub = ServiceSubmissionFactory(status="under_review", biotools_url="")
        _, token = self._open(superuser_client, sub)
        owner_text = "Reply to the reviewer. " + LONG_TEXT
        ServiceSubmission.objects.filter(pk=sub.pk).update(
            service_description=owner_text
        )
        mail.outbox.clear()
        resp = self._post(superuser_client, sub, token, **{button: "1"})
        assert resp.status_code == 302
        stored = ServiceSubmission.objects.get(pk=sub.pk)
        assert stored.status == "under_review"
        assert stored.service_description == owner_text
        assert mail.outbox == []
        assert not SubmissionChangeLog.objects.filter(submission=sub).exists()

    def test_status_button_on_a_current_page_still_works(self, superuser_client):
        sub = ServiceSubmissionFactory(status="under_review", biotools_url="")
        _, token = self._open(superuser_client, sub)
        resp = self._post(superuser_client, sub, token, _approve="1")
        assert resp.status_code == 302
        assert ServiceSubmission.objects.get(pk=sub.pk).status == "approved"

    def test_key_button_on_a_stale_page_changes_nothing(self, superuser_client):
        sub = _approved_sub()
        key, _ = APIKeyFactory.create_with_plaintext(submission=sub)
        _, token = self._open(superuser_client, sub)
        ServiceSubmission.objects.filter(pk=sub.pk).update(comments="Changed by John")
        self._post(superuser_client, sub, token, _revoke_all_keys="1")
        key.refresh_from_db()
        assert key.is_active
        assert ServiceSubmission.objects.get(pk=sub.pk).comments == "Changed by John"

    def test_page_without_fingerprint_is_still_accepted(self, superuser_client):
        sub = _approved_sub()
        resp = self._post(superuser_client, sub, None, comments="older page")
        assert resp.status_code == 302
        assert ServiceSubmission.objects.get(pk=sub.pk).comments == "older page"

    def test_validation_error_page_keeps_the_loaded_fingerprint(self, superuser_client):
        sub = _approved_sub()
        _, token = self._open(superuser_client, sub)
        assert token
        resp = self._post(
            superuser_client,
            sub,
            token,
            service_description="too short",
            comments="my note",
        )
        assert resp.status_code == 200
        assert _hidden_value(resp, "loaded_fingerprint") == token
        again = self._post(
            superuser_client,
            sub,
            _hidden_value(resp, "loaded_fingerprint"),
            service_description=LONG_TEXT * 2,
            comments="my note",
        )
        assert again.status_code == 302

    def test_add_form_has_no_fingerprint(self, superuser_client):
        resp = superuser_client.get(reverse("admin:submissions_servicesubmission_add"))
        assert resp.status_code == 200
        assert _hidden_value(resp, "loaded_fingerprint") is None


@pytest.mark.django_db(transaction=True)
class TestStoredRowIsLockedWhileSaving:
    """SQLite ignores row locks, so check that the lock is requested, and that it
    is requested inside the transaction that stores the edit (a lock taken in
    autocommit mode would be released before the write)."""

    def _record_locks(self):
        from django.db.models.query import QuerySet

        seen = []
        original = QuerySet.select_for_update

        def spy(queryset, *args, **kwargs):
            if queryset.model is ServiceSubmission:
                seen.append(_in_atomic_block())
            return original(queryset, *args, **kwargs)

        return seen, mock.patch.object(QuerySet, "select_for_update", spy)

    def test_owner_edit_locks_the_row_inside_its_transaction(self, client):
        sub = _approved_sub()
        _grant(client, sub)
        url = reverse("submissions:edit", args=[sub.pk])
        token = _hidden_value(client.get(url), "loaded_fingerprint")
        data = edit_form_data(sub, comments="note")
        data["loaded_fingerprint"] = token
        seen, patcher = self._record_locks()
        with patcher:
            assert client.post(url, data).status_code == 302
        assert seen and all(seen)

    def test_admin_save_locks_the_row_inside_its_transaction(self, superuser_client):
        sub = _approved_sub()
        page = superuser_client.get(reverse(CHANGE, args=[sub.pk]))
        payload = edit_form_payload(sub, comments="note")
        payload["loaded_fingerprint"] = _hidden_value(page, "loaded_fingerprint")
        seen, patcher = self._record_locks()
        with patcher:
            assert (
                superuser_client.post(
                    reverse(CHANGE, args=[sub.pk]), payload
                ).status_code
                == 302
            )
        assert seen and all(seen)


# ---------------------------------------------------------------------------
# One registration per form token / Idempotency-Key
# ---------------------------------------------------------------------------

REGISTER = "submissions:register"


def _api_payload():
    from django.utils import timezone

    from tests.factories import ServiceCenterFactory

    return {
        "date_of_entry": timezone.now().date().isoformat(),
        "submitter_first_name": "John",
        "submitter_last_name": "Doe",
        "submitter_affiliation": "Example Institute",
        "register_as_elixir": False,
        "service_name": "Example Service",
        "service_description": LONG_TEXT * 2,
        "year_established": 2021,
        "service_category_ids": [ServiceCategoryFactory().pk],
        "is_toolbox": False,
        "publications_pmids": "12345678",
        "responsible_pi_ids": [str(PIFactory().pk)],
        "host_institute": "Example Institute",
        "service_center_id": str(ServiceCenterFactory().pk),
        "public_contact_email": "contact@example.com",
        "internal_contact_name": "Jane Doe",
        "internal_contact_email": "jane@example.com",
        "website_url": "https://example.com",
        "terms_of_use_url": "https://example.com/terms",
        "licenses": [],
        "license_note": "Custom license",
        "kpi_monitoring": "planned",
        "kpi_start_year": "2021",
        "survey_participation": True,
        "data_protection_consent": True,
    }


@pytest.mark.django_db
class TestDoubleSubmitWebForm:
    def _token(self, client):
        resp = client.get(reverse(REGISTER))
        assert resp.status_code == 200
        token = _hidden_value(resp, "form_token")
        assert token
        return token

    def _data(self, token):
        from tests.helpers import base_form_data

        data = base_form_data()
        if token is not None:
            data["form_token"] = token
        return data

    def test_register_page_carries_a_fresh_token_per_render(self, client):
        first = self._token(client)
        second = self._token(client)
        assert re.fullmatch(r"[0-9a-f]{32}", first)
        assert first != second

    def test_saved_form_draft_never_restores_the_token(self, client):
        html = client.get(reverse(REGISTER)).content.decode()
        skip = re.search(r"SKIP_NAMES = new Set\(\[([^\]]*)\]", html)
        assert skip and "'form_token'" in skip.group(1)

    def test_second_post_with_the_same_token_creates_no_second_service(self, client):
        token = self._token(client)
        data = self._data(token)
        first = client.post(reverse(REGISTER), data)
        mails_after_first = len(mail.outbox)
        second = client.post(reverse(REGISTER), data)
        assert first.status_code == 302
        assert second.status_code == 302
        assert second.url == first.url
        assert ServiceSubmission.objects.count() == 1
        assert len(mail.outbox) == mails_after_first

    def test_only_one_key_is_issued(self, client):
        from apps.submissions.models import SubmissionAPIKey

        token = self._token(client)
        data = self._data(token)
        client.post(reverse(REGISTER), data)
        client.post(reverse(REGISTER), data)
        assert SubmissionAPIKey.objects.count() == 1

    def test_the_key_is_shown_on_the_page_both_posts_lead_to(self, client):
        token = self._token(client)
        data = self._data(token)
        client.post(reverse(REGISTER), data)
        second = client.post(reverse(REGISTER), data)
        page = client.get(second.url)
        assert page.status_code == 200
        assert b"api-key-display" in page.content

    def test_a_new_token_registers_a_new_service(self, client):
        first = self._data(self._token(client))
        second = self._data(self._token(client))
        client.post(reverse(REGISTER), first)
        client.post(reverse(REGISTER), second)
        assert ServiceSubmission.objects.count() == 2

    def test_post_without_a_token_is_accepted_as_before(self, client):
        client.post(reverse(REGISTER), self._data(None))
        client.post(reverse(REGISTER), self._data(None))
        assert ServiceSubmission.objects.count() == 2

    def test_malformed_token_is_ignored(self, client):
        data = self._data("not a token")
        client.post(reverse(REGISTER), data)
        client.post(reverse(REGISTER), data)
        assert ServiceSubmission.objects.count() == 2

    def test_invalid_form_does_not_use_up_the_token(self, client):
        token = self._token(client)
        bad = self._data(token)
        bad["service_description"] = "too short"
        assert client.post(reverse(REGISTER), bad).status_code == 422
        good = self._data(token)
        assert client.post(reverse(REGISTER), good).status_code == 302
        assert ServiceSubmission.objects.count() == 1

    def test_error_page_offers_a_fresh_token(self, client):
        token = self._token(client)
        bad = self._data(token)
        bad["service_description"] = "too short"
        resp = client.post(reverse(REGISTER), bad)
        assert _hidden_value(resp, "form_token")

    def test_failed_save_releases_the_token_for_a_retry(self, client):
        token = self._token(client)
        data = self._data(token)
        client.raise_request_exception = False
        with mock.patch(
            "apps.submissions.views.SubmissionAPIKey.create_for_submission",
            side_effect=RuntimeError("write failed"),
        ):
            assert client.post(reverse(REGISTER), data).status_code == 500
        retry = client.post(reverse(REGISTER), data)
        assert retry.status_code == 302

    def test_request_arriving_while_the_first_is_running_waits_for_it(self, client):
        from django.core.cache import cache

        from apps.submissions import idempotency

        token = self._token(client)
        data = self._data(token)
        first = client.post(reverse(REGISTER), data)
        sub = ServiceSubmission.objects.get()
        # Put the token back into the state of a request still being handled,
        # then let it finish while the second request is waiting.
        key = idempotency.cache_key(idempotency.WEB_FORM, token)
        cache.set(key, idempotency.PENDING)

        def finish(_seconds):
            cache.set(key, str(sub.pk))

        with mock.patch("apps.submissions.idempotency.time.sleep", side_effect=finish):
            second = client.post(reverse(REGISTER), data)
        assert second.status_code == 302
        assert second.url == first.url
        assert ServiceSubmission.objects.count() == 1

    def test_request_giving_up_on_a_stuck_first_one_asks_to_reload(self, client):
        from django.core.cache import cache

        from apps.submissions import idempotency

        token = self._token(client)
        data = self._data(token)
        cache.set(
            idempotency.cache_key(idempotency.WEB_FORM, token), idempotency.PENDING
        )
        with mock.patch("apps.submissions.idempotency.time.sleep") as slept:
            resp = client.post(reverse(REGISTER), data)
        assert slept.call_count == idempotency.WAIT_ATTEMPTS
        assert resp.status_code == 409
        assert "already being processed" in resp.content.decode()
        assert ServiceSubmission.objects.count() == 0
        # The retry form carries a new token and the entered values.
        assert _hidden_value(resp, "form_token") not in (None, token)
        assert data["service_name"] in resp.content.decode()


@pytest.mark.django_db
class TestDoubleSubmitApi:
    URL = "/api/v1/submissions/"

    def _post(self, api_client, payload, key=None):
        headers = {"HTTP_IDEMPOTENCY_KEY": key} if key is not None else {}
        return api_client.post(self.URL, payload, format="json", **headers)

    @pytest.fixture
    def api_client(self):
        from rest_framework.test import APIClient

        return APIClient()

    def test_repeated_request_with_the_same_key_is_refused(self, api_client):
        payload = _api_payload()
        first = self._post(api_client, payload, key="example-request-1")
        mails_after_first = len(mail.outbox)
        second = self._post(api_client, payload, key="example-request-1")
        assert first.status_code == 201
        assert second.status_code == 409
        assert "Idempotency-Key" in str(second.json())
        assert ServiceSubmission.objects.count() == 1
        assert len(mail.outbox) == mails_after_first

    def test_a_different_key_registers_another_service(self, api_client):
        payload = _api_payload()
        assert (
            self._post(api_client, payload, key="example-request-1").status_code == 201
        )
        assert (
            self._post(api_client, payload, key="example-request-2").status_code == 201
        )
        assert ServiceSubmission.objects.count() == 2

    def test_requests_without_a_key_behave_as_before(self, api_client):
        payload = _api_payload()
        assert self._post(api_client, payload).status_code == 201
        assert self._post(api_client, payload).status_code == 201
        assert ServiceSubmission.objects.count() == 2

    def test_invalid_payload_does_not_use_up_the_key(self, api_client):
        bad = _api_payload()
        bad["service_description"] = "too short"
        assert self._post(api_client, bad, key="example-request-1").status_code == 400
        good = _api_payload()
        assert self._post(api_client, good, key="example-request-1").status_code == 201

    def test_failed_save_releases_the_key_for_a_retry(self, api_client):
        payload = _api_payload()
        api_client.raise_request_exception = False
        with mock.patch(
            "apps.api.views.SubmissionAPIKey.create_for_submission",
            side_effect=RuntimeError("write failed"),
        ):
            assert (
                self._post(api_client, payload, key="example-request-1").status_code
                == 500
            )
        assert (
            self._post(api_client, payload, key="example-request-1").status_code == 201
        )

    @pytest.mark.parametrize("key", ["x" * 256, "bad\tkey", "café"])
    def test_out_of_range_key_is_rejected(self, api_client, key):
        resp = self._post(api_client, _api_payload(), key=key)
        assert resp.status_code == 400
        assert ServiceSubmission.objects.count() == 0

    def test_empty_key_counts_as_no_key(self, api_client):
        payload = _api_payload()
        assert self._post(api_client, payload, key="").status_code == 201
        assert self._post(api_client, payload, key="").status_code == 201

    def test_key_in_flight_is_refused_without_waiting(self, api_client):
        from django.core.cache import cache

        from apps.submissions import idempotency

        cache.set(
            idempotency.cache_key(idempotency.API_REQUEST, "example-request-1"),
            idempotency.PENDING,
        )
        with mock.patch("apps.submissions.idempotency.time.sleep") as slept:
            resp = self._post(api_client, _api_payload(), key="example-request-1")
        assert resp.status_code == 409
        slept.assert_not_called()
        assert ServiceSubmission.objects.count() == 0
