"""Tests for apps/submissions/context_processors.py"""

import pytest


@pytest.mark.django_db
def test_form_draft_ttl_days_default_is_seven():
    """FORM_DRAFT_TTL_DAYS defaults to 7 when site.toml has the default value."""
    from django.conf import settings

    assert settings.FORM_DRAFT_TTL_DAYS == 7


@pytest.mark.django_db
def test_form_draft_ttl_days_overridable(settings):
    """FORM_DRAFT_TTL_DAYS can be overridden in tests via the settings fixture."""
    settings.FORM_DRAFT_TTL_DAYS = 14
    assert settings.FORM_DRAFT_TTL_DAYS == 14


@pytest.mark.django_db
def test_site_context_exposes_form_draft_ttl_days(rf, settings):
    """site_context() must include FORM_DRAFT_TTL_DAYS for template use."""
    from apps.submissions.context_processors import site_context

    settings.FORM_DRAFT_TTL_DAYS = 14
    request = rf.get("/")
    ctx = site_context(request)
    assert "FORM_DRAFT_TTL_DAYS" in ctx
    assert ctx["FORM_DRAFT_TTL_DAYS"] == 14
