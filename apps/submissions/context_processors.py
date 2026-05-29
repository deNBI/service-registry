"""
Template context processors
============================
All values from config/site.toml are injected into every template as the
``SITE`` context variable.  Templates access them like:

    {{ SITE.contact.email }}
    {{ SITE.links.privacy_policy }}
    {{ SITE.name }}           {# shortcut for SITE.site.name #}
    {{ LOGO_URL }}            {# kept as a top-level shortcut #}

The full structure mirrors site.toml exactly, so adding a new key there
makes it immediately available in all templates without touching Python code.
"""

from pathlib import Path

from django.conf import settings as dj_settings
from django.templatetags.static import static


def site_context(request):
    """
    Inject the entire SITE_CONFIG dict plus convenience shortcuts into
    every template context.
    """
    sc: dict = getattr(dj_settings, "SITE_CONFIG", {})

    site = sc.get("site", {})
    cont = sc.get("contact", {})
    links = sc.get("links", {})
    email = sc.get("email", {})
    feats = sc.get("features", {})

    # ---------------------------------------------------------------------------
    # Logo URL resolution:
    #   1. site.toml [site] logo_url
    #   2. Static file auto-detection (static/img/logo.{svg,png,…})
    #   3. Empty string → CSS text fallback in base.html
    # ---------------------------------------------------------------------------
    logo_url = site.get("logo_url", "")
    if not logo_url:
        static_img = Path(dj_settings.BASE_DIR) / "static" / "img"
        for ext in ("svg", "png", "jpg", "jpeg", "webp"):
            if (static_img / f"logo.{ext}").exists():
                logo_url = static(f"img/logo.{ext}")
                break

    # ---------------------------------------------------------------------------
    # Favicon URL resolution:
    #   1. site.toml [site] favicon_url
    #   2. Static file auto-detection (static/img/favicon.ico or favicon.png)
    #   3. Empty string → no <link rel="icon"> rendered
    # ---------------------------------------------------------------------------
    favicon_url = site.get("favicon_url", "")
    if not favicon_url:
        static_img = Path(dj_settings.BASE_DIR) / "static" / "img"
        for fname in ("favicon.ico", "favicon.png", "favicon.svg"):
            if (static_img / fname).exists():
                favicon_url = static(f"img/{fname}")
                break

    return {
        # Full config dict — {{ SITE.contact.email }}, {{ SITE.links.kpi_cheatsheet }}, …
        "SITE": {
            **site,
            "contact": cont,
            "links": links,
            "email": email,
            "features": feats,
        },
        # Top-level shortcuts for the most-used values
        "LOGO_URL": logo_url,
        "FAVICON_URL": favicon_url,
        "SITE_NAME": site.get("name", "de.NBI Service Registration Platform"),
        "SITE_URL": site.get("url", ""),
        "CONTACT_EMAIL": cont.get("email", "servicecoordination@denbi.de"),
        "CONTACT_OFFICE": cont.get("office", ""),
        "CONTACT_ORG": cont.get(
            "organisation", "German Network for Bioinformatics Infrastructure"
        ),
        "PRIVACY_POLICY_URL": links.get(
            "privacy_policy", "https://www.denbi.de/privacy-policy"
        ),
        "IMPRINT_URL": links.get("imprint", "https://www.denbi.de/imprint"),
        "WEBSITE_URL": links.get("website", "https://www.denbi.de"),
        "USER_GUIDE_URL": links.get(
            "user_guide", "https://denbi.github.io/service-registry/user-guide/"
        ),
        "FORM_DRAFT_TTL_DAYS": getattr(dj_settings, "FORM_DRAFT_TTL_DAYS", 7),
    }
