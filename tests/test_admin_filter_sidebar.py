"""
Tests for the enhanced filter sidebar on ServiceSubmissionAdmin.

Covers:
  * Template — enhanced sidebar markup is rendered.
  * Template — active-filter pills appear when a filter is applied, not before.
  * Template — per-section search input is rendered for multi-option sections.
  * View — the changelist responds correctly for HTMX (HX-Request) round trips.
  * View — filter + search query params still work end-to-end with no regression.
  * Admin media — scoped CSS and JS paths are referenced only on this admin.
  * Template tag — pill removal query_string strips only the relevant filter.
"""

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

from apps.submissions.models import ServiceSubmission
from apps.submissions.templatetags.admin_filter_tags import (
    _pills_from_sections,
    _section_from_spec,
)
from tests.factories import PIFactory, ServiceCenterFactory, ServiceSubmissionFactory


def _changelist_url():
    return reverse("admin:submissions_servicesubmission_changelist")


@pytest.fixture
def admin_client(db):
    User = get_user_model()
    User.objects.create_superuser(
        username="testadmin_sidebar",
        password="adminpass123",
        email="sidebar@example.com",
    )
    c = Client()
    c.force_login(User.objects.get(username="testadmin_sidebar"))
    return c


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------


class TestSidebarTemplate:
    def test_enhanced_sidebar_class_rendered(self, admin_client):
        ServiceSubmissionFactory(status="submitted")
        resp = admin_client.get(_changelist_url())
        assert resp.status_code == 200
        assert b"changelist-filter--enhanced" in resp.content

    def test_scoped_static_assets_loaded(self, admin_client):
        ServiceSubmissionFactory(status="submitted")
        resp = admin_client.get(_changelist_url())
        assert resp.status_code == 200
        assert b"admin/css/submissions_filter_sidebar.css" in resp.content
        assert b"admin/js/submissions_filter_sidebar.js" in resp.content
        # HTMX is bundled at static/js/htmx.min.js
        assert b"js/htmx.min.js" in resp.content

    def test_pills_absent_when_no_filter_applied(self, admin_client):
        ServiceSubmissionFactory(status="submitted")
        resp = admin_client.get(_changelist_url())
        assert resp.status_code == 200
        assert b"active-filters-block" not in resp.content

    def test_pills_rendered_when_status_filter_applied(self, admin_client):
        ServiceSubmissionFactory(status="approved")
        ServiceSubmissionFactory(status="submitted")
        resp = admin_client.get(_changelist_url() + "?status__exact=approved")
        assert resp.status_code == 200
        assert b"active-filters-block" in resp.content
        assert b"pill-remove" in resp.content
        assert b"Clear all filters" in resp.content

    def test_per_section_search_input_rendered(self, admin_client):
        ServiceSubmissionFactory(status="submitted")
        resp = admin_client.get(_changelist_url())
        html = resp.content.decode()
        # Multi-option sections get a search input; at least one such input
        # exists in the rendered sidebar (status has >1 options).
        assert 'class="filter-search"' in html

    def test_filter_option_hrefs_have_single_leading_question_mark(self, admin_client):
        # Django's ChangeList.get_query_string() returns strings beginning
        # with "?", so the template must not prepend another "?".
        ServiceSubmissionFactory(status="submitted")
        resp = admin_client.get(_changelist_url())
        html = resp.content.decode()
        assert 'href="??' not in html
        # The filter-option anchors must carry valid "?<key>=<value>" hrefs.
        assert 'href="?status__exact=' in html

    def test_pill_remove_href_has_single_leading_question_mark(self, admin_client):
        ServiceSubmissionFactory(status="approved")
        resp = admin_client.get(_changelist_url() + "?status__exact=approved")
        html = resp.content.decode()
        assert 'href="??' not in html
        # pill-remove anchor must point at a valid query string.
        assert 'class="pill-remove"' in html

    def test_long_labels_wrap_via_data_label(self, admin_client):
        pi = PIFactory(
            last_name="Verylongsurname" * 4,
            first_name="Aloysius",
        )
        ServiceSubmissionFactory(status="submitted", responsible_pis=[pi])
        resp = admin_client.get(_changelist_url())
        assert resp.status_code == 200
        html = resp.content.decode()
        # The data-label attribute is set (lowercase) so client-side search
        # can match; wrapping is handled by CSS.
        assert "data-label=" in html


