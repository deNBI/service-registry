"""
de.NBI Service Registry — Django Settings
==========================================
All configuration is read from environment variables.
Copy .env.example to .env and fill in values for local development.

Required variables (no defaults — startup fails if missing):
  SECRET_KEY      Django secret key
  DB_PASSWORD     PostgreSQL password
  REDIS_PASSWORD  Redis password

See .env.example for the full variable reference.
"""

import os
import sys
import tomllib as _tomllib
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Site configuration — loaded from config/site.toml
# ---------------------------------------------------------------------------
# All non-secret, human-editable settings live in site.toml.
# Secrets and connection details stay in .env.
# ---------------------------------------------------------------------------

_SITE_CONFIG_PATH = BASE_DIR / "config" / "site.toml"
try:
    with open(_SITE_CONFIG_PATH, "rb") as _f:
        SITE_CONFIG: dict = _tomllib.load(_f)
except FileNotFoundError:
    import sys

    print(
        f"WARNING: {_SITE_CONFIG_PATH} not found. "
        "Using built-in defaults. Copy config/site.toml.example if needed.",
        file=sys.stderr,
    )
    SITE_CONFIG = {}

# Convenience accessors — these are used directly in settings below
_sc = SITE_CONFIG
_sc_site = _sc.get("site", {})
_sc_cont = _sc.get("contact", {})
_sc_email = _sc.get("email", {})
_sc_links = _sc.get("links", {})
_sc_api = _sc.get("api", {})
_sc_edam = _sc.get("edam", {})
_sc_admin = _sc.get("admin", {})
_sc_uploads = _sc.get("uploads", {})


def env(key, default=None, required=False):
    """
    Read an environment variable.
    Also checks <KEY>_FILE — if set, reads the value from that file path.
    This supports Docker Secrets (files mounted at /run/secrets/).
    """
    # Check for file-based secret first (Docker Swarm / Docker Secrets)
    file_path = os.environ.get(f"{key}_FILE")
    if file_path:
        try:
            with open(file_path) as fh:
                return fh.read().strip()
        except OSError as exc:
            raise RuntimeError(f"Cannot read secret file for '{key}': {exc}") from exc
    value = os.environ.get(key, default)
    if required and value is None:
        raise RuntimeError(
            f"Required environment variable '{key}' is not set. "
            f"Copy .env.example to .env and fill in all required values."
        )
    return value


def env_bool(key, default=False):
    return env(key, str(default)).lower() in ("true", "1", "yes")


def env_int(key, default=0):
    return int(env(key, str(default)))


def env_list(key, default="", sep=","):
    val = env(key, default)
    return [v.strip() for v in val.split(sep) if v.strip()]


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
SECRET_KEY = env("SECRET_KEY", required=True)
DEBUG = env_bool("DEBUG", default=False)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS", default="localhost,127.0.0.1")

# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "rest_framework",
    "drf_spectacular",
    "corsheaders",
    "axes",
    "csp",
    "django_ratelimit",
    "django_celery_results",
    "django_extensions",
    "apps.registry",
    "apps.submissions",
    "apps.api",
    "apps.edam",
    "apps.biotools",
]

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "csp.middleware.CSPMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "axes.middleware.AxesMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.submissions.middleware.RequestIDMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.submissions.context_processors.site_context",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME", "denbi_registry"),
        "USER": env("DB_USER", "denbi"),
        "PASSWORD": env("DB_PASSWORD", required=True),
        "HOST": env("DB_HOST", "db"),
        "PORT": env_int("DB_PORT", 5432),
        "CONN_MAX_AGE": 60,
        "OPTIONS": {"connect_timeout": 10},
    }
}

# ---------------------------------------------------------------------------
# Internationalisation
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", "Europe/Berlin")
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]  # project-level static files
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "mediafiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------
# Maximum logo file size in bytes. Configurable in config/site.toml [uploads].
LOGO_MAX_BYTES: int = _sc_uploads.get("logo_max_bytes", 10 * 1024 * 1024)

# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------
SECURE_HSTS_SECONDS = env_int("HSTS_SECONDS", 31536000)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", not DEBUG)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# Trust the X-Forwarded-Host header set by nginx so Django builds correct
# absolute URLs in emails and redirects when behind a reverse proxy.
USE_X_FORWARDED_HOST = True
SECURE_CONTENT_TYPE_NOSNIFF = True
URLIZE_ASSUME_HTTPS = True  # opt in now; becomes the hard default in Django 7.0

SESSION_COOKIE_SECURE = env_bool("SESSION_COOKIE_SECURE", not DEBUG)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Strict"
SESSION_COOKIE_AGE = env_int("SESSION_COOKIE_AGE", 3600)

CSRF_COOKIE_SECURE = env_bool("CSRF_COOKIE_SECURE", not DEBUG)
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = "Strict"

X_FRAME_OPTIONS = "DENY"

# Referrer Policy
REFERRER_POLICY = "strict-origin-when-cross-origin"

# EDAM OWL URL: site.toml → [edam] owl_url, overridden by EDAM_OWL_URL env var
EDAM_OWL_URL = env("EDAM_OWL_URL", default=None) or _sc_edam.get(
    "owl_url", "https://edamontology.org/EDAM_stable.owl"
)

# Admin URL prefix: site.toml → [admin] url_prefix, overridden by ADMIN_URL_PREFIX env var
ADMIN_URL_PREFIX = env("ADMIN_URL_PREFIX", default=None) or _sc_admin.get(
    "url_prefix", "admin"
)
RATE_LIMIT_SUBMIT = env("RATE_LIMIT_SUBMIT", "10/h")
RATE_LIMIT_UPDATE = env("RATE_LIMIT_UPDATE", "20/h")
RATE_LIMIT_CHALLENGE = env("RATE_LIMIT_CHALLENGE", "60/h")
RATE_LIMIT_BIOTOOLS = env("RATE_LIMIT_BIOTOOLS", "60/h")  # bio.tools proxy endpoints
RATE_LIMIT_VALIDATE = env("RATE_LIMIT_VALIDATE", "120/h")  # inline field validation

# ---------------------------------------------------------------------------
# ALTCHA — self-hosted proof-of-work CAPTCHA
# ---------------------------------------------------------------------------
# HMAC key used to sign and verify ALTCHA challenges.
# Set ALTCHA_HMAC_KEY to a strong random secret in production.
# When empty (default), ALTCHA verification is bypassed — safe for local
# development but must be configured before deploying publicly.
ALTCHA_HMAC_KEY = env("ALTCHA_HMAC_KEY", "")

# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

AXES_FAILURE_LIMIT = env_int("AXES_FAILURE_LIMIT", 5)
AXES_COOLOFF_TIME = env_int("AXES_COOLOFF_MINUTES", 30) / 60
AXES_LOCKOUT_CALLABLE = None
AXES_RESET_ON_SUCCESS = False  # keep AccessAttempt rows after a successful login
AXES_LOCKOUT_PARAMETERS = ["ip_address", "username"]
AXES_ENABLE_ACCESS_FAILURE_LOG = True  # Enable logging of all access failures
# Tell axes to read the real client IP from proxy headers rather than REMOTE_ADDR.
# REMOTE_ADDR is the internal nginx server IP when behind a reverse proxy;
# X-Real-IP is set by nginx to $remote_addr (the actual connecting client IP).
# X-Forwarded-For is also accepted as a fallback.
AXES_IPWARE_META_PRECEDENCE_ORDER = [
    "HTTP_X_REAL_IP",
    "HTTP_X_FORWARDED_FOR",
    "REMOTE_ADDR",
]

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    "django.core.mail.backends.console.EmailBackend"
    if DEBUG
    else "django.core.mail.backends.smtp.EmailBackend",
)
EMAIL_HOST = env("EMAIL_HOST", "localhost")
EMAIL_PORT = env_int("EMAIL_PORT", 587)
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
# Email from: env var overrides site.toml which overrides hardcoded default
DEFAULT_FROM_EMAIL = env(
    "EMAIL_FROM", _sc_email.get("from_address", "no-reply@denbi.de")
)
EMAIL_SUBJECT_PREFIX = _sc_email.get("subject_prefix", "[de.NBI Registry]")
SUBMISSION_NOTIFY_CC = env_list("SUBMISSION_NOTIFY_CC", "")
SUBMISSION_NOTIFY_OVERRIDE = env("SUBMISSION_NOTIFY_OVERRIDE", "")

