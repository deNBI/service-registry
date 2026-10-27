"""Only an approval publishes a service; later writes that leave it approved
republish it; everything else leaves the public copy alone."""

import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework.test import APIClient

from apps.catalogue import publishing
from apps.catalogue.models import PublishedRevision, PublishedService
from apps.submissions.lifecycle import get_no_reset_fields, set_status
from apps.submissions.models import ServiceSubmission
from tests.factories import APIKeyFactory, ServiceSubmissionFactory
from tests.helpers import edit_form_data, edit_form_payload

pytestmark = pytest.mark.django_db

CHANGELIST = "admin:submissions_servicesubmission_changelist"
CHANGE = "admin:submissions_servicesubmission_change"
LONG_TEXT = "A changed description that is definitely long enough to be valid."


@pytest.fixture
def exempt(settings):
    settings.SUBMISSION_NO_RESET_FIELDS = [
        "github_url",
        "website_url",
        "logo",
        "comments",
    ]
    get_no_reset_fields.cache_clear()
    yield
    get_no_reset_fields.cache_clear()


@pytest.fixture
def exempt_topics(settings):
    """EDAM topics can be edited without a review, as in the shipped settings."""
    settings.SUBMISSION_NO_RESET_FIELDS = ["edam_topics"]
    get_no_reset_fields.cache_clear()
    yield
    get_no_reset_fields.cache_clear()


def _topic(accession="topic_9101"):
    from apps.edam.models import EdamTerm

    return EdamTerm.objects.create(
        uri=f"http://edamontology.org/{accession}",
        accession=accession,
        branch="topic",
        label="Example topic",
    )


def _published(**kw):
    sub = ServiceSubmissionFactory(
        biotools_url="", internal_contact_email="pi@example.com", **kw
    )
    set_status(sub, "approved", actor="admin:jane")
    return sub


def _ps(sub):
    return PublishedService.objects.get(pk=sub.pk)


def _grant(client, sub):
    key_obj, _ = APIKeyFactory.create_with_plaintext(submission=sub)
    session = client.session
    session["edit_grants"] = {str(sub.pk): str(key_obj.pk)}
    session.save()


def _api(sub):
    _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
    return client


def _png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


def _logo():
    return SimpleUploadedFile("logo.png", _png(), content_type="image/png")