# ---------------------------------------------------------------------------
# HTMX round-trip
# ---------------------------------------------------------------------------


class TestHTMXRoundTrip:
    def test_htmx_request_returns_full_page_with_content_main(self, admin_client):
        ServiceSubmissionFactory(status="submitted")
        resp = admin_client.get(
            _changelist_url() + "?status__exact=submitted",
            HTTP_HX_REQUEST="true",
        )
        assert resp.status_code == 200
        # hx-select picks #content-main out of the full response.
        assert b'id="content-main"' in resp.content
        # And the enhanced sidebar is present in that response.
        assert b"changelist-filter--enhanced" in resp.content

    def test_filter_query_preserved_across_requests(self, admin_client):
        center = ServiceCenterFactory(short_name="ZZA", full_name="Centre ZZA")
        ServiceSubmissionFactory(service_center=center, service_name="Match")
        ServiceSubmissionFactory(service_name="NoMatch")
        resp = admin_client.get(
            _changelist_url() + f"?service_center__id__exact={center.pk}"
        )
        assert resp.status_code == 200
        assert b"Match" in resp.content
        # Pill reflects active filter.
        assert b"active-filters-block" in resp.content


# ---------------------------------------------------------------------------
# Template-tag helper unit tests
# ---------------------------------------------------------------------------


class TestTemplateTagHelpers:
    def test_pills_from_sections_extracts_active_label(self):
        sections = [
            {
                "field": "status",
                "title": "Status",
                "options": [
                    {
                        "display": "All",
                        "display_lower": "all",
                        "selected": False,
                        "query_string": "?",
                    },
                    {
                        "display": "Approved",
                        "display_lower": "approved",
                        "selected": True,
                        "query_string": "?status__exact=approved",
                    },
                ],
                "default_query_string": "?",
                "has_active": True,
            }
        ]
        pills = _pills_from_sections(sections)
        assert len(pills) == 1
        assert pills[0]["title"] == "Status"
        assert pills[0]["label"] == "Approved"
        assert pills[0]["remove_qs"] == "?"

    def test_pills_from_sections_no_active(self):
        sections = [
            {
                "field": "status",
                "title": "Status",
                "options": [
                    {
                        "display": "All",
                        "display_lower": "all",
                        "selected": True,
                        "query_string": "?",
                    },
                ],
                "default_query_string": "?",
                "has_active": False,
            }
        ]
        assert _pills_from_sections(sections) == []

    def test_section_from_spec_for_status(self, admin_client, rf):
        """_section_from_spec walks a real ChangeList filter spec."""
        from django.contrib import admin as dj_admin
        from django.contrib.auth import get_user_model

        ServiceSubmissionFactory(status="approved")
        ServiceSubmissionFactory(status="submitted")
        User = get_user_model()
        user = User.objects.get(username="testadmin_sidebar")

        model_admin = dj_admin.site._registry[ServiceSubmission]
        request = rf.get(_changelist_url() + "?status__exact=approved")
        request.user = user
        cl = model_admin.get_changelist_instance(request)

        # Find the 'status' filter spec.
        status_spec = next(
            s for s in cl.filter_specs if getattr(s, "field_path", "") == "status"
        )
        section = _section_from_spec(status_spec, cl)
        assert section is not None
        assert section["title"].lower() == "status"
        assert section["has_active"] is True
        # Lowercase labels for search matching.
        for opt in section["options"]:
            assert opt["display_lower"] == opt["display"].lower()


# ---------------------------------------------------------------------------
# Regression: existing changelist still works
# ---------------------------------------------------------------------------


class TestNoRegression:
    def test_search_query_still_works(self, admin_client):
        ServiceSubmissionFactory(service_name="Galaxy")
        ServiceSubmissionFactory(service_name="Nextflow")
        resp = admin_client.get(_changelist_url() + "?q=Galaxy")
        assert resp.status_code == 200
        assert b"Galaxy" in resp.content

    def test_date_hierarchy_link_still_works(self, admin_client):
        sub = ServiceSubmissionFactory()
        resp = admin_client.get(
            _changelist_url() + f"?submitted_at__year={sub.submitted_at.year}"
        )
        assert resp.status_code == 200
