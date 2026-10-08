"""
Logo uploads through every route that accepts one.

Each route is exercised with the same scenarios, so the rules in
apps/submissions/logo_utils.py are proven to apply everywhere a logo can
enter the system:

  register    POST /register/                    (public form, new submission)
  edit        POST /update/edit/<uuid>/          (public form, existing submission)
  api_create  POST /api/v1/submissions/          (REST API, multipart)
  api_patch   PATCH /api/v1/submissions/<pk>/    (REST API, multipart)
  admin       POST <admin>/.../change/           (Django admin changeform)

(The inline field-validation endpoint /register/validate/ does not receive
files at all; see tests/test_validate_field.py.)

Scenarios: a typical design-tool SVG is stored with its drawing intact; an SVG
with content outside the allowlist is stored without it; an oversized SVG, an
image over the pixel limit and a non-image are rejected with nothing stored;
and (update routes) saving without a new file keeps the stored logo as is.
"""

import io
from dataclasses import dataclass

import pytest
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.submissions.models import ServiceSubmission
from tests.factories import (
    APIKeyFactory,
    PIFactory,
    ServiceCategoryFactory,
    ServiceCenterFactory,
    ServiceSubmissionFactory,
)

# ---------------------------------------------------------------------------
# Test files
# ---------------------------------------------------------------------------

DESIGN_TOOL_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
    b"<defs><style>.a{fill:url(#g)}</style>"
    b'<linearGradient id="g"><stop offset="0" stop-color="#036"/></linearGradient>'
    b"</defs>"
    b'<rect class="a" width="10" height="10"/><text x="1" y="9">de.NBI</text></svg>'
)

DISALLOWED_CONTENT_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
    b'<foreignObject width="5" height="5"><div xmlns="http://www.w3.org/1999/xhtml">'
    b"x</div></foreignObject>"
    b'<a><set attributeName="opacity" to="0"/><text>keep</text></a>'
    b'<rect fill="url(https://example.org/x)" onclick="x()" width="1" height="1"/>'
    b"</svg>"
)


def _png(size: tuple[int, int]) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, (255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


def _upload(name: str, data: bytes, content_type: str) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, data, content_type=content_type)


# ---------------------------------------------------------------------------
# Route adapters
#
# Each adapter submits ``upload`` (or no file when None) through one route and
# returns (accepted, submission). ``submission`` is the record the route acted
# on, refreshed from the database (None if a create route stored nothing).
# ---------------------------------------------------------------------------


@dataclass
class Env:
    client: object
    admin_client: object
    settings: object


def _register_payload() -> dict:
    cat, center, pi = ServiceCategoryFactory(), ServiceCenterFactory(), PIFactory()
    return {
        "date_of_entry": timezone.now().date().isoformat(),
        "submitter_first_name": "Logo",
        "submitter_last_name": "Route",
        "submitter_affiliation": "Route Institute",
        "register_as_elixir": "False",
        "service_name": "Logo Route Service",
        "service_description": "A sufficiently long description of the logo route service.",
        "year_established": 2023,
        "service_categories": [cat.pk],
        "is_toolbox": "False",
        "toolbox_name": "",
        "user_knowledge_required": "",
        "publications_pmids": "12345678",
        "responsible_pis": [pi.pk],
        "associated_partner_note": "",
        "host_institute": "Route Institute",
        "service_center": center.pk,
        "public_contact_email": "route@example.com",
        "internal_contact_name": "Route Contact",
        "internal_contact_email": "route-int@example.com",
        "internal_contact_email_confirm": "route-int@example.com",
        "website_url": "https://route.example.com",
        "terms_of_use_url": "https://route.example.com/tos",
        "license_note": "MIT",
        "github_url": "",
        "biotools_url": "",
        "fairsharing_url": "",
        "other_registry_url": "",
        "kpi_monitoring": "yes",
        "kpi_start_year": "2023",
        "keywords_uncited": "",
        "keywords_seo": "",
        "survey_participation": "True",
        "comments": "",
        "data_protection_consent": "True",
    }


