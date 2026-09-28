"""PATCH on an approved submission resets it to "submitted" only when a
non-exempt field ACTUALLY changes, exactly like the web edit form.

Sending a field with the value it already has (a GET -> modify -> PATCH
round-trip, or the same text with other whitespace, Unicode form or line
endings) is not a change: status and maturity tags stay, nothing is logged
and the notification reports no reset.
"""

from unittest import mock

import pytest
from rest_framework.test import APIClient

from apps.submissions.lifecycle import get_no_reset_fields
from apps.submissions.models import ServiceSubmission, SubmissionChangeLog
from tests.factories import (
    APIKeyFactory,
    ServiceCategoryFactory,
    ServiceSubmissionFactory,
)

pytestmark = pytest.mark.django_db

DESC = "Stable description for an approved service.\nSecond line of text."


@pytest.fixture
def exempt(settings):
    """Set the no_reset_fields list for one test (cached, so clear around)."""

    def _set(fields):
        settings.SUBMISSION_NO_RESET_FIELDS = fields
        get_no_reset_fields.cache_clear()

    yield _set
    get_no_reset_fields.cache_clear()


@pytest.fixture
def notify():
    with mock.patch("apps.api.views.send_update_notification") as task:
        yield task.delay


def _approved(**extra):
    sub = ServiceSubmissionFactory(
        status="approved",
        biotools_url="",
        service_description=DESC,
        primary_maturity_tag="mature",
        secondary_maturity_tags=["unstable"],
        **extra,
    )
    return sub


def _patch(sub, body):
    _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
    resp = client.patch(f"/api/v1/submissions/{sub.pk}/", body, format="json")
    assert resp.status_code == 200, resp.content
    sub.refresh_from_db()
    return resp


def _logged_fields(sub):
    return [
        ch["field"]
        for log in SubmissionChangeLog.objects.filter(submission=sub)
        for ch in log.changes
    ]


def _assert_untouched(sub, notify):
    assert sub.status == "approved"
    assert sub.primary_maturity_tag == "mature"
    assert sub.secondary_maturity_tags == ["unstable"]
    assert sub.last_change_summary is None
    assert _logged_fields(sub) == []
    assert notify.call_args.kwargs == {"changes": [], "status_reset": False}


@pytest.mark.parametrize(
    "no_reset", [["github_url"], []], ids=["exempt_list", "no_list"]
)
@pytest.mark.parametrize(
    "sent",
    [
        DESC,
        "  " + DESC + "\n",  # surrounding whitespace is stripped on save
        DESC.replace("\n", "\r\n"),  # browser/Windows line endings
    ],
    ids=["identical", "padded", "crlf"],
)
def test_resending_the_stored_description_keeps_approval(
    exempt, notify, no_reset, sent
):
    exempt(no_reset)
    sub = _approved()
    _patch(sub, {"service_description": sent})
    _assert_untouched(sub, notify)


def test_resending_decomposed_unicode_keeps_approval(exempt, notify):
    exempt(["github_url"])
    sub = _approved(host_institute="Universität Zürich")
    _patch(sub, {"host_institute": "Universität Zürich"})  # NFD form
    _assert_untouched(sub, notify)


def test_resending_the_same_categories_keeps_approval(exempt, notify):
    exempt(["github_url"])
    sub = _approved()
    cat = ServiceCategoryFactory()
    sub.service_categories.set([cat])
    _patch(sub, {"service_category_ids": [cat.pk]})
    _assert_untouched(sub, notify)


def test_round_trip_with_one_exempt_change_keeps_approval(exempt, notify):
    """GET -> change an exempt field -> PATCH the echoed body back."""
    exempt(["github_url"])
    sub = _approved(service_name="Round Trip Tool")
    _patch(
        sub,
        {
            "service_name": "Round Trip Tool",
            "service_description": DESC,
            "host_institute": sub.host_institute,
            "github_url": "https://github.com/example/new-repo",
        },
    )
    assert sub.status == "approved"
    assert sub.primary_maturity_tag == "mature"
    assert _logged_fields(sub) == ["github_url"]
    assert notify.call_args.kwargs["status_reset"] is False


