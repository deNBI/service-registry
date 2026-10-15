"""
Pytest configuration and shared fixtures.

Django settings are handled by config/settings_test.py (see pytest.ini).
No environment variable juggling needed here.
"""

import pytest

# Rich assertion messages for the shared helpers in tests/helpers.py.
pytest.register_assert_rewrite("tests.helpers")


def pytest_collection_modifyitems(config, items):
    """Tests marked ``postgres`` run only with config.settings_test_postgres.

    ``make test-postgres`` sets REQUIRE_POSTGRES: then a run that is not on
    PostgreSQL stops instead of passing with those tests skipped."""
    import os

    from django.conf import settings

    if settings.DATABASES["default"]["ENGINE"].endswith("postgresql"):
        return
    if os.environ.get("REQUIRE_POSTGRES"):
        pytest.exit("REQUIRE_POSTGRES is set but the tests do not use PostgreSQL", 3)
    skip = pytest.mark.skip(reason="needs PostgreSQL (make test-postgres)")
    for item in items:
        if item.get_closest_marker("postgres"):
            item.add_marker(skip)


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
def _status_history_invariant(request):
    """After each database test, every submission's status events must form one
    chain from the creation and the latest event must equal its status. Opt out
    with @pytest.mark.allow_status_drift."""
    # A test that gets the database through the db fixture (no django_db
    # mark) would tear it down before this check: set it up first so it is
    # torn down after.
    for name in ("db", "transactional_db"):
        if name in request.fixturenames:
            request.getfixturevalue(name)
            break
    yield
    if request.node.get_closest_marker("allow_status_drift"):
        return
    from tests.helpers import status_history_broken, status_history_drift

    try:
        drift = status_history_drift()
        broken = status_history_broken()
    except RuntimeError as exc:  # test without database access
        if "Database access not allowed" in str(exc):
            return
        raise
    assert not drift, f"status history out of sync: {drift}"
    assert not broken, f"status history has gaps: {broken}"


@pytest.fixture
def locked_models(monkeypatch):
    """Names of the models whose rows a test locks with select_for_update.

    SQLite ignores row locks, so tests can only check that a lock is asked for.
    """
    from django.db.models.query import QuerySet

    calls = []
    real = QuerySet.select_for_update

    def spy(self, *args, **kwargs):
        calls.append(self.model.__name__)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(QuerySet, "select_for_update", spy)
    return calls
