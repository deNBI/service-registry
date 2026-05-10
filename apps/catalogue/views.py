from urllib.parse import urlencode

from django.conf import settings
from django.core.paginator import EmptyPage, Paginator
from django.http import Http404
from django.shortcuts import render
from django.urls import reverse

from .filters import CatalogueQueryParams
from .selectors import get_approved_services, get_filter_options, group_services


def _catalogue_enabled() -> bool:
    return (
        getattr(settings, "SITE_CONFIG", {})
        .get("features", {})
        .get("catalogue", False)
    )


def _catalogue_config() -> dict:
    sc = getattr(settings, "SITE_CONFIG", {})
    cat = sc.get("catalogue", {})
    try:
        per_page = max(1, int(cat.get("per_page", 12)))
    except (ValueError, TypeError):
        per_page = 12
    return {
        "per_page": per_page,
        "card_fields": cat.get(
            "card_fields", ["categories", "service_center", "updated_at"]
        ),
    }


def _paginate(qs, page: int, per_page: int):
    paginator = Paginator(qs, per_page)
    try:
        return paginator.page(page)
    except EmptyPage:
        return paginator.page(paginator.num_pages)


def catalogue_view(request):
    if not _catalogue_enabled():
        raise Http404
    config = _catalogue_config()
    params = CatalogueQueryParams.from_request(
        request.GET, default_per_page=config["per_page"]
    )
    qs = get_approved_services(**params.to_selector_kwargs())
    page_obj = _paginate(qs, params.page, config["per_page"])
    grouped = group_services(list(qs), params.group_by) if params.group_by else None

    ctx = {
        "params": params,
        "page_obj": page_obj,
        "grouped": grouped,
        "filter_options": get_filter_options(),
        "card_fields": config["card_fields"],
    }
    is_htmx = request.headers.get("HX-Request") == "true"
    is_restore = request.headers.get("HX-History-Restore-Request") == "true"
    if is_htmx and not is_restore:
        return render(request, "catalogue/partials/service_grid.html", ctx)
    return render(request, "catalogue/pages/catalogue.html", ctx)


def catalogue_grid_view(request):
    if not _catalogue_enabled():
        raise Http404
    config = _catalogue_config()
    params = CatalogueQueryParams.from_request(
        request.GET, default_per_page=config["per_page"]
    )
    qs = get_approved_services(**params.to_selector_kwargs())
    page_obj = _paginate(qs, params.page, config["per_page"])
    grouped = group_services(list(qs), params.group_by) if params.group_by else None

    ctx = {
        "params": params,
        "page_obj": page_obj,
        "grouped": grouped,
        "card_fields": config["card_fields"],
        "filter_options": get_filter_options(),
    }

    # History-restore requests need the full page, not just the grid partial.
    if request.headers.get("HX-History-Restore-Request") == "true":
        ctx["filter_options"] = get_filter_options()
        return render(request, "catalogue/pages/catalogue.html", ctx)

    response = render(request, "catalogue/partials/service_grid.html", ctx)

    # Push a clean /catalogue/?… URL to browser history so back-button
    # navigation lands on catalogue_view, not this grid-only endpoint.
    canonical_qs = params.to_query_string_dict()
    canonical_path = reverse("catalogue:index")
    if canonical_qs:
        canonical_path += "?" + urlencode(canonical_qs, doseq=True)
    response["HX-Push-Url"] = canonical_path

    return response


def catalogue_filters_view(request):
    if not _catalogue_enabled():
        raise Http404
    params = CatalogueQueryParams.from_request(request.GET)
    ctx = {
        "params": params,
        "filter_options": get_filter_options(),
    }
    return render(request, "catalogue/partials/filters.html", ctx)
