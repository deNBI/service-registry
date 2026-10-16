"""Restore the working copy from the published version."""

from html.parser import HTMLParser

import pytest
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db.models.signals import post_save
from django.test import Client
from django.urls import reverse

from apps.catalogue import publishing
from apps.catalogue.models import PublishedRevision, PublishedService
from apps.catalogue.public_fields import PUBLIC_M2M_FIELDS, PUBLIC_SCALAR_FIELDS
from apps.registry.models import PrincipalInvestigator
from apps.submissions.lifecycle import set_status
from apps.submissions.models import ServiceSubmission, SubmissionChangeLog
from tests.factories import (
    PIFactory,
    ServiceCategoryFactory,
    ServiceCenterFactory,
    ServiceSubmissionFactory,
)

pytestmark = pytest.mark.django_db
CHANGELIST = "admin:submissions_servicesubmission_changelist"
VERSION_FIELD = "restore_version"

WARNING = (
    "not restored and left unchanged: they changed after the confirmation page "
    "was shown, have no published version, or are no longer in the list."
)


def restored_message(restored, matched=0):
    return (
        f"Restored the published version of {restored} submission(s); "
        f"{matched} already matched it."
    )


class _HiddenInputs(HTMLParser):
    """Hidden inputs of every form on a page, one list per form."""

    def __init__(self):
        super().__init__()
        self.forms = []

    def handle_starttag(self, tag, attrs):
        if tag == "form":
            self.forms.append([])
        elif tag == "input" and self.forms:
            attrs = dict(attrs)
            if attrs.get("type") == "hidden":
                self.forms[-1].append((attrs["name"], attrs.get("value", "")))


def _confirmation_fields(response):
    """What the confirm button of the confirmation page posts back."""
    parser = _HiddenInputs()
    parser.feed(response.content.decode())
    (form,) = [f for f in parser.forms if ("post", "yes") in f]
    fields = {}
    for name, value in form:
        fields.setdefault(name, []).append(value)
    return fields


def _versions(fields):
    """The versions a confirmation form posts: service id -> version.
    All of them travel in one field, entries separated by white space."""
    (value,) = fields[VERSION_FIELD]
    return dict(entry.rpartition(":")[::2] for entry in value.split())


def _with_versions(fields, versions):
    """The form fields with the posted versions replaced."""
    value = " ".join(f"{key}:{version}" for key, version in versions.items())
    return {**fields, VERSION_FIELD: [value]}


def _ask(client, *subs, url=None, **extra):
    """Select the services and run the restore action: the confirmation page."""
    data = {
        "action": "action_restore_published",
        "_selected_action": [str(s.pk) for s in subs],
        **extra,
    }
    return client.post(url or reverse(CHANGELIST), data, follow=True)


def _confirm(client, page, url=None):
    """Press the confirm button of a confirmation page."""
    return client.post(
        url or reverse(CHANGELIST), _confirmation_fields(page), follow=True
    )


def _post(client, *subs, confirm=True, url=None):
    page = _ask(client, *subs, url=url)
    return _confirm(client, page, url=url) if confirm else page


def _messages(response):
    return [str(m) for m in response.context["messages"]]


def _published(**kw):
    sub = ServiceSubmissionFactory(biotools_url="", **kw)
    set_status(sub, "approved", actor="admin:jane")
    return sub


def _rejected_edit(sub, **changes):
    """The submitter edits a published service, review rejects the edit."""
    set_status(sub, "submitted", actor="submitter", owner_edit=True)
    for name, value in changes.items():
        setattr(sub, name, value)
    sub.save()
    set_status(sub, "rejected", actor="admin:jane")


def _save_behind(sub, **changes):
    """A save of the service by someone else, as after the page was shown."""
    row = ServiceSubmission.objects.get(pk=sub.pk)
    for name, value in changes.items():
        setattr(row, name, value)
    row.save()


EDIT = "An edit that would be discarded."


class TestConfirmation:
    """Restoring discards pending edits, so it asks for confirmation first."""

    def test_the_first_post_asks_and_changes_nothing(self, superuser_client):
        sub = _published()
        _rejected_edit(sub, service_description="An edit still waiting to be kept.")
        before = ServiceSubmission.objects.values().get(pk=sub.pk)
        resp = _post(superuser_client, sub, confirm=False)
        assert ServiceSubmission.objects.values().get(pk=sub.pk) == before
        assert not SubmissionChangeLog.objects.filter(
            changed_by="admin:testadmin"
        ).exists()
        assert not LogEntry.objects.filter(
            change_message__startswith="Restored"
        ).exists()
        assert resp.templates[0].name == (
            "admin/submissions/servicesubmission/restore_published_confirmation.html"
        )

    def test_the_page_lists_each_service_with_its_status(self, superuser_client):
        a = _published(service_name="Example Alpha Service")
        _rejected_edit(a, service_description="An edit of the alpha service text.")
        b = _published(service_name="Example Beta Service")
        page = _post(superuser_client, a, b, confirm=False).content.decode()
        assert "Example Alpha Service" in page
        assert "Example Beta Service" in page
        assert "Rejected" in page
        assert "Approved" in page
        assert "discarded" in page

    def test_the_page_names_the_revision_that_will_be_restored(self, superuser_client):
        sub = _published()
        page = _post(superuser_client, sub, confirm=False).content.decode()
        assert "Published version 1" in page

    def test_the_page_names_the_latest_revision(self, superuser_client):
        sub = _published()
        sub.website_url = "https://example.org/second"
        sub.save()
        publishing.publish(sub, reason="live_edit", actor="submitter")
        page = _post(superuser_client, sub, confirm=False).content.decode()
        assert "Published version 2" in page
        assert "Published version 1" not in page

    def test_the_page_names_services_without_a_published_version(
        self, superuser_client
    ):
        sub = ServiceSubmissionFactory(
            biotools_url="", service_name="Example Never Published"
        )
        page = _post(superuser_client, sub, confirm=False).content.decode()
        assert "Example Never Published" in page
        assert "No published version" in page

    def test_the_confirm_form_posts_back_the_same_selection(self, superuser_client):
        a, b = _published(), _published()
        resp = _post(superuser_client, a, b, confirm=False)
        form = resp.content.decode()
        for sub in (a, b):
            assert f'name="_selected_action" value="{sub.pk}"' in form
        assert 'name="action" value="action_restore_published"' in form
        assert 'name="post" value="yes"' in form

    def test_cancel_returns_to_the_filtered_changelist(self, superuser_client):
        sub = _published()
        url = reverse(CHANGELIST) + "?status__exact=approved"
        page = _post(superuser_client, sub, confirm=False, url=url).content.decode()
        assert f'href="{url}"' in page

    def test_confirming_restores(self, superuser_client):
        sub = _published()
        approved_text = sub.service_description
        _rejected_edit(sub, service_description="An edit that will be discarded.")
        _post(superuser_client, sub, confirm=True)
        sub.refresh_from_db()
        assert sub.service_description == approved_text

    def test_any_other_value_of_the_flag_changes_nothing(self, superuser_client):
        sub = _published()
        _rejected_edit(sub, service_description="An edit still waiting to be kept.")
        superuser_client.post(
            reverse(CHANGELIST),
            {
                "action": "action_restore_published",
                "_selected_action": [str(sub.pk)],
                "post": "no",
            },
        )
        sub.refresh_from_db()
        assert sub.service_description == "An edit still waiting to be kept."

    def test_users_without_change_permission_get_no_confirmation_page(self, rf):
        from django.contrib import admin
        from django.contrib.messages.storage.cookie import CookieStorage

        from apps.submissions.admin import ServiceSubmissionAdmin

        sub = _published()
        request = rf.post("/fake/")
        request.user = get_user_model().objects.create_user(
            "reader", password="pw12345678"
        )
        request._messages = CookieStorage(request)
        response = ServiceSubmissionAdmin(
            ServiceSubmission, admin.site
        ).action_restore_published(request, ServiceSubmission.objects.filter(pk=sub.pk))
        assert response is None