class TestApprovalPublishes:
    def test_approval_creates_the_public_copy(self):
        sub = _published()
        ps = _ps(sub)
        assert ps.service_name == sub.service_name
        assert ps.state == "active" and ps.pending_update is False
        assert ps.revision.reason == "approval"
        assert ps.revision.actor == "admin:jane"

    def test_a_service_created_as_approved_is_not_published_until_approved(self):
        sub = ServiceSubmissionFactory(status="approved", biotools_url="")
        assert not PublishedService.objects.filter(pk=sub.pk).exists()

    @pytest.mark.parametrize(
        "status", ["under_review", "rejected", "deprecated", "draft"]
    )
    def test_no_other_status_publishes(self, status):
        sub = ServiceSubmissionFactory(biotools_url="")
        set_status(sub, status, actor="admin:jane")
        assert not PublishedService.objects.filter(pk=sub.pk).exists()
        assert not PublishedRevision.objects.filter(submission=sub).exists()

    def test_unchanged_status_does_not_touch_the_public_copy(self):
        sub = _published()
        before = _ps(sub).public_modified_at
        assert set_status(sub, "approved", actor="admin:jane") is False
        assert _ps(sub).public_modified_at == before
        assert PublishedRevision.objects.filter(submission=sub).count() == 1

    def test_a_failed_publish_rolls_the_status_back(self, monkeypatch):
        sub = ServiceSubmissionFactory(biotools_url="")

        def boom(*args, **kwargs):
            raise RuntimeError("publish failed")

        monkeypatch.setattr(publishing, "publish", boom)
        with pytest.raises(RuntimeError):
            set_status(sub, "approved", actor="admin:jane")
        assert ServiceSubmission.objects.get(pk=sub.pk).status == "submitted"
        assert sub.status_events.count() == 1

    @pytest.mark.parametrize(
        "action",
        ["action_approve"],
    )
    def test_admin_bulk_approval_publishes_every_selected_service(
        self, superuser_client, action
    ):
        subs = [ServiceSubmissionFactory(biotools_url="") for _ in range(3)]
        superuser_client.post(
            reverse(CHANGELIST),
            {"action": action, "_selected_action": [str(s.pk) for s in subs]},
        )
        assert PublishedService.objects.filter(pk__in=[s.pk for s in subs]).count() == 3
        assert {r.actor for r in PublishedRevision.objects.all()} == {"admin:testadmin"}

    def test_bulk_approval_queries_grow_in_proportion_to_the_selection(
        self, superuser_client
    ):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        def queries_for(count):
            subs = [ServiceSubmissionFactory(biotools_url="") for _ in range(count)]
            with CaptureQueriesContext(connection) as ctx:
                superuser_client.post(
                    reverse(CHANGELIST),
                    {
                        "action": "action_approve",
                        "_selected_action": [str(s.pk) for s in subs],
                    },
                )
            return len(ctx)

        small, large = queries_for(2), queries_for(8)
        # Four times the services must cost at most four times the queries.
        assert large <= small * 4

    def test_admin_change_form_approval_publishes(self, superuser_client):
        sub = ServiceSubmissionFactory(biotools_url="")
        superuser_client.post(
            reverse(CHANGE, args=[sub.pk]), edit_form_payload(sub, _approve="1")
        )
        assert PublishedService.objects.filter(pk=sub.pk).exists()

    def test_admin_change_form_approval_publishes_edits_saved_in_the_same_post(
        self, superuser_client
    ):
        sub = ServiceSubmissionFactory(biotools_url="")
        superuser_client.post(
            reverse(CHANGE, args=[sub.pk]),
            edit_form_payload(
                sub, _approve="1", service_name="Example Renamed Service"
            ),
        )
        assert _ps(sub).service_name == "Example Renamed Service"
        assert PublishedRevision.objects.filter(submission=sub).count() == 1


