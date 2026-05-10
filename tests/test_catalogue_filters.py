import pytest
from urllib.parse import urlencode

from django.http import QueryDict

from apps.catalogue.filters import CatalogueQueryParams, MAX_SEARCH_LENGTH


def qd(params: dict) -> QueryDict:
    return QueryDict(urlencode(params, doseq=True))


class TestCatalogueQueryParams:
    def test_defaults_when_empty(self):
        params = CatalogueQueryParams.from_request(QueryDict(""))
        assert params.search == ""
        assert params.categories == []
        assert params.centers == []
        assert params.sort == "name_asc"
        assert params.group_by == ""
        assert params.page == 1
        assert params.per_page == 12

    def test_invalid_sort_falls_back_to_default(self):
        params = CatalogueQueryParams.from_request(qd({"sort": "nonsense"}))
        assert params.sort == "name_asc"

    def test_all_valid_sort_values_accepted(self):
        for sort in ["name_asc", "name_desc", "updated_desc", "updated_asc", "added_desc", "added_asc"]:
            params = CatalogueQueryParams.from_request(qd({"sort": sort}))
            assert params.sort == sort

    def test_invalid_group_by_falls_back_to_empty(self):
        params = CatalogueQueryParams.from_request(qd({"group_by": "nonsense"}))
        assert params.group_by == ""

    def test_valid_group_by_values_accepted(self):
        for group in ["category", "service_center", "pi"]:
            params = CatalogueQueryParams.from_request(qd({"group_by": group}))
            assert params.group_by == group

    def test_non_numeric_page_falls_back_to_1(self):
        params = CatalogueQueryParams.from_request(qd({"page": "abc"}))
        assert params.page == 1

    def test_negative_page_coerced_to_1(self):
        params = CatalogueQueryParams.from_request(qd({"page": "-5"}))
        assert params.page == 1

    def test_valid_page_accepted(self):
        params = CatalogueQueryParams.from_request(qd({"page": "3"}))
        assert params.page == 3

    def test_search_is_stripped(self):
        params = CatalogueQueryParams.from_request(qd({"q": "  galaxy  "}))
        assert params.search == "galaxy"

    def test_search_capped_at_max_length(self):
        long_search = "x" * (MAX_SEARCH_LENGTH + 50)
        params = CatalogueQueryParams.from_request(qd({"q": long_search}))
        assert len(params.search) == MAX_SEARCH_LENGTH

    def test_multiple_category_params_collected(self):
        qs = QueryDict("category=1&category=2&category=3")
        params = CatalogueQueryParams.from_request(qs)
        assert params.categories == ["1", "2", "3"]

    def test_unknown_params_ignored(self):
        params = CatalogueQueryParams.from_request(qd({"unknown_key": "ignored", "q": "test"}))
        assert params.search == "test"

    def test_to_selector_kwargs_empty_values_become_none(self):
        params = CatalogueQueryParams.from_request(QueryDict(""))
        kwargs = params.to_selector_kwargs()
        assert kwargs["search"] is None
        assert kwargs["categories"] is None
        assert kwargs["centers"] is None

    def test_to_selector_kwargs_passes_values(self):
        params = CatalogueQueryParams.from_request(qd({"q": "galaxy", "sort": "name_desc"}))
        kwargs = params.to_selector_kwargs()
        assert kwargs["search"] == "galaxy"
        assert kwargs["sort"] == "name_desc"

    def test_to_query_string_dict_empty_for_defaults(self):
        params = CatalogueQueryParams.from_request(QueryDict(""))
        assert params.to_query_string_dict() == {}

    def test_to_query_string_dict_includes_non_defaults(self):
        params = CatalogueQueryParams.from_request(qd({"q": "galaxy", "sort": "name_desc"}))
        d = params.to_query_string_dict()
        assert d["q"] == "galaxy"
        assert d["sort"] == "name_desc"
        assert "page" not in d

    def test_default_per_page_override(self):
        params = CatalogueQueryParams.from_request(QueryDict(""), default_per_page=24)
        assert params.per_page == 24


def test_view_defaults_to_grid():
    class FakeGET(dict):
        def get(self, key, default=""):
            return super().get(key, default)
        def getlist(self, key):
            return []
    params = CatalogueQueryParams.from_request(FakeGET({}))
    assert params.view == "grid"


def test_view_list_accepted():
    class FakeGET(dict):
        def get(self, key, default=""):
            return super().get(key, default)
        def getlist(self, key):
            return []

    fake = FakeGET({"view": "list"})
    params = CatalogueQueryParams.from_request(fake)
    assert params.view == "list"


def test_view_invalid_falls_back_to_grid():
    class FakeGET(dict):
        def get(self, key, default=""):
            return super().get(key, default)
        def getlist(self, key):
            return []

    fake = FakeGET({"view": "table"})
    params = CatalogueQueryParams.from_request(fake)
    assert params.view == "grid"


def test_view_list_included_in_query_string_dict():
    params = CatalogueQueryParams(view="list")
    d = params.to_query_string_dict()
    assert d.get("view") == "list"


def test_view_grid_omitted_from_query_string_dict():
    params = CatalogueQueryParams(view="grid")
    d = params.to_query_string_dict()
    assert "view" not in d