@pytest.mark.parametrize(
    "no_reset", [["github_url"], []], ids=["exempt_list", "no_list"]
)
def test_real_description_change_resets_and_is_logged(exempt, notify, no_reset):
    exempt(no_reset)
    sub = _approved()
    new = "A genuinely different description that is long enough to be valid."
    _patch(sub, {"service_description": new})
    assert sub.status == "submitted"
    assert sub.primary_maturity_tag is None
    assert sub.secondary_maturity_tags == []
    assert sub.service_description == new
    logged = _logged_fields(sub)
    # Same entries as before the change: the content field plus the reset.
    assert {"service_description", "status", "primary_maturity_tag"} <= set(logged)
    assert {c["field"] for c in sub.last_change_summary["changes"]} == set(logged)
    assert notify.call_args.kwargs["status_reset"] is True


def test_exempt_only_change_without_list_resets(exempt, notify):
    """No no_reset_fields configured: any real change resets (web form rule)."""
    exempt([])
    sub = _approved()
    _patch(sub, {"github_url": "https://github.com/example/other"})
    assert sub.status == "submitted"
    assert notify.call_args.kwargs["status_reset"] is True


def test_not_approved_submission_is_never_reset(exempt, notify):
    exempt([])
    sub = ServiceSubmissionFactory(status="submitted", biotools_url="")
    _patch(
        sub,
        {
            "service_description": "Another valid description that is long enough to pass."
        },
    )
    assert sub.status == "submitted"
    assert notify.call_args.kwargs["status_reset"] is False


# ---------------------------------------------------------------------------
# Remaining paths, with the REAL no_reset_fields from config/site.toml (no
# override): M2M, admin keys, empty bodies, ignored name changes, logos.
# ---------------------------------------------------------------------------


@pytest.fixture
def site_defaults():
    """Use the shipped no_reset_fields (settings from site.toml)."""
    get_no_reset_fields.cache_clear()
    exempt = get_no_reset_fields()
    assert {"github_url", "edam_topics", "logo", "comments"} <= exempt
    assert not {"service_description", "service_categories"} & exempt
    yield exempt
    get_no_reset_fields.cache_clear()


def _admin_patch(sub, body, **kw):
    import hashlib
    import secrets

    from apps.api.models import AdminAPIKey

    plaintext = secrets.token_urlsafe(48)
    AdminAPIKey.objects.create(
        label="Ops key",
        key_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
        scope="full",
        is_active=True,
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"AdminKey {plaintext}")
    resp = client.patch(f"/api/v1/submissions/{sub.pk}/", body, **kw)
    assert resp.status_code == 200, resp.content
    sub.refresh_from_db()
    return resp


def _edam_topic(n):
    from apps.edam.models import EdamTerm

    return EdamTerm.objects.create(
        uri=f"http://edamontology.org/topic_{n:04d}",
        label=f"Topic {n}",
        branch="topic",
        accession=f"topic_{n:04d}",
    )


@pytest.mark.parametrize("field", ["service_category_ids", "responsible_pi_ids"])
def test_real_m2m_change_resets(site_defaults, notify, field):
    from tests.factories import PIFactory

    sub = _approved()
    new = ServiceCategoryFactory() if field == "service_category_ids" else PIFactory()
    _patch(sub, {field: [new.pk]})
    assert sub.status == "submitted"
    assert notify.call_args.kwargs["status_reset"] is True


def test_exempt_m2m_change_keeps_approval_and_is_logged(site_defaults, notify):
    sub = _approved()
    topic = _edam_topic(3000)
    _patch(sub, {"edam_topic_ids": [topic.pk]})
    assert sub.status == "approved"
    assert sub.primary_maturity_tag == "mature"
    assert _logged_fields(sub) == ["edam_topics"]
    assert notify.call_args.kwargs["status_reset"] is False


def test_resending_the_same_edam_topics_logs_nothing(site_defaults, notify):
    sub = _approved()
    topic = _edam_topic(3001)
    sub.edam_topics.set([topic])
    _patch(sub, {"edam_topic_ids": [topic.pk]})
    _assert_untouched(sub, notify)


