import itertools

from django.db import models

from apps.submissions.models import ServiceSubmission, SubmissionStatus

SORT_MAP = {
    "name_asc": "service_name",
    "name_desc": "-service_name",
    "updated_desc": "-updated_at",
    "updated_asc": "updated_at",
    "added_desc": "-submitted_at",
    "added_asc": "submitted_at",
}


def get_approved_services(
    search: str | None = None,
    categories: list | None = None,
    centers: list | None = None,
    sort: str = "name_asc",
    group_by: str | None = None,
):
    qs = (
        ServiceSubmission.objects.filter(status=SubmissionStatus.APPROVED)
        .select_related("service_center")
        .prefetch_related(
            "service_categories",
            "responsible_pis",
            "edam_topics",
            "edam_operations",
            "licenses",
        )
    )

    if search:
        qs = _apply_search(qs, search)

    if categories:
        qs = qs.filter(service_categories__id__in=categories).distinct()

    if centers:
        qs = qs.filter(service_center__id__in=centers)

    qs = _apply_sort(qs, sort)

    return qs


def _apply_search(qs, search_term: str):
    return qs.filter(
        models.Q(service_name__icontains=search_term)
        | models.Q(service_categories__name__icontains=search_term)
        | models.Q(service_center__short_name__icontains=search_term)
        | models.Q(service_center__full_name__icontains=search_term)
        | models.Q(responsible_pis__last_name__icontains=search_term)
        | models.Q(responsible_pis__first_name__icontains=search_term)
    ).distinct()


def _apply_sort(qs, sort: str):
    return qs.order_by(SORT_MAP.get(sort, "service_name"))


def get_filter_options() -> dict:
    from apps.registry.models import ServiceCategory, ServiceCenter

    categories = list(
        ServiceCategory.objects.filter(is_active=True)
        .values("id", "name")
        .order_by("name")
    )
    centers = list(
        ServiceCenter.objects.filter(is_active=True)
        .values("id", "short_name", "full_name")
        .order_by("full_name")
    )
    return {"categories": categories, "centers": centers}


def group_services(services, group_by: str) -> list:
    if group_by == "category":
        def key_fn(s):
            cats = list(s.service_categories.all())
            return cats[0].name if cats else "Uncategorised"
    elif group_by == "service_center":
        def key_fn(s):
            return s.service_center.short_name if s.service_center else "Unknown"
    elif group_by == "pi":
        def key_fn(s):
            pis = list(s.responsible_pis.all())
            return str(pis[0]) if pis else "Unknown"
    else:
        return [("All Services", list(services))]

    items = sorted(list(services), key=key_fn)
    return [(label, list(group)) for label, group in itertools.groupby(items, key=key_fn)]
