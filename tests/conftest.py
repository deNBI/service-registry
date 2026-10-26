"""
Pytest configuration and shared fixtures.

Django settings are handled by config/settings_test.py (see pytest.ini).
No environment variable juggling needed here.
"""

import gc

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


@pytest.fixture
def frozen_ratelimit_clock(monkeypatch):
    """Freeze django-ratelimit's clock for the test.

    Its window is ``ts - ts % period + crc32(key) % period``, so a test that
    fills a bucket with many requests is flaky if a window boundary passes
    mid-loop (the counter resets and the "limited" request is let through).
    Replacing the ``time`` module inside django_ratelimit.core pins every
    request of the test to one window without touching time.time elsewhere.
    """
    from types import SimpleNamespace

    import django_ratelimit.core

    monkeypatch.setattr(
        django_ratelimit.core, "time", SimpleNamespace(time=lambda: 1_800_000_000.0)
    )


@pytest.fixture(autouse=True)
def _collect_garbage_after_each_test():
    """pytest.ini turns ResourceWarning (a file or socket that is never
    closed) into an error. Objects that hold one are often freed only by the
    garbage collector, at some later point; collecting the two youngest
    generations when each test ends reports them on the test that left them
    open. A full collection per test would cost minutes on this suite; the
    young generations cost well under a second in total."""
    yield
    gc.collect(1)


@pytest.fixture(scope="session", autouse=True)
def _collect_all_garbage_at_the_end():
    """One full collection after the last test, for any object that was
    moved to the oldest generation before its test ended."""
    yield
    gc.collect()