def _existing_submission() -> ServiceSubmission:
    return ServiceSubmissionFactory(biotools_url="", license_note="MIT")


def route_register(env, upload, sub=None):
    data = _register_payload()
    if upload is not None:
        data["logo"] = upload
    resp = env.client.post(reverse("submissions:register"), data=data)
    assert resp.status_code in (302, 422), resp.status_code
    created = ServiceSubmission.objects.filter(service_name="Logo Route Service")
    return resp.status_code == 302, created.first()


def route_edit(env, upload, sub=None):
    from tests.test_views import TestEditView as _EditViewTests

    helper = _EditViewTests()
    sub = sub or _existing_submission()
    helper._setup_edit_session(env.client, sub)
    extra = {"logo": upload} if upload is not None else {"comments": "edited"}
    data = helper._edit_form_data(sub, **extra)
    resp = env.client.post(reverse("submissions:edit", args=[sub.pk]), data=data)
    assert resp.status_code in (302, 422), resp.status_code
    sub.refresh_from_db()
    return resp.status_code == 302, sub


def route_api_create(env, upload, sub=None):
    from tests.test_api import _valid_payload

    data = _valid_payload()
    data["service_name"] = "Logo Route API Service"
    data = {k: v for k, v in data.items() if v != []}  # multipart: omit empty lists
    if upload is not None:
        data["logo"] = upload
    resp = APIClient().post("/api/v1/submissions/", data, format="multipart")
    assert resp.status_code in (201, 400), (resp.status_code, resp.content[:500])
    created = ServiceSubmission.objects.filter(service_name="Logo Route API Service")
    return resp.status_code == 201, created.first()


def route_api_patch(env, upload, sub=None):
    sub = sub or _existing_submission()
    _, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
    data = {"logo": upload} if upload is not None else {"comments": "edited"}
    resp = api.patch(f"/api/v1/submissions/{sub.id}/", data, format="multipart")
    assert resp.status_code in (200, 400), (resp.status_code, resp.content[:500])
    sub.refresh_from_db()
    return resp.status_code == 200, sub


def route_admin(env, upload, sub=None):
    from tests.test_admin import _admin_changeform_data

    sub = sub or _existing_submission()
    url = reverse("admin:submissions_servicesubmission_change", args=[sub.pk])
    data = _admin_changeform_data(env.admin_client, sub)
    if upload is not None:
        data["logo"] = upload
    else:
        data["comments"] = "edited"
    resp = env.admin_client.post(url, data)
    assert resp.status_code in (200, 302), resp.status_code
    sub.refresh_from_db()
    return resp.status_code == 302, sub


ALL_ROUTES = {
    "register": route_register,
    "edit": route_edit,
    "api_create": route_api_create,
    "api_patch": route_api_patch,
    "admin": route_admin,
}
UPDATE_ROUTES = {k: ALL_ROUTES[k] for k in ("edit", "api_patch", "admin")}


