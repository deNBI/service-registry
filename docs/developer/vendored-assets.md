---
icon: material/package-variant-closed
---

# Vendored Static Assets

All third-party CSS and JavaScript is downloaded once and committed to `static/`, so pages load no files from a CDN. This is a project rule: browser requests to jsDelivr, Google Fonts, unpkg or any other CDN would send visitor data to third parties.

## Current inventory

This is the single inventory of static files; the [Architecture](../architecture.md#static-files) page links here.

**Third-party libraries**

| Asset           | Version | Location                                                                           | Used by                                                                                  |
| --------------- | ------- | ---------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| Bootstrap       | 5.3.3   | `static/css/bootstrap.min.css`, `static/js/bootstrap.bundle.min.js`                | All pages                                                                                |
| HTMX            | 1.9.12  | `static/js/htmx.min.js`                                                            | All public pages (loaded in `base.html`): bio.tools prefill, inline field validation, catalogue; admin submission list |
| Tom-Select      | 2.3.1   | `static/css/tom-select.bootstrap5.min.css`, `static/js/tom-select.complete.min.js` | Affiliation combobox (`submitter_affiliation`, `host_institute`)                         |
| ALTCHA          | 2.3.0   | `static/js/altcha.min.js`                                                          | Registration and edit forms (CAPTCHA widget)                                             |
| swagger-ui-dist | 5.18.2  | `static/swagger-ui/` (4 files)                                                     | `/api/docs/`                                                                             |
| ReDoc           | 2.2.0   | `static/redoc/bundles/redoc.standalone.js`                                         | `/api/redoc/`                                                                            |

**Project files**

| File                                                                       | Used by                                                                                       |
| -------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| `static/css/registry.css`                                                  | All public pages (including the `.md-rendered` description styles)                            |
| `static/js/edam-autocomplete.js`                                           | EDAM topic/operation pickers and the compact select widgets (see [Frontend](frontend.md))     |
| `static/js/markdown-editor.js`                                             | Write / Preview description editor (public forms and admin, when the Markdown flag is on)     |
| `static/js/csrf-token-refresh.js`, `static/js/htmx-csrf-refresh.js`        | Refresh the CSRF token from the live cookie for native form posts and HTMX requests           |
| `static/js/admin_submission_change.js`                                     | Admin submission change form                                                                  |
| `static/admin/css/markdown_preview.css`                                    | Admin description editor preview                                                              |
| `static/admin/css/submissions_filter_sidebar.css`, `static/admin/js/submissions_filter_sidebar.js` | Admin submission list filter sidebar                                     |
| `static/img/favicon.ico`, `static/img/denbi-logo-color.svg`                | Favicon and logo                                                                              |
| `static/img/icons/github.svg`, `biotools.png`, `fairsharing.svg`, `link.svg` | Catalogue link badges (GitHub, bio.tools, FAIRsharing, other registry)                     |

WhiteNoise serves all of these through Gunicorn; `collectstatic` runs when the image is built.

## Updating a library

=== "Bootstrap"

    ```bash
    VERSION=5.3.4
    BASE=https://cdn.jsdelivr.net/npm/bootstrap@${VERSION}/dist
    curl -sSfL ${BASE}/css/bootstrap.min.css -o static/css/bootstrap.min.css
    curl -sSfL ${BASE}/js/bootstrap.bundle.min.js -o static/js/bootstrap.bundle.min.js
    ```

=== "HTMX"

    ```bash
    VERSION=1.9.13
    curl -sSfL https://unpkg.com/htmx.org@${VERSION}/dist/htmx.min.js -o static/js/htmx.min.js
    ```

=== "Tom-Select"

    ```bash
    VERSION=2.4.1
    BASE=https://cdn.jsdelivr.net/npm/tom-select@${VERSION}/dist
    curl -sSfL ${BASE}/css/tom-select.bootstrap5.min.css -o static/css/tom-select.bootstrap5.min.css
    curl -sSfL ${BASE}/js/tom-select.complete.min.js -o static/js/tom-select.complete.min.js
    ```

=== "swagger-ui-dist"

    ```bash
    VERSION=5.18.3
    BASE=https://cdn.jsdelivr.net/npm/swagger-ui-dist@${VERSION}
    for f in swagger-ui.css swagger-ui-bundle.js swagger-ui-standalone-preset.js favicon-32x32.png; do
        curl -sSfL ${BASE}/${f} -o static/swagger-ui/${f}
    done
    ```

    Then update the version comment in `config/settings.py`.

=== "ReDoc"

    ```bash
    VERSION=2.2.0
    curl -sSfL https://cdn.jsdelivr.net/npm/redoc@${VERSION}/bundles/redoc.standalone.js \
        -o static/redoc/bundles/redoc.standalone.js
    ```

=== "ALTCHA"

    ```bash
    VERSION=2.3.0
    curl -sSfL https://cdn.jsdelivr.net/gh/altcha-org/altcha@v${VERSION}/dist/altcha.min.js \
        -o static/js/altcha.min.js
    ```

    The version comment at the top of the downloaded file confirms which release was
    fetched.  Update the version entry in the inventory table above when upgrading.

## Can I use an external URL instead of vendoring?

| Asset type                                 | External URL OK? | Notes                                                                          |
| ------------------------------------------ | ---------------- | ------------------------------------------------------------------------------ |
| **Logo** (`logo_url` in `site.toml`)       | Yes              | CSP `img-src` is built dynamically from this URL                               |
| **Favicon** (`favicon_url` in `site.toml`) | Yes              | Same dynamic CSP behaviour                                                     |
| **JS/CSS frameworks**                      | **No**           | Would make browser requests to third-party CDNs (see the project rule above)   |
| **Swagger UI / ReDoc**                     | **No**           | drf-spectacular defaults to jsDelivr; we override to `/static/`. Do not revert |

## Checking for CDN leakage

Open browser DevTools → Network tab. All requests should resolve to `localhost` in dev or your own domain in prod. Any CDN request will also violate `default-src 'self'` and appear as a blocked request in the browser console.