# ---------------------------------------------------------------------------
# Redis / Celery
# ---------------------------------------------------------------------------
_redis_host = env("REDIS_HOST", "redis")
_redis_port = env_int("REDIS_PORT", 6379)
_redis_password = env("REDIS_PASSWORD", required=True)
_redis_url = f"redis://:{_redis_password}@{_redis_host}:{_redis_port}/0"

CELERY_BROKER_URL = _redis_url
CELERY_RESULT_BACKEND = "django-db"
CELERY_CACHE_BACKEND = "default"
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {
    "cleanup-stale-drafts": {
        "task": "submissions.cleanup_stale_drafts",
        "schedule": 86400,  # every 24 hours
    },
    "sync-biotools-daily": {
        "task": "biotools.sync_all",
        "schedule": 86400,  # daily
    },
    "sync-edam-monthly": {
        "task": "edam.sync",
        "schedule": 2592000,  # every 30 days (~monthly)
    },
}

# ---------------------------------------------------------------------------
# Django REST Framework
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.api.authentication.SubmissionAPIKeyAuthentication",
        "apps.api.authentication.AdminAPIKeyAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": env_int("API_PAGE_SIZE", 20),
    "MAX_PAGINATE_BY": env_int("API_MAX_PAGE_SIZE", 100),
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "10/min",
        "user": env("RATE_LIMIT_API", "60/m"),
    },
    "EXCEPTION_HANDLER": "apps.api.exceptions.custom_exception_handler",
}

# ---------------------------------------------------------------------------
# drf-spectacular
# ---------------------------------------------------------------------------
SPECTACULAR_SETTINGS = {
    "TITLE": _sc_api.get("title", "de.NBI Service Registry API"),
    "DESCRIPTION": (
        "REST API for the de.NBI & ELIXIR-DE Service Registration system.\n\n"
        "## Authentication\n\n"
        "### Admin API Key (list all submissions)\n"
        "Create an admin API key in the Django admin under **API → Admin API Keys**. "
        "Choose scope (read-only or full) and copy the key. Then click **Authorize** "
        "above and enter:\n"
        "```\nAdminKey <paste-your-key-here>\n```\n\n"
        "### Submission API Key (access your own submission)\n"
        "Your API key is returned once when you submit the registration form. "
        "Click **Authorize** and enter:\n"
        "```\nApiKey <paste-your-api-key-here>\n```\n"
    ),
    "VERSION": _sc_api.get("version", "1.0.0"),
    "SERVE_INCLUDE_SCHEMA": False,
    "CONTACT": {
        "email": _sc_cont.get("email", "servicecoordination@denbi.de"),
        "url": _sc_site.get("url", ""),
    },
    "LICENSE": {"name": _sc_api.get("license_name", "MIT")},
    "SWAGGER_UI_SETTINGS": {
        "persistAuthorization": True,
        "displayRequestDuration": True,
    },
    # Vendor swagger-ui and redoc locally — no CDN requests (GDPR).
    # swagger-ui-dist 5.18.2, redoc 2.2.0 — vendored in static/swagger-ui/ and static/redoc/
    "SWAGGER_UI_DIST": "/static/swagger-ui",
    "SWAGGER_UI_FAVICON_HREF": "/static/swagger-ui/favicon-32x32.png",
    "REDOC_DIST": "/static/redoc",
    # Apply both schemes globally so every endpoint shows the lock icon.
    # The scheme definitions themselves come from the OpenApiAuthenticationExtension
    # subclasses in apps/api/spectacular_extensions.py (loaded via ApiConfig.ready).
    "SECURITY": [{"AdminKey": []}, {"SubmissionApiKey": []}],
}

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", "")
CORS_ALLOW_CREDENTIALS = env_bool("CORS_ALLOW_CREDENTIALS", False)
CORS_ALLOW_METHODS = ["GET", "POST", "PATCH", "OPTIONS"]
CORS_ALLOW_HEADERS = ["authorization", "content-type", "x-csrftoken"]
CORS_PREFLIGHT_MAX_AGE = 86400


