from dataclasses import dataclass, field

VALID_SORT_OPTIONS = frozenset({
    "name_asc", "name_desc",
    "updated_desc", "updated_asc",
    "added_desc", "added_asc",
})
VALID_GROUP_OPTIONS = frozenset({"", "category", "service_center", "pi"})
VALID_VIEW_OPTIONS = frozenset({"grid", "list"})
MAX_SEARCH_LENGTH = 200


@dataclass
class CatalogueQueryParams:
    search: str = ""
    categories: list = field(default_factory=list)
    centers: list = field(default_factory=list)
    sort: str = "name_asc"
    group_by: str = ""
    view: str = "grid"
    page: int = 1
    per_page: int = 12

    @classmethod
    def from_request(cls, GET, default_per_page: int = 12) -> "CatalogueQueryParams":
        search = GET.get("q", "").strip()[:MAX_SEARCH_LENGTH]

        sort = GET.get("sort", "name_asc")
        if sort not in VALID_SORT_OPTIONS:
            sort = "name_asc"

        group_by = GET.get("group_by", "")
        if group_by not in VALID_GROUP_OPTIONS:
            group_by = ""

        view = GET.get("view", "grid")
        if view not in VALID_VIEW_OPTIONS:
            view = "grid"

        try:
            page = max(1, int(GET.get("page", 1)))
        except (ValueError, TypeError):
            page = 1

        def _valid_ids(values):
            result = []
            for v in values:
                try:
                    int(v)
                    result.append(v)
                except (ValueError, TypeError):
                    pass
            return result

        categories = _valid_ids(GET.getlist("category"))
        centers = _valid_ids(GET.getlist("center"))

        return cls(
            search=search,
            categories=categories,
            centers=centers,
            sort=sort,
            group_by=group_by,
            view=view,
            page=page,
            per_page=default_per_page,
        )

    def to_selector_kwargs(self) -> dict:
        return {
            "search": self.search or None,
            "categories": self.categories or None,
            "centers": self.centers or None,
            "sort": self.sort,
            "group_by": self.group_by or None,
        }

    def to_query_string_dict(self) -> dict:
        d: dict = {}
        if self.search:
            d["q"] = self.search
        if self.categories:
            d["category"] = self.categories
        if self.centers:
            d["center"] = self.centers
        if self.sort != "name_asc":
            d["sort"] = self.sort
        if self.group_by:
            d["group_by"] = self.group_by
        if self.view != "grid":
            d["view"] = self.view
        return d