class TestWebEdit:
    def test_live_edit_republishes(self, client, exempt):
        sub = _published()
        _grant(client, sub)
        data = edit_form_data(sub, github_url="https://github.com/example/new-repo")
        client.post(reverse("submissions:edit", args=[sub.pk]), data=data)
        ps = _ps(sub)
        assert ps.github_url == "https://github.com/example/new-repo"
        assert ps.pending_update is False
        assert ps.revision.reason == "live_edit"
        assert ps.revision.actor == "submitter"

    def test_live_edit_of_a_relation_republishes_the_saved_relation(
        self, client, exempt_topics, settings
    ):
        """The copy is built after the relation rows are stored."""
        settings.ALTCHA_HMAC_KEY = ""
        sub = _published()
        topic = _topic()
        _grant(client, sub)
        data = edit_form_data(sub)
        data["edam_topics"] = [topic.pk]
        resp = client.post(reverse("submissions:edit", args=[sub.pk]), data=data)
        assert resp.status_code == 302
        assert ServiceSubmission.objects.get(pk=sub.pk).status == "approved"
        ps = _ps(sub)
        assert list(ps.edam_topics.values_list("pk", flat=True)) == [topic.pk]
        assert ps.revision.reason == "live_edit"
        assert ps.revision.content["edam_topics"] == [topic.pk]
        assert ps.pending_update is False

    def test_live_edit_of_a_private_field_does_not_create_a_revision(
        self, client, exempt
    ):
        sub = _published()
        before = _ps(sub).public_modified_at
        _grant(client, sub)
        data = edit_form_data(sub, comments="A private remark")
        client.post(reverse("submissions:edit", args=[sub.pk]), data=data)
        assert ServiceSubmission.objects.get(pk=sub.pk).comments == "A private remark"
        assert PublishedRevision.objects.filter(submission=sub).count() == 1
        assert _ps(sub).public_modified_at == before

    def test_reviewed_edit_keeps_the_last_version_public(self, client, exempt):
        sub = _published()
        old = sub.service_description
        _grant(client, sub)
        data = edit_form_data(sub, service_description=LONG_TEXT)
        client.post(reverse("submissions:edit", args=[sub.pk]), data=data)
        ps = _ps(sub)
        assert ps.service_description == old
        assert ps.pending_update is True
        assert ServiceSubmission.objects.get(pk=sub.pk).service_description == LONG_TEXT
        assert PublishedRevision.objects.filter(submission=sub).count() == 1

    def test_mixed_edit_keeps_every_field_waiting_for_review(self, client, exempt):
        sub = _published()
        old_github = sub.github_url
        _grant(client, sub)
        data = edit_form_data(
            sub,
            service_description=LONG_TEXT,
            github_url="https://github.com/example/new-repo",
        )
        client.post(reverse("submissions:edit", args=[sub.pk]), data=data)
        ps = _ps(sub)
        assert ps.github_url == old_github
        assert ps.pending_update is True

    def test_approving_the_reviewed_edit_publishes_it(
        self, client, exempt, superuser_client
    ):
        sub = _published()
        _grant(client, sub)
        data = edit_form_data(sub, service_description=LONG_TEXT)
        client.post(reverse("submissions:edit", args=[sub.pk]), data=data)
        superuser_client.post(
            reverse(CHANGELIST),
            {"action": "action_approve", "_selected_action": [str(sub.pk)]},
        )
        ps = _ps(sub)
        assert ps.service_description == LONG_TEXT
        assert ps.pending_update is False
        assert PublishedRevision.objects.filter(submission=sub).count() == 2

    def test_edit_of_an_unpublished_service_publishes_nothing(self, client, exempt):
        sub = ServiceSubmissionFactory(biotools_url="")
        _grant(client, sub)
        data = edit_form_data(sub, github_url="https://github.com/example/new-repo")
        client.post(reverse("submissions:edit", args=[sub.pk]), data=data)
        assert not PublishedService.objects.filter(pk=sub.pk).exists()

    def test_edit_while_under_review_is_not_published(self, client, exempt):
        sub = _published()
        set_status(sub, "under_review", actor="admin:jane")
        _grant(client, sub)
        data = edit_form_data(sub, github_url="https://github.com/example/new-repo")
        client.post(reverse("submissions:edit", args=[sub.pk]), data=data)
        assert _ps(sub).github_url != "https://github.com/example/new-repo"
        assert _ps(sub).pending_update is True

    def test_logo_upload_is_a_live_edit(self, client, exempt, settings, tmp_path):
        settings.MEDIA_ROOT = tmp_path
        settings.ALTCHA_HMAC_KEY = ""
        sub = _published()
        _grant(client, sub)
        data = edit_form_data(sub, logo=_logo())
        resp = client.post(reverse("submissions:edit", args=[sub.pk]), data=data)
        assert resp.status_code == 302
        stored = ServiceSubmission.objects.get(pk=sub.pk).logo.name
        assert stored.startswith("logos/")
        assert _ps(sub).logo.name == stored

    def test_self_deprecate_marks_the_public_copy_deprecated(self, client):
        sub = _published(primary_maturity_tag="mature")
        _grant(client, sub)
        client.post(reverse("submissions:edit", args=[sub.pk]), {"_deprecate": "1"})
        ps = _ps(sub)
        assert (ps.state, ps.pending_update) == ("deprecated", False)
        assert PublishedRevision.objects.filter(submission=sub).count() == 1

    def test_self_deprecate_of_an_unpublished_service_stays_private(self, client):
        sub = ServiceSubmissionFactory(biotools_url="")
        _grant(client, sub)
        client.post(reverse("submissions:edit", args=[sub.pk]), {"_deprecate": "1"})
        assert not PublishedService.objects.filter(pk=sub.pk).exists()


