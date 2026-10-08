"""
de.NBI Service Registration Platform — Root URL Configuration
=================================================
"""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve as _static_serve

from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

# Customise Django admin site header/title from site.toml
_site_name = (
    getattr(settings, "SITE_CONFIG", {})
    .get("site", {})
    .get("name", "de.NBI Service Registration Platform")
)
admin.site.site_header = f"{_site_name} Administration"
admin.site.site_title = f"{_site_name} Admin"
admin.site.index_title = "Administration Portal"

admin_prefix = getattr(settings, "ADMIN_URL_PREFIX", "admin-denbi")

urlpatterns = [
    # Admin — URL path is obfuscated via [admin] url_prefix in site.toml (or ADMIN_URL_PREFIX env var)
    path(f"{admin_prefix}/", admin.site.urls),
    # Public form routes
    path("", include("apps.submissions.urls")),
    # bio.tools form integration (HTMX prefill + name search)
    path("biotools/", include("apps.biotools.urls")),
    # Public Registry Catalogue
    path("catalogue/", include("apps.catalogue.urls")),
    # REST API v1
    path("api/v1/", include("apps.api.urls")),
    # OpenAPI schema + docs
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    # Health checks (used by Docker / load balancer)
    path("health/", include("apps.submissions.health_urls")),
]

# Policy for uploaded media opened directly in the browser. A logo is a
# self-contained image: it needs its own inline styles and data: resources,
# nothing else. Pages showing logos via <img> are not affected by this header.
_MEDIA_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
    "font-src data:; sandbox"
)


def _serve_media(request, path):
    response = _static_serve(request, path, document_root=settings.MEDIA_ROOT)
    # Set here so the site-wide CSPMiddleware leaves it in place.
    response["Content-Security-Policy"] = _MEDIA_CSP
    return response


# Serve uploaded media files (logos) through Gunicorn.
# Logos are non-sensitive brand assets destined for the public de.NBI catalogue.
# Paths are UUID-based (/media/logos/<uuid4>.ext) — not enumerable without first
# obtaining logo_url from an authenticated API response.
urlpatterns += [
    re_path(
        r"^media/(?P<path>.*)$",
        _serve_media,
    ),
]
