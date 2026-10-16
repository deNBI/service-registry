"""Private values must not appear on any page or endpoint an anonymous
visitor can reach.

Every private field of a service gets a unique marker; the service is
approved and published (the strongest case), and so are services in every
other status. The pages and endpoints that exist today are fetched without
credentials and must not contain a single marker.
"""

import json

import pytest
from rest_framework.test import APIClient

from apps.catalogue.models import PublishedRevision, PublishedService
from apps.submissions.models import ServiceSubmission
from tests.factories import (
    AssociatedPartnerPIFactory,
    BioToolsRecordFactory,
    PIFactory,
    ServiceSubmissionFactory,
)

pytestmark = pytest.mark.django_db

SITE = {
    "features": {"catalogue": True, "biotools_prefill": False},
    "catalogue": {
        "per_page": 12,
        "card_fields": [
            "categories",
            "service_center",
            "updated_at",
            "edam_topics",
            "maturity_tag",
        ],
    },
    "site": {"name": "Test Registry", "url": "https://example.com"},
    "contact": {},
    "links": {},
    "email": {},
}

PRIVATE = {
    "submitter_first_name": "MARKER-SUBMITTER-FIRST",
    "submitter_last_name": "MARKER-SUBMITTER-LAST",
    "submitter_affiliation": "MARKER-SUBMITTER-AFFILIATION",
    "internal_contact_name": "MARKER-INTERNAL-NAME",
    "internal_contact_email": "marker-internal@example.org",
    "comments": "MARKER-COMMENTS",
    "kpi_start_year": "MARKER-KPI",
    "keywords_uncited": "MARKER-UNCITED",
    "associated_partner_note": "MARKER-PARTNER-NOTE",
    "user_agent_hash": "MARKER-UA-HASH",
    "submission_ip": "203.0.113.77",
}
OTHER_MARKERS = [
    "MARKER-SUMMARY",
    "MARKER-PI-EMAIL",
    "MARKER-SYNC-ERROR",
    "MARKER-UNAPPROVED-NAME",
    "MARKER-RAW",
]


@pytest.fixture
def world(settings):
    settings.SITE_CONFIG = SITE
    pi = PIFactory(last_name="Marker", email="marker-pi-email@example.org")
    pi.email = "MARKER-PI-EMAIL@example.org"
    pi.save()
    partner = AssociatedPartnerPIFactory()
    sub = ServiceSubmissionFactory(
        status="approved",
        biotools_url="",
        responsible_pis=[pi, partner],
        **PRIVATE,
    )
    sub.secondary_maturity_tags = ["unstable"]
    sub.register_as_elixir = True
    sub.last_change_summary = {"changes": [{"old": "MARKER-SUMMARY"}]}
    sub.save()
    from apps.catalogue import publishing

    publishing.publish(sub, reason="approval", actor="admin:jane")
    others = [
        ServiceSubmissionFactory(
            status=status,
            biotools_url="",
            service_name=f"MARKER-UNAPPROVED-NAME-{status}",
            **PRIVATE,
        )
        for status in ("draft", "submitted", "under_review", "rejected", "deprecated")
    ]
    record = BioToolsRecordFactory(
        submission=sub, sync_error="MARKER-SYNC-ERROR", raw_json={"x": "MARKER-RAW"}
    )
    return {"sub": sub, "others": others, "record": record}


def _markers():
    return [*PRIVATE.values(), *OTHER_MARKERS]


def _urls(world):
    base = [
        "/catalogue/",
        "/catalogue/?view=list",
        "/catalogue/?q=MARKER",
        "/catalogue/?q=Test",
        "/catalogue/?group_by=pi",
        "/catalogue/?group_by=category",
        "/catalogue/?sort=updated_desc&view=list",
        "/catalogue/?page=2",
        "/catalogue/grid/",
        "/catalogue/grid/?view=list&q=Test",
        "/catalogue/filters/",
        "/register/",
        "/update/",
        "/api/v1/categories/",
        "/api/v1/service-centers/",
        "/api/v1/pis/",
        "/api/v1/edam/",
        "/api/v1/submissions/",
        f"/api/v1/submissions/{world['sub'].pk}/",
        f"/api/v1/biotools/{world['record'].biotools_id}/",
        "/api/v1/biotools/",
        "/api/schema/?format=json",
        "/health/",
    ]
    return base


def _fetch(url):
    return APIClient().get(url)


def test_no_marker_appears_on_any_anonymous_page_or_endpoint(world):
    unexpected = {}
    for url in _urls(world):
        resp = _fetch(url)
        body = resp.content.decode("utf-8", errors="replace")
        found = [m for m in _markers() if m in body]
        if found:
            unexpected[url] = found
    assert unexpected == {}


def test_the_catalogue_still_shows_the_published_service(world):
    """Guard against a vacuous pass: the pages do list the approved service."""
    body = _fetch("/catalogue/?view=list").content.decode()
    assert world["sub"].service_name in body


def test_unapproved_services_are_not_in_the_catalogue(world):
    body = _fetch("/catalogue/").content.decode()
    assert "MARKER-UNAPPROVED-NAME" not in body


def test_published_rows_carry_no_marker(world):
    blob = json.dumps([r.content for r in PublishedRevision.objects.all()], default=str)
    for ps in PublishedService.objects.all():
        blob += json.dumps(
            {f.attname: str(getattr(ps, f.attname)) for f in ps._meta.concrete_fields}
        )
    for marker in _markers():
        assert marker not in blob


def test_markers_really_are_stored_in_the_working_copy(world):
    """Guard against a vacuous pass: every marker is stored somewhere."""
    from django.core import serializers

    from apps.biotools.models import BioToolsRecord
    from apps.registry.models import PrincipalInvestigator

    stored = ServiceSubmission.objects.get(pk=world["sub"].pk)
    assert stored.comments == "MARKER-COMMENTS"
    assert stored.internal_contact_email == "marker-internal@example.org"
    dump = serializers.serialize(
        "json",
        [
            *ServiceSubmission.objects.all(),
            *PrincipalInvestigator.objects.all(),
            *BioToolsRecord.objects.all(),
        ],
    )
    for marker in _markers():
        assert marker in dump, marker


def test_secondary_maturity_tags_are_not_shown(world):
    """Secondary tags are private; their label must not reach the catalogue."""
    assert ServiceSubmission.objects.get(pk=world["sub"].pk).secondary_maturity_tags
    for url in _urls(world):
        if url.startswith("/catalogue/"):
            assert "Unstable" not in _fetch(url).content.decode(), url
