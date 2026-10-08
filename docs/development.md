---
icon: material/code-braces
---

# Development Setup

## Quick start

Everything you need to go from a fresh clone to a running local stack.

### 1. Prerequisites

| Tool                    | Minimum version | Install                                                                                                                                          |
| ----------------------- | --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| Docker Engine + Compose | 24 / v2         | [docs.docker.com](https://docs.docker.com/engine/install/)                                                                                       |
| Git                     | any             | system package                                                                                                                                   |
| Conda / Miniforge       | any             | [github.com/conda-forge/miniforge](https://github.com/conda-forge/miniforge) — only needed for local Python work (tests, linting) without Docker |

### 2. Clone and configure

```bash
git clone https://github.com/deNBI/service-registry
cd service-registry
cp .env.example .env
```

Open `.env` and set at minimum:

```bash
SECRET_KEY=any-long-random-string    # generate with: python -c "import secrets; print(secrets.token_hex(50))"
DB_PASSWORD=devpassword
REDIS_PASSWORD=devpassword
```

All other values in `.env.example` have safe defaults for local development.

### 3. Build and start

```bash
make build    # builds Docker images from scratch (no cache)
make dev      # starts web + worker + beat + db + redis
```

!!! info "Migrations run automatically"
The container entrypoint runs `manage.py migrate` before starting. On a fresh database this also auto-seeds the EDAM ontology (~3 400 terms, ~30 s). No manual migrate step needed.

### 4. Create a superuser

```bash
make superuser
```

### 5. Access the app

| URL                                | What                              |
| ---------------------------------- | --------------------------------- |
| http://localhost:8000              | Public registration form          |
| http://localhost:8000/admin-denbi/ | Admin portal (superuser login)    |
| http://localhost:8000/api/docs/    | Interactive API docs (Swagger UI) |
| http://localhost:8000/api/redoc/   | ReDoc API reference               |

---

## Day-to-day workflow

### Starting and stopping

```bash
make dev          # start stack (migrations run automatically on first start)
make dev-down     # stop stack (volumes preserved — data survives)
make logs         # tail all service logs
```

### After changing a model

Migrations must be generated **locally** (not inside the container) because the container's non-root user cannot write migration files back to the bind-mounted source tree:

```bash
# 1. Generate the migration file (runs in your local conda env)
make makemigrations

# 2. Apply it to the running dev database
make migrate
```

Commit the generated migration file alongside your model changes.

### Full clean reset

Wipes all containers, volumes, and data then rebuilds from scratch:

```bash
make nuke
```

Use this when you want a guaranteed clean state — e.g. after pulling migrations that conflict with your local DB, or when debugging a migration issue.

### Running the test suite

Tests use SQLite in-memory and a local-memory cache — no Docker or external services required:

```bash
make test          # pytest — must stay ≥ 80% coverage
make test-cov      # pytest + HTML report → open htmlcov/index.html
```

Or activate the conda environment first and run pytest directly:

```bash
conda activate denbi-registry
pytest tests/ -v --tb=short
```

### Linting and formatting

```bash
make lint          # ruff check + format check (read-only)
make lint-fix      # auto-fix all fixable issues
make audit         # pip-audit against production requirements
make typecheck     # mypy
```

---

## Make targets reference

**Development**

| Target                | What it does                                                      |
| --------------------- | ----------------------------------------------------------------- |
| `make build`          | Rebuild all Docker images with `--no-cache`                       |
| `make dev`            | Start full dev stack (web + worker + beat + db + redis)           |
| `make dev-down`       | Stop the dev stack (data preserved)                               |
| `make logs`           | Tail all dev stack logs                                           |
| `make migrate`        | Run pending migrations in the running `web` container             |
| `make makemigrations` | Generate new migration files locally (needed after model changes) |
| `make superuser`      | Create a Django superuser                                         |
| `make shell`          | Open Django `shell_plus` in the `web` container                   |
| `make collectstatic`  | Collect static files into the container                           |

**Testing and quality** (requires `pip install -r requirements/development.txt`)

| Target           | What it does                                                |
| ---------------- | ----------------------------------------------------------- |
| `make test`      | pytest with SQLite in-memory — no Docker needed             |
| `make test-cov`  | pytest + HTML coverage report (`htmlcov/`)                  |
| `make lint`      | ruff check + format check                                   |
| `make lint-fix`  | Auto-fix ruff lint and formatting issues                    |
| `make audit`     | `pip-audit` against production requirements                 |
| `make typecheck` | Run mypy type checker                                       |
| `make dead-code` | `vulture` dead-code detection (unused functions, variables) |
| `make security`  | `bandit` SAST security scan (medium + high severity)        |

**Documentation**

| Target            | What it does                                       |
| ----------------- | -------------------------------------------------- |
| `make docs`       | Serve MkDocs locally at http://127.0.0.1:8001      |
| `make docs-build` | Build static MkDocs site into `site/` (`--strict`) |

**Production**

| Target              | What it does                                    |
| ------------------- | ----------------------------------------------- |
| `make prod-up`      | Start production stack (compose + prod overlay) |
| `make prod-down`    | Stop production stack                           |
| `make prod-migrate` | Run migrations in the production web container  |
| `make prod-logs`    | Tail production logs                            |

**Cleanup**

| Target       | What it does                                                                                       |
| ------------ | -------------------------------------------------------------------------------------------------- |
| `make clean` | Stop containers + remove all volumes — **permanently deletes DB data**, prompts for confirmation   |
| `make nuke`  | Full reset: `clean` → `build` → `dev` → wait for migrations — one command to a fresh working stack |

---

## Conda environment (for local Python work)

The conda environment is used for tests, linting, and generating migrations — tasks where you want a fast feedback loop without Docker.

```bash
conda create -n denbi-registry python=3.12
conda activate denbi-registry
pip install -r requirements/development.txt
```

Point Django at the test settings for anything that needs Django but not a real database:

```bash
export DJANGO_SETTINGS_MODULE=config.settings_test
export SECRET_KEY=any-value
export DB_PASSWORD=any-value
export REDIS_PASSWORD=any-value
```

---

## Project layout

```
denbi_service_registry/
├── apps/
│   ├── api/          — DRF viewsets, serializers, authentication
│   ├── biotools/     — bio.tools HTTP client, sync, signal, Celery tasks
│   ├── edam/         — EDAM ontology model, sync management command
│   ├── registry/     — Reference data (PIs, categories, service centres)
│   └── submissions/  — Core model, registration form, views, admin, diff_utils
├── config/
│   ├── settings.py       — Main Django settings
│   ├── settings_test.py  — Test overrides (SQLite, no Redis)
│   ├── celery.py         — Celery app definition
│   └── site.toml         — Non-secret site configuration
├── docs/             — MkDocs documentation source
├── nginx/host/       — Host nginx vhost configuration
├── requirements/     — base.txt, production.txt, development.txt
├── scripts/
│   └── entrypoint.sh — Docker entrypoint: runs migrate before CMD
├── static/           — Vendored static assets (Bootstrap, HTMX, Tom-Select, swagger-ui, redoc)
├── templates/        — Django HTML templates
└── tests/            — pytest test suite
```

---

## Vendored static assets

All third-party CSS and JavaScript is downloaded once and committed to `static/`. No CDN is contacted at runtime. This is a hard requirement for GDPR compliance — browser requests to jsDelivr, Google Fonts, unpkg, or any other CDN would constitute data transfers to third parties without user consent.

### Current inventory

| Asset           | Version | Location                                                                           | Used by                                        |
| --------------- | ------- | ---------------------------------------------------------------------------------- | ---------------------------------------------- |
| Bootstrap       | 5.3.3   | `static/css/bootstrap.min.css`, `static/js/bootstrap.bundle.min.js`                | All pages                                      |
| HTMX            | 1.9.12  | `static/js/htmx.min.js`                                                            | bio.tools prefill                              |
| Tom-Select      | 2.3.1   | `static/css/tom-select.bootstrap5.min.css`, `static/js/tom-select.complete.min.js` | EDAM multi-select widget, affiliation combobox |
| ALTCHA          | 2.3.0   | `static/js/altcha.min.js`                                                          | Registration and edit forms (CAPTCHA widget)   |
| swagger-ui-dist | 5.18.2  | `static/swagger-ui/` (4 files)                                                     | `/api/docs/`                                   |
| ReDoc           | 2.2.0   | `static/redoc/bundles/redoc.standalone.js`                                         | `/api/redoc/`                                  |
| de.NBI favicon  | —       | `static/img/favicon.ico`                                                           | All pages, admin                               |

### Updating a library

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

### Can I use an external URL instead of vendoring?

| Asset type                                 | External URL OK? | Notes                                                                          |
| ------------------------------------------ | ---------------- | ------------------------------------------------------------------------------ |
| **Logo** (`logo_url` in `site.toml`)       | Yes              | CSP `img-src` is built dynamically from this URL                               |
| **Favicon** (`favicon_url` in `site.toml`) | Yes              | Same dynamic CSP behaviour                                                     |
| **JS/CSS frameworks**                      | **No**           | Would make browser requests to third-party CDNs — GDPR violation               |
| **Swagger UI / ReDoc**                     | **No**           | drf-spectacular defaults to jsDelivr; we override to `/static/`. Do not revert |

### Checking for CDN leakage

Open browser DevTools → Network tab. All requests should resolve to `localhost` in dev or your own domain in prod. Any CDN request will also violate `default-src 'self'` and appear as a blocked request in the browser console.

---

## Custom template tags

Custom template tags and filters live in
`apps/submissions/templatetags/registry_tags.py` and are loaded in templates with
`{% load registry_tags %}`.

### Available filters

#### `linkify_description`

Renders a section description string from `form_texts.yaml` as safe HTML.
Use this filter (not Django's built-in `urlize`) for all section description output.

| Input syntax                                 | Output                                        |
| -------------------------------------------- | --------------------------------------------- |
| `[link text](https://example.com)`           | `<a href="https://example.com">link text</a>` |
| `https://example.com`                        | auto-linked anchor                            |
| Blank line (`\n\n`)                          | paragraph break — wraps each block in `<p>`   |
| Single newline (`\n`, using YAML `\|` block) | `<br>`                                        |
| Raw `<html>`                                 | escaped — never rendered as markup            |

Only `http://` and `https://` schemes are accepted for links. `javascript:` and other
schemes in `[text](...)` syntax are not matched and pass through as escaped plain text.

**Template usage:**

```django
{% load registry_tags %}
<div class="section-description">{{ desc|linkify_description }}</div>
```

**Extending or testing the filter:**

The filter is unit-tested in `tests/test_template_tags.py` (`TestLinkifyDescriptionFilter`).
Add a new test there whenever you extend the filter's behaviour.
Integration tests that render `form_body.html` or `register.html` with patched YAML
data live in `tests/test_forms.py` (`TestSectionDescriptionsYAML`).

**Styling:**

`.section-description` is styled in `static/css/registry.css` as a light tinted callout
box — subtle `rgba(0,0,0,0.03)` background with rounded corners and muted text — to
visually distinguish it from form input labels and fields. If you change the style, keep
the distinction clear: the description is contextual guidance, not an actionable form
element.

---

## Markdown service descriptions (`markdown_render`)

`service_description` can be rendered as Markdown when `[features] markdown_descriptions` is on (see [Configuration](configuration.md#feature-flags)). `linkify_description` above is unrelated: it only formats section descriptions from `form_texts.yaml`.

All rendering goes through one module, `apps/submissions/markdown_render.py`, used by the catalogue, the API, the editor preview and the admin, so output is identical everywhere:

| Function                              | Purpose                                                                                                   |
| ------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| `render_markdown(text)`               | Markdown to sanitized `SafeString` (flag-independent, uncached)                                            |
| `render_with_notice(text)`            | `(html, removed)` for the preview; `removed` is true when sanitization dropped content                     |
| `markdown_to_text(text, limit=300)`   | Plain-text snippet derived from `render_markdown()` output, so cards show the same text as the list view   |
| `render_submission_description(sub)`  | Flag-aware, cached HTML. Flag off returns the entity-decoded plain string (not safe) so templates autoescape it once |
| `submission_description_snippet(sub)` | Flag-aware, cached plain-text snippet (flag off: entity-decoded plain string)                              |
| `decode_legacy_entities(text)`        | `html.unescape` applied once: the one rule every plain-text display surface uses for legacy rows           |
| `markdown_enabled()`                  | Reads the feature flag from `SITE_CONFIG`                                                                  |

**Pipeline:** [markdown-it-py](https://markdown-it-py.readthedocs.io/) (CommonMark) with raw HTML off, `breaks` on (a single newline is a line break), `<url>` autolinks disabled and a `shift_headings` core rule, then `bleach.clean` with an allowlist, then anchor hardening, then removal of paragraphs left empty, then `mark_safe`. The parser instance is built once at import (`_PARSER`); rendering keeps all state per call. Descriptions are stored raw and sanitized on output only.

- **Why markdown-it-py:** it is linear-time and caps nesting (`MAX_NESTING = 20`), so every input up to `DESCRIPTION_MAX_LENGTH` renders in well under a second. Python-Markdown, used before, took minutes on 5,000 backticks and raised `RecursionError` on deeply nested lists, which made the public preview endpoint a denial-of-service vector. `tests/test_markdown_robustness.py` checks such inputs scale linearly (a ratio, not a wall-clock budget, so it holds under coverage and load). Content nested deeper than the cap is not rendered and is reported as removed.
- **Link destinations:** `validateLink` accepts every URL so that the bleach href filter below is the only place that decides; markdown-it's own check would leave a blocked link as literal `[x](javascript:...)` text.
- **Headings** are shifted down three levels so they never compete with the page's own headings: `#` becomes `h4`, `##` `h5`, `###` and deeper `h6`. The allowlist is `p`, `br`, `strong`, `em`, `ul`, `ol`, `li`, `a`, `blockquote`, `h4`, `h5`, `h6`.
- **Ordered lists** keep a numeric `start` attribute (`_allowed_ol_attr`, up to 9 digits), so `2024. Launched` is numbered from 2024.
- **Strict href filter:** `a` keeps only `title` and an `href` that starts with an explicit `http:`, `https:` or `mailto:` scheme (`_allowed_a_attr`). bleach's protocol check alone would let scheme-less values such as `/x`, `//host` or `#frag` through, so relative links are dropped too.
- **Anchor hardening** (`_harden_anchors`): an anchor whose `href` was dropped is unwrapped to its text, and so is an anchor with no visible text (tags stripped, entities decoded, whitespace including U+00A0 trimmed, zero-width characters such as U+200B removed; for example a README badge whose image was stripped). Kept anchors get `rel="nofollow noopener noreferrer"`; only `http`/`https` anchors also get `target="_blank"`, `mailto` anchors do not.
- `render_with_notice()` reports `removed` when a Markdown-generated anchor or tag did not survive sanitization (images, code, horizontal rules, dropped links) or when the nesting cap skipped content. Headings are allowed, so they never trigger it.
- **Line endings:** browsers submit textareas with CRLF, JSON clients send LF. `normalize_newlines()` (`apps/submissions/validation.py`) is applied when saving (`_sanitise_text`), when measuring the description length (form, model, API and preview, so a line break is one character on every channel and in the editor counter) and when diffing (`snapshot()`), so an untouched description is never reported as changed and never resets an approved service on a web edit.
- **Legacy entities:** rows saved by the old web form hold HTML entities (`x &gt; 5`). Markdown decodes them itself; every other display surface (flag-off catalogue and API, emails) goes through `decode_legacy_entities()`, so all surfaces agree in both flag states. Source views (admin textarea, raw API field, exports) show the stored value.

**Caching:** the flag-aware helpers cache per `(pk, updated_at)` for 24 hours (`MD_CACHE_TTL`). The key also contains `RENDER_FINGERPRINT`, a hash of `markdown_render.py` itself plus the installed markdown-it-py, mdurl and bleach versions, computed at import. Any change to the rules (they all live in that module) or a library upgrade therefore invalidates cached HTML automatically, and during a rolling deploy old and new workers never share entries. Every write of a description goes through `save()`, which bumps `updated_at`, so an edit is never served stale. The card snippet is derived from the cached list HTML (one Markdown parse per edit). Cached values are the sanitized HTML and are trusted on read (`mark_safe`), which relies on Redis being private: it is password-protected and not published on a host port; keep it that way.

**API:** `SubmissionDetailSerializer.get_service_description_html` returns `conditional_escape(render_submission_description(obj))`: the sanitized `SafeString` as is when the flag is on, and the entity-decoded plain text escaped exactly once when it is off.

**Template filters** (`apps/catalogue/templatetags/markdown_tags.py`, `{% load markdown_tags %}`):

```django
{% with description=service|md_description %}{% if description %}          {# list view #}
<div class="catalogue-list-desc-text md-rendered">{{ description }}</div>
{% endif %}{% endwith %}
<p class="catalogue-description">{{ service|md_snippet }}</p>              {# card #}
```

The list view hides the description toggle when nothing renders (for example an image-only description). **Styling:** `static/css/registry.css` has one `.md-rendered` rule set, used by both the catalogue list view and the editor preview, with heading sizes in `em` so the preview matches the catalogue at either base font size; `static/admin/css/markdown_preview.css` mirrors the same scale with admin theme colours.

**Preview endpoint:** `markdown_preview_view` (`POST /markdown-preview/`, URL name `submissions:markdown-preview`) renders `templates/submissions/partials/markdown_preview.html` from the posted `service_description`, NFC-normalised and stripped exactly as `clean_service_description` does, so the length check matches the form. It returns 404 when the flag is off. Blank input gives "Nothing to preview."; input over `DESCRIPTION_MAX_LENGTH` gives "Description is too long to preview."; otherwise the rendered HTML, preceded by a "Some formatting was removed" notice when `render_with_notice()` reports `removed`. It uses the same `RATE_LIMIT_VALIDATE` limit as inline field validation (separate counter) and does not block: a throttled request gets a 200 fragment with "Too many previews. Please wait a moment and try again." Error fragments use `role="alert"`, which the editor script uses to avoid caching them.

**Editor:** the Write | Preview editor is one partial, `templates/submissions/partials/markdown_editor_box.html`, shared by the public forms and the admin:

- Public forms: `form_body.html` includes `markdown_editor.html` for `service_description` when the flag is on (otherwise the plain `field.html`). `markdown_editor.html` wraps the box with the same `field_label.html` and `field_feedback.html` partials that `field.html` uses; the box includes `field_widget.html`, so the textarea keeps its inline-validation wrapper. `field.html` is composed of those partials (the fieldset branch keeps its own `<legend>` and reuses `field_widget.html` and `field_feedback.html`), with unchanged output. `form_scripts.html` loads `js/markdown-editor.js` when the flag is on.
- Admin: `MarkdownTextareaWidget` (`apps/submissions/widgets.py`) renders the admin textarea and passes it to the box as `widget_html` (it must be non-empty; an empty value falls back to the `field` include). Its `Media` ships `admin/css/markdown_preview.css` and `js/markdown-editor.js`. `ServiceSubmissionAdmin.get_form` swaps the widget in per request when the flag is on, keeping the existing textarea attrs.
- Box contract: the root carries `data-md-editor` and `data-md-preview-url`; tabs are `[role=tab][data-md-tab=write|preview]`, panels `[data-md-panel=write|preview]` (the preview panel is focusable with `tabindex="0"` and deliberately has no `aria-live`); the counter is `[data-md-counter]` with `data-min`/`data-max` from the `{% description_length_limits %}` tag in `registry_tags` (the single source for `DESCRIPTION_MIN_LENGTH`/`DESCRIPTION_MAX_LENGTH`); the help toggle is `[data-md-help-toggle]` and opens `markdown_help.html`. The tab strip and footer ship with `hidden` and `data-md-enhance`, so without JavaScript only the textarea shows.
- `static/js/markdown-editor.js` reveals those controls and initialises every `[data-md-editor]` on load and after `htmx:afterSettle` (`window.MarkdownEditor.init(root)` is also exposed). Tabs follow the WAI-ARIA tabs pattern with automatic activation (Left/Right/Home/End). Selecting Preview POSTs the text with `fetch` (CSRF token from the `csrftoken` cookie, falling back to the form field), showing "Loading preview…" meanwhile; if the text is unchanged since the last successful render, the cached HTML is reused without a request. Stale responses are discarded by a sequence counter. The counter counts like the server (LF line endings, so a line break is one character; NFC, Python `str.strip()` whitespace, code points): neutral at 0, `is-error` with "at least N characters" below the minimum, `is-warn` from 90% of the maximum, `is-error` above it.

**Plain-text emails:** render every `.txt` email body with `render_plaintext(template_name, context)` from `apps/submissions/tasks.py`, not `render_to_string()`. It disables autoescaping, so the raw Markdown (and characters such as `&`, `<`, `>`, `'`) arrive unescaped. HTML email bodies keep using `render_to_string()` and stay escaped. Both are rendered from `_decoded_description_context(context)`: it adds `service_description_text` (used by `notification.txt`) and runs the description's before/after values in `changes` through `decode_legacy_entities()`, so legacy rows show `x > 5`, not `x &gt; 5`, in both parts, matching the catalogue and the API. Other fields are left as stored.

**Tests:** `tests/test_markdown_render.py` (pipeline), `tests/test_markdown_robustness.py` (linear scaling on hostile max-length input, nesting cap, ordered-list start), `tests/test_markdown_output_matrix.py` (exact output of every surface x flag state x input class), `tests/test_markdown_consistency.py` (XSS and cross-surface parity), `tests/test_markdown_behaviour.py` (storage, edit status reset, template guards), `tests/test_markdown_tags.py`, `tests/test_catalogue_markdown_render.py`, `tests/test_markdown_preview_view.py`, `tests/test_markdown_editor_render.py`, `tests/test_markdown_input_unification.py`, `tests/test_field_partials.py`, `tests/test_api_markdown.py`, `tests/test_admin_markdown.py`, `tests/test_audit_markdown_command.py`, `tests/test_export_formula_guard.py` and `tests/test_email_plaintext.py`.

---

## Field-level diff (`diff_utils`)

`apps/submissions/diff_utils.py` computes a human-readable before/after diff for any submission save. It is used by all three edit paths — the submitter web form (`views.py`), the admin backend (`admin.py`), and the REST API (`api/views.py`).

### Key functions

| Function                    | Purpose                                                                                                              |
| --------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `snapshot(instance)`        | Returns a `dict` of current scalar field values; **must be called before** `form.is_valid()` or admin `save_model()` |
| `snapshot_m2m(instance)`    | Returns a `dict` of current M2M field values as sorted lists of strings                                              |
| `build_diff(before, after)` | Compares two snapshots; returns `[{field, label, old, new}]` for changed fields only                                 |
| `filter_sanitization_artifacts(changes, form_changed_data, form_field_names)` | Removes false-positive diff entries where form sanitization (bleach, NFC normalisation, whitespace stripping) produced a raw difference that resolved to the same stored value. Always applied after `build_diff()` in the web-form edit path. |

### Snapshot timing

Django's `ModelForm._post_clean()` calls `construct_instance()` during `is_valid()`, which writes POST data onto `form.instance` **before** `form.save()` is called. Always take the `snapshot()` and `snapshot_m2m()` calls before constructing the form:

```python
before_scalar = snapshot(submission)          # ← before form construction
before_m2m    = snapshot_m2m(submission)
form = SubmissionForm(request.POST, instance=submission)
form.is_valid()                               # ← would corrupt before-state if snapshot taken here
form.save()
after_scalar = snapshot(submission)
after_m2m    = snapshot_m2m(submission)
changes = build_diff({**before_scalar, **before_m2m}, {**after_scalar, **after_m2m})
```

In the admin, `save_model()` receives `obj` already populated with new form values — re-fetch the original from the database for the before-snapshot:

```python
original = obj.__class__.objects.get(pk=obj.pk)
before_scalar = snapshot(original)
```

DRF serializers do **not** modify `self.instance` during `is_valid()`, so the snapshot can safely be taken before serializer construction.

### Adding a diffable field

1. Add an entry to `DIFFABLE_FIELDS` (scalar) or `DIFFABLE_M2M` in `diff_utils.py`
2. If the field uses `choices`, add it to `_CHOICE_FIELDS` so `get_FOO_display()` is used
3. Add a test to `tests/test_diff_utils.py`

### Sanitization artifacts

Form `clean_*` methods (bleach, `unicodedata.normalize`, `.strip()`) may produce a cleaned value identical to the stored value even when the raw POST string differed slightly (trailing spaces, NFC variants). `build_diff()` compares snapshots of the **stored model value** before and after save, so such non-changes do not appear in the diff.

`filter_sanitization_artifacts()` provides a second layer: it removes any diff entry where the field is managed by the form **and** is absent from `form.changed_data`. It is called after `build_diff()` in the web-form path before persisting `last_change_summary`.

**Status reset and sanitization:** `EditView` determines whether to reset status using a *prospective diff* — it compares `before_scalar` against `snapshot(updated)` taken from the in-memory instance after `form.save(commit=False)`. Because `_post_clean()` has already applied `clean_*` normalization at that point, sanitization artifacts never appear in the prospective diff and cannot trigger a false status reset.

---

## Logo upload security pipeline

Uploaded service logos pass through `apps/submissions/logo_utils.py` before being
stored. Understanding this module is useful when modifying file upload handling.

### Entry point

```python
from apps.submissions.logo_utils import validate_and_process_logo

result = validate_and_process_logo(file_obj)  # → InMemoryUploadedFile
```

### Processing steps (in order)

| Step                 | What happens                                                                                                                                                                                                                     |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Size check           | Raises `ValidationError` if file exceeds `settings.LOGO_MAX_BYTES` (configurable in `site.toml`)                                                                                                                                 |
| Magic-byte detection | Reads first bytes to determine type — never trusts file extension or MIME header                                                                                                                                                 |
| JPEG / PNG           | Re-encoded via Pillow: strips EXIF metadata, verifies image integrity. JPEG output is always RGB — RGBA/LA images are composited on a white background; CMYK/YCbCr/L are converted to RGB; palette images with transparency are treated as RGBA. This ensures all major browsers can render the stored file. |
| SVG                  | Parsed by stdlib `xml.etree.ElementTree` (safe on Python 3.12+/Expat 2.7.1, which blocks XXE and entity-expansion attacks), then scrubbed of `<script>` elements, `on*` event-handler attributes, non-fragment `href`/`src` URLs |
| UUID filename        | Original filename is discarded; `_logo_upload_to()` in `models.py` assigns `logos/<uuid4>.<ext>`                                                                                                                                 |

### Known limitation

CSS-based side-channels in SVG (e.g. `url()` inside `<style>` tags) are not fully
mitigated. If stricter guarantees are needed, reject SVG entirely or render to raster
via `cairosvg` before storage.

### Adding a new allowed format

1. Add magic-byte detection to `_sniff_type()` — return a new type string
2. Add a processing function (strip metadata, verify integrity)
3. Add the new branch to `validate_and_process_logo()`
4. Add tests to `tests/test_logo_utils.py`

### Media files in development

In development (`docker-compose.yml`), the project root is bind-mounted as
`.:/app`. Uploaded files land in `mediafiles/logos/` inside the container,
which maps to `<project-root>/mediafiles/` on your host. The directory is
listed in `.gitignore` — do not commit uploaded logos.

### Media files in tests

`config/settings_test.py` overrides `MEDIA_ROOT` to a temporary directory
(`tempfile.mkdtemp()`), so test file uploads never touch the project's
`mediafiles/` directory. The temp directory is cleaned up by the OS after
the test process exits. Tests that assert on specific file URLs should use
the `settings` + `tmp_path` fixtures to set a deterministic `MEDIA_ROOT`
(see `tests/test_api.py → TestLogoUpload` for the pattern).

---

### Available simple tags

| Tag                              | Purpose                                                               |
| -------------------------------- | --------------------------------------------------------------------- |
| `{% site_logo_url %}`            | Returns logo URL from `site.toml`, or empty string                    |
| `{% site_favicon_url %}`         | Returns favicon URL (auto-detects `static/img/favicon.*` as fallback) |
| `{% site_setting section key %}` | Generic accessor for any `site.toml` value                            |

---

## Form draft (localStorage)

The registration form (`/register/`) and the edit form (`/update/edit/<submission_id>/`) auto-save field values to the browser's `localStorage` so that a partially completed form survives a tab close or accidental navigation.

The API-key entry page (`/update/`) is deliberately **not** draft-enabled — it carries no draft key and does not load the draft script — so a typed API key is never written to `localStorage`. The key is also never a field on the register/edit forms, so it cannot enter a draft by that route either.

### Draft key scheme

| Form | Key pattern | Invalidated when |
|---|---|---|
| Register | `denbi_draft_register` | Form is submitted (cleared in `submit` event handler) |
| Edit | `denbi_draft_edit_{submission_uuid}_{updated_at_unix}` | Submission `updated_at` changes (any server-side save) **or** form is submitted |

The edit form embeds `updated_at` as a Unix timestamp in the key. Any server-side modification (admin edit, status change, API PATCH) advances `updated_at`, making the old draft key unreachable on the next page load — the user always sees the current DB values.

### Draft payload structure

```json
{
  "_savedAt": 1748000000000,
  "service_name": "MyTool",
  "service_description": "..."
}
```

`_savedAt` is a `Date.now()` millisecond timestamp written by `saveDraft()`. It is used exclusively for TTL enforcement and is not a form field name.

### Fields excluded from drafts (`SKIP_NAMES`)

| Field | Reason |
|---|---|
| `csrfmiddlewaretoken` | Security — must come from the server |
| `data_protection_consent` | Must be re-checked deliberately on every submission |
| `altcha` | Proof-of-work challenges expire; stale values are rejected server-side |
| `logo` | File inputs cannot be restored from localStorage |

### TTL and global purge

Every form load calls `purgeStaleDrafts()` before `restoreDraft()`. It:

1. Iterates all `localStorage` keys starting with `denbi_draft_`.
2. Removes any entry whose `_savedAt` is older than the configured TTL, or whose `_savedAt` is absent.
3. For the edit form specifically: also removes any `denbi_draft_edit_{uuid}_*` key that does not match the current draft key (old-version cleanup).

The TTL is configured in `site.toml` under `[submission] draft_ttl_days` (default 7). It is exposed to JavaScript as `data-draft-ttl-days` on the `<form>` element.

### Restoring a draft

`restoreDraft()` is called after `purgeStaleDrafts()`. It reads `DRAFT_KEY` from `localStorage`, verifies the TTL, then restores each field. A "Draft restored" banner with a **Clear draft** button is shown if any fields were actually restored. Server-side validation errors suppress draft restoration so the user sees the real error rather than a stale draft overwriting the submitted values.

### Testing

JavaScript logic is not covered by the Python test suite. Python tests cover:

- `FORM_DRAFT_TTL_DAYS` is present in the template context (`tests/test_context_processors.py`)
- `data-draft-ttl-days` attribute is rendered in both form pages (`tests/test_views.py`)

Manual browser verification: use DevTools → Application → Local Storage to inspect and manipulate `_savedAt` values.

---

## Submission lifecycle (status reset)

When a submitter edits an **approved** service via the web form or REST API, the platform determines whether to reset the status to `submitted` (triggering a re-review) based on which fields actually changed.

### How "changed" is determined

**Scalar fields** — a *prospective diff* compares the `before_scalar` snapshot (taken before form processing) with a snapshot of the in-memory instance after `form.save(commit=False)`. This uses the already-cleaned values, so sanitization artifacts (whitespace, NFC normalization) do not produce false positives.

**M2M fields** — `form.changed_data` filtered to the set of tracked M2M field names (`DIFFABLE_M2M`). M2M comparison is PK-based with no sanitization, so `form.changed_data` is reliable here.

### `no_reset_fields` exemption

Fields listed in `settings.SUBMISSION_NO_RESET_FIELDS` (configured via `site.toml [submission] no_reset_fields`) are exempt. If all actually-changed fields are exempt, the `approved` status is preserved. If any non-exempt field changed, status is reset and maturity tags are cleared (they are only valid on approved services).

The exemption is enforced identically in:

- `apps/submissions/views.py` `EditView.post()` — web form path
- `apps/api/views.py` `partial_update()` — REST API path

### `lifecycle.py`

`apps/submissions/lifecycle.py` provides `get_no_reset_fields()` — an `@lru_cache`-decorated function that reads `settings.SUBMISSION_NO_RESET_FIELDS`, validates each entry against `DIFFABLE_FIELDS`/`DIFFABLE_M2M`, rejects system-controlled fields, and returns a `frozenset` of valid exempt names. The cache is populated at startup via `SubmissionsConfig.ready()` so misconfigured entries are logged immediately.

Call `get_no_reset_fields.cache_clear()` in tests that override `settings.SUBMISSION_NO_RESET_FIELDS`.

### When status resets

On reset, both the web form and API:

1. Set `status = "submitted"`
2. Clear `primary_maturity_tag = None` and `secondary_maturity_tags = []`
3. Pass `status_reset=True` to `send_update_notification` so the submitter email includes a lifecycle notice

---

## Frontend JavaScript widgets

All form widget JS lives in `static/js/edam-autocomplete.js`. It is loaded globally
via `base.html` and provides three independent functions:

### `buildEdamPicker(selectEl)`

Enhances any `<select class="edam-autocomplete">` into a pill-zone + search + dropdown
picker for EDAM ontology terms. Applied automatically to all matching elements on
`DOMContentLoaded`. Configure via data attributes on the `<select>`:

| Attribute          | Default                | Purpose                                         |
| ------------------ | ---------------------- | ----------------------------------------------- |
| `data-max-items`   | `6`                    | Maximum selectable terms                        |
| `data-placeholder` | `"Search EDAM terms…"` | Search input placeholder                        |
| `data-branch`      | `""`                   | EDAM branch filter (`topic`, `operation`, etc.) |

### `buildCompactSelect(selectEl, label)`

Enhances any `<select multiple data-compact-select="label">` into a searchable
checkbox list with selected pills shown at the top (matching the EDAM picker layout).

**To apply to a new field**, add the data attribute to the widget in `forms.py`:

```python
"my_field": forms.SelectMultiple(
    attrs={"class": "form-select", "data-compact-select": "items"}
),
```

No JS changes needed — the boot code auto-discovers all `[data-compact-select]` elements.

Currently used by: `responsible_pis` (`"PIs"`), `service_categories` (`"categories"`).

### Tom Select combobox (`data-affiliation-combobox`)

The `submitter_affiliation` field uses Tom Select (vendored at
`static/js/tom-select.complete.min.js`) initialised in `register.html` and
`edit.html`. It provides a single-value searchable combobox with `create: true`
(free-text entry). To add Tom Select to another field, load the JS in the relevant
template's `{% block extra_js %}` and initialise with `new TomSelect(el, {...})`.

---

## Reusable form widgets

**Four reusable widget classes** in `apps/submissions/widgets.py` provide consistent UX across form fields:

| Widget                      | Use case                                                                       | Output                                                                                 |
| --------------------------- | ------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------- |
| `EdamAutocompleteWidget`    | EDAM term fields (`edam_topics`, `edam_operations`)                            | Searchable multi-select with pills, max 6 items                                        |
| `AffiliationComboboxWidget` | Institute/affiliation autocomplete (`submitter_affiliation`, `host_institute`) | Tom Select single-select combobox with free-text entry                                 |
| `CompactSelectWidget`       | Multi-select fields (`service_categories`, `responsible_pis`)                  | Searchable list with checkboxes, selected items shown as pills                         |
| `CompactSelectSingleWidget` | Single-select fields (`service_center`)                                        | Searchable list with **radio buttons** (not checkboxes), clarifies "pick one" to users |

**To add a new instance of these**, just use them in `forms.py`:

```python
"my_instit_field": AffiliationComboboxWidget(placeholder="e.g. Max-Planck-Institut"),
"my_multi_field": CompactSelectWidget(label="Items"),
"my_single_field": CompactSelectSingleWidget(label="Choice"),
```

No JS changes needed — widgets declare their Media (CSS/JS dependencies) and register data attributes that the boot code auto-discovers.

**Implementation details** — see `apps/submissions/widgets.py` docstrings for architectural rationale (progressive enhancement, accessibility, GDPR compliance for vendored assets).

---

## Adding a feature

1. Create a branch: `git checkout -b feature/my-feature`
2. Make changes; add or update tests
3. `make lint-fix && make test` — lint and coverage must pass
4. Open a pull request against `main`
