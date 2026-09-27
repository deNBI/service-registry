"""Shared Markdown rendering + sanitization for service_description.

Single source of truth: every surface (catalogue, API, HTMX preview, admin)
renders through render_markdown() so output is identical everywhere.
"""

from django.conf import settings


def markdown_enabled() -> bool:
    """True when the markdown_descriptions feature flag is on."""
    return bool(
        getattr(settings, "SITE_CONFIG", {})
        .get("features", {})
        .get("markdown_descriptions", False)
    )
