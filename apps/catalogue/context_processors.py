from django.conf import settings


def catalogue_context(request):
    sc = getattr(settings, "SITE_CONFIG", {})
    cat = sc.get("catalogue", {})
    return {
        "CATALOGUE_CARD_FIELDS": cat.get(
            "card_fields", ["categories", "service_center", "updated_at"]
        ),
        "CATALOGUE_PER_PAGE": cat.get("per_page", 12),
        "CATALOGUE_ENABLED": sc.get("features", {}).get("catalogue", False),
        "CATALOGUE_META_DESCRIPTION": cat.get(
            "meta_description",
            "Browse all approved de.NBI & ELIXIR-DE bioinformatics services.",
        ),
    }