def test_restore_discards_rejected_edit(superuser_client):
    sub = _published()
    approved_text = sub.service_description
    _rejected_edit(
        sub, service_description="An edit that was later rejected by the office."
    )

    _post(superuser_client, sub)

    sub.refresh_from_db()
    assert sub.service_description == approved_text
    assert sub.status == "rejected"
    log = SubmissionChangeLog.objects.filter(submission=sub).latest("changed_at")
    assert log.changed_by == "admin:testadmin"
    assert [c["field"] for c in log.changes] == ["service_description"]
    assert sub.last_change_summary["changed_by"] == "admin:testadmin"
    assert PublishedService.objects.get(pk=sub.pk).service_description == approved_text


def test_restore_does_not_change_the_status_or_the_public_copy(superuser_client):
    sub = _published()
    _rejected_edit(sub, service_name="Example Rejected Name")
    before = PublishedService.objects.values().get(pk=sub.pk)
    _post(superuser_client, sub)
    assert ServiceSubmission.objects.get(pk=sub.pk).status == "rejected"
    assert PublishedService.objects.values().get(pk=sub.pk) == before
    assert PublishedRevision.objects.filter(submission=sub).count() == 1
    assert sub.status_events.count() == 4  # created, approved, submitted, rejected


def test_restore_brings_back_relations(superuser_client):
    sub = _published()
    original = set(sub.service_categories.all())
    _rejected_edit(sub)
    sub.service_categories.set([ServiceCategoryFactory()])
    _post(superuser_client, sub)
    assert set(sub.service_categories.all()) == original
    log = SubmissionChangeLog.objects.filter(submission=sub).latest("changed_at")
    assert "service_categories" in [c["field"] for c in log.changes]


def test_restore_leaves_private_fields_alone(superuser_client):
    sub = _published()
    _rejected_edit(sub, service_name="Example Rejected Name")
    _save_behind(sub, comments="A private remark", internal_contact_email="n@e.org")
    _post(superuser_client, sub)
    sub.refresh_from_db()
    assert sub.comments == "A private remark"
    assert sub.internal_contact_email == "n@e.org"


def test_restore_brings_back_the_tag_of_the_version_after_review(superuser_client):
    """Tags are kept while a service is in review, so the restore puts the
    published one back like any other public field."""
    sub = _published(primary_maturity_tag="mature")
    set_status(sub, "under_review", actor="admin:jane")
    _save_behind(sub, primary_maturity_tag="emerging")
    _post(superuser_client, sub)
    sub.refresh_from_db()
    assert sub.status == "under_review"
    assert sub.primary_maturity_tag == "mature"


def test_restore_keeps_a_deprecated_service_free_of_tags(superuser_client):
    sub = _published(primary_maturity_tag="mature")
    set_status(sub, "deprecated", actor="admin:jane")
    _post(superuser_client, sub)
    sub.refresh_from_db()
    assert sub.primary_maturity_tag is None
    sub.full_clean(exclude=["logo"])  # still a valid record


def test_restore_keeps_an_approved_service_publishable(superuser_client):
    sub = _published()
    _save_behind(sub, website_url="https://example.org/db")
    _post(superuser_client, sub)
    sub.refresh_from_db()
    assert sub.status == "approved"
    assert sub.website_url != "https://example.org/db"


