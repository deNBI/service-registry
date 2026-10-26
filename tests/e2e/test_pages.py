"""Public pages in a real browser: they load without script errors, fit the
viewport on desktop and mobile, meet basic accessibility checks, and their
links resolve."""

from urllib.parse import urlencode, urljoin, urlparse

import pytest
from playwright.sync_api import expect

from tests.e2e.conftest import LONG_TOKEN_NAME, MARKDOWN_NAME

pytestmark = pytest.mark.browser

PAGES = ["/", "/catalogue/", "/register/", "/update/"]
MARKDOWN_VIEWS = [
    "/catalogue/?" + urlencode({"q": MARKDOWN_NAME}),
    "/catalogue/?" + urlencode({"q": MARKDOWN_NAME, "view": "list"}),
]
LONG_NAME_VIEWS = ["/catalogue/?q=longtoken", "/catalogue/?q=longtoken&view=list"]
ALL_VIEWS = PAGES + MARKDOWN_VIEWS + LONG_NAME_VIEWS
WIDTHS = [(1280, 900), (390, 844)]
API_DOCS = ["/api/docs/", "/api/redoc/", "/api/schema/"]


def open_page(page, path):
    """Load ``path`` with console listeners attached first; return the HTTP
    status and every console error or uncaught exception."""
    errors = []
    page.on(
        "console",
        lambda m: errors.append(f"{m.text} ({m.location.get('url')})")
        if m.type == "error"
        else None,
    )
    page.on("pageerror", lambda e: errors.append(f"uncaught: {e}"))
    response = page.goto(path, wait_until="networkidle")
    return response.status, errors


def overflow_px(page):
    return page.evaluate(
        "() => document.documentElement.scrollWidth - window.innerWidth"
    )


@pytest.mark.parametrize(
    "path,name",
    [(v, MARKDOWN_NAME) for v in MARKDOWN_VIEWS]
    + [(v, LONG_TOKEN_NAME) for v in LONG_NAME_VIEWS],
)
def test_search_views_show_the_service_they_check(catalogue_data, page, path, name):
    """The overflow, alt and h1 checks on these views are only meaningful if
    the searched service is on the page."""
    page.goto(path, wait_until="networkidle")
    expect(page.locator("#catalogue-grid").get_by_text(name, exact=True)).to_have_count(
        1
    )


@pytest.mark.parametrize("path", ALL_VIEWS)
def test_page_loads_without_console_errors(catalogue_data, page, path):
    status, errors = open_page(page, path)
    assert (status, errors) == (200, [])


@pytest.mark.parametrize("path", MARKDOWN_VIEWS)
def test_markdown_views_load_without_console_errors_with_the_flag_on(
    page, catalogue_data, markdown_flag, path
):
    markdown_flag(True)
    status, errors = open_page(page, path)
    assert (status, errors) == (200, [])


@pytest.mark.parametrize("width,height", WIDTHS)
@pytest.mark.parametrize("path", ALL_VIEWS)
def test_no_horizontal_overflow(catalogue_data, page, path, width, height):
    page.set_viewport_size({"width": width, "height": height})
    page.goto(path, wait_until="networkidle")
    assert overflow_px(page) <= 1


@pytest.mark.parametrize("width,height", WIDTHS)
@pytest.mark.parametrize("path", MARKDOWN_VIEWS)
def test_no_horizontal_overflow_with_the_markdown_flag_on(
    page, catalogue_data, markdown_flag, path, width, height
):
    markdown_flag(True)
    page.set_viewport_size({"width": width, "height": height})
    page.goto(path, wait_until="networkidle")
    assert overflow_px(page) <= 1


@pytest.mark.parametrize("path", ALL_VIEWS)
def test_images_have_alt(catalogue_data, page, path):
    page.goto(path, wait_until="networkidle")
    missing = page.evaluate(
        "() => [...document.images].filter(i => !i.hasAttribute('alt')).map(i => i.src)"
    )
    assert missing == []


def test_the_logo_fixture_is_on_the_catalogue(catalogue_data, page):
    """Keeps the alt check meaningful: a service logo is rendered."""
    page.goto("/catalogue/?q=Approved+Demo", wait_until="networkidle")
    logo = page.locator(".catalogue-card img[src*='/media/logos/']")
    expect(logo).to_have_count(1)
    assert logo.evaluate("img => img.complete && img.naturalWidth") == 10


@pytest.mark.parametrize("path", ALL_VIEWS)
def test_single_h1(catalogue_data, page, path):
    page.goto(path, wait_until="networkidle")
    assert page.locator("h1").count() == 1


def test_same_origin_links_resolve(catalogue_data, page, base_url):
    hrefs = set()
    for path in ALL_VIEWS + ["/catalogue/?page=2"]:
        page.goto(path, wait_until="networkidle")
        hrefs.update(
            page.evaluate(
                "() => [...document.querySelectorAll('a[href]')].map(a => a.getAttribute('href'))"
            )
        )
    host = urlparse(base_url).netloc
    urls = sorted(
        {
            urljoin(base_url + "/", h).split("#")[0]
            for h in hrefs
            if h and not h.startswith(("mailto:", "tel:", "javascript:", "#"))
        }
    )
    urls = [u for u in urls if urlparse(u).netloc == host]
    paths = {urlparse(u).path for u in urls}
    assert {"/", "/catalogue/", "/register/", "/update/", "/api/docs/"} <= paths
    statuses = [(u, page.request.get(u).status) for u in urls]
    assert [(u, s) for u, s in statuses if s >= 400] == []


