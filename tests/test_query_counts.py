"""List endpoints must not issue queries per row (N+1).

Measured by comparing query counts for a small and a larger data set: any
per-row query makes the larger count grow. Both sizes fit on one page.
"""

import hashlib
import secrets

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APIClient

from tests.factories import (
    PIFactory,
    ServiceCategoryFactory,
    ServiceSubmissionFactory,
)

pytestmark = pytest.mark.django_db


def _seed(n):
    from apps.licenses.models import SpdxLicense

    lic_a, _ = SpdxLicense.objects.get_or_create(
        license_id="MIT", defaults={"name": "MIT License"}
    )
    lic_b, _ = SpdxLicense.objects.get_or_create(
        license_id="Apache-2.0", defaults={"name": "Apache License 2.0"}
    )
    cat, pi = ServiceCategoryFactory(), PIFactory()
    for i in range(n):
        sub = ServiceSubmissionFactory(
            status="approved", biotools_url="", service_name=f"QC Tool {i:03d}"
        )
        sub.licenses.set([lic_a, lic_b][: 1 + i % 2])
        sub.service_categories.set([cat])
        sub.responsible_pis.set([pi])


def _count(fn):
    with CaptureQueriesContext(connection) as q:
        resp = fn()
    assert resp.status_code == 200
    return len(q), resp


def _admin_api():
    from apps.api.models import AdminAPIKey

    plaintext = secrets.token_urlsafe(48)
    AdminAPIKey.objects.create(
        label="qc",
        key_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
        scope="full",
        is_active=True,
    )
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"AdminKey {plaintext}")
    return client


def test_api_list_query_count_is_independent_of_rows():
    api = _admin_api()
    _seed(3)
    small, resp = _count(lambda: api.get("/api/v1/submissions/"))
    assert resp.json()["count"] == 3
    _seed(9)
    large, resp = _count(lambda: api.get("/api/v1/submissions/"))
    assert resp.json()["count"] == 12
    assert large == small, f"{small} queries for 3 rows, {large} for 12"
    # The licenses are still serialised (from the prefetch).
    assert any(r["licenses"] for r in resp.json()["results"])


def test_admin_changelist_query_count_is_independent_of_rows(superuser_client):
    url = reverse("admin:submissions_servicesubmission_changelist")
    _seed(3)
    small, _ = _count(lambda: superuser_client.get(url))
    _seed(9)
    large, resp = _count(lambda: superuser_client.get(url))
    assert large == small, f"{small} queries for 3 rows, {large} for 12"
    # The licenses column still shows the SPDX ids.
    assert "Apache-2.0" in resp.content.decode()
