"""Fixtures for the browser tests in tests/e2e/.

Every test collected from this directory is marked ``browser`` (see
``pytest_collection_modifyitems`` below). The default ``pytest tests/`` run
leaves them out; ``make test-browser`` runs them against Django's live server
in headless Chromium (pytest-playwright).
"""

from pathlib import Path

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from tests.factories import (
    PIFactory,
    ServiceCategoryFactory,
    ServiceCenterFactory,
    ServiceSubmissionFactory,
)

HERE = Path(__file__).parent
LONG_CATEGORY = "Molecular Interactions, Pathways and Networks Analysis Platform"
LONG_TOKEN_NAME = "Service-" + "x" * 60 + "-longtoken"
MARKDOWN_NAME = "Markdown Demo Service"
MARKDOWN_DESCRIPTION = (
    "**Bold** and _italic_ text.\n\n"
    "- first bullet\n- second bullet\n\n"
    "1. step one\n2. step two\n\n"
    "Legacy entity: x &gt; 5 and a generic List<String> type.\n\n"
    "See [the de.NBI site](https://www.denbi.de) for details."
)
LOGO_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
    b'<rect width="10" height="10" fill="#5c9d25"/></svg>'
)


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    """Mark every test in this directory ``browser`` before pytest applies
    ``-m``, so a module that forgets the mark still stays out of the default
    run (which has no browser)."""
    for item in items:
        if HERE in Path(item.path).parents:
            item.add_marker(pytest.mark.browser)


@pytest.fixture(scope="session", autouse=True)
def _allow_orm_next_to_playwright():
    """Playwright's sync API keeps an asyncio loop running in the test thread
    from its first use until the session ends, and Django refuses ORM calls
    from a thread with a running loop. The live server and database fixtures
    (session scope) need the ORM, so the check is switched off for the whole
    session; it is restored at the end. In a run that mixes browser tests
    with other tests the check therefore stays off for those tests too."""
    mp = pytest.MonkeyPatch()
    mp.setenv("DJANGO_ALLOW_ASYNC_UNSAFE", "true")
    yield
    mp.undo()


@pytest.fixture(scope="session")
def base_url(live_server):
    """pytest-playwright resolves ``page.goto("/path")`` against this URL."""
    return live_server.url


@pytest.fixture
def markdown_flag(settings):
    """Set the ``markdown_descriptions`` feature flag. The live server runs in
    this process, so the change reaches the pages it serves."""

    def set_flag(enabled: bool) -> None:
        settings.SITE_CONFIG = {
            **settings.SITE_CONFIG,
            "features": {
                **settings.SITE_CONFIG["features"],
                "markdown_descriptions": enabled,
            },
        }

    return set_flag


@pytest.fixture
def catalogue_data(transactional_db, settings, tmp_path):
    """Approved services that fill more than one catalogue page and cover the
    layouts that broke before: a long category name, a long unbroken service
    name, a logo, and a service with two PIs, one with a non-ASCII first
    name."""
    settings.MEDIA_ROOT = tmp_path
    short = ServiceCategoryFactory(name="Database")
    long = ServiceCategoryFactory(name=LONG_CATEGORY)
    centre = ServiceCenterFactory(short_name="BiGi", full_name="Bielefeld Centre")
    john = PIFactory(first_name="John", last_name="Doe")
    zoe = PIFactory(first_name="Zoë", last_name="Doe")
    jane = PIFactory(first_name="Jane", last_name="Doe")

    def make(name, cats, pis=(john,), **kw):
        return ServiceSubmissionFactory(
            service_name=name,
            status="approved",
            service_center=centre,
            service_categories=list(cats),
            responsible_pis=list(pis),
            **kw,
        )

    data = {
        "approved": make(
            "Approved Demo Service",
            [short, long],
            website_url="https://example.org/approved-demo",
            logo=SimpleUploadedFile("logo.svg", LOGO_SVG, "image/svg+xml"),
        ),
        "multi_pi": make("Alpha MultiPI Demo Service", [short, long], pis=(zoe, jane)),
        "long_token": make(LONG_TOKEN_NAME, [short]),
        "markdown": make(
            MARKDOWN_NAME, [short], service_description=MARKDOWN_DESCRIPTION
        ),
        "long_category": LONG_CATEGORY,
    }
    for i in range(1, 15):
        make(f"Bulk Demo Service {i:02d}", [long] if i % 3 == 0 else [short])
    ServiceSubmissionFactory(service_name="Submitted Demo Service", status="submitted")
    return data
