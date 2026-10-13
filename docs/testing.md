---
icon: material/test-tube
---

# Testing

## Running tests

```bash
# Full test suite with coverage (requires ≥ 80%)
conda run -n denbi-registry pytest tests/

# Or via make:
make test

# HTML coverage report
make test-cov
# then open htmlcov/index.html
```

Tests use SQLite in-memory and local-memory cache. No PostgreSQL, Redis, or external network access is required.

---

## Test configuration

**`pytest.ini`** — points to `config.settings_test` and enables coverage by default:

```ini
[pytest]
DJANGO_SETTINGS_MODULE = config.settings_test
addopts = -v --tb=short --cov=apps --cov-report=term-missing --cov-fail-under=80
```

**`config/settings_test.py`** — key overrides:

| Setting | Value | Why |
|---|---|---|
| `DATABASES` | SQLite `:memory:` | Zero setup, fast |
| `CACHES` | `LocMemCache` | No Redis needed |
| `CELERY_TASK_ALWAYS_EAGER` | `True` | Tasks run synchronously |
| `EMAIL_BACKEND` | `locmem` | Emails captured in `mail.outbox` |
| `PASSWORD_HASHERS` | MD5 | Fast hashing in tests |
| `ALTCHA_HMAC_KEY` | `""` | CAPTCHA verification bypassed — tests submit forms without solving PoW. Tests that specifically need ALTCHA active use `override_settings(ALTCHA_HMAC_KEY="test-key")` |
| `RATELIMIT_ENABLE` | `False` | No throttle blocking mid-suite |
| `DEFAULT_THROTTLE_CLASSES` | `[]` | No DRF throttling |
| `MEDIA_ROOT` | `tempfile.mkdtemp()` | File uploads go to a temp dir — never accumulate in the project tree |

---

## Test files

