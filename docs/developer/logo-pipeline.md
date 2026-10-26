---
icon: material/image-filter-center-focus
---

# Logo Upload Pipeline and Media Serving

Uploaded service logos pass through `apps/submissions/logo_utils.py` before being
stored. Understanding this module is useful when modifying file upload handling.

## Entry point

```python
from apps.submissions.logo_utils import validate_and_process_logo

result = validate_and_process_logo(file_obj)  # → InMemoryUploadedFile
```

## Processing steps (in order)

| Step                 | What happens                                                                                                                                                                                                                     |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Size check           | Raises `ValidationError` if file exceeds `settings.LOGO_MAX_BYTES` (configurable in `site.toml`)                                                                                                                                 |
| Magic-byte detection | Reads first bytes to determine type — never trusts file extension or MIME header                                                                                                                                                 |
| Pixel check          | PNG/JPEG only: raises `ValidationError` if width × height exceeds `settings.LOGO_MAX_PIXELS` (configurable in `site.toml`); read from the image header before decoding |
| SVG size check       | SVG only: raises `ValidationError` if the file exceeds `settings.LOGO_MAX_SVG_BYTES` (configurable in `site.toml`); checked before parsing |
| JPEG / PNG           | Re-encoded via Pillow: strips EXIF metadata, verifies image integrity. JPEG output is always RGB — RGBA/LA images are composited on a white background; CMYK/YCbCr/L are converted to RGB; palette images with transparency are treated as RGBA. This ensures all major browsers can render the stored file. |
| SVG                  | Parsed by stdlib `xml.etree.ElementTree` (Python 3.12+/Expat 2.7.1: external entities are not loaded, and text from internal entities goes through the same rules as the rest of the file). The root must be an SVG `<svg>`; nesting deeper than `_SVG_MAX_DEPTH` (100) is rejected; only elements in `_SVG_ALLOWED_ELEMENTS` are kept (shapes, text, gradients, patterns, clip paths, masks, filters, structure), and the text following a removed element is preserved (each child list is rebuilt once, linear time); namespaced attributes other than `_KEPT_NAMESPACED_ATTRS` (`xlink:href`, `xml:space`, `xml:lang`), `on*` attributes, `src`/`action`/`formaction`/`ping` are dropped; a non-fragment `href` is dropped on `<a>` and rejects the upload on any other element (`_reject_outside_reference`), so an `<image>` wrapping a bitmap is never stored as a blank logo. CSS in `<style>`, `style` and other un-namespaced attributes that contains `url(`, `image-set(`, `image(`, `src(`, `@import` or a backslash is read with tinycss2 (CSS Syntax spec, which decodes escapes such as `u\72 l(`; other text cannot reference a resource and is left as is): `url()` keeps only `#fragment` and `data:` references (others become `none`), top-level `@import` is removed, and parse errors, nested `@import` and `image-set()`/`image()`/`src()` are rejected. CSS that needs no change is kept byte for byte |
| UUID filename        | Original filename is discarded; `_logo_upload_to()` in `models.py` assigns `logos/<uuid4>.<ext>`                                                                                                                                 |

## When processing runs

Only a newly uploaded file is processed. Every upload route calls the same code:
`SubmissionForm.clean_logo()` (registration and edit forms) and
`ServiceSubmissionAdminForm.clean_logo()` go through `process_new_logo_upload()`;
the API serializer's `validate_logo()` calls `validate_and_process_logo()`.
The inline field-validation endpoint (`/register/validate/`) does not receive
uploaded files, so it never processes a logo.
Saving a record without choosing a new file keeps the stored logo as it is, so
changes to these rules never rewrite logos that are already stored.

## Limits shown to users

`logo_limits_text()` / `logo_help_text()` build the limit text from the settings.
It is used by the public form (via the `{logo_limits}` placeholder in
`form_texts.yaml`), the admin form and the API serializer (OpenAPI schema).

## Serving uploaded media

`config/urls.py` serves `/media/` through `_serve_media()`, which sets a
restrictive `Content-Security-Policy` (`_MEDIA_CSP`: sandboxed, no scripts, only
inline styles and `data:` images/fonts). The site-wide `CSPMiddleware` leaves an
existing header in place. The header has no effect on pages that show logos
through `<img>`; it applies when a logo URL is opened directly.

## Tests

- `tests/test_logo_utils.py` — the processing rules, unit level (including
  linear-scaling checks for the CSS handling and for element removal)
- `tests/test_logo_upload_routes.py` — the same scenarios through every upload
  route (registration, edit, API create, API PATCH, admin), plus the help text
- `tests/test_media_serving.py` — the `/media/` response header

## Adding a new allowed format

1. Add magic-byte detection to `_sniff_type()` — return a new type string
2. Add a processing function (strip metadata, verify integrity)
3. Add the new branch to `validate_and_process_logo()`
4. Add tests to `tests/test_logo_utils.py` and a scenario to
   `tests/test_logo_upload_routes.py`
5. Update `logo_limits_text()` and the docs if the format has its own limit

## Media files in development

In development (`docker-compose.yml`), the project root is bind-mounted as
`.:/app`. Uploaded files land in `mediafiles/logos/` inside the container,
which maps to `<project-root>/mediafiles/` on your host. The directory is
listed in `.gitignore` — do not commit uploaded logos.

The container runs as a non-root user, so the bind-mounted directory must exist
and be writable from inside the container before uploads can be stored locally:

```bash
mkdir -p mediafiles && chmod 777 mediafiles   # local development only
```

## Media files in tests

`config/settings_test.py` overrides `MEDIA_ROOT` to a temporary directory
(`tempfile.mkdtemp()`), so test file uploads never touch the project's
`mediafiles/` directory. The temp directory is cleaned up by the OS after
the test process exits. Tests that assert on specific file URLs should use
the `settings` + `tmp_path` fixtures to set a deterministic `MEDIA_ROOT`
(see `tests/test_api.py → TestLogoUpload` for the pattern).
