"""Entity tags on the submission detail endpoint and conditional PATCH.

GET and PATCH responses carry a strong ETag of the returned representation.
A PATCH with If-Match is applied only if the tag still matches the current
representation (otherwise 412 and nothing is stored); without If-Match the
PATCH works as before.
"""

import re

import pytest
from django.core import mail
from rest_framework.test import APIClient

from apps.submissions.models import ServiceSubmission, SubmissionChangeLog
from tests.factories import APIKeyFactory, ServiceSubmissionFactory

pytestmark = pytest.mark.django_db

DESCRIPTION = "A new description that is long enough for the fifty character rule."
STRONG_TAG = re.compile(r'^"[0-9a-f]{32}"$')


@pytest.fixture
def sub():
    return ServiceSubmissionFactory(status="approved", biotools_url="")


@pytest.fixture
def api(sub):
    _, key = APIKeyFactory.create_with_plaintext(submission=sub, scope="write")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"ApiKey {key}")
    return client


def _url(sub):
    return f"/api/v1/submissions/{sub.pk}/"


def _etag(api, sub):
    response = api.get(_url(sub))
    assert response.status_code == 200
    return response["ETag"]


def _patch(api, sub, if_match=None, **data):
    headers = {} if if_match is None else {"HTTP_IF_MATCH": if_match}
    return api.patch(
        _url(sub), data or {"comments": "Checked by Jane"}, format="json", **headers
    )


def _change_meanwhile(sub):
    other = ServiceSubmission.objects.get(pk=sub.pk)
    other.comments = "Changed by John"
    other.save()


class TestEntityTag:
    def test_get_returns_a_strong_tag(self, api, sub):
        assert STRONG_TAG.match(_etag(api, sub))

    def test_tag_is_stable_while_nothing_changes(self, api, sub):
        assert _etag(api, sub) == _etag(api, sub)

    def test_tag_changes_when_the_service_changes(self, api, sub):
        before = _etag(api, sub)
        _change_meanwhile(sub)
        assert _etag(api, sub) != before

    def test_patch_response_carries_the_tag_of_the_stored_state(self, api, sub):
        response = _patch(api, sub, service_description=DESCRIPTION)
        assert response.status_code == 200
        assert response["ETag"] == _etag(api, sub)

    def test_patch_with_a_name_warning_tags_the_representation_only(self, api, sub):
        response = _patch(api, sub, service_name="Another name")
        assert response.status_code == 200 and "warnings" in response.json()
        assert response["ETag"] == _etag(api, sub)


class TestIfMatch:
    def test_current_tag_applies_the_change(self, api, sub):
        response = _patch(api, sub, if_match=_etag(api, sub))
        assert response.status_code == 200
        assert ServiceSubmission.objects.get(pk=sub.pk).comments == "Checked by Jane"

    def test_outdated_tag_is_refused_and_nothing_is_stored(self, api, sub):
        tag = _etag(api, sub)
        _change_meanwhile(sub)
        mail.outbox.clear()
        response = _patch(api, sub, if_match=tag, service_description=DESCRIPTION)
        assert response.status_code == 412
        body = response.json()
        assert body["error"]["detail"] and "request_id" in body
        stored = ServiceSubmission.objects.get(pk=sub.pk)
        assert (stored.comments, stored.status) == ("Changed by John", "approved")
        assert stored.service_description != DESCRIPTION
        assert not SubmissionChangeLog.objects.filter(submission=sub).exists()
        assert mail.outbox == []

    def test_star_matches_any_current_state(self, api, sub):
        _change_meanwhile(sub)
        assert _patch(api, sub, if_match="*").status_code == 200

    def test_a_list_that_contains_the_current_tag_matches(self, api, sub):
        header = f'"0123", {_etag(api, sub)}'
        assert _patch(api, sub, if_match=header).status_code == 200

    def test_a_weak_tag_never_matches(self, api, sub):
        assert _patch(api, sub, if_match="W/" + _etag(api, sub)).status_code == 412

    def test_an_empty_header_does_not_match(self, api, sub):
        assert _patch(api, sub, if_match="").status_code == 412

    def test_without_if_match_the_patch_works_as_before(self, api, sub):
        _change_meanwhile(sub)
        response = _patch(api, sub)
        assert response.status_code == 200
        assert ServiceSubmission.objects.get(pk=sub.pk).comments == "Checked by Jane"

    def test_invalid_body_with_an_outdated_tag_reports_the_precondition(self, api, sub):
        tag = _etag(api, sub)
        _change_meanwhile(sub)
        response = _patch(api, sub, if_match=tag, service_description="short")
        assert response.status_code == 412


def test_openapi_documents_etag_and_if_match(admin_user):
    from drf_spectacular.generators import SchemaGenerator

    schema = SchemaGenerator().get_schema(request=None, public=True)
    detail = schema["paths"]["/api/v1/submissions/{id}/"]
    patch_params = {p["name"] for p in detail["patch"].get("parameters", [])}
    assert "If-Match" in patch_params
    assert "412" in detail["patch"]["responses"]
    assert "ETag" in detail["get"]["responses"]["200"].get("headers", {})
    assert "ETag" in detail["patch"]["responses"]["200"].get("headers", {})


def test_patch_tag_matches_after_a_link_change_and_a_status_reset(api, sub):
    from tests.factories import ServiceCategoryFactory

    category = ServiceCategoryFactory()
    response = _patch(
        api, sub, service_categories=[category.pk], service_description=DESCRIPTION
    )
    assert response.status_code == 200, response.content
    assert ServiceSubmission.objects.get(pk=sub.pk).status == "submitted"
    assert response["ETag"] == _etag(api, sub)


class TestCrossOrigin:
    ORIGIN = "https://client.example.org"

    @pytest.fixture(autouse=True)
    def _allow_origin(self, settings):
        settings.CORS_ALLOWED_ORIGINS = [self.ORIGIN]

    def test_browsers_may_send_if_match(self, client, sub):
        response = client.options(
            _url(sub),
            HTTP_ORIGIN=self.ORIGIN,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="PATCH",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="if-match",
        )
        assert "if-match" in response["Access-Control-Allow-Headers"].lower()

    def test_browsers_may_read_the_etag(self, api, sub):
        response = api.get(_url(sub), HTTP_ORIGIN=self.ORIGIN)
        assert "etag" in response["Access-Control-Expose-Headers"].lower()