@pytest.mark.parametrize("no_reset", [None, []], ids=["site_defaults", "no_list"])
def test_empty_body_is_not_a_change(exempt, notify, no_reset):
    """Previously an empty PATCH reset an approved service when no exempt list
    was configured."""
    if no_reset is not None:
        exempt(no_reset)
    else:
        get_no_reset_fields.cache_clear()
    sub = _approved()
    _patch(sub, {})
    _assert_untouched(sub, notify)


def test_ignored_service_name_change_is_not_a_change(site_defaults, notify):
    sub = _approved(service_name="Locked Name Tool")
    resp = _patch(sub, {"service_name": "Another Name"})
    assert "warnings" in resp.json()
    assert sub.service_name == "Locked Name Tool"
    _assert_untouched(sub, notify)


def test_admin_key_follows_the_same_rule(site_defaults, notify):
    sub = _approved()
    _admin_patch(sub, {"service_description": DESC})  # identical
    _assert_untouched(sub, notify)
    _admin_patch(
        sub,
        {
            "service_description": "An admin-edited description, long enough to be valid."
        },
    )
    assert sub.status == "submitted"
    assert sub.last_change_summary["changed_by"] == "api:Ops key"
    assert notify.call_args.kwargs["status_reset"] is True


def test_real_change_with_site_defaults_resets(site_defaults, notify):
    sub = _approved()
    _patch(sub, {"host_institute": "A Different Institute"})
    assert sub.status == "submitted"
    assert set(_logged_fields(sub)) >= {"host_institute", "status"}


@pytest.mark.parametrize("no_reset", [None, []], ids=["site_defaults", "no_list"])
def test_logo_upload_follows_the_exempt_list(
    exempt, notify, tmp_path, settings, no_reset
):
    """logo is exempt by default: a new upload is logged but keeps approval;
    without an exempt list it resets like any other real change."""
    from django.core.files.uploadedfile import SimpleUploadedFile

    from tests.test_api import _make_png_bytes

    settings.MEDIA_ROOT = tmp_path
    if no_reset is not None:
        exempt(no_reset)
    else:
        get_no_reset_fields.cache_clear()
    sub = _approved()
    logo = SimpleUploadedFile("logo.png", _make_png_bytes(), content_type="image/png")
    _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
    resp = client.patch(
        f"/api/v1/submissions/{sub.pk}/", {"logo": logo}, format="multipart"
    )
    assert resp.status_code == 200, resp.content
    sub.refresh_from_db()
    assert sub.logo
    assert "logo" in _logged_fields(sub)
    assert sub.status == ("approved" if no_reset is None else "submitted")


def test_no_change_patch_emails_admin_only_without_a_diff(exempt, settings):
    """Documented contract: no submitter email and no diff table when nothing
    actually changed (the admin still gets the report, as for a web save)."""
    from django.core import mail

    exempt(["github_url"])
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True
    settings.SUBMISSION_NOTIFY_OVERRIDE = ""
    sub = _approved(internal_contact_email="owner@example.com")
    _patch(sub, {"service_description": DESC})
    assert sub.status == "approved"
    assert len(mail.outbox) == 1
    msg = mail.outbox[0]
    assert "owner@example.com" not in msg.to
    assert "WHAT CHANGED" not in msg.body


def test_failed_reset_rolls_back_the_content_change(exempt, notify):
    """Content and reset are one transaction: an approved service can never
    keep changed content without being sent back for review."""
    exempt(["github_url"])
    sub = _approved()
    original_save = ServiceSubmission.save

    def failing_reset(self, *args, **kwargs):
        if "status" in (kwargs.get("update_fields") or ()):
            raise RuntimeError("database went away")
        return original_save(self, *args, **kwargs)

    _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
    with mock.patch.object(ServiceSubmission, "save", failing_reset):
        with pytest.raises(RuntimeError):
            client.patch(
                f"/api/v1/submissions/{sub.pk}/",
                {
                    "service_description": "Changed text that would need a re-review by the office."
                },
                format="json",
            )
    sub.refresh_from_db()
    assert sub.service_description == DESC
    assert sub.status == "approved"
    notify.assert_not_called()
