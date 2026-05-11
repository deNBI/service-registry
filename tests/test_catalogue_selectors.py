import pytest

from apps.catalogue.selectors import (
    get_approved_services,
    get_filter_options,
    group_services,
)
from tests.factories import (
    PIFactory,
    ServiceCategoryFactory,
    ServiceCenterFactory,
    ServiceSubmissionFactory,
)


@pytest.mark.django_db
class TestGetApprovedServices:
    def test_only_approved_services_returned(self):
        ServiceSubmissionFactory(status="submitted")
        ServiceSubmissionFactory(status="under_review")
        ServiceSubmissionFactory(status="rejected")
        ServiceSubmissionFactory(status="deprecated")
        approved = ServiceSubmissionFactory(status="approved")

        results = list(get_approved_services())
        assert len(results) == 1
        assert results[0].pk == approved.pk

    def test_empty_queryset_when_no_approved(self):
        ServiceSubmissionFactory(status="submitted")
        assert list(get_approved_services()) == []

    def test_search_matches_service_name(self):
        ServiceSubmissionFactory(status="approved", service_name="Galaxy Workflow")
        ServiceSubmissionFactory(status="approved", service_name="Unrelated Tool")

        results = list(get_approved_services(search="Galaxy"))
        assert len(results) == 1
        assert results[0].service_name == "Galaxy Workflow"

    def test_search_is_case_insensitive(self):
        ServiceSubmissionFactory(status="approved", service_name="Galaxy Workflow")
        results = list(get_approved_services(search="galaxy"))
        assert len(results) == 1

    def test_search_matches_category_name(self):
        cat = ServiceCategoryFactory(name="Proteomics Pipeline")
        ServiceSubmissionFactory(status="approved", service_categories=[cat])
        ServiceSubmissionFactory(status="approved")
        results = list(get_approved_services(search="proteomics"))
        assert len(results) == 1

    def test_search_matches_service_center(self):
        ctr = ServiceCenterFactory(short_name="HD-HuB", full_name="Heidelberg Hub")
        ServiceSubmissionFactory(status="approved", service_center=ctr)
        ServiceSubmissionFactory(status="approved")
        results = list(get_approved_services(search="Heidelberg"))
        assert len(results) == 1

    def test_search_matches_pi_name(self):
        pi = PIFactory(last_name="Müller", first_name="Anna")
        s = ServiceSubmissionFactory(status="approved", responsible_pis=[pi])
        ServiceSubmissionFactory(status="approved")
        results = list(get_approved_services(search="Müller"))
        assert len(results) == 1
        assert results[0].pk == s.pk

    def test_category_filter_narrows_results(self):
        cat_db = ServiceCategoryFactory(name="Database")
        cat_tool = ServiceCategoryFactory(name="Tool")
        s1 = ServiceSubmissionFactory(status="approved", service_categories=[cat_db])
        s2 = ServiceSubmissionFactory(status="approved", service_categories=[cat_tool])

        results = list(get_approved_services(categories=[str(cat_db.id)]))
        pks = [r.pk for r in results]
        assert s1.pk in pks
        assert s2.pk not in pks

    def test_multi_category_filter_uses_or(self):
        cat_a = ServiceCategoryFactory(name="Database")
        cat_b = ServiceCategoryFactory(name="Tool")
        s1 = ServiceSubmissionFactory(status="approved", service_categories=[cat_a])
        s2 = ServiceSubmissionFactory(status="approved", service_categories=[cat_b])

        results = list(get_approved_services(categories=[str(cat_a.id), str(cat_b.id)]))
        pks = [r.pk for r in results]
        assert s1.pk in pks
        assert s2.pk in pks

    def test_center_filter_narrows_results(self):
        ctr_a = ServiceCenterFactory(short_name="HD-HuB")
        ctr_b = ServiceCenterFactory(short_name="BiGi")
        s1 = ServiceSubmissionFactory(status="approved", service_center=ctr_a)
        s2 = ServiceSubmissionFactory(status="approved", service_center=ctr_b)

        results = list(get_approved_services(centers=[str(ctr_a.id)]))
        pks = [r.pk for r in results]
        assert s1.pk in pks
        assert s2.pk not in pks

    def test_sort_name_asc(self):
        ServiceSubmissionFactory(status="approved", service_name="Zebra Tool")
        ServiceSubmissionFactory(status="approved", service_name="Alpha Tool")
        results = list(get_approved_services(sort="name_asc"))
        assert results[0].service_name == "Alpha Tool"
        assert results[-1].service_name == "Zebra Tool"

    def test_sort_name_desc(self):
        ServiceSubmissionFactory(status="approved", service_name="Zebra Tool")
        ServiceSubmissionFactory(status="approved", service_name="Alpha Tool")
        results = list(get_approved_services(sort="name_desc"))
        assert results[0].service_name == "Zebra Tool"
        assert results[-1].service_name == "Alpha Tool"

    def test_no_n_plus_one_queries(self, django_assert_num_queries):
        for _ in range(8):
            ServiceSubmissionFactory(status="approved")

        # 1 main query + 5 prefetch queries (service_categories, responsible_pis,
        # edam_topics, edam_operations, licenses) = 6 total regardless of row count
        with django_assert_num_queries(6):
            list(get_approved_services())


