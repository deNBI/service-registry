"""Regression tests for catalogue ID-based filtering across mixed PK types.

ServiceCategory uses an integer PK while ServiceCenter (and PI) use UUID PKs.
The query-param validator must accept both, or UUID-keyed filters get silently
discarded (the original bug: only category filtering worked).
"""

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


@pytest.mark.django_db
class TestCatalogueIdFiltering:
    def test_grid_center_filter_narrows(self, client, settings):
        settings.SITE_CONFIG = CATALOGUE_ON
        from tests.factories import ServiceCenterFactory, ServiceSubmissionFactory

        ca = ServiceCenterFactory(short_name="AAA", full_name="Center Alpha")
        cb = ServiceCenterFactory(short_name="BBB", full_name="Center Beta")
        ServiceSubmissionFactory(
            status="approved",
            service_name="AlphaSvc",
            service_center=ca,
            biotools_url="",
        )
        ServiceSubmissionFactory(
            status="approved",
            service_name="BetaSvc",
            service_center=cb,
            biotools_url="",
        )

        resp = client.get(f"/catalogue/grid/?center={ca.id}")
        body = resp.content.decode()
        assert "AlphaSvc" in body
        assert "BetaSvc" not in body

    def test_grid_category_filter_narrows(self, client, settings):
        settings.SITE_CONFIG = CATALOGUE_ON
        from tests.factories import ServiceCategoryFactory, ServiceSubmissionFactory

        cat_a = ServiceCategoryFactory(name="CatA")
        cat_b = ServiceCategoryFactory(name="CatB")
        sa = ServiceSubmissionFactory(
            status="approved", service_name="HasCatA", biotools_url=""
        )
        sb = ServiceSubmissionFactory(
            status="approved", service_name="HasCatB", biotools_url=""
        )
        sa.service_categories.set([cat_a])
        sb.service_categories.set([cat_b])

        resp = client.get(f"/catalogue/grid/?category={cat_a.id}")
        body = resp.content.decode()
        assert "HasCatA" in body
        assert "HasCatB" not in body

    def test_combined_center_and_category_filter(self, client, settings):
        settings.SITE_CONFIG = CATALOGUE_ON
        from tests.factories import (
            ServiceCategoryFactory,
            ServiceCenterFactory,
            ServiceSubmissionFactory,
        )

        ca = ServiceCenterFactory(short_name="CA", full_name="Center A")
        cat = ServiceCategoryFactory(name="Genomics")
        match = ServiceSubmissionFactory(
            status="approved",
            service_name="MatchSvc",
            service_center=ca,
            biotools_url="",
        )
        match.service_categories.set([cat])
        # Same center, different category -> excluded by category filter
        other_cat = ServiceCategoryFactory(name="Proteomics")
        miss = ServiceSubmissionFactory(
            status="approved",
            service_name="MissSvc",
            service_center=ca,
            biotools_url="",
        )
        miss.service_categories.set([other_cat])

        resp = client.get(f"/catalogue/grid/?center={ca.id}&category={cat.id}")
        body = resp.content.decode()
        assert "MatchSvc" in body
        assert "MissSvc" not in body

    def test_center_checkbox_renders_checked_when_active(self, client, settings):
        settings.SITE_CONFIG = CATALOGUE_ON
        from tests.factories import ServiceCenterFactory, ServiceSubmissionFactory

        ca = ServiceCenterFactory(short_name="ZZZ", full_name="Center Zeta")
        ServiceSubmissionFactory(status="approved", service_center=ca, biotools_url="")

        resp = client.get(f"/catalogue/?center={ca.id}")
        body = resp.content.decode()
        # The matching center checkbox must render as checked.
        import re

        m = re.search(r'<input[^>]*id="ctr-' + re.escape(str(ca.id)) + r'"[^>]*>', body)
        assert m, "center checkbox should render"
        assert "checked" in m.group(0), "active center checkbox should be checked"


@pytest.mark.django_db
class TestValidIdsParsing:
    def test_uuid_ids_preserved(self):
        from django.http import QueryDict

        from apps.catalogue.filters import CatalogueQueryParams

        u = "639f487a-39bf-4b15-9246-e8376a4cccd5"
        p = CatalogueQueryParams.from_request(QueryDict(f"center={u}"))
        assert p.centers == [u]

    def test_integer_ids_preserved(self):
        from django.http import QueryDict

        from apps.catalogue.filters import CatalogueQueryParams

        p = CatalogueQueryParams.from_request(QueryDict("category=42"))
        assert p.categories == ["42"]

    def test_garbage_ids_discarded(self):
        from django.http import QueryDict

        from apps.catalogue.filters import CatalogueQueryParams

        p = CatalogueQueryParams.from_request(
            QueryDict(
                "center=not-a-real-id&center=" + "639f487a-39bf-4b15-9246-e8376a4cccd5"
            )
        )
        assert p.centers == ["639f487a-39bf-4b15-9246-e8376a4cccd5"]
