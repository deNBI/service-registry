import pytest

CATALOGUE_ON = {
    "features": {
        "catalogue": True,
        "biotools_prefill": False,
        "edam_annotations": False,
    },
    "catalogue": {
        "per_page": 12,
        "card_fields": ["categories", "service_center", "updated_at"],
    },
    "site": {"name": "Test Registry", "url": "https://example.com"},
    "contact": {},
    "links": {},
    "email": {},
}
CATALOGUE_OFF = {
    **CATALOGUE_ON,
    "features": {"catalogue": False},
}


@pytest.mark.django_db
class TestCatalogueView:
    def test_200_when_enabled(self, client, settings):
        settings.SITE_CONFIG = CATALOGUE_ON
        response = client.get("/catalogue/")
        assert response.status_code == 200

    def test_404_when_disabled(self, client, settings):
        settings.SITE_CONFIG = CATALOGUE_OFF
        response = client.get("/catalogue/")
        assert response.status_code == 404

    def test_only_approved_services_shown(self, client, settings):
        from tests.factories import ServiceSubmissionFactory

        settings.SITE_CONFIG = CATALOGUE_ON
        ServiceSubmissionFactory(status="submitted", service_name="Hidden Service")
        ServiceSubmissionFactory(status="approved", service_name="Visible Service")

        response = client.get("/catalogue/")
        assert b"Visible Service" in response.content
        assert b"Hidden Service" not in response.content

    def test_empty_state_when_no_approved_services(self, client, settings):
        settings.SITE_CONFIG = CATALOGUE_ON
        response = client.get("/catalogue/")
        assert response.status_code == 200
        content = response.content.decode().lower()
        assert "no approved services" in content or "no services" in content

    def test_htmx_request_returns_partial_without_html_tag(self, client, settings):
        settings.SITE_CONFIG = CATALOGUE_ON
        response = client.get("/catalogue/", HTTP_HX_REQUEST="true")
        assert response.status_code == 200
        assert b"<html" not in response.content

    def test_full_request_returns_page_with_html_tag(self, client, settings):
        settings.SITE_CONFIG = CATALOGUE_ON
        response = client.get("/catalogue/")
        assert b"<html" in response.content


@pytest.mark.django_db
class TestCatalogueGridView:
    def test_returns_partial_html(self, client, settings):
        settings.SITE_CONFIG = CATALOGUE_ON
        response = client.get("/catalogue/grid/")
        assert response.status_code == 200
        assert b"<html" not in response.content

    def test_404_when_disabled(self, client, settings):
        settings.SITE_CONFIG = CATALOGUE_OFF
        response = client.get("/catalogue/grid/")
        assert response.status_code == 404

    def test_out_of_range_page_returns_200_not_404(self, client, settings):
        from tests.factories import ServiceSubmissionFactory

        settings.SITE_CONFIG = CATALOGUE_ON
        ServiceSubmissionFactory(status="approved")
        response = client.get("/catalogue/grid/?page=9999")
        assert response.status_code == 200

    def test_search_filters_results(self, client, settings):
        from tests.factories import ServiceSubmissionFactory

        settings.SITE_CONFIG = CATALOGUE_ON
        ServiceSubmissionFactory(status="approved", service_name="Galaxy Workflow")
        ServiceSubmissionFactory(status="approved", service_name="Unrelated Tool")

        response = client.get("/catalogue/grid/?q=Galaxy")
        assert b"Galaxy Workflow" in response.content
        assert b"Unrelated Tool" not in response.content


@pytest.mark.django_db
class TestCatalogueFiltersView:
    def test_returns_partial_html(self, client, settings):
        settings.SITE_CONFIG = CATALOGUE_ON
        response = client.get("/catalogue/filters/")
        assert response.status_code == 200
        assert b"<html" not in response.content

    def test_404_when_disabled(self, client, settings):
        settings.SITE_CONFIG = CATALOGUE_OFF
        response = client.get("/catalogue/filters/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestCatalogueListView:
    def test_catalogue_grid_view_default_is_grid(self, client, settings):
        settings.SITE_CONFIG = CATALOGUE_ON
        from tests.factories import ServiceSubmissionFactory

        ServiceSubmissionFactory(status="approved", biotools_url="")
        resp = client.get("/catalogue/grid/")
        assert resp.status_code == 200
        assert b"catalogue-card-grid" in resp.content

    def test_catalogue_grid_view_list_mode(self, client, settings):
        settings.SITE_CONFIG = CATALOGUE_ON
        from tests.factories import ServiceSubmissionFactory

        ServiceSubmissionFactory(status="approved", biotools_url="")
        resp = client.get("/catalogue/grid/?view=list")
        assert resp.status_code == 200
        assert b"catalogue-list-item" in resp.content

    def test_catalogue_grid_view_returns_full_page_on_history_restore(
        self, client, settings
    ):
        settings.SITE_CONFIG = CATALOGUE_ON
        resp = client.get(
            "/catalogue/grid/",
            HTTP_HX_REQUEST="true",
            HTTP_HX_HISTORY_RESTORE_REQUEST="true",
        )
        assert resp.status_code == 200
        # Full page contains the outer layout, not just the grid partial
        assert b"catalogue-toolbar" in resp.content
        assert b"catalogue-filter-sidebar" in resp.content

    def test_catalogue_grid_view_sets_push_url_header(self, client, settings):
        settings.SITE_CONFIG = CATALOGUE_ON
        resp = client.get(
            "/catalogue/grid/?q=bio&sort=updated_desc",
            HTTP_HX_REQUEST="true",
        )
        assert resp.status_code == 200
        assert "HX-Push-Url" in resp
        assert resp["HX-Push-Url"].startswith("/catalogue/")
        assert "q=bio" in resp["HX-Push-Url"]
        assert "sort=updated_desc" in resp["HX-Push-Url"]

    def test_catalogue_view_returns_full_page_on_history_restore(
        self, client, settings
    ):
        settings.SITE_CONFIG = CATALOGUE_ON
        resp = client.get(
            "/catalogue/?q=bio",
            HTTP_HX_REQUEST="true",
            HTTP_HX_HISTORY_RESTORE_REQUEST="true",
        )
        assert resp.status_code == 200
        assert b"catalogue-toolbar" in resp.content
        assert b"catalogue-filter-sidebar" in resp.content