def test_restore_brings_back_the_published_logo(superuser_client, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    sub = ServiceSubmissionFactory(biotools_url="")
    sub.logo.save("a.png", ContentFile(b"first"))
    set_status(sub, "approved", actor="admin:jane")
    published_logo = PublishedService.objects.get(pk=sub.pk).logo.name
    set_status(sub, "submitted", actor="submitter", owner_edit=True)
    sub.logo.save("b.png", ContentFile(b"second"))
    set_status(sub, "rejected", actor="admin:jane")
    _post(superuser_client, sub)
    sub.refresh_from_db()
    assert sub.logo.name == published_logo


def test_restore_with_nothing_to_restore_writes_no_log(superuser_client):
    sub = _published()
    _post(superuser_client, sub)
    assert not SubmissionChangeLog.objects.filter(submission=sub).exists()


def test_restore_refuses_a_service_without_a_published_version(superuser_client):
    sub = ServiceSubmissionFactory(biotools_url="")
    before = ServiceSubmission.objects.values().get(pk=sub.pk)
    resp = _post(superuser_client, sub)
    assert resp.status_code == 200
    assert _messages(resp) == [f"1 submission(s) {WARNING}"]
    assert ServiceSubmission.objects.values().get(pk=sub.pk) == before
    assert not SubmissionChangeLog.objects.filter(submission=sub).exists()


def test_restore_handles_a_selection_of_several_services(superuser_client):
    a, b, c = _published(), _published(), ServiceSubmissionFactory(biotools_url="")
    _rejected_edit(a, service_name="Example Rejected A")
    _rejected_edit(b, service_name="Example Rejected B")
    resp = _post(superuser_client, a, b, c)
    assert ServiceSubmission.objects.get(pk=a.pk).service_name != "Example Rejected A"
    assert ServiceSubmission.objects.get(pk=b.pk).service_name != "Example Rejected B"
    assert _messages(resp) == [restored_message(2), f"1 submission(s) {WARNING}"]


def test_restore_is_recorded_in_the_admin_log(superuser_client):
    sub = _published()
    _rejected_edit(sub, service_name="Example Rejected Name")
    _post(superuser_client, sub)
    message = LogEntry.objects.filter(object_id=str(sub.pk)).latest("action_time")
    assert "Restored published revision 1" in message.change_message


def test_restore_uses_the_latest_revision(superuser_client):
    sub = _published()
    sub.website_url = "https://example.org/second"
    sub.save()
    publishing.publish(sub, reason="live_edit", actor="submitter")
    _rejected_edit(sub, website_url="https://example.org/rejected")
    _post(superuser_client, sub)
    assert (
        ServiceSubmission.objects.get(pk=sub.pk).website_url
        == "https://example.org/second"
    )


def test_user_without_change_permission_cannot_restore():
    sub = _published()
    _rejected_edit(sub, service_name="Example Rejected Name")
    user = get_user_model().objects.create_user(
        "viewer", password="pw12345678", is_staff=True
    )
    from django.contrib.auth.models import Permission

    user.user_permissions.add(Permission.objects.get(codename="view_servicesubmission"))
    client = Client()
    client.force_login(user)
    page = _ask(client, sub)
    assert not page.templates or "restore_published" not in page.templates[0].name
    client.post(  # a forged confirmation: there is no page to take it from
        reverse(CHANGELIST),
        {
            "action": "action_restore_published",
            "_selected_action": [str(sub.pk)],
            "post": "yes",
            VERSION_FIELD: f"{sub.pk}:{ServiceSubmission.objects.get(pk=sub.pk).version}",
        },
    )
    assert (
        ServiceSubmission.objects.get(pk=sub.pk).service_name == "Example Rejected Name"
    )


def test_action_is_offered_to_users_who_can_change(superuser_client):
    ServiceSubmissionFactory(biotools_url="")
    resp = superuser_client.get(reverse(CHANGELIST))
    assert b"Restore published version" in resp.content


def test_the_action_itself_refuses_users_without_change_permission(rf):
    """The action checks the permission itself, not only the admin's own gate."""
    from django.contrib import admin
    from django.contrib.messages.storage.cookie import CookieStorage

    from apps.submissions.admin import ServiceSubmissionAdmin

    sub = _published()
    _rejected_edit(sub, service_name="Example Rejected Name")
    user = get_user_model().objects.create_user("reader", password="pw12345678")
    request = rf.post("/fake/", {"post": "yes"})
    request.user = user
    request._messages = CookieStorage(request)
    action = ServiceSubmissionAdmin(ServiceSubmission, admin.site)
    action.action_restore_published(
        request, ServiceSubmission.objects.filter(pk=sub.pk)
    )
    assert (
        ServiceSubmission.objects.get(pk=sub.pk).service_name == "Example Rejected Name"
    )


class TestConfirmationStaysCurrent:
    """The confirm button only restores what the page showed. Anything that
    changed, or left the list, after the page was shown is left alone and
    reported."""

    def _rejected(self, **kw):
        sub = _published(**kw)
        _rejected_edit(sub, service_description=EDIT)
        return sub

    def test_a_save_after_the_page_was_shown_is_kept(self, superuser_client):
        sub = self._rejected()
        page = _ask(superuser_client, sub)
        _save_behind(sub, service_description="A newer edit after the page.")
        resp = _confirm(superuser_client, page)
        sub.refresh_from_db()
        assert sub.service_description == "A newer edit after the page."
        assert _messages(resp) == [f"1 submission(s) {WARNING}"]
        assert not SubmissionChangeLog.objects.filter(
            changed_by="admin:testadmin"
        ).exists()
        assert not LogEntry.objects.filter(
            change_message__startswith="Restored"
        ).exists()

    def test_a_status_change_after_the_page_was_shown_stops_the_restore(
        self, superuser_client
    ):
        sub = self._rejected()
        page = _ask(superuser_client, sub)
        set_status(ServiceSubmission.objects.get(pk=sub.pk), "under_review", actor="x")
        resp = _confirm(superuser_client, page)
        sub.refresh_from_db()
        assert sub.status == "under_review"
        assert sub.service_description == EDIT
        assert f"1 submission(s) {WARNING}" in _messages(resp)

    @pytest.mark.parametrize("kind", ["category", "pi", "licence"])
    def test_a_change_of_links_after_the_page_was_shown_stops_the_restore(
        self, superuser_client, kind
    ):
        sub = self._rejected()
        page = _ask(superuser_client, sub)
        row = ServiceSubmission.objects.get(pk=sub.pk)
        if kind == "category":
            row.service_categories.add(ServiceCategoryFactory())
        elif kind == "pi":
            row.responsible_pis.add(PIFactory())
        else:
            row.licenses.add(_another_row("licenses"))
        before = ServiceSubmission.objects.values().get(pk=sub.pk)
        resp = _confirm(superuser_client, page)
        assert _messages(resp) == [f"1 submission(s) {WARNING}"]
        assert ServiceSubmission.objects.values().get(pk=sub.pk) == before
        assert ServiceSubmission.objects.get(pk=sub.pk).service_description == EDIT

    def test_a_service_deleted_after_the_page_was_shown_is_reported(
        self, superuser_client
    ):
        gone, kept = self._rejected(), self._rejected()
        page = _ask(superuser_client, gone, kept)
        ServiceSubmission.objects.filter(pk=gone.pk).delete()
        resp = _confirm(superuser_client, page)
        assert _messages(resp) == [restored_message(1), f"1 submission(s) {WARNING}"]
        kept.refresh_from_db()
        assert kept.service_description != EDIT

    def test_a_service_that_no_longer_matches_the_filter_is_reported(
        self, superuser_client
    ):
        leaves, stays = self._rejected(), self._rejected()
        url = reverse(CHANGELIST) + "?status__exact=rejected"
        page = _ask(superuser_client, leaves, stays, url=url)
        set_status(
            ServiceSubmission.objects.get(pk=leaves.pk), "under_review", actor="x"
        )
        resp = _confirm(superuser_client, page, url=url)
        assert _messages(resp) == [restored_message(1), f"1 submission(s) {WARNING}"]
        leaves.refresh_from_db()
        assert leaves.service_description == EDIT

    def test_a_wrong_version_is_not_restored(self, superuser_client):
        sub = self._rejected()
        fields = _confirmation_fields(_ask(superuser_client, sub))
        wrong = int(_versions(fields)[str(sub.pk)]) + 1
        fields = _with_versions(fields, {sub.pk: wrong})
        resp = superuser_client.post(reverse(CHANGELIST), fields, follow=True)
        sub.refresh_from_db()
        assert sub.service_description == EDIT
        assert _messages(resp) == [f"1 submission(s) {WARNING}"]

    def test_a_missing_version_is_not_restored(self, superuser_client):
        sub = self._rejected()
        fields = _confirmation_fields(_ask(superuser_client, sub))
        del fields[VERSION_FIELD]
        resp = superuser_client.post(reverse(CHANGELIST), fields, follow=True)
        sub.refresh_from_db()
        assert sub.service_description == EDIT
        assert _messages(resp) == [f"1 submission(s) {WARNING}"]

    def test_versions_swapped_between_services_are_not_accepted(self, superuser_client):
        a = self._rejected(service_name="Example Alpha Service")
        _save_behind(a, website_url="https://example.org/alpha")  # versions differ
        b = self._rejected(service_name="Example Beta Service")
        fields = _confirmation_fields(_ask(superuser_client, a, b))
        versions = _versions(fields)
        assert versions[str(a.pk)] != versions[str(b.pk)]
        fields = _with_versions(
            fields, {a.pk: versions[str(b.pk)], b.pk: versions[str(a.pk)]}
        )
        superuser_client.post(reverse(CHANGELIST), fields, follow=True)
        for sub in (a, b):
            sub.refresh_from_db()
            assert sub.service_description == EDIT

    def test_a_select_all_confirmation_without_versions_restores_nothing(
        self, superuser_client
    ):
        subs = [self._rejected() for _ in range(2)]
        resp = superuser_client.post(
            reverse(CHANGELIST),
            {
                "action": "action_restore_published",
                "select_across": "1",
                "post": "yes",
                "_selected_action": [str(subs[0].pk)],
            },
            follow=True,
        )
        for sub in subs:
            sub.refresh_from_db()
            assert sub.service_description == EDIT
        assert f"2 submission(s) {WARNING}" in _messages(resp)

    def test_the_page_posts_one_version_per_service(self, superuser_client):
        a, b = self._rejected(), self._rejected()
        fields = _confirmation_fields(_ask(superuser_client, a, b))
        versions = _versions(fields)
        assert sorted(versions) == sorted([str(a.pk), str(b.pk)])
        for sub in (a, b):
            assert versions[str(sub.pk)] == str(
                ServiceSubmission.objects.get(pk=sub.pk).version
            )

    def test_select_all_still_restores_every_service(self, superuser_client):
        subs = [self._rejected() for _ in range(3)]
        page = _ask(superuser_client, subs[0], select_across="1")
        resp = _confirm(superuser_client, page)
        assert _messages(resp) == [restored_message(3)]


class TestFormSize:
    """The confirm button posts one field per service plus one for all the
    versions, so a large selection stays below the request field limit."""

    @staticmethod
    def _field_count(client, count):
        subs = [ServiceSubmissionFactory(biotools_url="") for _ in range(count)]
        fields = _confirmation_fields(_ask(client, *subs))
        assert len(fields[VERSION_FIELD]) == 1
        return sum(len(values) for values in fields.values())

    def test_each_service_adds_one_field(self, superuser_client):
        assert self._field_count(superuser_client, 8) - self._field_count(
            superuser_client, 3
        ) == (8 - 3)

    def test_a_selection_near_the_field_limit_is_confirmed(
        self, superuser_client, settings
    ):
        subs = [_published() for _ in range(40)]
        page = _ask(superuser_client, *subs)
        settings.DATA_UPLOAD_MAX_NUMBER_FIELDS = 50
        resp = _confirm(superuser_client, page)
        assert resp.status_code == 200
        assert _messages(resp) == [restored_message(0, 40)]


class TestRowsWithNothingToDiscard:
    """A working copy that already equals the latest published version has
    nothing to restore: it is not written, not logged and counted apart."""

    def test_the_page_marks_only_those_rows(self, superuser_client):
        same = _published(service_name="Example Same Service")
        edited = _published(service_name="Example Edited Service")
        _rejected_edit(edited, service_description=EDIT)
        page = _post(superuser_client, same, edited, confirm=False).content.decode()
        assert page.count("Nothing to discard") == 1
        same_row = page[page.index("Example Same Service") :]
        assert "Nothing to discard" in same_row[: same_row.index("</tr>")]

    def test_confirming_writes_nothing(self, superuser_client):
        sub = _published()
        before = ServiceSubmission.objects.values().get(pk=sub.pk)
        resp = _post(superuser_client, sub)
        assert not SubmissionChangeLog.objects.filter(submission=sub).exists()
        assert not LogEntry.objects.filter(
            change_message__startswith="Restored"
        ).exists()
        assert ServiceSubmission.objects.values().get(pk=sub.pk) == before
        assert _messages(resp) == [restored_message(0, 1)]

    def test_confirming_does_not_save_the_row(self, superuser_client):
        sub = _published()
        saves = []

        def count(sender, instance, **kwargs):
            saves.append(instance.pk)

        post_save.connect(count, sender=ServiceSubmission)
        try:
            _post(superuser_client, sub)
        finally:
            post_save.disconnect(count, sender=ServiceSubmission)
        assert saves == []

    def test_a_mixed_selection_gets_each_count(self, superuser_client):
        edited = _published()
        _rejected_edit(edited, service_description=EDIT)
        same = _published()
        unpublished = ServiceSubmissionFactory(biotools_url="")
        late = _published()
        _rejected_edit(late, service_description=EDIT)
        page = _ask(superuser_client, edited, same, unpublished, late)
        _save_behind(late, service_description="A newer edit after the page.")
        messages = _messages(_confirm(superuser_client, page))
        assert messages == [restored_message(1, 1), f"2 submission(s) {WARNING}"]


def _rich_published(**kw):
    """A published service with several relations, a logo and a maturity tag."""
    sub = ServiceSubmissionFactory(biotools_url="", **kw)
    sub.service_categories.set([ServiceCategoryFactory() for _ in range(3)])
    sub.responsible_pis.set([PIFactory() for _ in range(3)])
    ServiceSubmission.objects.filter(pk=sub.pk).update(logo="logos/example-logo.png")
    set_status(ServiceSubmission.objects.get(pk=sub.pk), "approved", actor="admin:jane")
    sub = ServiceSubmission.objects.get(pk=sub.pk)
    sub.primary_maturity_tag = "mature"
    sub.save()
    publishing.republish(sub, reason="tag_change", actor="admin:jane")
    return ServiceSubmission.objects.get(pk=sub.pk)


class TestPageAndConfirmAgree:
    """The page and the confirm button read the same version, so a service
    nobody touched is never reported as changed."""

    @pytest.mark.parametrize(
        "query",
        [
            "",
            "?status__exact=rejected",
            "?q=Example",
            "?o=-1",
            "?o=2.1&status__exact=rejected",
        ],
    )
    def test_a_rich_service_is_restored_under_filters_search_and_ordering(
        self, superuser_client, query
    ):
        sub = _rich_published(service_name="Example Rich Service")
        _rejected_edit(sub, service_description=EDIT)
        ServiceSubmission.objects.get(pk=sub.pk).responsible_pis.set(
            list(sub.responsible_pis.all())[:1]
        )
        url = reverse(CHANGELIST) + query
        resp = _confirm(superuser_client, _ask(superuser_client, sub, url=url), url=url)
        assert _messages(resp) == [restored_message(1)]
        assert ServiceSubmission.objects.get(pk=sub.pk).responsible_pis.count() == 3

    def test_select_all_on_the_second_page_restores_every_row(
        self, superuser_client, monkeypatch
    ):
        from apps.submissions.admin import ServiceSubmissionAdmin

        subs = [_published(service_name=f"Example Paged {i}") for i in range(5)]
        for i, sub in enumerate(subs):
            _rejected_edit(sub, service_description=f"An edit to discard {i}.")
        monkeypatch.setattr(ServiceSubmissionAdmin, "list_per_page", 2)
        url = reverse(CHANGELIST) + "?p=2"
        page = _ask(superuser_client, subs[0], url=url, select_across="1")
        assert len(_versions(_confirmation_fields(page))) == 5
        resp = _confirm(superuser_client, page, url=url)
        assert _messages(resp) == [restored_message(5)]


class TestRestoreOfNamesakes:
    def test_a_swap_between_pis_with_the_same_name_is_restored_and_logged(
        self, superuser_client
    ):
        original = PrincipalInvestigator.objects.create(
            last_name="Doe",
            first_name="John",
            email="john.doe@example.org",
            institute="Example Institute A",
            is_active=True,
        )
        namesake = PrincipalInvestigator.objects.create(
            last_name="Doe",
            first_name="John",
            email="j.doe@example.com",
            institute="Example Institute B",
            is_active=True,
        )
        sub = ServiceSubmissionFactory(biotools_url="")
        sub.responsible_pis.set([original])
        set_status(sub, "approved", actor="admin:jane")
        set_status(sub, "submitted", actor="submitter", owner_edit=True)
        sub.responsible_pis.set([namesake])
        set_status(sub, "rejected", actor="admin:jane")

        resp = _post(superuser_client, sub)

        assert list(sub.responsible_pis.all()) == [original]
        assert _messages(resp) == [restored_message(1)]
        assert LogEntry.objects.filter(
            object_id=str(sub.pk), change_message__startswith="Restored"
        ).exists()
        # The field-level diff compares names and is empty here.
        assert not SubmissionChangeLog.objects.filter(
            submission=sub, changed_by="admin:testadmin"
        ).exists()


class TestMessages:
    def test_no_success_message_when_every_row_was_left_alone(self, superuser_client):
        sub = _published()
        _rejected_edit(sub, service_description=EDIT)
        page = _ask(superuser_client, sub)
        _save_behind(sub, service_description="A newer edit after the page.")
        assert _messages(_confirm(superuser_client, page)) == [
            f"1 submission(s) {WARNING}"
        ]

    def test_a_selection_deleted_before_the_page_says_so(self, superuser_client):
        gone = ServiceSubmissionFactory(biotools_url="")
        ServiceSubmission.objects.filter(pk=gone.pk).delete()
        resp = _ask(superuser_client, gone)
        assert _messages(resp) == ["The selected submissions no longer exist."]
        assert "restore_published_confirmation.html" not in [
            t.name.rsplit("/", 1)[-1] for t in resp.templates
        ]

    @pytest.mark.parametrize("flag", ["0", "false", ""])
    def test_select_across_switched_off_still_names_the_deleted_selection(
        self, superuser_client, flag
    ):
        gone = ServiceSubmissionFactory(biotools_url="")
        ServiceSubmission.objects.filter(pk=gone.pk).delete()
        resp = _ask(superuser_client, gone, select_across=flag)
        assert _messages(resp) == ["The selected submissions no longer exist."]

    def test_a_selection_deleted_before_the_confirmation_is_reported(
        self, superuser_client
    ):
        gone = ServiceSubmissionFactory(biotools_url="")
        page = _ask(superuser_client, gone)
        ServiceSubmission.objects.filter(pk=gone.pk).delete()
        assert _messages(_confirm(superuser_client, page)) == [
            f"1 submission(s) {WARNING}"
        ]

    def test_an_empty_select_all_warns_and_shows_no_confirmation(
        self, superuser_client
    ):
        resp = superuser_client.post(
            reverse(CHANGELIST) + "?q=NoSuchServiceAnywhere",
            {
                "action": "action_restore_published",
                "select_across": "1",
                "_selected_action": ["00000000-0000-0000-0000-000000000000"],
            },
            follow=True,
        )
        assert _messages(resp) == ["No submissions selected."]
        assert "restore_published_confirmation.html" not in [
            t.name.rsplit("/", 1)[-1] for t in resp.templates
        ]


def _restore_directly(rf, loaded, shown):
    """Run the action on rows loaded earlier (as the changelist does). ``shown``
    is the ``<id>:<version>`` text the confirmation page posted."""
    from django.contrib import admin
    from django.contrib.messages.storage.cookie import CookieStorage

    from apps.submissions.admin import ServiceSubmissionAdmin

    request = rf.post(
        "/fake/",
        {
            "post": "yes",
            VERSION_FIELD: shown,
            "_selected_action": [str(s.pk) for s in loaded],
        },
    )
    request.user = get_user_model().objects.create_superuser(
        "restorer", password="pw12345678"
    )
    request._messages = CookieStorage(request)
    ServiceSubmissionAdmin(ServiceSubmission, admin.site).action_restore_published(
        request, loaded
    )
    return [str(m) for m in request._messages._queued_messages]


def _shown(*subs):
    """What the confirmation page would post for these services right now."""
    return " ".join(
        f"{pk}:{version}"
        for pk, version in ServiceSubmission.objects.filter(
            pk__in=[s.pk for s in subs]
        ).values_list("pk", "version")
    )


def test_restore_reads_the_service_again_under_its_lock(rf):
    """The changelist loaded the service, then a live edit was saved before
    the restore took the row lock: the restore leaves it alone."""
    sub = _published(service_name="Example First Version")
    loaded = [ServiceSubmission.objects.get(pk=sub.pk)]
    shown = _shown(sub)
    live = ServiceSubmission.objects.get(pk=sub.pk)
    live.service_name = "Example Live Edit"
    live.save()
    publishing.republish(live, reason="live_edit", actor="submitter")
    messages = _restore_directly(rf, loaded, shown)
    assert ServiceSubmission.objects.get(pk=sub.pk).service_name == "Example Live Edit"
    assert not SubmissionChangeLog.objects.filter(submission=sub).exists()
    assert messages == [f"1 submission(s) {WARNING}"]


def test_restore_of_a_service_deleted_meanwhile_is_reported(rf):
    sub = _published()
    loaded = [ServiceSubmission.objects.get(pk=sub.pk)]
    shown = _shown(sub)
    ServiceSubmission.objects.filter(pk=sub.pk).delete()
    assert _restore_directly(rf, loaded, shown) == [f"1 submission(s) {WARNING}"]


def test_restore_of_a_queryset_without_rows_warns(rf):
    messages = _restore_directly(rf, ServiceSubmission.objects.none(), shown="")
    assert messages == ["No submissions selected."]


def test_restore_locks_the_row_before_reading_the_revision(rf, locked_models):
    sub = _published()
    _rejected_edit(sub, service_name="Example Rejected Name")
    loaded = [ServiceSubmission.objects.get(pk=sub.pk)]
    shown = _shown(sub)
    del locked_models[:]
    _restore_directly(rf, loaded, shown)
    assert locked_models[:1] == ["ServiceSubmission"]


def test_one_stale_row_does_not_stop_the_others(rf):
    stale, fresh = _published(), _published()
    _rejected_edit(stale, service_name="Example Stale")
    _rejected_edit(fresh, service_name="Example Fresh")
    loaded = list(ServiceSubmission.objects.filter(pk__in=[stale.pk, fresh.pk]))
    shown = _shown(stale, fresh)
    _save_behind(stale, website_url="https://example.org/newer")
    messages = _restore_directly(rf, loaded, shown)
    assert messages == [restored_message(1), f"1 submission(s) {WARNING}"]
    assert ServiceSubmission.objects.get(pk=stale.pk).service_name == "Example Stale"
    assert ServiceSubmission.objects.get(pk=fresh.pk).service_name != "Example Fresh"


CLEANED_TEXT_FIELDS = [
    "service_name",
    "service_description",
    "toolbox_name",
    "user_knowledge_required",
    "host_institute",
    "keywords_seo",
]
# Stored text that a full save rewrites: line ends, edge whitespace, and
# decomposed characters.
UNCLEAN_TEXT = {
    "crlf": "Example first line\r\nExample second line",
    "trailing_space": "Example text with a trailing space ",
    "decomposed": "Example für text",
}


def _published_with_stored_text(field, stored):
    """Published from a row whose text was written without a full save (as
    the data migration that created the first versions wrote it)."""
    sub = ServiceSubmissionFactory(biotools_url="")
    ServiceSubmission.objects.filter(pk=sub.pk).update(**{field: stored})
    set_status(ServiceSubmission.objects.get(pk=sub.pk), "approved", actor="admin:jane")
    assert PublishedRevision.objects.get(submission=sub).content[field] == stored
    return ServiceSubmission.objects.get(pk=sub.pk)


def _restore_logs(sub):
    return LogEntry.objects.filter(
        object_id=str(sub.pk), change_message__startswith="Restored"
    ).count()


class TestVersionTextThatASaveRewrites:
    """The comparison uses the text the working copy holds after the restore
    and its save, so a no-op restore is recognised as one."""

    @pytest.mark.parametrize("variant", sorted(UNCLEAN_TEXT))
    @pytest.mark.parametrize("field", CLEANED_TEXT_FIELDS)
    def test_the_page_marks_a_working_copy_that_was_saved_since(
        self, superuser_client, field, variant
    ):
        sub = _published_with_stored_text(field, UNCLEAN_TEXT[variant])
        _rejected_edit(ServiceSubmission.objects.get(pk=sub.pk))
        page = _ask(superuser_client, sub).content.decode()
        assert page.count("Nothing to discard") == 1

    @pytest.mark.parametrize("field", CLEANED_TEXT_FIELDS)
    def test_confirming_counts_it_as_matching_and_writes_nothing(
        self, superuser_client, field
    ):
        sub = _published_with_stored_text(field, UNCLEAN_TEXT["crlf"])
        _rejected_edit(ServiceSubmission.objects.get(pk=sub.pk))
        before = ServiceSubmission.objects.values().get(pk=sub.pk)
        saves = []

        def count(sender, instance, **kwargs):
            saves.append(instance.pk)

        page = _ask(superuser_client, sub)
        post_save.connect(count, sender=ServiceSubmission)
        try:
            resp = _confirm(superuser_client, page)
        finally:
            post_save.disconnect(count, sender=ServiceSubmission)
        assert saves == []
        assert ServiceSubmission.objects.values().get(pk=sub.pk) == before
        assert _restore_logs(sub) == 0
        assert _messages(resp) == [restored_message(0, 1)]

    @pytest.mark.parametrize("field", CLEANED_TEXT_FIELDS)
    def test_one_restore_leaves_the_row_matching(self, superuser_client, field):
        sub = _published_with_stored_text(field, UNCLEAN_TEXT["crlf"])
        _rejected_edit(
            ServiceSubmission.objects.get(pk=sub.pk),
            website_url="https://example.org/rejected-edit",
        )
        resp = _confirm(superuser_client, _ask(superuser_client, sub))
        assert _messages(resp) == [restored_message(1)]
        assert _restore_logs(sub) == 1
        page = _ask(superuser_client, sub).content.decode()
        assert page.count("Nothing to discard") == 1


class TestVersionNamingDeletedReferenceRows:
    """A reference row deleted since the version was published cannot be
    restored, so it does not count as a difference."""

    def _published_with(self, relation, rows):
        sub = ServiceSubmissionFactory(biotools_url="")
        getattr(sub, relation).set(rows)
        set_status(sub, "approved", actor="admin:jane")
        return ServiceSubmission.objects.get(pk=sub.pk)

    @pytest.mark.parametrize(
        "relation,factory",
        [
            ("service_categories", ServiceCategoryFactory),
            ("responsible_pis", PIFactory),
        ],
    )
    def test_a_restore_that_changes_nothing_is_counted_as_matching(
        self, superuser_client, relation, factory
    ):
        keep, gone = factory(), factory()
        sub = self._published_with(relation, [keep, gone])
        set_status(sub, "under_review", actor="admin:jane")
        set_status(sub, "rejected", actor="admin:jane")
        type(gone).objects.filter(pk=gone.pk).delete()
        before = ServiceSubmission.objects.values().get(pk=sub.pk)
        page = _ask(superuser_client, sub)
        assert page.content.decode().count("Nothing to discard") == 1
        resp = _confirm(superuser_client, page)
        assert _messages(resp) == [restored_message(0, 1)]
        assert ServiceSubmission.objects.values().get(pk=sub.pk) == before
        assert _restore_logs(sub) == 0

    @pytest.mark.parametrize(
        "relation,factory",
        [
            ("service_categories", ServiceCategoryFactory),
            ("responsible_pis", PIFactory),
        ],
    )
    def test_one_restore_leaves_the_row_matching(
        self, superuser_client, relation, factory
    ):
        keep, gone = factory(), factory()
        sub = self._published_with(relation, [keep, gone])
        _rejected_edit(sub, service_description=EDIT)
        type(gone).objects.filter(pk=gone.pk).delete()
        resp = _confirm(superuser_client, _ask(superuser_client, sub))
        assert _messages(resp) == [restored_message(1)]
        assert list(getattr(sub, relation).all()) == [keep]
        page = _ask(superuser_client, sub).content.decode()
        assert page.count("Nothing to discard") == 1


def _without(sub, *names):
    """Make the latest version one that was written before ``names`` were
    public fields."""
    revision = PublishedRevision.objects.get(submission=sub)
    older = {k: v for k, v in revision.content.items() if k not in names}
    PublishedRevision.objects.filter(pk=revision.pk).update(
        content=older, content_hash=publishing.content_hash(older)
    )


class TestVersionWithoutAField:
    """A version written before a field became public does not have it."""

    def test_the_service_centre_of_the_working_copy_is_kept(self, superuser_client):
        sub = _published()
        _without(sub, "service_center")
        _rejected_edit(sub, service_description=EDIT)
        newer = ServiceCenterFactory()
        _save_behind(sub, service_center=newer)
        resp = _confirm(superuser_client, _ask(superuser_client, sub))
        assert _messages(resp) == [restored_message(1)]
        row = ServiceSubmission.objects.get(pk=sub.pk)
        assert row.service_center_id == newer.pk
        assert row.service_description != EDIT

    def test_a_working_copy_that_otherwise_matches_is_not_rewritten(
        self, superuser_client
    ):
        sub = _published()
        _without(sub, "service_center")
        set_status(sub, "under_review", actor="admin:jane")
        set_status(sub, "rejected", actor="admin:jane")
        page = _ask(superuser_client, sub)
        assert page.content.decode().count("Nothing to discard") == 1

    def test_a_required_value_without_a_default_is_kept(self, superuser_client):
        sub = _published(year_established=2010)
        _without(sub, "year_established")
        _rejected_edit(sub, year_established=2015, service_description=EDIT)
        _confirm(superuser_client, _ask(superuser_client, sub))
        row = ServiceSubmission.objects.get(pk=sub.pk)
        assert row.year_established == 2015
        assert row.service_description != EDIT

    def test_an_optional_value_is_kept(self, superuser_client):
        sub = _published(primary_maturity_tag="mature")
        _without(sub, "primary_maturity_tag")
        _save_behind(sub, service_description=EDIT)
        _confirm(superuser_client, _ask(superuser_client, sub))
        row = ServiceSubmission.objects.get(pk=sub.pk)
        assert row.primary_maturity_tag == "mature"
        assert row.service_description != EDIT

    @pytest.mark.parametrize("field", PUBLIC_SCALAR_FIELDS)
    def test_every_scalar_field_keeps_its_value_when_missing(
        self, superuser_client, field
    ):
        sub = _published()
        other = "license_note" if field != "license_note" else "github_url"
        _without(sub, field)
        ServiceSubmission.objects.filter(pk=sub.pk).update(
            **{field: FIELD_EDITS[field], other: FIELD_EDITS[other]}
        )
        resp = _confirm(superuser_client, _ask(superuser_client, sub))
        assert _messages(resp) == [restored_message(1)]
        row = ServiceSubmission.objects.get(pk=sub.pk)
        kept = FIELD_EDITS[field]
        if isinstance(kept, str):
            kept = kept.replace("\r\n", "\n")  # a save stores LF
        assert getattr(row, field) == kept
        assert getattr(row, other) != FIELD_EDITS[other]

    @pytest.mark.parametrize("relation", PUBLIC_M2M_FIELDS)
    def test_every_relation_keeps_its_rows_when_missing(
        self, superuser_client, relation
    ):
        sub = _published()
        _without(sub, relation)
        extra = _another_row(relation)
        getattr(ServiceSubmission.objects.get(pk=sub.pk), relation).add(extra)
        ServiceSubmission.objects.filter(pk=sub.pk).update(
            license_note=FIELD_EDITS["license_note"]
        )
        resp = _confirm(superuser_client, _ask(superuser_client, sub))
        assert _messages(resp) == [restored_message(1)]
        row = ServiceSubmission.objects.get(pk=sub.pk)
        assert extra in getattr(row, relation).all()
        assert row.license_note != FIELD_EDITS["license_note"]


class TestConfirmationPageQueries:
    """The number of queries the confirmation page runs does not grow with the
    number of rows (compared as a ratio, never as a time)."""

    @staticmethod
    def _full_services(count, shared):
        subs = []
        for _ in range(count):
            sub = ServiceSubmissionFactory(biotools_url="")
            sub.service_categories.set([ServiceCategoryFactory() for _ in range(2)])
            sub.responsible_pis.set([PIFactory() for _ in range(2)])
            for relation, row in shared.items():
                getattr(sub, relation).add(row)
            set_status(ServiceSubmission.objects.get(pk=sub.pk), "approved", actor="a")
            subs.append(sub)
        return subs

    def _page_queries(self, client, subs):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as queries:
            page = _ask(client, *subs)
        assert page.content.decode().count("Nothing to discard") == len(subs)
        return len(queries)

    def test_queries_per_row_stay_bounded(self, superuser_client):
        shared = {
            name: _another_row(name)
            for name in ("edam_topics", "edam_operations", "licenses")
        }
        few = self._full_services(5, shared)
        many = self._full_services(20, shared)
        small = self._page_queries(superuser_client, few)
        large = self._page_queries(superuser_client, many)
        # Fifteen more rows cost a few queries at most, far from linear growth.
        assert large - small <= 3
        assert large < 1.2 * small

    def test_the_page_content_does_not_depend_on_the_prefetch(self, superuser_client):
        sub = _rich_published()
        _rejected_edit(sub, service_description=EDIT)
        ServiceSubmission.objects.get(pk=sub.pk).responsible_pis.set(
            list(sub.responsible_pis.all())[:1]
        )
        page = _ask(superuser_client, sub).content.decode()
        assert "Nothing to discard" not in page
        resp = _confirm(superuser_client, _ask(superuser_client, sub))
        assert _messages(resp) == [restored_message(1)]
        assert ServiceSubmission.objects.get(pk=sub.pk).responsible_pis.count() == 3


class TestVersionWithAFieldThatIsNotPublicAnyMore:
    def _with_a_retired_field(self, sub):
        revision = PublishedRevision.objects.get(submission=sub)
        older = {**revision.content, "retired_field": "Example"}
        PublishedRevision.objects.filter(pk=revision.pk).update(
            content=older, content_hash=publishing.content_hash(older)
        )

    def test_the_page_marks_a_matching_working_copy(self, superuser_client):
        sub = _published()
        self._with_a_retired_field(sub)
        page = _ask(superuser_client, sub).content.decode()
        assert page.count("Nothing to discard") == 1

    def test_confirming_counts_it_as_matching_and_writes_nothing(
        self, superuser_client
    ):
        sub = _published()
        self._with_a_retired_field(sub)
        resp = _confirm(superuser_client, _ask(superuser_client, sub))
        assert _messages(resp) == [restored_message(0, 1)]
        assert not LogEntry.objects.filter(
            change_message__startswith="Restored"
        ).exists()
        assert not SubmissionChangeLog.objects.filter(submission=sub).exists()


class TestConfirmationPageReadsOneVersionPerService:
    def _versions_loaded_by_the_page(self, superuser_client, subs):
        from django.db.models.signals import post_init

        loaded = []

        def count(sender, instance, **kwargs):
            loaded.append(instance.submission_id)

        post_init.connect(count, sender=PublishedRevision)
        try:
            _ask(superuser_client, *subs)
        finally:
            post_init.disconnect(count, sender=PublishedRevision)
        return loaded

    def _with_versions(self, count, **kw):
        sub = _published(**kw)
        for i in range(count - 1):
            row = ServiceSubmission.objects.get(pk=sub.pk)
            row.website_url = f"https://example.org/version-{i}"
            row.save()
            publishing.republish(row, reason="live_edit", actor="submitter")
        assert PublishedRevision.objects.filter(submission=sub).count() == count
        return ServiceSubmission.objects.get(pk=sub.pk)

    def test_the_number_of_versions_does_not_change_what_is_loaded(
        self, superuser_client
    ):
        few = [self._with_versions(2) for _ in range(2)]
        many = [self._with_versions(10) for _ in range(2)]
        assert sorted(self._versions_loaded_by_the_page(superuser_client, few)) == (
            sorted(s.pk for s in few)
        )
        assert sorted(self._versions_loaded_by_the_page(superuser_client, many)) == (
            sorted(s.pk for s in many)
        )

    def test_the_newest_version_is_the_one_shown(self, superuser_client):
        sub = self._with_versions(4)
        page = _ask(superuser_client, sub).content.decode()
        assert "Published version 4" in page


class TestWordingWhenNothingIsDiscarded:
    CONTINUE = 'value="Yes, continue"'
    DISCARD = 'value="Yes, restore and discard the edits"'

    def _matching(self):
        sub = _published()
        set_status(
            ServiceSubmission.objects.get(pk=sub.pk), "under_review", actor="admin:jane"
        )
        set_status(
            ServiceSubmission.objects.get(pk=sub.pk), "rejected", actor="admin:jane"
        )
        return sub

    def _differing(self):
        sub = _published()
        _rejected_edit(sub, service_description=EDIT)
        return sub

    def test_rows_that_all_match_say_nothing_will_be_discarded(self, superuser_client):
        page = _ask(superuser_client, self._matching(), self._matching())
        content = page.content.decode()
        assert "Nothing will be discarded" in content
        assert "Any edits made since that version was published" not in content
        assert self.CONTINUE in content
        assert self.DISCARD not in content

    def test_rows_without_a_version_count_as_nothing_to_discard(self, superuser_client):
        unpublished = ServiceSubmissionFactory(biotools_url="")
        content = _ask(superuser_client, unpublished, self._matching()).content.decode()
        assert "Nothing will be discarded" in content
        assert self.CONTINUE in content

    def test_a_selection_of_only_unpublished_rows_is_neutral_too(
        self, superuser_client
    ):
        content = _ask(
            superuser_client, ServiceSubmissionFactory(biotools_url="")
        ).content.decode()
        assert self.CONTINUE in content

    def test_one_row_with_edits_keeps_the_discard_wording(self, superuser_client):
        content = _ask(
            superuser_client, self._matching(), self._differing()
        ).content.decode()
        assert "Any edits made since that version was published" in content
        assert "Nothing will be discarded" not in content
        assert self.DISCARD in content
        assert self.CONTINUE not in content

    def test_the_continue_button_still_confirms(self, superuser_client):
        sub = self._matching()
        resp = _confirm(superuser_client, _ask(superuser_client, sub))
        assert _messages(resp) == [restored_message(0, 1)]


# A change to each kind of public field, written straight to the database
# (as a rejected edit leaves it).
FIELD_EDITS = {
    "service_name": "Example Renamed Service",
    "service_description": "A different example description that is long enough.\r\nWith a second line.",
    "user_knowledge_required": "Some other knowledge.",
    "primary_maturity_tag": "mature",
    "host_institute": "Example Other Institute",
    "year_established": 2001,
    "is_toolbox": True,
    "toolbox_name": "Example Toolbox Name",
    "website_url": "https://example.org/other",
    "terms_of_use_url": "https://example.org/terms-other",
    "license_note": "Other note",
    "public_contact_email": "jane.doe@example.org",
    "github_url": "https://github.com/example/other",
    "biotools_url": "https://bio.tools/example_other",
    "fairsharing_url": "https://fairsharing.org/other",
    "other_registry_url": "https://example.org/registry-other",
    "keywords_seo": "other, keywords",
    "publications_pmids": "12345",
    "logo": "logos/other.png",
}


def _another_row(relation):
    """A reference row for ``relation`` that no service has yet."""
    from apps.edam.models import EdamTerm
    from apps.licenses.models import SpdxLicense

    return {
        "service_categories": ServiceCategoryFactory,
        "responsible_pis": PIFactory,
        "edam_topics": lambda: EdamTerm.objects.create(
            uri="http://edamontology.org/topic_9100",
            accession="topic_9100",
            branch="topic",
            label="Example topic",
        ),
        "edam_operations": lambda: EdamTerm.objects.create(
            uri="http://edamontology.org/operation_9100",
            accession="operation_9100",
            branch="operation",
            label="Example operation",
        ),
        "licenses": lambda: SpdxLicense.objects.create(
            license_id="Example-1.0", name="Example licence"
        ),
    }[relation]()


class TestOneRestoreLeavesEveryFieldKindMatching:
    """After a restore the working copy holds the published content, whatever
    kind of field was edited."""

    def _assert_restored_and_matching(self, client, sub):
        resp = _confirm(client, _ask(client, sub))
        assert _messages(resp) == [restored_message(1)]
        row = ServiceSubmission.objects.get(pk=sub.pk)
        newest = PublishedRevision.objects.filter(submission=sub).latest("number")
        assert publishing.content_hash(publishing.build_content(row)) == (
            newest.content_hash
        )
        assert _ask(client, sub).content.decode().count("Nothing to discard") == 1

    @pytest.mark.parametrize("field", sorted(FIELD_EDITS))
    def test_scalar_fields(self, superuser_client, field):
        sub = _published(primary_maturity_tag=None)
        ServiceSubmission.objects.filter(pk=sub.pk).update(
            **{field: FIELD_EDITS[field]}
        )
        assert _ask(superuser_client, sub).content.decode().count("Nothing") == 0
        self._assert_restored_and_matching(superuser_client, sub)

    def test_service_centre(self, superuser_client):
        sub = _published()
        ServiceSubmission.objects.filter(pk=sub.pk).update(
            service_center=ServiceCenterFactory()
        )
        self._assert_restored_and_matching(superuser_client, sub)

    @pytest.mark.parametrize("relation", PUBLIC_M2M_FIELDS)
    def test_relations(self, superuser_client, relation):
        sub = _published()
        getattr(ServiceSubmission.objects.get(pk=sub.pk), relation).add(
            _another_row(relation)
        )
        self._assert_restored_and_matching(superuser_client, sub)


class TestPostedIdForms:
    """The same service is recognised whichever way its id is written."""

    @staticmethod
    def _written(pk, form):
        return {
            "upper": str(pk).upper(),
            "braces": "{" + str(pk) + "}",
            "hex": pk.hex,
            "urn": f"urn:uuid:{pk}",
        }[form]

    @pytest.mark.parametrize("form", ["upper", "braces", "hex", "urn"])
    def test_the_id_in_the_versions_is_recognised(self, superuser_client, form):
        sub = _published()
        _rejected_edit(sub, service_description=EDIT)
        fields = _confirmation_fields(_ask(superuser_client, sub))
        (version,) = _versions(fields).values()
        fields = _with_versions(fields, {self._written(sub.pk, form): version})
        resp = superuser_client.post(reverse(CHANGELIST), fields, follow=True)
        assert _messages(resp) == [restored_message(1)]

    @pytest.mark.parametrize("form", ["upper", "braces", "hex", "urn"])
    def test_the_id_in_the_selection_is_recognised(self, superuser_client, form):
        sub = _published()
        _rejected_edit(sub, service_description=EDIT)
        fields = _confirmation_fields(_ask(superuser_client, sub))
        fields["_selected_action"] = [self._written(sub.pk, form)]
        resp = superuser_client.post(reverse(CHANGELIST), fields, follow=True)
        assert _messages(resp) == [restored_message(1)]


class TestMalformedVersionField:
    @pytest.mark.parametrize(
        "value",
        [
            "",
            "   ",
            "no-colon-at-all",
            ":",
            "::",
            "{not json}",
            "a:b:c d",
            "x:-1",
            "x:1.5",
        ],
    )
    def test_a_malformed_field_restores_nothing(self, superuser_client, value):
        sub = _published()
        _rejected_edit(sub, service_description=EDIT)
        fields = {**_confirmation_fields(_ask(superuser_client, sub))}
        fields[VERSION_FIELD] = [value]
        resp = superuser_client.post(reverse(CHANGELIST), fields, follow=True)
        sub.refresh_from_db()
        assert sub.service_description == EDIT
        assert _messages(resp) == [f"1 submission(s) {WARNING}"]

    @pytest.mark.parametrize(
        "version", ["abc", "-1", "1.5", "", "99999999999999999999"]
    )
    def test_a_version_that_is_not_a_number_restores_nothing(
        self, superuser_client, version
    ):
        sub = _published()
        _rejected_edit(sub, service_description=EDIT)
        fields = _confirmation_fields(_ask(superuser_client, sub))
        fields[VERSION_FIELD] = [f"{sub.pk}:{version}"]
        resp = superuser_client.post(reverse(CHANGELIST), fields, follow=True)
        assert resp.status_code == 200
        sub.refresh_from_db()
        assert sub.service_description == EDIT
        assert _messages(resp) == [f"1 submission(s) {WARNING}"]

    def test_one_malformed_entry_leaves_the_others_working(self, superuser_client):
        a, b = _published(), _published()
        _rejected_edit(a, service_description=EDIT)
        _rejected_edit(b, service_description=EDIT)
        fields = _confirmation_fields(_ask(superuser_client, a, b))
        versions = _versions(fields)
        fields[VERSION_FIELD] = [f"garbage {a.pk}:{versions[str(a.pk)]}"]
        resp = superuser_client.post(reverse(CHANGELIST), fields, follow=True)
        a.refresh_from_db()
        b.refresh_from_db()
        assert a.service_description != EDIT
        assert b.service_description == EDIT
        assert f"1 submission(s) {WARNING}" in _messages(resp)


class TestSubmittingTheConfirmationTwice:
    def test_the_second_post_restores_nothing_and_changes_nothing(
        self, superuser_client
    ):
        sub = _published()
        _rejected_edit(sub, service_description=EDIT)
        page = _ask(superuser_client, sub)
        _confirm(superuser_client, page)
        after_first = ServiceSubmission.objects.values().get(pk=sub.pk)
        logs = LogEntry.objects.count()
        changes = SubmissionChangeLog.objects.count()
        resp = _confirm(superuser_client, page)
        assert _messages(resp) == [f"1 submission(s) {WARNING}"]
        assert ServiceSubmission.objects.values().get(pk=sub.pk) == after_first
        assert LogEntry.objects.count() == logs
        assert SubmissionChangeLog.objects.count() == changes

    def test_a_row_without_a_version_whose_page_is_stale_is_reported_once(
        self, superuser_client
    ):
        sub = ServiceSubmissionFactory(biotools_url="")
        page = _ask(superuser_client, sub)
        set_status(ServiceSubmission.objects.get(pk=sub.pk), "rejected", actor="x")
        resp = _confirm(superuser_client, page)
        assert _messages(resp) == [f"1 submission(s) {WARNING}"]


class TestAdminLogOfARestore:
    def test_the_entry_names_the_service_the_action_and_the_user(
        self, superuser_client
    ):
        from django.contrib.admin.models import CHANGE
        from django.contrib.contenttypes.models import ContentType

        sub = _published()
        other = _published()
        _rejected_edit(sub, service_description=EDIT)
        _confirm(superuser_client, _ask(superuser_client, sub, other))
        (entry,) = LogEntry.objects.filter(change_message__startswith="Restored")
        assert entry.object_id == str(sub.pk)
        assert entry.action_flag == CHANGE
        assert entry.user.username == "testadmin"
        assert entry.change_message == "Restored published revision 1"
        assert entry.content_type == ContentType.objects.get_for_model(
            ServiceSubmission
        )