| File | What it covers |
|---|---|
| `test_models.py` | `ServiceSubmission`, `SubmissionAPIKey` validation, sanitisation, sensitive fields |
| `test_views.py` | Registration form, update flow, session handling, logo upload via views, deprecation via edit form, ALTCHA challenge endpoint, ALTCHA verification on register and edit (missing payload, invalid payload, expired challenge, valid solved challenge, bypass when key empty, widget presence/absence in GET responses), health endpoints |
| `test_forms.py` | `SubmissionForm` required fields, cross-field rules, URL validation, logo `clean_logo()` |
| `test_api.py` | All REST endpoints — auth, permissions, response shape, error envelopes, logo upload, `?status=deprecated` filter |
| `test_admin.py` | Admin bulk actions (deprecate/undeprecate), change-view buttons, CSV/JSON export completeness |
| `test_security.py` | API key auth, logging scrubber, CSRF, request ID middleware, ALTCHA challenge security (public access, JSON content type, HMAC key not leaked, non-empty fields, `Cache-Control: no-store`) |
| `test_tasks.py` | Celery email notification and cleanup tasks |
| `test_biotools.py` | bio.tools client (HTTP mocks), sync logic, tasks, signals, views |
| `test_management_commands.py` | `sync_edam`, `sync_biotools` management commands, template tags, context processor |
| `test_logo_utils.py` | `validate_and_process_logo()` — magic bytes, size, pixel and SVG size limits, EXIF stripping, SVG element allowlist and CSS reference rules (incl. linear-scaling checks), XML entity handling, path traversal |
| `test_logo_upload_routes.py` | The same logo scenarios through every upload route (registration, edit, API create, API PATCH, admin); stored logos kept on save; limits shown in form, admin and API schema |
| `test_media_serving.py` | `/media/` response policy header; files served unchanged; site pages keep the site policy |
| `test_nginx_conf.py` | The bundled host nginx vhost raises the 64k body limit to 12m on every upload location (`/api/`, `/register/`, `/update/` and the default admin prefix) |
| `test_migrations_complete.py` | Every model change has a migration (`makemigrations --check` reports nothing pending) |
| `test_template_tags.py` | `linkify_description` filter — named links, bare URLs, paragraph/line breaks, HTML escaping |
| `test_markdown_*.py`, `test_catalogue_markdown_render.py`, `test_api_markdown.py`, `test_admin_markdown.py` | Markdown descriptions: render/sanitize pipeline (heading shift, link filtering), removal of disallowed markup, cross-surface consistency, template filters, preview endpoint, Write/Preview editor markup, counter limits and WCAG AA text contrast of the editor CSS (`test_markdown_editor_render.py`), API `service_description_html`, admin editor widget and the view-only rendered row |
| `test_markdown_robustness.py` | Markdown engine on slow-to-parse input: each of the max-length inputs tested scales linearly (4x input must cost under 10x, a load- and coverage-independent check; Python-Markdown, tried during development, measured 14-29x) and finishes, nesting cap reported as removed, ordered-list `start` filter |
| `test_markdown_cache.py` | Description cache: one cache read per catalogue page, a service shown in two groups rendered once, pages and single renders still work with the cache unreachable |
| `test_markdown_output_matrix.py` | Exact output of every description surface (catalogue, API, admin, preview, emails, exports, audit) for both flag states and each input class |
| `test_field_partials.py` | `field.html` composed of `field_label.html` / `field_widget.html` / `field_feedback.html` (the fieldset branch reuses the widget and feedback partials): label or legend, inline-validation wrapper and error markup, with and without errors |
| `test_audit_markdown_command.py` | `audit_markdown_descriptions` flagging, diff-centred previews, CSV output and exit status |
| `test_export_formula_guard.py` | CSV cells that start like a formula get a leading `'` (`csv_safe` in `apps/submissions/csv_utils.py`) |
| `test_email_plaintext.py` | `render_plaintext()`: plain-text email bodies rendered without HTML escaping |
| `test_query_counts.py` | List endpoints (API list, admin changelist) issue the same number of queries for 3 and 12 rows: no per-row (N+1) queries |
| `test_api_patch_reset.py` | API `PATCH` on an approved submission resets it only on a real non-exempt change (re-sent, re-padded or NFD values keep approval), content + reset in one transaction, no-change notification contract |
| `test_http_utils.py` | `get_client_ip()` returns only valid IPs (malformed `X-Real-IP`/`X-Forwarded-For` fall through), `get_ratelimit_ip()` always gives django-ratelimit a parseable IP |
| `test_catalogue_views.py`, `test_catalogue_selectors.py`, `test_catalogue_filters.py`, `test_catalogue_id_filtering.py` | Registry Catalogue: views and HTMX responses, search/filter/sort/group selectors, query-parameter validation, integer and UUID ids in filters |
| `test_permissions.py` | `setup_groups` command and the group-based admin permissions |
| `test_registry_admin.py` | Deletion guard on categories, service centres and PIs |
| `test_admin_filter_sidebar.py` | Admin submission list filter sidebar |
| `test_lifecycle.py` | `get_no_reset_fields()`: valid, unknown and system-controlled entries |
| `test_diff_utils.py` | Field-level diff helpers in `diff_utils.py` |
| `test_line_endings.py` | CRLF and LF are stored, measured and diffed the same on every channel |
| `test_validation.py` | Shared validators in `apps/submissions/validation.py` |
| `test_validate_field.py` | Inline field-validation endpoint (`/register/validate/`): CSRF and rate limit |
| `test_widgets.py` | Form widget rendering |
| `test_context_processors.py` | Template context from `site.toml` |
| `test_middleware.py` | `RequestIDMiddleware`: request id per request, no unbounded log-factory chaining |
| `test_spdx_sync.py` | SPDX licence sync: import, update in place, deprecation of removed entries |

Run `pytest --collect-only -q` for the current test count; a coverage threshold of ≥ 80% is enforced.

---

## Running the checks in Docker

`docker/docker-compose.ci.yml` runs the same checks in containers, without a local Python environment: `docker compose -f docker/docker-compose.ci.yml run --rm test` (also `lint`, `audit`, `typecheck`).

## Shared fixtures and helpers

`tests/conftest.py` (available in every test):