@pytest.mark.django_db
class TestGroupServices:
    def test_group_by_category_groups_correctly(self):
        cat_a = ServiceCategoryFactory(name="Aardvark")
        cat_b = ServiceCategoryFactory(name="Zebra")
        ServiceSubmissionFactory(status="approved", service_categories=[cat_a])
        ServiceSubmissionFactory(status="approved", service_categories=[cat_b])

        services = list(get_approved_services())
        groups = group_services(services, "category")
        labels = [g[0] for g in groups]
        assert "Aardvark" in labels
        assert "Zebra" in labels

    def test_group_by_service_center(self):
        ctr = ServiceCenterFactory(short_name="HD-HuB")
        ServiceSubmissionFactory(status="approved", service_center=ctr)

        services = list(get_approved_services())
        groups = group_services(services, "service_center")
        labels = [g[0] for g in groups]
        assert "HD-HuB" in labels

    def test_no_group_returns_single_all_services_group(self):
        ServiceSubmissionFactory(status="approved")
        ServiceSubmissionFactory(status="approved")

        services = list(get_approved_services())
        groups = group_services(services, "")
        assert len(groups) == 1
        assert groups[0][0] == "All Services"
        assert len(groups[0][1]) == 2

    def test_group_returns_list_of_label_services_tuples(self):
        cat = ServiceCategoryFactory(name="Database")
        ServiceSubmissionFactory(status="approved", service_categories=[cat])

        services = list(get_approved_services())
        groups = group_services(services, "category")
        assert isinstance(groups, list)
        label, items = groups[0]
        assert isinstance(label, str)
        assert isinstance(items, list)


@pytest.mark.django_db
class TestGetFilterOptions:
    def test_returns_only_active_categories(self):
        ServiceCategoryFactory(name="Active DB", is_active=True)
        ServiceCategoryFactory(name="Inactive Tool", is_active=False)

        opts = get_filter_options()
        names = [c["name"] for c in opts["categories"]]
        assert "Active DB" in names
        assert "Inactive Tool" not in names

    def test_returns_only_active_centers(self):
        ServiceCenterFactory(short_name="CTR-ON", is_active=True)
        ServiceCenterFactory(short_name="CTR-OFF", is_active=False)

        opts = get_filter_options()
        shorts = [c["short_name"] for c in opts["centers"]]
        assert "CTR-ON" in shorts
        assert "CTR-OFF" not in shorts

    def test_returns_id_name_for_categories(self):
        ServiceCategoryFactory(name="Database", is_active=True)
        opts = get_filter_options()
        assert "id" in opts["categories"][0]
        assert "name" in opts["categories"][0]

    def test_returns_id_short_name_full_name_for_centers(self):
        ServiceCenterFactory(
            short_name="CTR", full_name="Centre Full Name", is_active=True
        )
        opts = get_filter_options()
        assert "id" in opts["centers"][0]
        assert "short_name" in opts["centers"][0]
        assert "full_name" in opts["centers"][0]
