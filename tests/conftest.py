"""
Pytest configuration and shared fixtures.

Django settings are handled by config/settings_test.py (see pytest.ini).
No environment variable juggling needed here.
"""

import pytest

# Rich assertion messages for the shared helpers in tests/helpers.py.
pytest.register_assert_rewrite("tests.helpers")


@pytest.fixture
def rf():
    from django.test import RequestFactory

    return RequestFactory()


@pytest.fixture
def superuser_client(db):
    """Django test client logged in as a fresh superuser ("testadmin").

    Distinct from pytest-django's built-in ``admin_client`` (user "admin"),
    which other modules rely on unchanged.
    """
    from django.contrib.auth import get_user_model
    from django.test import Client

    user = get_user_model().objects.create_superuser(
        username="testadmin", password="adminpass123", email="admin@example.com"
    )
    c = Client()
    c.force_login(user)
    return c
