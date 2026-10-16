"""Shared request-payload builders for tests.

Plain functions (not fixtures) so any test module can import them without
importing another test module.
"""

import re

from tests.factories import PIFactory, ServiceCategoryFactory, ServiceCenterFactory


def base_form_data(overrides=None):
    """Return a dict of minimal valid POST data for SubmissionForm."""
    from django.utils import timezone

    cat = ServiceCategoryFactory()
    center = ServiceCenterFactory()
    pi = PIFactory()

    data = {
        # Section A
        "date_of_entry": timezone.now().date().isoformat(),
        "submitter_first_name": "Test",
        "submitter_last_name": "Researcher",
        "submitter_affiliation": "Test University",
        "register_as_elixir": False,
        # Section B
        "service_name": "Test Service",
        "service_description": "A detailed description of the test service exceeding fifty characters minimum.",
        "year_established": 2020,
        "service_categories": [cat.pk],
        "is_toolbox": False,
        "toolbox_name": "",
        "user_knowledge_required": "",
        "publications_pmids": "12345678",
        # Section C
        "responsible_pis": [pi.pk],
        "associated_partner_note": "",
        "host_institute": "Test Institute",
        "service_center": center.pk,
        "public_contact_email": "public@example.com",
        "internal_contact_name": "Test Contact, Institute",
        "internal_contact_email": "internal@example.com",
        "internal_contact_email_confirm": "internal@example.com",
        # Section D
        "website_url": "https://example.com",
        "terms_of_use_url": "https://example.com/tos",
        "licenses": [],
        "license_note": "Other",
        "github_url": "",
        "biotools_url": "",
        "fairsharing_url": "",
        "other_registry_url": "",
        # Section E
        "kpi_monitoring": "yes",
        "kpi_start_year": "2021",
        # Section F
        "keywords_uncited": "",
        "keywords_seo": "",
        "survey_participation": True,
        "comments": "",
        # Section G
        "data_protection_consent": True,
    }
    if overrides:
        data.update(overrides)
    return data


def edit_form_data(sub, **overrides):
    """Build a complete POST payload for the edit view from a submission instance."""
    data = {
        "date_of_entry": sub.date_of_entry.isoformat(),
        "submitter_first_name": sub.submitter_first_name,
        "submitter_last_name": sub.submitter_last_name,
        "submitter_affiliation": sub.submitter_affiliation,
        "register_as_elixir": str(sub.register_as_elixir),
        "service_name": sub.service_name,
        "service_description": sub.service_description,
        "year_established": sub.year_established,
        "service_categories": [c.pk for c in sub.service_categories.all()],
        "is_toolbox": str(sub.is_toolbox),
        "toolbox_name": sub.toolbox_name or "",
        "user_knowledge_required": sub.user_knowledge_required or "",
        "publications_pmids": sub.publications_pmids or "",
        "responsible_pis": [p.pk for p in sub.responsible_pis.all()],
        "associated_partner_note": sub.associated_partner_note or "",
        "host_institute": sub.host_institute,
        "service_center": sub.service_center.pk,
        "public_contact_email": sub.public_contact_email,
        "internal_contact_name": sub.internal_contact_name,
        "internal_contact_email": sub.internal_contact_email,
        "internal_contact_email_confirm": sub.internal_contact_email,
        "website_url": sub.website_url,
        "terms_of_use_url": sub.terms_of_use_url,
        "licenses": [lic.pk for lic in sub.licenses.all()],
        "license_note": sub.license_note or "",
        "github_url": sub.github_url or "",
        "biotools_url": sub.biotools_url or "",
        "fairsharing_url": sub.fairsharing_url or "",
        "other_registry_url": sub.other_registry_url or "",
        "kpi_monitoring": sub.kpi_monitoring,
        "kpi_start_year": sub.kpi_start_year or "",
        "keywords_uncited": sub.keywords_uncited or "",
        "keywords_seo": sub.keywords_seo or "",
        "survey_participation": str(sub.survey_participation),
        "comments": sub.comments or "",
        "data_protection_consent": str(sub.data_protection_consent),
    }
    data.update(overrides)
    return data