class TestApiPatch:
    def test_live_edit_republishes(self, exempt):
        sub = _published()
        resp = _api(sub).patch(
            f"/api/v1/submissions/{sub.pk}/",
            {"website_url": "https://example.org/moved"},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        ps = _ps(sub)
        assert ps.website_url == "https://example.org/moved"
        assert ps.revision.reason == "live_edit"
        assert ps.revision.actor == "api:Test key"

    def test_live_edit_of_a_relation_republishes_the_saved_relation(
        self, exempt_topics
    ):
        sub = _published()
        topic = _topic()
        resp = _api(sub).patch(
            f"/api/v1/submissions/{sub.pk}/",
            {"edam_topic_ids": [topic.pk]},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        assert ServiceSubmission.objects.get(pk=sub.pk).status == "approved"
        ps = _ps(sub)
        assert list(ps.edam_topics.values_list("pk", flat=True)) == [topic.pk]
        assert ps.revision.content["edam_topics"] == [topic.pk]
        assert ps.revision.reason == "live_edit"

    def test_reviewed_edit_keeps_the_last_version_public(self, exempt):
        sub = _published()
        old = sub.service_description
        resp = _api(sub).patch(
            f"/api/v1/submissions/{sub.pk}/",
            {"service_description": LONG_TEXT},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        ps = _ps(sub)
        assert ps.service_description == old
        assert ps.pending_update is True

    def test_patch_without_a_real_change_does_not_create_a_revision(self, exempt):
        sub = _published()
        _api(sub).patch(
            f"/api/v1/submissions/{sub.pk}/",
            {"website_url": sub.website_url},
            format="json",
        )
        assert PublishedRevision.objects.filter(submission=sub).count() == 1

    def test_logo_upload_through_the_api_is_a_live_edit(
        self, exempt, settings, tmp_path
    ):
        settings.MEDIA_ROOT = tmp_path
        sub = _published()
        resp = _api(sub).patch(
            f"/api/v1/submissions/{sub.pk}/", {"logo": _logo()}, format="multipart"
        )
        assert resp.status_code == 200, resp.content
        stored = ServiceSubmission.objects.get(pk=sub.pk).logo.name
        assert stored and _ps(sub).logo.name == stored

    def test_edit_of_an_unpublished_service_publishes_nothing(self, exempt):
        sub = ServiceSubmissionFactory(biotools_url="")
        _api(sub).patch(
            f"/api/v1/submissions/{sub.pk}/",
            {"website_url": "https://example.org/moved"},
            format="json",
        )
        assert not PublishedService.objects.filter(pk=sub.pk).exists()


class TestApiPatchWithAnAdminKey:
    """The admin key edits any service through the same endpoint."""

    @staticmethod
    def _client():
        import hashlib
        import secrets

        from apps.api.models import AdminAPIKey

        plaintext = secrets.token_urlsafe(48)
        AdminAPIKey.objects.create(
            key_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
            label="Example admin key",
            scope="full",
            is_active=True,
        )
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION=f"AdminKey {plaintext}")
        return client

    def test_live_edit_republishes(self, exempt):
        sub = _published()
        resp = self._client().patch(
            f"/api/v1/submissions/{sub.pk}/",
            {"website_url": "https://example.org/moved"},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        ps = _ps(sub)
        assert ps.website_url == "https://example.org/moved"
        assert ps.revision.actor == "api:Example admin key"

    def test_reviewed_edit_keeps_the_last_version_public(self, exempt):
        sub = _published()
        old = sub.service_description
        resp = self._client().patch(
            f"/api/v1/submissions/{sub.pk}/",
            {"service_description": LONG_TEXT},
            format="json",
        )
        assert resp.status_code == 200, resp.content
        ps = _ps(sub)
        assert (ps.service_description, ps.pending_update) == (old, True)
        event = sub.status_events.order_by("id").last()
        assert (event.to_status, event.changed_by) == (
            "submitted",
            "api:Example admin key",
        )


class TestAdminEdit:
    def test_admin_edit_of_an_approved_service_goes_live(self, superuser_client):
        sub = _published()
        resp = superuser_client.post(
            reverse(CHANGE, args=[sub.pk]),
            edit_form_payload(sub, service_name="Example Renamed Service"),
        )
        assert resp.status_code == 302, resp.content[:2000]
        ps = _ps(sub)
        assert ps.service_name == "Example Renamed Service"
        assert ps.revision.reason == "admin_edit"
        assert ps.revision.actor == "admin:testadmin"

    def test_admin_edit_of_relations_goes_live(self, superuser_client):
        from tests.factories import ServiceCategoryFactory

        sub = _published()
        extra = ServiceCategoryFactory()
        categories = [c.pk for c in sub.service_categories.all()] + [extra.pk]
        superuser_client.post(
            reverse(CHANGE, args=[sub.pk]),
            edit_form_payload(sub, service_categories=categories),
        )
        assert extra in _ps(sub).service_categories.all()

    def test_admin_edit_of_a_private_field_creates_no_revision(self, superuser_client):
        sub = _published()
        superuser_client.post(
            reverse(CHANGE, args=[sub.pk]),
            edit_form_payload(sub, comments="A private remark"),
        )
        assert PublishedRevision.objects.filter(submission=sub).count() == 1

    def test_admin_edit_while_not_approved_is_not_published(self, superuser_client):
        sub = _published()
        set_status(sub, "submitted", actor="submitter")
        sub.refresh_from_db()
        superuser_client.post(
            reverse(CHANGE, args=[sub.pk]),
            edit_form_payload(sub, service_name="Example Renamed Service"),
        )
        ps = _ps(sub)
        assert ps.service_name != "Example Renamed Service"
        assert ps.pending_update is True

    def test_admin_edit_of_a_deprecated_service_is_not_published(
        self, superuser_client
    ):
        sub = _published()
        set_status(sub, "deprecated", actor="admin:jane")
        sub.refresh_from_db()
        superuser_client.post(
            reverse(CHANGE, args=[sub.pk]),
            edit_form_payload(sub, service_name="Example Renamed Service"),
        )
        assert _ps(sub).service_name != "Example Renamed Service"

    def test_admin_edit_of_an_unpublished_service_publishes_nothing(
        self, superuser_client
    ):
        sub = ServiceSubmissionFactory(biotools_url="")
        superuser_client.post(
            reverse(CHANGE, args=[sub.pk]),
            edit_form_payload(sub, service_name="Example Renamed Service"),
        )
        assert not PublishedService.objects.filter(pk=sub.pk).exists()

    def test_admin_add_publishes_nothing(self, superuser_client):
        template = ServiceSubmissionFactory(biotools_url="")
        payload = edit_form_payload(template, service_name="Example Admin Added")
        superuser_client.post(
            reverse("admin:submissions_servicesubmission_add"), data=payload
        )
        assert PublishedService.objects.count() == 0

    def test_admin_logo_upload_goes_live(self, superuser_client, settings, tmp_path):
        settings.MEDIA_ROOT = tmp_path
        sub = _published()
        resp = superuser_client.post(
            reverse(CHANGE, args=[sub.pk]), edit_form_payload(sub, logo=_logo())
        )
        assert resp.status_code == 302, resp.content[:2000]
        stored = ServiceSubmission.objects.get(pk=sub.pk).logo.name
        assert stored and _ps(sub).logo.name == stored

    def test_admin_logo_removal_goes_live(self, superuser_client, settings, tmp_path):
        from django.core.files.base import ContentFile

        settings.MEDIA_ROOT = tmp_path
        sub = ServiceSubmissionFactory(biotools_url="")
        sub.logo.save("x.png", ContentFile(_png()))
        set_status(sub, "approved", actor="admin:jane")
        assert _ps(sub).logo.name
        resp = superuser_client.post(
            reverse(CHANGE, args=[sub.pk]),
            edit_form_payload(sub, **{"logo-clear": "on"}),
        )
        assert resp.status_code == 302, resp.content[:2000]
        assert not _ps(sub).logo

    @pytest.mark.parametrize(
        "button,expected",
        [
            ("_under_review", ("active", True)),
            ("_reject", ("active", False)),
            ("_deprecate", ("deprecated", False)),
        ],
    )
    def test_status_buttons_change_state_and_flag(
        self, superuser_client, button, expected
    ):
        sub = _published()
        superuser_client.post(
            reverse(CHANGE, args=[sub.pk]), edit_form_payload(sub, **{button: "1"})
        )
        ps = _ps(sub)
        assert (ps.state, ps.pending_update) == expected
        assert PublishedRevision.objects.filter(submission=sub).count() == 1

    def test_undeprecate_keeps_the_public_copy_deprecated_until_approved(
        self, superuser_client
    ):
        sub = _published()
        action = lambda name: superuser_client.post(  # noqa: E731
            reverse(CHANGELIST),
            {"action": name, "_selected_action": [str(sub.pk)]},
        )
        action("action_deprecate")
        action("action_undeprecate")
        ps = _ps(sub)
        assert (ps.state, ps.pending_update) == ("deprecated", True)
        action("action_approve")
        ps = _ps(sub)
        assert (ps.state, ps.pending_update) == ("active", False)


class TestMaturityTags:
    def _assign(self, client, subs, primary):
        return client.post(
            reverse(CHANGELIST),
            {
                "action": "action_assign_maturity_tags",
                "_selected_action": [str(s.pk) for s in subs],
                "_assign_tags": "1",
                "primary_maturity_tag": primary,
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

    def test_tag_action_republishes(self, superuser_client):
        sub = _published()
        resp = self._assign(superuser_client, [sub], "mature")
        assert resp.json()["status"] == "success"
        ps = _ps(sub)
        assert ps.primary_maturity_tag == "mature"
        assert ps.revision.reason == "tag_change"
        assert ps.revision.actor == "admin:testadmin"

    def test_same_tag_again_creates_no_revision(self, superuser_client):
        sub = _published(primary_maturity_tag="mature")
        # The factory wrote the tag before publishing, so it is already public.
        PublishedService.objects.filter(pk=sub.pk).update(primary_maturity_tag="mature")
        self._assign(superuser_client, [sub], "mature")
        assert PublishedRevision.objects.filter(submission=sub).count() == 1

    def test_tag_action_skips_services_that_are_not_approved(self, superuser_client):
        approved = _published()
        waiting = _published()
        set_status(waiting, "submitted", actor="submitter")
        self._assign(superuser_client, [approved, waiting], "emerging")
        assert _ps(approved).primary_maturity_tag == "emerging"
        assert _ps(waiting).primary_maturity_tag in (None, "")

    def test_deprecation_clears_the_working_copy_tag_and_keeps_the_published_one(
        self, superuser_client
    ):
        sub = _published()
        self._assign(superuser_client, [sub], "mature")
        superuser_client.post(
            reverse(CHANGELIST),
            {"action": "action_deprecate", "_selected_action": [str(sub.pk)]},
        )
        ps = _ps(sub)
        assert ps.state == "deprecated"
        # The tag is cleared in the working copy; the published copy keeps the
        # last approved version unchanged (a deprecated page hides the tag).
        assert ServiceSubmission.objects.get(pk=sub.pk).primary_maturity_tag is None
        assert ps.primary_maturity_tag == "mature"
