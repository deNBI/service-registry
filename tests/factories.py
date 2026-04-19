"""
Test Factories
==============
factory_boy factories for creating test fixtures.
All factories produce valid, minimal model instances by default.
Override individual fields in tests as needed.

Usage:
    from tests.factories import ServiceSubmissionFactory, APIKeyFactory

    sub = ServiceSubmissionFactory()                      # one valid submission
    sub = ServiceSubmissionFactory(service_name="Galaxy") # override a field
    key, plaintext = APIKeyFactory.create_with_plaintext(submission=sub)
"""

import secrets
from datetime import date

import factory
from factory.django import DjangoModelFactory

from apps.biotools.models import BioToolsFunction, BioToolsRecord
from apps.registry.models import PrincipalInvestigator, ServiceCategory, ServiceCenter
from apps.submissions.models import ServiceSubmission, SubmissionAPIKey, _hash_key


# ---------------------------------------------------------------------------
# Registry factories
# ---------------------------------------------------------------------------


class ServiceCategoryFactory(DjangoModelFactory):
    class Meta:
        model = ServiceCategory
        django_get_or_create = ("name",)

    name = factory.Sequence(lambda n: f"Category {n}")
    is_active = True


class ServiceCenterFactory(DjangoModelFactory):
    class Meta:
        model = ServiceCenter
        django_get_or_create = ("short_name",)

    short_name = factory.Sequence(lambda n: f"CTR{n}")
    full_name = factory.LazyAttribute(lambda o: f"Test Centre {o.short_name}")
    website = "https://example.com"
    is_active = True


class PIFactory(DjangoModelFactory):
    class Meta:
        model = PrincipalInvestigator
        django_get_or_create = ("last_name", "first_name")

    last_name = factory.Sequence(lambda n: f"Scientist{n}")
    first_name = "Test"
    email = factory.LazyAttribute(lambda o: f"{o.last_name.lower()}@example.com")
    institute = "Test Institute"
    is_active = True
    is_associated_partner = False


class AssociatedPartnerPIFactory(PIFactory):
    """The special 'Associated partner' PI entry."""

    last_name = "Associated partner"
    first_name = "[please state below]"
    is_associated_partner = True


# ---------------------------------------------------------------------------
# ServiceSubmission factory
# ---------------------------------------------------------------------------


class ServiceSubmissionFactory(DjangoModelFactory):
    class Meta:
        model = ServiceSubmission
        skip_postgeneration_save = True

    # Section A
    date_of_entry = factory.LazyFunction(date.today)
    submitter_first_name = "Test"
    submitter_last_name = "Researcher"
    submitter_affiliation = "Test University"
    register_as_elixir = False

    # Section B
    service_name = factory.Sequence(lambda n: f"Test Service {n}")
    service_description = factory.LazyFunction(
        lambda: "This is a detailed test service description that exceeds the minimum length requirement. "
        * 2
    )
    year_established = 2020
    is_toolbox = False
    toolbox_name = ""
    user_knowledge_required = ""
    publications_pmids = "12345678"

    # Section C
    host_institute = "Test University Hospital"
    service_center = factory.SubFactory(ServiceCenterFactory)
    public_contact_email = "public@example.com"
    internal_contact_name = "Test Contact, Test University"
    internal_contact_email = "internal@example.com"
    associated_partner_note = ""

    # Section D
    website_url = "https://example.com/service"
    terms_of_use_url = "https://example.com/tos"
    license_note = "Not applicable"
    github_url = ""
    biotools_url = ""
    fairsharing_url = ""
    other_registry_url = ""

    # Section E
    kpi_monitoring = "yes"
    kpi_start_year = "2021"

    # Section F
    keywords_uncited = "test tool"
    keywords_seo = "bioinformatics test"
    survey_participation = True
    comments = ""

    # Section G
    data_protection_consent = True

    # Meta
    status = "submitted"
    primary_maturity_tag = None
    secondary_maturity_tags = factory.LazyFunction(lambda: [])

    @factory.post_generation
    def service_categories(self, create, extracted, **kwargs):
        if not create:
            return
        if extracted is not None:
            for cat in extracted:
                self.service_categories.add(cat)
        else:
            self.service_categories.add(ServiceCategoryFactory())

    @factory.post_generation
    def responsible_pis(self, create, extracted, **kwargs):
        if not create:
            return
        if extracted is not None:
            for pi in extracted:
                self.responsible_pis.add(pi)
        else:
            self.responsible_pis.add(PIFactory())


# ---------------------------------------------------------------------------
# BioToolsRecord factory
# ---------------------------------------------------------------------------


class BioToolsRecordFactory(DjangoModelFactory):
    class Meta:
        model = BioToolsRecord

    submission = factory.SubFactory(ServiceSubmissionFactory)
    biotools_id = factory.Sequence(lambda n: f"testtool{n}")
    name = factory.LazyAttribute(lambda o: f"Test Tool {o.biotools_id}")
    description = "A test bio.tools record."
    raw_json = factory.LazyFunction(dict)


class BioToolsFunctionFactory(DjangoModelFactory):
    class Meta:
        model = BioToolsFunction

    record = factory.SubFactory(BioToolsRecordFactory)
    position = factory.Sequence(lambda n: n)
    operations = factory.LazyFunction(list)
    inputs = factory.LazyFunction(list)
    outputs = factory.LazyFunction(list)


# ---------------------------------------------------------------------------
# SubmissionAPIKey factory
# ---------------------------------------------------------------------------


class APIKeyFactory(DjangoModelFactory):
    class Meta:
        model = SubmissionAPIKey

    submission = factory.SubFactory(ServiceSubmissionFactory)
    key_hash = factory.LazyFunction(lambda: _hash_key(secrets.token_urlsafe(48)))
    label = "Test key"
    created_by = "submitter"
    is_active = True
    scope = "write"
    last_used_at = None

    @classmethod
    def create_with_plaintext(cls, **kwargs) -> tuple[SubmissionAPIKey, str]:
        """
        Create a key and return (instance, plaintext).
        Use this in auth tests where you need the actual plaintext.
        """
        submission = kwargs.get("submission") or ServiceSubmissionFactory()
        key_obj, plaintext = SubmissionAPIKey.create_for_submission(
            submission=submission,
            label=kwargs.get("label", "Test key"),
            created_by=kwargs.get("created_by", "submitter"),
        )
        return key_obj, plaintext