def edit_form_payload(sub, **overrides):
    """Minimal admin change-view POST payload for a ServiceSubmission."""
    payload = {
        "date_of_entry": sub.date_of_entry.isoformat(),
        "submitter_first_name": sub.submitter_first_name,
        "submitter_last_name": sub.submitter_last_name,
        "submitter_affiliation": sub.submitter_affiliation,
        "register_as_elixir": "False",
        "service_name": sub.service_name,
        "service_description": sub.service_description,
        "year_established": str(sub.year_established),
        "service_categories": [c.pk for c in sub.service_categories.all()],
        "is_toolbox": "False",
        "toolbox_name": "",
        "user_knowledge_required": sub.user_knowledge_required or "",
        "publications_pmids": sub.publications_pmids,
        "responsible_pis": [p.pk for p in sub.responsible_pis.all()],
        "associated_partner_note": "",
        "host_institute": sub.host_institute,
        "service_center": sub.service_center.pk,
        "public_contact_email": sub.public_contact_email,
        "internal_contact_name": sub.internal_contact_name,
        "internal_contact_email": sub.internal_contact_email,
        "website_url": sub.website_url,
        "terms_of_use_url": sub.terms_of_use_url,
        "licenses": [lic.pk for lic in sub.licenses.all()],
        "license_note": sub.license_note or "",
        "github_url": sub.github_url or "",
        "biotools_url": sub.biotools_url or "",
        "fairsharing_url": sub.fairsharing_url or "",
        "other_registry_url": sub.other_registry_url or "",
        "kpi_monitoring": sub.kpi_monitoring,
        "kpi_start_year": sub.kpi_start_year or "",
        "keywords_uncited": sub.keywords_uncited or "",
        "keywords_seo": sub.keywords_seo or "",
        "survey_participation": "True",
        "comments": sub.comments or "",
        "data_protection_consent": "True",
        # Required Django admin hidden fields
        "_save": "Save",
        "api_keys-TOTAL_FORMS": "0",
        "api_keys-INITIAL_FORMS": "0",
        "api_keys-MIN_NUM_FORMS": "0",
        "api_keys-MAX_NUM_FORMS": "0",
        "status_events-TOTAL_FORMS": "0",
        "status_events-INITIAL_FORMS": "0",
        "status_events-MIN_NUM_FORMS": "0",
        "status_events-MAX_NUM_FORMS": "0",
        "edam_topics": [],
        "edam_operations": [],
        "primary_maturity_tag": sub.primary_maturity_tag or "",
        "secondary_maturity_tags": sub.secondary_maturity_tags or [],
    }
    payload.update(overrides)
    return payload


def api_create_payload():
    """Complete, valid POST payload for API submission creation."""
    from django.utils import timezone

    cat = ServiceCategoryFactory()
    center = ServiceCenterFactory()
    pi = PIFactory()
    return {
        "date_of_entry": timezone.now().date().isoformat(),
        "submitter_first_name": "Jane",
        "submitter_last_name": "Doe",
        "submitter_affiliation": "Example Institute",
        "register_as_elixir": False,
        "service_name": "Example API Service",
        "service_description": (
            "A description created through the API that is long enough to pass "
            "the minimum length validation of the model."
        ),
        "year_established": 2021,
        "service_category_ids": [cat.pk],
        "is_toolbox": False,
        "publications_pmids": "12345678",
        "responsible_pi_ids": [str(pi.pk)],
        "host_institute": "Example Institute",
        "service_center_id": str(center.pk),
        "public_contact_email": "contact@example.org",
        "internal_contact_name": "John Doe",
        "internal_contact_email": "john.doe@example.org",
        "website_url": "https://example.org",
        "terms_of_use_url": "https://example.org/terms",
        "licenses": [],
        "license_note": "Custom licence",
        "kpi_monitoring": "planned",
        "kpi_start_year": "2021",
        "survey_participation": True,
        "data_protection_consent": True,
    }


def list_desc(content: bytes) -> str:
    m = re.search(
        r'<div class="catalogue-list-desc-text md-rendered">(.*?)</div>',
        content.decode(),
        re.DOTALL,
    )
    assert m, "list-view description container not found"
    return m.group(1)


def card_desc(content: bytes) -> str:
    m = re.search(
        r'<p class="text-muted small catalogue-description mb-3">(.*?)</p>',
        content.decode(),
        re.DOTALL,
    )
    assert m, "card description not found"
    return m.group(1)


def status_history_drift():
    """(id, status, latest event status) for submissions whose latest status
    event does not match their current status."""
    from apps.submissions.selectors import status_history_drift as drift

    return drift()


def status_history_broken():
    """Ids of submissions whose status events do not form one chain."""
    from apps.submissions.selectors import status_history_broken as broken

    return broken()
