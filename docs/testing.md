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
| `test_logo_utils.py` | `validate_and_process_logo()` — magic bytes, size limits, EXIF stripping, SVG sanitisation, XML attack prevention (XXE/billion-laughs), path traversal |
| `test_template_tags.py` | `linkify_description` filter — named links, bare URLs, paragraph/line breaks, XSS escaping |

Total: **~450 tests** (enforced ≥ 80% coverage threshold).

---

## Factories

`tests/factories.py` provides `factory_boy` factories for all models:

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

Current coverage by module (approximate):

| Module | Coverage |
|---|---|
| `api/` | ~90% |
| `biotools/` | ~90% |
| `submissions/models.py` | ~96% |
| `submissions/forms.py` | ~88% |
| `submissions/logo_utils.py` | ~91% |
| `submissions/views.py` | ~88% |
| `submissions/admin.py` | ~53% (admin UI is hard to test) |
| `edam/management/commands/sync_edam.py` | ~91% |

Admin code is intentionally at lower coverage — Django's admin class methods require
a running admin site with a logged-in superuser, which adds significant test setup complexity
with limited return value.