| Fixture                  | Purpose                                                                                       |
| ------------------------ | --------------------------------------------------------------------------------------------- |
| `rf`                     | Django `RequestFactory`                                                                       |
| `superuser_client`       | Test client logged in as a fresh superuser (`testadmin`)                                      |
| `frozen_ratelimit_clock` | Freezes django-ratelimit's clock so rate-limit tests do not depend on the current time window |

`tests/helpers.py` (import explicitly):

| Helper                 | Purpose                                                                 |
| ---------------------- | ----------------------------------------------------------------------- |
| `base_form_data()`     | Minimal valid POST data for the registration form                        |
| `edit_form_data(sub)`  | Complete POST payload for the web edit form from a submission            |
| `edit_form_payload(sub)` | Minimal POST payload for the admin change form                        |
| `list_desc(content)`, `card_desc(content)` | Extract the description from a rendered catalogue list row or card |

## Factories

`tests/factories.py` provides `factory_boy` factories for submissions, reference data, bio.tools records and submission API keys (`ServiceSubmissionFactory`, `ServiceCategoryFactory`, `ServiceCenterFactory`, `PIFactory`, `AssociatedPartnerPIFactory`, `BioToolsRecordFactory`, `BioToolsFunctionFactory`, `APIKeyFactory`). Other models (EDAM terms, SPDX licences, Admin API keys, change logs) are created directly in the tests that need them:

```python
from tests.factories import ServiceSubmissionFactory, APIKeyFactory

# Creates a complete submission with all required fields
submission = ServiceSubmissionFactory()

# Override specific fields
submission = ServiceSubmissionFactory(
    service_name="My Service",
    biotools_url="",   # empty so signal doesn't trigger sync
)

# Create an API key with the plaintext for testing auth
key, plaintext = APIKeyFactory.create_with_plaintext(submission=submission)
```

!!! warning "Signal side effect"
    Creating a `ServiceSubmissionFactory` with a non-empty `biotools_url` triggers the
    `post_save` signal, which runs `sync_biotools_record` eagerly (Celery eager mode).
    If you need to test sync separately, create the submission with `biotools_url=""`
    and trigger sync manually.

---

## Writing new tests

### Standard test class structure

```python
import pytest
from tests.factories import ServiceSubmissionFactory

@pytest.mark.django_db
class TestMyFeature:

    def test_something(self):
        submission = ServiceSubmissionFactory()
        # ... assertions
```

### Mocking HTTP calls

Never make real network calls in tests. Use `unittest.mock.patch`:

```python
from unittest.mock import patch

def test_biotools_client():
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"name": "BLAST"}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        client = BioToolsClient()
        # test client behaviour...
```

### Mocking Celery tasks

Tasks with internal imports need to be patched at the source module, not at the point of import:

```python
# In tasks.py: from .sync import sync_tool
# Correct patch target:
with patch("apps.biotools.sync.sync_tool", return_value=expected):
    result = sync_biotools_record(str(submission.pk))
```

### Testing API endpoints

```python
from rest_framework.test import APIClient
from tests.factories import ServiceSubmissionFactory, APIKeyFactory

@pytest.mark.django_db
class TestMyEndpoint:

    def setup_method(self):
        self.client = APIClient()

    def test_requires_auth(self):
        response = self.client.get("/api/v1/submissions/some-id/")
        assert response.status_code in (401, 403)

    def test_with_valid_key(self):
        submission = ServiceSubmissionFactory(biotools_url="")
        key, plaintext = APIKeyFactory.create_with_plaintext(submission=submission)
        self.client.credentials(HTTP_AUTHORIZATION=f"ApiKey {plaintext}")
        response = self.client.get(f"/api/v1/submissions/{submission.pk}/")
        assert response.status_code == 200
```

### Testing email dispatch

```python
from django.core import mail

def test_sends_email(self):
    # ... trigger action that sends email
    assert len(mail.outbox) == 1
    assert "My Service" in mail.outbox[0].subject
```

---

## Coverage

The coverage threshold is **80%** enforced by `--cov-fail-under=80` in `pytest.ini`. The CI pipeline fails if coverage drops below this.

Run `make test-cov` for the current per-module figures (HTML report in `htmlcov/`).