@pytest.mark.parametrize("path", API_DOCS)
def test_api_docs_reachable(transactional_db, page, base_url, path):
    assert page.request.get(base_url + path).status == 200


def test_register_page_with_captcha_loads_without_console_errors(
    page, transactional_db, settings
):
    settings.ALTCHA_HMAC_KEY = "test-only-key"
    status, errors = open_page(page, "/register/")
    assert (status, errors) == (200, [])
    expect(page.locator("altcha-widget")).to_have_count(1)


def test_footer_github_link(catalogue_data, page):
    page.goto("/catalogue/", wait_until="networkidle")
    link = page.locator('footer a[href="https://github.com/deNBI/service-registry"]')
    expect(link).to_have_count(1)
    expect(link).to_be_visible()


@pytest.mark.parametrize(
    "typed,found,not_found",
    [
        ("John Doe", "approved", "multi_pi"),
        ("Zoë Doe", "multi_pi", "approved"),
    ],
)
def test_live_search_finds_pi_service(catalogue_data, page, typed, found, not_found):
    page.goto("/catalogue/", wait_until="networkidle")
    grid = page.locator("#catalogue-grid")
    other = catalogue_data[not_found].service_name
    expect(grid.get_by_text(other, exact=True)).to_be_visible()
    box = page.locator("#catalogue-search")
    box.click()
    box.press_sequentially(typed, delay=30)
    expect(page).to_have_url("/catalogue/?" + urlencode({"q": typed}))
    expect(grid.get_by_text(other, exact=True)).to_have_count(0)
    name = catalogue_data[found].service_name
    expect(grid.get_by_text(name, exact=True)).to_be_visible()


@pytest.mark.parametrize("width,height", WIDTHS)
def test_long_category_pill_stays_in_card(catalogue_data, page, width, height):
    page.set_viewport_size({"width": width, "height": height})
    page.goto("/catalogue/?q=Approved+Demo", wait_until="networkidle")
    card = page.locator(".catalogue-card").filter(has_text="Approved Demo Service")
    expect(card).to_have_count(1)
    pill = card.get_by_text(catalogue_data["long_category"], exact=True)
    expect(pill).to_be_visible()
    pill_box, card_box = pill.bounding_box(), card.bounding_box()
    assert card_box["x"] - 1 <= pill_box["x"]
    assert pill_box["x"] + pill_box["width"] <= card_box["x"] + card_box["width"] + 1


def test_card_links_to_service_website(catalogue_data, page):
    page.goto("/catalogue/?q=Approved+Demo", wait_until="networkidle")
    card = page.locator(".catalogue-card").filter(has_text="Approved Demo Service")
    expect(
        card.locator('a[href="https://example.org/approved-demo"]')
    ).not_to_have_count(0)


def test_register_builds_a_picker_for_every_hidden_select(transactional_db, page):
    page.goto("/register/", wait_until="networkidle")
    expect(page.locator("#edam-section select.edam-autocomplete")).to_have_count(2)
    expect(page.locator("#edam-section input.ep-input")).to_have_count(2)
    # The licence field reuses the same picker outside the EDAM section.
    expect(page.locator("select.edam-autocomplete")).to_have_count(3)
    expect(page.locator("input.ep-input")).to_have_count(3)


@pytest.mark.parametrize(
    "enabled,bold_tags,literal_stars",
    [(True, 1, False), (False, 0, True)],
    ids=["flag-on", "flag-off"],
)
def test_markdown_description_in_list_view(
    page, catalogue_data, markdown_flag, enabled, bold_tags, literal_stars
):
    markdown_flag(enabled)
    page.goto(MARKDOWN_VIEWS[1], wait_until="networkidle")
    desc = page.locator(".catalogue-list-desc-text")
    expect(desc).to_have_count(1)
    expect(desc.locator("strong", has_text="Bold")).to_have_count(bold_tags)
    assert ("**Bold**" in desc.text_content()) is literal_stars
    expect(desc).to_contain_text("x > 5")
    expect(desc).to_contain_text("List<String>")


def test_card_shows_the_description_as_plain_text(catalogue_data, page, markdown_flag):
    markdown_flag(True)
    page.goto(MARKDOWN_VIEWS[0], wait_until="networkidle")
    snippet = (
        page.locator(".catalogue-card")
        .filter(has_text=MARKDOWN_NAME)
        .locator(".catalogue-description")
    )
    expect(snippet).to_have_count(1)
    expect(snippet).to_contain_text("Bold and italic text.")
    expect(snippet.locator("strong")).to_have_count(0)
    expect(snippet).not_to_contain_text("**")
