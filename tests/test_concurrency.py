"""
Concurrency and data-integrity guards
=====================================
Each class covers one behaviour that must hold when requests overlap:

  TestEmailQueuedAfterCommit  - notifications are queued only once the data is committed
  TestStaleWebEditForm        - the owner's edit form refuses to overwrite newer changes
  TestStaleAdminChangeForm    - the admin change form (and its status buttons) likewise

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
    EditConflict,
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
class TestVersion:
    """Every save and every change of a service's links raises its version;
    a copy saved with an older version is refused."""

    @staticmethod
    def _sub():
        """An approved service without links, so its version starts at 1."""
        return _approved_sub(service_categories=[], responsible_pis=[])

    def _stored(self, sub):
        return ServiceSubmission.objects.values_list("version", flat=True).get(
            pk=sub.pk
        )

    def test_a_new_service_starts_at_one(self):
        assert self._stored(self._sub()) == 1

    def test_every_save_raises_the_version_by_one(self):
        sub = self._sub()
        sub.comments = "first"
        sub.save()
        assert (sub.version, self._stored(sub)) == (2, 2)
        sub.comments = "second"
        sub.save(update_fields=["comments"])
        assert (sub.version, self._stored(sub)) == (3, 3)

    def test_saving_an_older_copy_is_refused(self):
        sub = self._sub()
        older = ServiceSubmission.objects.get(pk=sub.pk)
        _change_meanwhile(sub, comments="Changed by Jane")
        older.comments = "Changed by John"
        with pytest.raises(EditConflict):
            older.save()
        stored = ServiceSubmission.objects.get(pk=sub.pk)
        assert (stored.comments, stored.version) == ("Changed by Jane", 2)

    def test_saving_listed_fields_of_an_older_copy_is_refused(self):
        sub = self._sub()
        older = ServiceSubmission.objects.get(pk=sub.pk)
        _change_meanwhile(sub, comments="Changed by Jane")
        older.status = "rejected"
        with pytest.raises(EditConflict):
            older.save(update_fields=["status"])
        assert ServiceSubmission.objects.get(pk=sub.pk).status == "approved"

    def test_a_refused_copy_keeps_its_version_and_works_after_a_reload(self):
        sub = self._sub()
        older = ServiceSubmission.objects.get(pk=sub.pk)
        _change_meanwhile(sub, comments="Changed by Jane")
        with pytest.raises(EditConflict):
            older.save()
        assert older.version == 1
        older.refresh_from_db()
        older.keywords_seo = "after reload"
        older.save()
        assert self._stored(sub) == 3

    def test_a_failed_save_leaves_the_copy_at_its_version(self, monkeypatch):
        from django.db import models

        sub = self._sub()

        def fail(*args, **kwargs):
            raise RuntimeError("write failed")

        monkeypatch.setattr(models.Model, "save", fail)
        with pytest.raises(RuntimeError):
            sub.save()
        monkeypatch.undo()
        assert (sub.version, self._stored(sub)) == (1, 1)
        sub.save()
        assert self._stored(sub) == 2

    def test_saving_a_deleted_service_is_refused_and_does_not_insert_it(self):
        sub = self._sub()
        copy = ServiceSubmission.objects.get(pk=sub.pk)
        ServiceSubmission.objects.filter(pk=sub.pk).delete()
        with pytest.raises(EditConflict):
            copy.save()
        assert not ServiceSubmission.objects.filter(pk=sub.pk).exists()

    @pytest.mark.parametrize(
        "relation,make",
        [
            ("service_categories", lambda: ServiceCategoryFactory()),
            ("responsible_pis", lambda: PIFactory()),
            ("edam_topics", lambda: _edam("topic")),
            ("edam_operations", lambda: _edam("operation")),
            ("licenses", lambda: _licence()),
        ],
    )
    def test_each_link_change_raises_the_version(self, relation, make):
        sub = self._sub()
        row = make()
        manager = getattr(sub, relation)
        manager.add(row)
        assert (sub.version, self._stored(sub)) == (2, 2)
        manager.remove(row)
        assert (sub.version, self._stored(sub)) == (3, 3)
        manager.set([row])
        assert (sub.version, self._stored(sub)) == (4, 4)
        manager.clear()
        assert (sub.version, self._stored(sub)) == (5, 5)

    def test_a_link_change_that_changes_nothing_keeps_the_version(self):
        sub = self._sub()
        category = ServiceCategoryFactory()
        sub.service_categories.add(category)
        sub.service_categories.add(category)
        sub.service_categories.set([category])
        assert (sub.version, self._stored(sub)) == (2, 2)

    def test_a_change_from_the_linked_row_raises_the_version(self):
        sub = self._sub()
        other = self._sub()
        category = ServiceCategoryFactory()
        category.submissions.add(sub, other)
        assert (self._stored(sub), self._stored(other)) == (2, 2)
        category.submissions.remove(other)
        assert (self._stored(sub), self._stored(other)) == (2, 3)
        category.submissions.clear()
        assert (self._stored(sub), self._stored(other)) == (3, 3)

    def test_a_copy_that_changed_links_can_be_saved_again(self):
        sub = self._sub()
        sub.service_categories.add(ServiceCategoryFactory())
        sub.comments = "after the link change"
        sub.save(update_fields=["comments"])
        assert self._stored(sub) == 3

    def test_a_form_loaded_before_a_link_change_is_refused(self):
        sub = self._sub()
        older = ServiceSubmission.objects.get(pk=sub.pk)
        sub.responsible_pis.add(PIFactory())
        with pytest.raises(EditConflict):
            older.save()

    def test_no_code_writes_services_around_save(self):
        """QuerySet.update() and bulk_update() skip save() and do not raise
        the version; only the link receiver may use them."""
        import pathlib

        allowed = {pathlib.Path("apps/submissions/signals.py")}
        offenders = []
        for path in pathlib.Path("apps").rglob("*.py"):
            if "migrations" in path.parts or path in allowed:
                continue
            text = path.read_text()
            if "ServiceSubmission" not in text:
                continue
            if re.search(
                r"ServiceSubmission\.objects[^\n]*\.(update|bulk_update)\(", text
            ):
                offenders.append(str(path))
        assert offenders == []


def _change_meanwhile(sub, **fields):
    """Another request saves the service between page load and post."""
    other = ServiceSubmission.objects.get(pk=sub.pk)
    for name, value in fields.items():
        setattr(other, name, value)
    other.save()


def _edam(branch):
    from apps.edam.models import EdamTerm

    n = EdamTerm.objects.count() + 9100
    return EdamTerm.objects.create(
        uri=f"http://edamontology.org/{branch}_{n}",
        accession=f"{branch}_{n}",
        branch=branch,
        label=f"Example {branch} {n}",
    )


def _licence():
    from apps.licenses.models import SpdxLicense

    n = SpdxLicense.objects.count()
    return SpdxLicense.objects.create(license_id=f"Example-{n}", name=f"Example {n}")


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
        return resp, _hidden_value(resp, "loaded_version")

    def _post(self, client, sub, token, **overrides):
        data = edit_form_data(sub, **overrides)
        if token is not None:
            data["loaded_version"] = token
        return client.post(reverse("submissions:edit", args=[sub.pk]), data)

    def test_edit_page_carries_the_version(self, client):
        sub = _approved_sub()
        _, token = self._open(client, sub)
        assert token and token == str(sub.version)

    def test_version_is_the_same_on_every_load_of_unchanged_data(self, client):
        sub = _approved_sub()
        _, first = self._open(client, sub)
        _, second = self._open(client, sub)
        assert first == second

    def test_saved_form_draft_never_restores_the_version(self, client):
        """The browser's draft restore must not overwrite the hidden value."""
        sub = _approved_sub()
        resp, _ = self._open(client, sub)
        skip = re.search(r"SKIP_NAMES = new Set\(\[([^\]]*)\]", resp.content.decode())
        assert skip and "'loaded_version'" in skip.group(1)

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
        owner_payload["loaded_version"] = token
        _change_meanwhile(sub, website_url="https://example.org/fixed")
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
        _change_meanwhile(sub, comments="Changed by Jane")
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

    def test_form_without_version_is_still_accepted(self, client):
        """Pages rendered before the check existed carry no version."""
        sub = _approved_sub()
        _grant(client, sub)
        resp = self._post(client, sub, None, comments="older page")
        assert resp.status_code == 302

    def test_validation_error_page_keeps_the_loaded_version(self, client):
        sub = _approved_sub()
        _, token = self._open(client, sub)
        assert token
        # Another field is edited too: the instance holds that value by now.
        resp = self._post(
            client, sub, token, service_description="too short", comments="my note"
        )
        assert resp.status_code == 422
        assert _hidden_value(resp, "loaded_version") == token
        # Corrected and posted again with the token the error page carried.
        again = self._post(
            client,
            sub,
            _hidden_value(resp, "loaded_version"),
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
        _change_meanwhile(sub, comments="Changed by Jane")
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
        return resp, _hidden_value(resp, "loaded_version")

    def _post(self, admin_client, sub, token, **overrides):
        payload = edit_form_payload(sub, **overrides)
        if token is not None:
            payload["loaded_version"] = token
        return admin_client.post(reverse(CHANGE, args=[sub.pk]), payload)

    def test_change_page_carries_the_version(self, superuser_client):
        sub = _approved_sub()
        _, token = self._open(superuser_client, sub)
        assert token and token == str(sub.version)

    def test_admin_and_owner_form_agree_on_the_version(self, superuser_client, client):
        sub = _approved_sub()
        _, admin_token = self._open(superuser_client, sub)
        _grant(client, sub)
        owner = client.get(reverse("submissions:edit", args=[sub.pk]))
        assert _hidden_value(owner, "loaded_version") == admin_token

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
        owner_payload["loaded_version"] = _hidden_value(
            client.get(reverse("submissions:edit", args=[sub.pk])),
            "loaded_version",
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
        _change_meanwhile(sub, comments="Changed by John")
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
        _change_meanwhile(sub, service_description=owner_text)
        owner_text = ServiceSubmission.objects.get(pk=sub.pk).service_description
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
        _change_meanwhile(sub, comments="Changed by John")
        self._post(superuser_client, sub, token, _revoke_all_keys="1")
        key.refresh_from_db()
        assert key.is_active
        assert ServiceSubmission.objects.get(pk=sub.pk).comments == "Changed by John"

    def test_page_without_version_is_still_accepted(self, superuser_client):
        sub = _approved_sub()
        resp = self._post(superuser_client, sub, None, comments="older page")
        assert resp.status_code == 302
        assert ServiceSubmission.objects.get(pk=sub.pk).comments == "older page"

    def test_validation_error_page_keeps_the_loaded_version(self, superuser_client):
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
        assert _hidden_value(resp, "loaded_version") == token
        again = self._post(
            superuser_client,
            sub,
            _hidden_value(resp, "loaded_version"),
            service_description=LONG_TEXT * 2,
            comments="my note",
        )
        assert again.status_code == 302

    def test_add_form_has_no_version(self, superuser_client):
        resp = superuser_client.get(reverse("admin:submissions_servicesubmission_add"))
        assert resp.status_code == 200
        assert _hidden_value(resp, "loaded_version") is None


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
        token = _hidden_value(client.get(url), "loaded_version")
        data = edit_form_data(sub, comments="note")
        data["loaded_version"] = token
        seen, patcher = self._record_locks()
        with patcher:
            assert client.post(url, data).status_code == 302
        assert seen and all(seen)

    def test_admin_save_locks_the_row_inside_its_transaction(self, superuser_client):
        sub = _approved_sub()
        page = superuser_client.get(reverse(CHANGE, args=[sub.pk]))
        payload = edit_form_payload(sub, comments="note")
        payload["loaded_version"] = _hidden_value(page, "loaded_version")
        seen, patcher = self._record_locks()
        with patcher:
            assert (
                superuser_client.post(
                    reverse(CHANGE, args=[sub.pk]), payload
                ).status_code
                == 302
            )
        assert seen and all(seen)
