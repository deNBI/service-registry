"""
Test settings for running the suite on PostgreSQL (``make test-postgres``).

Same as config/settings_test.py but on a real PostgreSQL server, so row locks
and concurrent transactions behave as in production and the tests marked
``postgres`` run. Connection values come
from the environment:

    TEST_DB_HOST (default 127.0.0.1), TEST_DB_PORT (5432), TEST_DB_NAME
    (denbi_registry), TEST_DB_USER (denbi), TEST_DB_PASSWORD (required)

pytest-django creates and drops a separate ``test_<name>`` database.
"""

import os

from config.settings_test import *  # noqa: F401, F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": os.environ.get("TEST_DB_HOST", "127.0.0.1"),
        "PORT": int(os.environ.get("TEST_DB_PORT", "5432")),
        "NAME": os.environ.get("TEST_DB_NAME", "denbi_registry"),
        "USER": os.environ.get("TEST_DB_USER", "denbi"),
        "PASSWORD": os.environ["TEST_DB_PASSWORD"],
    }
}