# ---------------------------------------------------------------------------
# Content Security Policy
# ---------------------------------------------------------------------------
# img-src: allow 'self' + data URIs always.
# If logo_url or favicon_url in site.toml points to an external origin,
# extract just that origin (scheme + host) and add it — so the CSP stays
# tight even when the logo is hosted elsewhere.
def _csp_img_origins() -> tuple:
    from urllib.parse import urlparse

    origins = {"'self'", "data:"}
    for key in ("logo_url", "favicon_url"):
        url = _sc_site.get(key, "")
        if url and url.startswith("https://"):
            parsed = urlparse(url)
            origins.add(f"{parsed.scheme}://{parsed.netloc}")
    return tuple(sorted(origins))


CONTENT_SECURITY_POLICY = {
    "DIRECTIVES": {
        "default-src": ("'self'",),
        # 'unsafe-inline' is required because templates use inline <script> blocks
        # and inline event handlers (e.g. onerror). To remove it, extract all
        # inline JS to static files and use nonces on remaining script elements.
        "script-src": ("'self'", "'unsafe-inline'"),
        "style-src": (
            "'self'",
            "'unsafe-inline'",
        ),  # Bootstrap uses inline styles via JS
        "img-src": _csp_img_origins(),
        "font-src": ("'self'",),
        "connect-src": ("'self'",),
        # Altcha proof-of-work widget spawns Web Workers via blob: URLs to run
        # SHA-256 computation off the main thread. Without this, workers are
        # blocked and verification never completes.
        "worker-src": ("blob:",),
        "frame-src": ("'none'",),
        "frame-ancestors": ("'none'",),
        "form-action": ("'self'",),
        "base-uri": ("'self'",),
        "object-src": ("'none'",),
        "upgrade-insecure-requests": not DEBUG,
    },
}

# ---------------------------------------------------------------------------
# Rate limiting / API keys / Cache
# ---------------------------------------------------------------------------
RATELIMIT_USE_CACHE = "default"
RATELIMIT_FAIL_OPEN = False

API_KEY_ENTROPY_BYTES = env_int("API_KEY_ENTROPY_BYTES", 48)
API_KEY_HASH_ALGORITHM = env("API_KEY_HASH_ALGORITHM", "sha256")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": _redis_url,
    }
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "require_debug_false": {"()": "django.utils.log.RequireDebugFalse"},
        "scrub_sensitive": {
            "()": "apps.submissions.logging_filters.ScrubSensitiveFilter"
        },
    },
    "formatters": {
        "json": {
            "()": "pythonjsonlogger.jsonlogger.JsonFormatter",
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s %(request_id)s",
        },
        "verbose": {"format": "{levelname} {asctime} {module} {message}", "style": "{"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json" if not DEBUG else "verbose",
            "filters": ["scrub_sensitive"],
            "stream": sys.stdout,
        },
        "mail_admins": {
            "level": "ERROR",
            "class": "django.utils.log.AdminEmailHandler",
            "filters": ["require_debug_false"],
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.request": {
            "handlers": ["console", "mail_admins"],
            "level": "ERROR",
            "propagate": False,
        },
        "apps": {
            "handlers": ["console"],
            "level": "DEBUG" if DEBUG else "INFO",
            "propagate": False,
        },
        "celery": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}

# ---------------------------------------------------------------------------
# Sentry (optional)
# ---------------------------------------------------------------------------
_sentry_dsn = env("SENTRY_DSN", "")
if _sentry_dsn:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration
    from sentry_sdk.integrations.celery import CeleryIntegration

    sentry_sdk.init(
        dsn=_sentry_dsn,
        integrations=[DjangoIntegration(), CeleryIntegration()],
        traces_sample_rate=float(env("SENTRY_TRACES_SAMPLE_RATE", "0.1")),
        send_default_pii=False,
    )
