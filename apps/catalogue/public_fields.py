"""
The public field allowlist: the only ServiceSubmission fields that may reach
any public output (catalogue, detail page, public API, metadata exports).

Pinned by tests/test_publishing.py. ``publications_pmids`` is copied so later
lookups use the published value.

Changing these lists changes the revision content: raise
PublishedRevision.SCHEMA_VERSION and add a data migration that publishes the
approved services again. Older revisions stay valid (missing fields get the
read model's default, see publishing.complete_content).
"""

PUBLIC_SCALAR_FIELDS = (
    "service_name",
    "service_description",
    "user_knowledge_required",
    "logo",
    "primary_maturity_tag",
    "host_institute",
    "year_established",
    "is_toolbox",
    "toolbox_name",
    "website_url",
    "terms_of_use_url",
    "license_note",
    "public_contact_email",
    "github_url",
    "biotools_url",
    "fairsharing_url",
    "other_registry_url",
    "keywords_seo",
    "publications_pmids",
)
PUBLIC_FK_FIELDS = ("service_center",)
PUBLIC_M2M_FIELDS = (
    "service_categories",
    "responsible_pis",
    "edam_topics",
    "edam_operations",
    "licenses",
)
ALL_PUBLIC_FIELDS = PUBLIC_SCALAR_FIELDS + PUBLIC_FK_FIELDS + PUBLIC_M2M_FIELDS
