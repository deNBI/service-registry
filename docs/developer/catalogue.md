---
icon: material/view-grid
---

# Registry Catalogue Internals

The public catalogue lives in `apps/catalogue/`. It has no models of its own: it reads approved `ServiceSubmission` rows. It is switched on with `[features] catalogue = true` in `config/site.toml`; with the flag off every catalogue URL returns 404 and the navbar link is hidden. Operator settings (`[catalogue]` card fields, page size, meta description) are described in [Configuration](../configuration.md#registry-catalogue).

## Endpoints

| URL                   | View                     | Returns                                                                                          |
| --------------------- | ------------------------ | ------------------------------------------------------------------------------------------------ |
| `/catalogue/`         | `catalogue_view`         | Full page; the grid partial only for an HTMX request that is not a history restore               |
| `/catalogue/grid/`    | `catalogue_grid_view`    | Always the grid partial (`#catalogue-grid` swap target), plus an `HX-Push-Url` header; the full page for a history restore |
| `/catalogue/filters/` | `catalogue_filters_view` | The filter sidebar partial                                                                       |

## Query parameters

`CatalogueQueryParams` (`apps/catalogue/filters.py`) validates every parameter and falls back to the default for anything invalid:

| Parameter  | Values                                                                                  | Default    |
| ---------- | --------------------------------------------------------------------------------------- | ---------- |
| `q`        | Search text, trimmed and cut to 200 characters                                          | empty      |
| `category` | Category id; repeat for several                                                         | none       |
| `center`   | Service centre id; repeat for several                                                   | none       |
| `sort`     | `name_asc`, `name_desc`, `updated_desc`, `updated_asc`, `added_desc`, `added_asc`       | `name_asc` |
| `group_by` | `category`, `service_center`, `pi` or empty                                             | empty      |
| `view`     | `grid`, `list`                                                                          | `grid`     |
| `page`     | Page number                                                                             | `1`        |

Category ids are integers and centre ids are UUIDs; the validator accepts both (`tests/test_catalogue_id_filtering.py`).

## Selectors

`apps/catalogue/selectors.py`:

- `get_approved_services()` filters to `status="approved"` and applies search, filters and sort. Search is `icontains` on the service name, category names, the service centre short and full name, and responsible PI first and last names (a full "First Last" name also matches). `.distinct()` removes duplicates caused by the many-to-many joins. Tests run on SQLite, so no PostgreSQL full-text search is used.
- `group_services()` groups the full queryset, not the current page; pagination is not shown while grouping. A service with several PIs or categories appears under each of them, so per-group counts can add up to more than the number of distinct services.
- `get_filter_options()` builds the sidebar choices.

## HTMX and browser history

- Every search, filter, sort, view and page change requests `/catalogue/grid/` and swaps `#catalogue-grid`.
- `catalogue_grid_view` sets `HX-Push-Url` to the matching `/catalogue/?…` URL, so the browser history holds clean catalogue URLs, never `/catalogue/grid/…`.
- Back and forward send `HX-History-Restore-Request: true`; both views answer it with the full page.
- `templates/catalogue/pages/catalogue.html` sets `historyCacheSize: 0`, so HTMX keeps no snapshots and going back always asks the server.
- The view (grid or list) comes from the URL, so a shared or bookmarked URL shows the same view.

## Descriptions

The views call `prime_descriptions()` for the services they show, which reads all their rendered descriptions from the cache in one request (see [Markdown Rendering](markdown.md)). The list view shows the rendered description; cards show a plain-text snippet.

## Templates and tags

| File                                                | Purpose                                                                      |
| --------------------------------------------------- | ---------------------------------------------------------------------------- |
| `templates/catalogue/pages/catalogue.html`          | Full page: toolbar, filter sidebar, `#catalogue-grid`, inline page script     |
| `templates/catalogue/partials/service_grid.html`    | Grid partial with the active-filter pills                                    |
| `templates/catalogue/partials/service_card.html`    | Card in grid view                                                            |
| `templates/catalogue/partials/service_list_item.html` | Row in list view with the foldable description                             |
| `templates/catalogue/partials/filters.html`         | Collapsible filter sidebar                                                   |
| `apps/catalogue/templatetags/catalogue_tags.py`     | `catalogue_service_url`, `as_query_string`, `highlight` (escapes its input)  |
| `apps/catalogue/templatetags/markdown_tags.py`      | `md_description`, `md_snippet`                                               |

The inline script guards every `localStorage` access (`lsGet` / `lsSet`), binds document-level listeners once, and runs `catalogueInit()` on load and on `htmx:historyRestore`.

## Tests

`tests/test_catalogue_views.py`, `tests/test_catalogue_selectors.py`, `tests/test_catalogue_filters.py`, `tests/test_catalogue_id_filtering.py` and `tests/test_catalogue_markdown_render.py`.
