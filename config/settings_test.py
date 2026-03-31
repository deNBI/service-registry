"""
Test settings — overrides for fast, isolated test runs.

Uses SQLite in-memory (no PostgreSQL required) and a local-memory cache
(no Redis required). These are automatically used when pytest.ini points
DJANGO_SETTINGS_MODULE here, or via:

    DJANGO_SETTINGS_MODULE=config.settings_test pytest

Do NOT use these settings outside of testing.
"""

import os

# Provide required env vars before importing base settings.
# These are placeholders — SQLite and locmem are used instead of real services.
os.environ.setdefault("SECRET_KEY", "test-only-not-for-production-do-not-use")
os.environ.setdefault("DB_PASSWORD", "unused-in-test")
os.environ.setdefault("REDIS_PASSWORD", "unused-in-test")

from config.settings import *  # noqa: F401, F403

# ---------------------------------------------------------------------------
# Database — SQLite in-memory for speed and zero external dependencies
# ---------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# ---------------------------------------------------------------------------
# Cache — local memory (no Redis required)
# ---------------------------------------------------------------------------
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}

# ---------------------------------------------------------------------------
# Celery — run tasks synchronously in tests
# ---------------------------------------------------------------------------
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# ---------------------------------------------------------------------------
# Security — relax for test runner
# ---------------------------------------------------------------------------
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# ---------------------------------------------------------------------------
# Email — capture in memory, don't send
# ---------------------------------------------------------------------------
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# ---------------------------------------------------------------------------
# Password hashing — fast hasher so tests don't spend time on bcrypt
# ---------------------------------------------------------------------------
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

# ---------------------------------------------------------------------------
# Static files — suppress "No directory at staticfiles/" warning in tests
# ---------------------------------------------------------------------------
STATICFILES_STORAGE = "django.contrib.staticfiles.storage.StaticFilesStorage"
WHITENOISE_AUTOREFRESH = True

# ---------------------------------------------------------------------------
# Media files — use a temp directory so uploads don't accumulate in the
# project's mediafiles/ directory between test runs.
# ---------------------------------------------------------------------------
import tempfile  # noqa: E402

MEDIA_ROOT = tempfile.mkdtemp(prefix="denbi_test_media_")

# ---------------------------------------------------------------------------
# ALTCHA — disable verification in tests (empty key = bypassed)
# ---------------------------------------------------------------------------
ALTCHA_HMAC_KEY = ""

# ---------------------------------------------------------------------------
# Rate limiting — disable so tests aren't blocked by rate limits
# ---------------------------------------------------------------------------
RATELIMIT_ENABLE = False

# Disable DRF throttle classes — the locmem cache is shared across tests,
# so the anon rate limit (10/min) is exhausted mid-suite without this.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,  # type: ignore[name-defined]  # noqa: F405
    "DEFAULT_THROTTLE_CLASSES": [],
    "DEFAULT_THROTTLE_RATES": {},
}