@pytest.fixture
def env(client, admin_client, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    settings.ALTCHA_HMAC_KEY = ""
    settings.CELERY_TASK_ALWAYS_EAGER = True
    return Env(client=client, admin_client=admin_client, settings=settings)


def _stored(settings, sub) -> bytes:
    assert sub is not None and sub.logo, "expected a stored logo"
    return (settings.MEDIA_ROOT / sub.logo.name).read_bytes()


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize("route", ALL_ROUTES.values(), ids=ALL_ROUTES.keys())
class TestEveryUploadRoute:
    def test_design_tool_svg_is_stored_with_drawing_intact(self, env, route):
        ok, sub = route(env, _upload("logo.svg", DESIGN_TOOL_SVG, "image/svg+xml"))
        assert ok
        stored = _stored(env.settings, sub)
        assert sub.logo.name.endswith(".svg")
        for kept in (b"<linearGradient", b"url(#g)", b"de.NBI", b'class="a"'):
            assert kept in stored

    def test_stored_logo_is_served_with_the_media_policy(self, env, route):
        ok, sub = route(env, _upload("logo.svg", DESIGN_TOOL_SVG, "image/svg+xml"))
        assert ok
        resp = env.client.get(sub.logo.url)
        assert resp.status_code == 200
        assert resp["Content-Type"] == "image/svg+xml"
        policy = resp["Content-Security-Policy"]
        assert "sandbox" in policy and "default-src 'none'" in policy

    def test_disallowed_svg_content_is_not_stored(self, env, route):
        ok, sub = route(
            env, _upload("logo.svg", DISALLOWED_CONTENT_SVG, "image/svg+xml")
        )
        assert ok
        stored = _stored(env.settings, sub)
        for removed in (b"foreignObject", b"<set", b"onclick", b"example.org", b"div"):
            assert removed not in stored
        assert b"keep" in stored

    def test_oversized_svg_is_rejected(self, env, route):
        env.settings.LOGO_MAX_SVG_BYTES = len(DESIGN_TOOL_SVG) - 1
        ok, sub = route(env, _upload("logo.svg", DESIGN_TOOL_SVG, "image/svg+xml"))
        assert not ok
        assert sub is None or not sub.logo

    def test_image_over_pixel_limit_is_rejected(self, env, route):
        env.settings.LOGO_MAX_PIXELS = 100
        ok, sub = route(env, _upload("logo.png", _png((11, 10)), "image/png"))
        assert not ok
        assert sub is None or not sub.logo

    def test_non_image_is_rejected(self, env, route):
        ok, sub = route(env, _upload("page.html", b"<html></html>", "text/html"))
        assert not ok
        assert sub is None or not sub.logo

    def test_png_is_stored_as_png(self, env, route):
        ok, sub = route(env, _upload("logo.png", _png((4, 4)), "image/png"))
        assert ok
        assert _stored(env.settings, sub).startswith(b"\x89PNG")


@pytest.mark.django_db
@pytest.mark.parametrize("route", UPDATE_ROUTES.values(), ids=UPDATE_ROUTES.keys())
class TestEveryUpdateRoute:
    def test_save_without_new_file_keeps_stored_logo(self, env, route):
        sub = _existing_submission()
        # Stored before the current rules: would not pass them if re-checked.
        sub.logo.save("legacy.png", ContentFile(b"stored before current rules"))
        before_name = sub.logo.name
        ok, sub = route(env, None, sub)
        assert ok
        assert sub.logo.name == before_name
        assert _stored(env.settings, sub) == b"stored before current rules"
        assert len(list((env.settings.MEDIA_ROOT / "logos").iterdir())) == 1


@pytest.mark.django_db
class TestAdminSpecific:
    def test_rejection_message_is_shown_on_the_changeform(self, env):
        sub = _existing_submission()
        url = reverse("admin:submissions_servicesubmission_change", args=[sub.pk])
        from tests.test_admin import _admin_changeform_data

        data = _admin_changeform_data(env.admin_client, sub)
        data["logo"] = _upload("page.html", b"<html></html>", "text/html")
        resp = env.admin_client.post(url, data)
        assert resp.status_code == 200
        assert b"Unsupported file type" in resp.content

    def test_clear_checkbox_removes_logo(self, env):
        from tests.test_admin import _admin_changeform_data

        sub = _existing_submission()
        sub.logo.save("x.png", ContentFile(b"stored"))
        url = reverse("admin:submissions_servicesubmission_change", args=[sub.pk])
        data = _admin_changeform_data(env.admin_client, sub)
        data["logo-clear"] = "on"
        resp = env.admin_client.post(url, data)
        assert resp.status_code == 302
        sub.refresh_from_db()
        assert not sub.logo


# ---------------------------------------------------------------------------
# Limits shown to users (form, admin and API schema) follow the settings
# ---------------------------------------------------------------------------
