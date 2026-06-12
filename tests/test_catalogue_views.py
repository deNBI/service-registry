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
CATALOGUE_WITH_EDAM_MATURITY = {
    **CATALOGUE_ON,
    "catalogue": {
        "per_page": 12,
        "card_fields": [
            "categories",
            "service_center",
            "updated_at",
            "edam_topics",
            "maturity_tag",
        ],
    },
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
class TestCatalogueResultCount:
    """The result count must live inside the HTMX swap target so it updates
    when the user filters or searches (previously it sat in the page header
    and went stale on every filter interaction)."""

    def test_grid_count_reflects_filtered_results(self, client, settings):
        from tests.factories import ServiceSubmissionFactory

        settings.SITE_CONFIG = CATALOGUE_ON
        ServiceSubmissionFactory(
            status="approved", service_name="Galaxy Workflow", biotools_url=""
        )
        ServiceSubmissionFactory(
            status="approved", service_name="Other Tool A", biotools_url=""
        )
        ServiceSubmissionFactory(
            status="approved", service_name="Other Tool B", biotools_url=""
        )

        resp = client.get("/catalogue/grid/?q=Galaxy")
        content = resp.content.decode()
        # Filtered count (1) appears with "result" wording; total (3) does not leak in.
        assert "1</strong> result" in content
        assert "3</strong> service" not in content

    def test_grid_count_unfiltered_wording(self, client, settings):
        from tests.factories import ServiceSubmissionFactory

        settings.SITE_CONFIG = CATALOGUE_ON
        ServiceSubmissionFactory(status="approved", biotools_url="")
        ServiceSubmissionFactory(status="approved", biotools_url="")

        resp = client.get("/catalogue/grid/")
        content = resp.content.decode()
        assert "2</strong> service" in content

    def test_grid_count_is_live_region(self, client, settings):
        from tests.factories import ServiceSubmissionFactory

        settings.SITE_CONFIG = CATALOGUE_ON
        ServiceSubmissionFactory(status="approved", biotools_url="")

        resp = client.get("/catalogue/grid/")
        content = resp.content.decode()
        assert "catalogue-result-count" in content
        assert 'aria-live="polite"' in content

    def test_full_page_filtered_url_count_is_accurate(self, client, settings):
        """Landing directly on a filtered URL shows the filtered count, not the total."""
        from tests.factories import ServiceSubmissionFactory

        settings.SITE_CONFIG = CATALOGUE_ON
        ServiceSubmissionFactory(
            status="approved", service_name="Galaxy Workflow", biotools_url=""
        )
        ServiceSubmissionFactory(
            status="approved", service_name="Other Tool", biotools_url=""
        )

        resp = client.get("/catalogue/?q=Galaxy")
        content = resp.content.decode()
        assert "1</strong> result" in content
        assert "2</strong> service" not in content


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


@pytest.mark.django_db
class TestCatalogueCardTooltips:
    """Verify that card view badges carry the correct title tooltip."""

    def test_category_badge_has_tooltip(self, client, settings):
        from tests.factories import ServiceCategoryFactory, ServiceSubmissionFactory

        settings.SITE_CONFIG = CATALOGUE_ON
        cat = ServiceCategoryFactory(name="Sequence Analysis")
        sub = ServiceSubmissionFactory(status="approved", biotools_url="")
        sub.service_categories.set([cat])
        resp = client.get("/catalogue/grid/")
        content = resp.content.decode()
        assert 'title="Service category"' in content
        assert "Sequence Analysis" in content

    def test_edam_topic_badge_has_tooltip(self, client, settings):
        from apps.edam.models import EdamTerm
        from tests.factories import ServiceSubmissionFactory

        settings.SITE_CONFIG = CATALOGUE_WITH_EDAM_MATURITY
        sub = ServiceSubmissionFactory(status="approved", biotools_url="")
        topic = EdamTerm.objects.create(
            uri="http://edamontology.org/topic_0091",
            label="Bioinformatics",
            branch="topic",
            accession="topic_0091",
        )
        sub.edam_topics.add(topic)
        resp = client.get("/catalogue/grid/")
        content = resp.content.decode()
        assert 'title="EDAM topic"' in content
        assert "Bioinformatics" in content

    def test_maturity_badge_has_tooltip(self, client, settings):
        from tests.factories import ServiceSubmissionFactory

        settings.SITE_CONFIG = CATALOGUE_WITH_EDAM_MATURITY
        ServiceSubmissionFactory(
            status="approved", primary_maturity_tag="mature", biotools_url=""
        )
        resp = client.get("/catalogue/grid/")
        content = resp.content.decode()
        assert 'title="Service maturity"' in content
        assert "Mature" in content


@pytest.mark.django_db
class TestCatalogueListParity:
    """Verify list view shows the same pills as card view, with tooltips."""

    def test_category_badge_has_tooltip_in_list_view(self, client, settings):
        from tests.factories import ServiceCategoryFactory, ServiceSubmissionFactory

        settings.SITE_CONFIG = CATALOGUE_ON
        cat = ServiceCategoryFactory(name="Genomics")
        sub = ServiceSubmissionFactory(status="approved", biotools_url="")
        sub.service_categories.set([cat])
        resp = client.get("/catalogue/grid/?view=list")
        content = resp.content.decode()
        assert 'title="Service category"' in content
        assert "Genomics" in content

    def test_list_view_shows_edam_topic_with_tooltip(self, client, settings):
        from apps.edam.models import EdamTerm
        from tests.factories import ServiceSubmissionFactory

        settings.SITE_CONFIG = CATALOGUE_WITH_EDAM_MATURITY
        sub = ServiceSubmissionFactory(status="approved", biotools_url="")
        topic = EdamTerm.objects.create(
            uri="http://edamontology.org/topic_0092",
            label="Data visualisation",
            branch="topic",
            accession="topic_0092",
        )
        sub.edam_topics.add(topic)
        resp = client.get("/catalogue/grid/?view=list")
        content = resp.content.decode()
        assert 'title="EDAM topic"' in content
        assert "Data visualisation" in content

    def test_list_view_shows_maturity_badge_with_tooltip(self, client, settings):
        from tests.factories import ServiceSubmissionFactory

        settings.SITE_CONFIG = CATALOGUE_WITH_EDAM_MATURITY
        ServiceSubmissionFactory(
            status="approved", primary_maturity_tag="emerging", biotools_url=""
        )
        resp = client.get("/catalogue/grid/?view=list")
        content = resp.content.decode()
        assert 'title="Service maturity"' in content
        assert "Emerging" in content

    def test_list_view_edam_overflow_count(self, client, settings):
        """When a service has more than 3 EDAM topics, list view shows +N overflow."""
        from apps.edam.models import EdamTerm
        from tests.factories import ServiceSubmissionFactory

        settings.SITE_CONFIG = CATALOGUE_WITH_EDAM_MATURITY
        sub = ServiceSubmissionFactory(status="approved", biotools_url="")
        for i in range(4):
            topic = EdamTerm.objects.create(
                uri=f"http://edamontology.org/topic_100{i}",
                label=f"Topic {i}",
                branch="topic",
                accession=f"topic_100{i}",
            )
            sub.edam_topics.add(topic)
        resp = client.get("/catalogue/grid/?view=list")
        assert b"+1" in resp.content

    def test_list_view_no_edam_when_not_in_card_fields(self, client, settings):
        """EDAM section is absent when edam_topics not in card_fields."""
        from apps.edam.models import EdamTerm
        from tests.factories import ServiceSubmissionFactory

        settings.SITE_CONFIG = CATALOGUE_ON  # card_fields has no edam_topics
        sub = ServiceSubmissionFactory(status="approved", biotools_url="")
        topic = EdamTerm.objects.create(
            uri="http://edamontology.org/topic_0093",
            label="Should Not Appear",
            branch="topic",
            accession="topic_0093",
        )
        sub.edam_topics.add(topic)
        resp = client.get("/catalogue/grid/?view=list")
        assert b"Should Not Appear" not in resp.content
