"""Publishing layer: models, allowlist, publish/republish/state, rebuild."""

import json
from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.catalogue import publishing
from apps.catalogue.models import PublishedRevision, PublishedService, WithdrawnService
from apps.catalogue.public_fields import (
    ALL_PUBLIC_FIELDS,
    PUBLIC_FK_FIELDS,
    PUBLIC_M2M_FIELDS,
    PUBLIC_SCALAR_FIELDS,
)
from apps.edam.models import EdamTerm
from apps.licenses.models import SpdxLicense
from apps.submissions.lifecycle import set_status
from apps.submissions.models import (
    ServiceSubmission,
    SubmissionChangeLog,
    SubmissionStatusEvent,
)
from tests.factories import (
    PIFactory,
    ServiceCategoryFactory,
    ServiceSubmissionFactory,
)

pytestmark = pytest.mark.django_db

NEVER_PUBLIC = {
    "submitter_first_name",
    "submitter_last_name",
    "submitter_affiliation",
    "internal_contact_name",
    "internal_contact_email",
    "comments",
    "kpi_monitoring",
    "kpi_start_year",
    "survey_participation",
    "data_protection_consent",
    "keywords_uncited",
    "submission_ip",
    "user_agent_hash",
    "date_of_entry",
    "submitted_at",
    "updated_at",
    "last_change_summary",
    "associated_partner_note",
    "register_as_elixir",
    "secondary_maturity_tags",
    "status",
    "version",
}
# Not content: identity.
STRUCTURAL = {"id"}


def test_allowlist_is_pinned():
    assert PUBLIC_SCALAR_FIELDS == (
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
    assert PUBLIC_FK_FIELDS == ("service_center",)
    assert PUBLIC_M2M_FIELDS == (
        "service_categories",
        "responsible_pis",
        "edam_topics",
        "edam_operations",
        "licenses",
    )
    assert ALL_PUBLIC_FIELDS == (
        PUBLIC_SCALAR_FIELDS + PUBLIC_FK_FIELDS + PUBLIC_M2M_FIELDS
    )
    assert not (set(ALL_PUBLIC_FIELDS) & NEVER_PUBLIC)


def test_every_submission_field_is_classified_as_public_or_private():
    """A new ServiceSubmission field must be added to one of the two lists."""
    names = {
        f.name
        for f in ServiceSubmission._meta.get_fields()
        if not f.auto_created or f.concrete
    }
    unclassified = names - set(ALL_PUBLIC_FIELDS) - NEVER_PUBLIC - STRUCTURAL
    assert unclassified == set()


def test_published_service_mirrors_submission_fields():
    for name in ALL_PUBLIC_FIELDS:
        pub = PublishedService._meta.get_field(name)
        src = ServiceSubmission._meta.get_field(name)
        assert type(pub) is type(src), name
        assert getattr(pub, "max_length", None) == getattr(src, "max_length", None), (
            name
        )
        assert list(getattr(pub, "choices", None) or []) == list(
            getattr(src, "choices", None) or []
        ), name
        assert pub.blank == src.blank, name
        if pub.is_relation:
            assert pub.related_model is src.related_model, name


def test_published_service_has_no_private_fields():
    names = {f.name for f in PublishedService._meta.get_fields()}
    assert not (names & (NEVER_PUBLIC - {"updated_at"}))


def test_published_service_updated_at_is_the_public_modified_time():
    from django.utils import timezone

    ps = PublishedService(public_modified_at=timezone.now())
    assert ps.updated_at == ps.public_modified_at


def test_withdrawn_service_stores_no_name_or_content():
    names = {f.name for f in WithdrawnService._meta.get_fields()}
    assert names == {"id", "withdrawn_at"}


def test_revision_refuses_updates():
    rev = PublishedRevision(
        submission_id=None, number=1, reason="approval", actor="system", content={}
    )
    rev._state.adding = False
    with pytest.raises(ValueError, match="immutable"):
        rev.save()


def test_saved_revision_cannot_be_saved_again():
    sub = ServiceSubmissionFactory(status="approved", biotools_url="")
    rev = PublishedRevision.objects.create(
        submission=sub,
        number=1,
        public_modified_at=timezone.now(),
        reason="approval",
        actor="system",
        content={"a": 1},
        content_hash="x",
    )
    rev.content = {"a": 2}
    with pytest.raises(ValueError, match="immutable"):
        rev.save()
    assert PublishedRevision.objects.get(pk=rev.pk).content == {"a": 1}


def test_revision_numbers_are_unique_per_submission():
    sub = ServiceSubmissionFactory(status="approved", biotools_url="")
    other = ServiceSubmissionFactory(status="approved", biotools_url="")
    kwargs = dict(
        public_modified_at=timezone.now(),
        reason="approval",
        actor="system",
        content={},
        content_hash="x",
    )
    PublishedRevision.objects.create(submission=sub, number=1, **kwargs)
    PublishedRevision.objects.create(submission=other, number=1, **kwargs)
    with pytest.raises(IntegrityError), transaction.atomic():
        PublishedRevision.objects.create(submission=sub, number=1, **kwargs)


def _approved(**kw):
    """Approved in the database but not published yet (the factory writes the
    status directly, so no publish hook runs)."""
    return ServiceSubmissionFactory(status="approved", biotools_url="", **kw)


def _topic(n=1):
    return EdamTerm.objects.create(
        uri=f"http://edamontology.org/topic_{9000 + n}",
        accession=f"topic_{9000 + n}",
        branch="topic",
        label=f"Example topic {n}",
    )


def _licence(spdx="MIT"):
    return SpdxLicense.objects.create(license_id=spdx, name=f"Example {spdx}")


class TestBuildContent:
    def test_content_has_exactly_the_allowlisted_keys(self):
        sub = _approved()
        content = publishing.build_content(sub)
        assert set(content) == set(ALL_PUBLIC_FIELDS)

    def test_content_is_json_serialisable_and_round_trips(self):
        sub = _approved()
        sub.edam_topics.add(_topic())
        sub.licenses.add(_licence())
        content = publishing.build_content(sub)
        assert json.loads(json.dumps(content)) == content

    def test_logo_is_stored_as_its_path(self):
        sub = _approved()
        ServiceSubmission.objects.filter(pk=sub.pk).update(logo="logos/example.png")
        sub.refresh_from_db()
        assert publishing.build_content(sub)["logo"] == "logos/example.png"

    def test_missing_logo_is_an_empty_string(self):
        assert publishing.build_content(_approved())["logo"] == ""

    def test_reference_rows_are_stored_as_sorted_ids(self):
        sub = _approved()
        a, b = _topic(1), _topic(2)
        sub.edam_topics.add(b, a)
        assert publishing.build_content(sub)["edam_topics"] == sorted([a.pk, b.pk])

    def test_ids_are_sorted_whatever_order_the_database_returns_them_in(self):
        class _Unordered:
            def values_list(self, *args, **kwargs):
                return [3, 1, 2]

        class _Submission:
            def __init__(self, real):
                self._real = real

            def __getattr__(self, name):
                if name in PUBLIC_M2M_FIELDS:
                    return _Unordered()
                return getattr(self._real, name)

        content = publishing.build_content(_Submission(_approved()))
        assert content["edam_topics"] == [1, 2, 3]

    def test_insertion_order_of_relations_does_not_change_the_hash(self):
        first, second = _topic(1), _topic(2)
        a, b = _approved(), _approved()
        ServiceSubmission.objects.filter(pk=b.pk).update(
            service_center=a.service_center,
            service_name=a.service_name,
            service_description=a.service_description,
        )
        b.refresh_from_db()
        a.edam_topics.add(first, second)
        b.edam_topics.add(second)
        b.edam_topics.add(first)
        a_content = publishing.build_content(a)
        b_content = publishing.build_content(b)
        assert a_content["edam_topics"] == b_content["edam_topics"]
        assert a_content["edam_topics"] == sorted([first.pk, second.pk])

    def test_service_center_and_pis_are_stored_as_ids(self):
        sub = _approved()
        content = publishing.build_content(sub)
        assert content["service_center"] == str(sub.service_center_id)
        assert content["responsible_pis"] == sorted(
            str(pk) for pk in sub.responsible_pis.values_list("pk", flat=True)
        )

    def test_private_fields_never_reach_the_content(self):
        markers = {
            "submitter_first_name": "MARKER-SUBMITTER-FIRST",
            "submitter_last_name": "MARKER-SUBMITTER-LAST",
            "submitter_affiliation": "MARKER-SUBMITTER-AFFILIATION",
            "internal_contact_name": "MARKER-INTERNAL-NAME",
            "internal_contact_email": "marker-internal@example.org",
            "comments": "MARKER-COMMENTS",
            "kpi_start_year": "MARKER-KPI",
            "keywords_uncited": "MARKER-UNCITED",
            "associated_partner_note": "MARKER-PARTNER-NOTE",
            "user_agent_hash": "MARKER-UA",
            "submission_ip": "203.0.113.77",
        }
        sub = _approved(**markers)
        sub.secondary_maturity_tags = ["unstable"]
        sub.last_change_summary = {"changes": [{"old": "MARKER-SUMMARY"}]}
        sub.register_as_elixir = True
        sub.save()
        publishing.publish(sub, reason="approval", actor="admin:jane")
        rendered = json.dumps(PublishedRevision.objects.get().content)
        assert "MARKER-SUMMARY" not in rendered
        assert "unstable" not in rendered
        for value in markers.values():
            assert value not in rendered
        ps = PublishedService.objects.get()
        for field in ps._meta.concrete_fields:
            value = str(getattr(ps, field.attname))
            for marker in markers.values():
                assert marker not in value


class TestPublish:
    def test_publish_creates_revision_and_read_model(self):
        sub = _approved()
        rev = publishing.publish(sub, reason="approval", actor="admin:jane")
        assert rev.number == 1
        assert rev.actor == "admin:jane"
        assert rev.reason == "approval"
        assert rev.schema_version == PublishedRevision.SCHEMA_VERSION
        assert rev.content["service_name"] == sub.service_name
        assert "comments" not in rev.content
        ps = PublishedService.objects.get(pk=sub.pk)
        assert ps.revision == rev
        assert ps.service_name == sub.service_name
        assert ps.state == PublishedService.ACTIVE
        assert ps.pending_update is False
        assert set(ps.service_categories.all()) == set(sub.service_categories.all())
        assert set(ps.responsible_pis.all()) == set(sub.responsible_pis.all())
        assert ps.service_center_id == sub.service_center_id

    def test_read_model_copies_every_public_value(self):
        sub = _approved(
            user_knowledge_required="Some background.",
            github_url="https://github.com/example/tool",
            toolbox_name="Example toolbox",
            is_toolbox=True,
            keywords_seo="alpha, beta",
        )
        sub.edam_topics.add(_topic())
        sub.licenses.add(_licence())
        publishing.publish(sub, reason="approval", actor="admin:jane")
        ps = PublishedService.objects.get(pk=sub.pk)
        for name in PUBLIC_SCALAR_FIELDS:
            if name == "logo":
                continue
            assert getattr(ps, name) == getattr(sub, name), name
        assert [t.pk for t in ps.edam_topics.all()] == [
            t.pk for t in sub.edam_topics.all()
        ]
        assert [x.pk for x in ps.licenses.all()] == [x.pk for x in sub.licenses.all()]

    def test_publish_does_not_touch_the_working_copy(self):
        sub = _approved()
        before = ServiceSubmission.objects.values().get(pk=sub.pk)
        publishing.publish(sub, reason="approval", actor="admin:jane")
        assert ServiceSubmission.objects.values().get(pk=sub.pk) == before

    def test_first_published_at_is_the_first_real_approval(self):
        sub = ServiceSubmissionFactory(biotools_url="")
        set_status(sub, "approved", actor="admin:jane")
        approved_at = sub.status_events.order_by("id").last().changed_at
        publishing.publish(sub, reason="approval", actor="admin:jane")
        assert PublishedService.objects.get(pk=sub.pk).first_published_at == approved_at

    def test_first_published_at_is_unknown_for_synthetic_approvals(self):
        sub = _approved()
        SubmissionStatusEvent.objects.filter(submission=sub).update(is_synthetic=True)
        publishing.publish(sub, reason="backfill", actor="system")
        assert PublishedService.objects.get(pk=sub.pk).first_published_at is None

    def test_first_published_at_does_not_move_on_republish(self):
        sub = ServiceSubmissionFactory(biotools_url="")
        set_status(sub, "approved", actor="admin:jane")
        publishing.publish(sub, reason="approval", actor="admin:jane")
        first = PublishedService.objects.get(pk=sub.pk).first_published_at
        sub.website_url = "https://example.org/moved"
        sub.save()
        publishing.publish(sub, reason="live_edit", actor="submitter")
        assert PublishedService.objects.get(pk=sub.pk).first_published_at == first

    def test_unchanged_content_is_a_no_op(self):
        sub = _approved()
        publishing.publish(sub, reason="approval", actor="admin:jane")
        modified = PublishedService.objects.get(pk=sub.pk).public_modified_at
        assert publishing.publish(sub, reason="live_edit", actor="submitter") is None
        assert PublishedRevision.objects.filter(submission=sub).count() == 1
        assert PublishedService.objects.get(pk=sub.pk).public_modified_at == modified

    @pytest.mark.parametrize(
        "field,value",
        [
            ("comments", "Private note"),
            ("internal_contact_email", "other@example.org"),
            ("kpi_start_year", "2019"),
            ("submitter_first_name", "Zoë"),
            ("keywords_uncited", "another term"),
            ("associated_partner_note", "Jane Doe, Example Institute"),
            ("register_as_elixir", True),
            ("secondary_maturity_tags", ["unstable"]),
        ],
    )
    def test_private_field_change_does_not_republish(self, field, value):
        sub = _approved()
        publishing.publish(sub, reason="approval", actor="admin:jane")
        setattr(sub, field, value)
        sub.save()
        assert publishing.publish(sub, reason="live_edit", actor="submitter") is None
        assert PublishedRevision.objects.filter(submission=sub).count() == 1

    @pytest.mark.parametrize(
        "field,value",
        [
            ("service_name", "Example Renamed"),
            (
                "service_description",
                "A new description that is long enough to be valid.",
            ),
            ("user_knowledge_required", "Read the manual first."),
            ("primary_maturity_tag", "mature"),
            ("host_institute", "Example Host Institute"),
            ("year_established", 2015),
            ("is_toolbox", True),
            ("toolbox_name", "Example toolbox"),
            ("website_url", "https://example.org/new"),
            ("terms_of_use_url", "https://example.org/new-terms"),
            ("license_note", "Custom"),
            ("public_contact_email", "other@example.org"),
            ("github_url", "https://github.com/example/tool"),
            ("fairsharing_url", "https://fairsharing.org/example"),
            ("other_registry_url", "https://example.org/registry"),
            ("keywords_seo", "new, words"),
            ("publications_pmids", "87654321"),
        ],
    )
    def test_each_public_scalar_change_republishes(self, field, value):
        sub = _approved()
        publishing.publish(sub, reason="approval", actor="admin:jane")
        setattr(sub, field, value)
        sub.save()
        rev = publishing.publish(sub, reason="live_edit", actor="submitter")
        assert rev is not None and rev.number == 2
        assert getattr(PublishedService.objects.get(pk=sub.pk), field) == value

    def test_biotools_url_change_republishes(self):
        sub = _approved()
        publishing.publish(sub, reason="approval", actor="admin:jane")
        ServiceSubmission.objects.filter(pk=sub.pk).update(
            biotools_url="https://bio.tools/example"
        )
        sub.refresh_from_db()
        assert publishing.publish(sub, reason="live_edit", actor="submitter")

    @pytest.mark.parametrize("relation", PUBLIC_M2M_FIELDS)
    def test_each_public_relation_change_republishes(self, relation):
        sub = _approved()
        publishing.publish(sub, reason="approval", actor="admin:jane")
        extra = {
            "service_categories": ServiceCategoryFactory,
            "responsible_pis": PIFactory,
            "edam_topics": _topic,
            "edam_operations": lambda: EdamTerm.objects.create(
                uri="http://edamontology.org/operation_9001",
                accession="operation_9001",
                branch="operation",
                label="Example operation",
            ),
            "licenses": _licence,
        }[relation]()
        getattr(sub, relation).add(extra)
        rev = publishing.publish(sub, reason="live_edit", actor="submitter")
        assert rev is not None and rev.number == 2
        assert extra in getattr(PublishedService.objects.get(pk=sub.pk), relation).all()

    def test_service_center_change_republishes(self):
        from tests.factories import ServiceCenterFactory

        sub = _approved()
        publishing.publish(sub, reason="approval", actor="admin:jane")
        sub.service_center = ServiceCenterFactory()
        sub.save()
        assert publishing.publish(sub, reason="admin_edit", actor="admin:jane")
        assert (
            PublishedService.objects.get(pk=sub.pk).service_center_id
            == sub.service_center_id
        )

    def test_removing_a_relation_republishes(self):
        sub = _approved()
        topic = _topic()
        sub.edam_topics.add(topic)
        publishing.publish(sub, reason="approval", actor="admin:jane")
        sub.edam_topics.remove(topic)
        assert publishing.publish(sub, reason="live_edit", actor="submitter")
        assert PublishedService.objects.get(pk=sub.pk).edam_topics.count() == 0

    def test_public_change_creates_next_revision(self):
        sub = _approved()
        publishing.publish(sub, reason="approval", actor="admin:jane")
        sub.github_url = "https://github.com/example/tool"
        sub.save()
        sub.responsible_pis.add(PIFactory())
        before = PublishedService.objects.get(pk=sub.pk).public_modified_at
        rev = publishing.publish(sub, reason="live_edit", actor="submitter")
        assert rev.number == 2
        ps = PublishedService.objects.get(pk=sub.pk)
        assert ps.github_url == "https://github.com/example/tool"
        assert ps.responsible_pis.count() == sub.responsible_pis.count()
        assert ps.public_modified_at != before  # moved (the clock can step back)
        assert ps.revision == rev

    def test_old_revisions_are_kept_unchanged(self):
        sub = _approved()
        first = publishing.publish(sub, reason="approval", actor="admin:jane")
        original = dict(first.content)
        sub.website_url = "https://example.org/moved"
        sub.save()
        publishing.publish(sub, reason="live_edit", actor="submitter")
        assert PublishedRevision.objects.get(pk=first.pk).content == original

    def test_revision_numbers_increase_per_submission(self):
        a, b = _approved(), _approved()
        publishing.publish(a, reason="approval", actor="x")
        for i in range(3):
            a.website_url = f"https://example.org/{i}"
            a.save()
            publishing.publish(a, reason="live_edit", actor="x")
        publishing.publish(b, reason="approval", actor="x")
        assert list(
            PublishedRevision.objects.filter(submission=a)
            .order_by("number")
            .values_list("number", flat=True)
        ) == [1, 2, 3, 4]
        assert PublishedRevision.objects.get(submission=b).number == 1

    def test_publish_reads_the_database_row_not_a_stale_instance(self):
        sub = _approved()
        stale = ServiceSubmission.objects.get(pk=sub.pk)
        ServiceSubmission.objects.filter(pk=sub.pk).update(
            website_url="https://example.org/db"
        )
        publishing.publish(stale, reason="approval", actor="admin:jane")
        assert (
            PublishedService.objects.get(pk=sub.pk).website_url
            == "https://example.org/db"
        )

    def test_publish_is_atomic_when_the_read_model_write_fails(self, monkeypatch):
        sub = _approved()

        def boom(*args, **kwargs):
            raise RuntimeError("read model write failed")

        monkeypatch.setattr(publishing, "_apply_m2m", boom)
        with pytest.raises(RuntimeError):
            publishing.publish(sub, reason="approval", actor="admin:jane")
        assert not PublishedRevision.objects.filter(submission=sub).exists()
        assert not PublishedService.objects.filter(pk=sub.pk).exists()

    def test_missing_read_model_is_recreated_without_a_new_revision(self):
        sub = _approved()
        publishing.publish(sub, reason="approval", actor="admin:jane")
        PublishedService.objects.filter(pk=sub.pk).delete()
        assert publishing.publish(sub, reason="live_edit", actor="submitter") is None
        assert PublishedRevision.objects.filter(submission=sub).count() == 1
        assert PublishedService.objects.filter(pk=sub.pk).exists()

    def test_content_hash_is_order_independent(self):
        a = {"x": 1, "y": [1, 2]}
        b = {"y": [1, 2], "x": 1}
        assert publishing.content_hash(a) == publishing.content_hash(b)
        assert publishing.content_hash(a) != publishing.content_hash(
            {"x": 2, "y": [1, 2]}
        )

    def test_unicode_content_hashes_consistently(self):
        sub = _approved(service_name="Example Zoë Service \U0001f9ec")
        publishing.publish(sub, reason="approval", actor="admin:jane")
        assert publishing.publish(sub, reason="live_edit", actor="submitter") is None
        assert (
            PublishedService.objects.get(pk=sub.pk).service_name
            == "Example Zoë Service \U0001f9ec"
        )


class TestRepublish:
    def test_republish_requires_existing_publication(self):
        sub = _approved()
        assert publishing.republish(sub, reason="live_edit", actor="submitter") is None
        assert not PublishedService.objects.filter(pk=sub.pk).exists()

    @pytest.mark.parametrize(
        "path",
        [
            ("under_review",),
            ("under_review", "rejected"),
            ("deprecated",),
            ("deprecated", "submitted"),
        ],
    )
    def test_republish_requires_approved_status(self, path):
        sub = _approved()
        publishing.publish(sub, reason="approval", actor="admin:jane")
        for status in path:
            set_status(sub, status, actor="admin:jane")
        sub.website_url = "https://example.org/new"
        sub.save()
        assert publishing.republish(sub, reason="live_edit", actor="submitter") is None
        assert (
            PublishedService.objects.get(pk=sub.pk).website_url
            != "https://example.org/new"
        )

    def test_republish_after_an_owner_edit_reset_does_nothing(self):
        sub = _approved()
        publishing.publish(sub, reason="approval", actor="admin:jane")
        set_status(sub, "submitted", actor="submitter", owner_edit=True)
        sub.website_url = "https://example.org/new"
        sub.save()
        assert publishing.republish(sub, reason="live_edit", actor="submitter") is None

    def test_republish_uses_the_database_status_not_the_instance(self):
        sub = _approved()
        publishing.publish(sub, reason="approval", actor="admin:jane")
        stale = ServiceSubmission.objects.get(pk=sub.pk)
        set_status(sub, "under_review", actor="admin:jane")
        assert stale.status == "approved"
        assert (
            publishing.republish(stale, reason="live_edit", actor="submitter") is None
        )

    def test_republish_publishes_changes(self):
        sub = _approved()
        publishing.publish(sub, reason="approval", actor="admin:jane")
        sub.website_url = "https://example.org/new"
        sub.save()
        rev = publishing.republish(sub, reason="live_edit", actor="submitter")
        assert rev is not None and rev.number == 2

    def test_republish_of_a_deprecated_service_does_nothing(self):
        sub = _approved()
        publishing.publish(sub, reason="approval", actor="admin:jane")
        set_status(sub, "deprecated", actor="admin:jane")
        sub.website_url = "https://example.org/new"
        sub.save()
        assert (
            publishing.republish(sub, reason="admin_edit", actor="admin:jane") is None
        )


class TestStateChanges:
    def _published(self):
        sub = _approved()
        publishing.publish(sub, reason="approval", actor="admin:jane")
        return sub

    def _ps(self, sub):
        return PublishedService.objects.get(pk=sub.pk)

    @pytest.mark.parametrize("target", ["submitted", "under_review"])
    def test_unapprove_sets_pending(self, target):
        sub = self._published()
        publishing.on_status_change(sub, target, actor="submitter")
        ps = self._ps(sub)
        assert ps.pending_update is True and ps.state == "active"

    def test_deprecate_published(self):
        sub = self._published()
        before = self._ps(sub).public_modified_at
        publishing.on_status_change(sub, "deprecated", actor="admin:jane")
        ps = self._ps(sub)
        assert ps.state == "deprecated"
        assert ps.public_modified_at != before  # moved (the clock can step back)

    def test_deprecate_clears_a_pending_update(self):
        sub = self._published()
        publishing.on_status_change(sub, "submitted", actor="submitter")
        publishing.on_status_change(sub, "deprecated", actor="admin:jane")
        ps = self._ps(sub)
        assert (ps.state, ps.pending_update) == ("deprecated", False)

    def test_deprecate_never_published_does_nothing(self):
        sub = ServiceSubmissionFactory(biotools_url="")
        publishing.on_status_change(sub, "deprecated", actor="submitter")
        assert not PublishedService.objects.filter(pk=sub.pk).exists()
        assert not PublishedRevision.objects.filter(submission=sub).exists()

    @pytest.mark.parametrize("target", ["submitted", "under_review", "rejected"])
    def test_non_approval_never_publishes_a_new_service(self, target):
        sub = ServiceSubmissionFactory(biotools_url="")
        publishing.on_status_change(sub, target, actor="admin:jane")
        assert not PublishedService.objects.filter(pk=sub.pk).exists()

    def test_undeprecate_keeps_deprecated_with_pending(self):
        sub = self._published()
        publishing.on_status_change(sub, "deprecated", actor="admin:jane")
        publishing.on_status_change(sub, "submitted", actor="admin:jane")
        ps = self._ps(sub)
        assert (ps.state, ps.pending_update) == ("deprecated", True)

    def test_reject_after_publication_keeps_last_version(self):
        sub = self._published()
        publishing.on_status_change(sub, "submitted", actor="submitter")
        publishing.on_status_change(sub, "rejected", actor="admin:jane")
        ps = self._ps(sub)
        assert (ps.state, ps.pending_update) == ("active", False)
        assert ps.service_name == sub.service_name

    def test_reapproval_reactivates(self):
        sub = self._published()
        publishing.on_status_change(sub, "deprecated", actor="admin:jane")
        publishing.on_status_change(sub, "approved", actor="admin:jane")
        ps = self._ps(sub)
        assert (ps.state, ps.pending_update) == ("active", False)

    def test_reapproval_publishes_the_reviewed_edit(self):
        sub = self._published()
        publishing.on_status_change(sub, "submitted", actor="submitter")
        sub.service_description = "The reviewed new description of the service. " * 2
        sub.save()
        publishing.on_status_change(sub, "approved", actor="admin:jane")
        ps = self._ps(sub)
        assert ps.service_description == sub.service_description
        assert ps.pending_update is False
        assert ps.revision.reason == "approval"

    def test_reapproval_without_content_change_clears_pending_only(self):
        sub = self._published()
        publishing.on_status_change(sub, "submitted", actor="submitter")
        before = self._ps(sub).public_modified_at
        publishing.on_status_change(sub, "approved", actor="admin:jane")
        ps = self._ps(sub)
        assert ps.pending_update is False
        assert ps.public_modified_at == before
        assert PublishedRevision.objects.filter(submission=sub).count() == 1

    def test_pending_toggle_does_not_bump_modified(self):
        sub = self._published()
        before = self._ps(sub).public_modified_at
        publishing.on_status_change(sub, "submitted", actor="submitter")
        publishing.on_status_change(sub, "rejected", actor="admin:jane")
        assert self._ps(sub).public_modified_at == before

    def test_state_change_does_not_create_a_revision(self):
        sub = self._published()
        publishing.on_status_change(sub, "deprecated", actor="admin:jane")
        assert PublishedRevision.objects.filter(submission=sub).count() == 1

    def test_set_publication_state_ignores_unknown_submission(self):
        import uuid

        publishing.set_publication_state(uuid.uuid4(), pending_update=True)


class TestRebuild:
    @staticmethod
    def _revision_without(sub, *names):
        """The stored revision as written before ``names`` were public fields."""
        rev = PublishedRevision.objects.get(submission=sub)
        content = {k: v for k, v in rev.content.items() if k not in names}
        PublishedRevision.objects.filter(pk=rev.pk).update(
            content=content, content_hash=publishing.content_hash(content)
        )

    def test_rebuild_from_a_revision_older_than_a_public_field(self):
        """Fields added to the public list later are missing from older
        revisions; the read model then gets the field's default."""
        sub = _approved(is_toolbox=True, toolbox_name="Example Toolbox")
        publishing.publish(sub, reason="approval", actor="admin:jane")
        self._revision_without(sub, "is_toolbox", "toolbox_name", "licenses")
        PublishedService.objects.filter(pk=sub.pk).delete()
        ps = publishing.rebuild_from_revision(sub.pk)
        assert ps.is_toolbox is False
        assert ps.toolbox_name == ""
        assert list(ps.licenses.all()) == []

    def test_complete_content_fills_every_public_field_with_its_default(self):
        content = publishing.complete_content({"service_name": "Example Kept"})
        assert set(content) == set(ALL_PUBLIC_FIELDS) - set(PUBLIC_FK_FIELDS)
        assert content["service_name"] == "Example Kept"
        assert content["is_toolbox"] is False
        assert content["logo"] == ""
        for name in PUBLIC_M2M_FIELDS:
            assert content[name] == []

    def test_complete_content_keeps_given_values_and_the_input(self):
        given = {"is_toolbox": True, "licenses": ["MIT"]}
        content = publishing.complete_content(given)
        assert (content["is_toolbox"], content["licenses"]) == (True, ["MIT"])
        assert given == {"is_toolbox": True, "licenses": ["MIT"]}

    def test_rebuild_of_an_existing_copy_from_an_older_revision(self):
        sub = _approved(is_toolbox=True)
        publishing.publish(sub, reason="approval", actor="admin:jane")
        self._revision_without(sub, "is_toolbox")
        ps = publishing.rebuild_from_revision(sub.pk)
        assert ps.is_toolbox is False

    def test_rebuild_restores_drifted_read_model(self):
        sub = _approved()
        publishing.publish(sub, reason="approval", actor="admin:jane")
        PublishedService.objects.filter(pk=sub.pk).update(service_name="Drifted")
        PublishedService.objects.get(pk=sub.pk).service_categories.clear()
        ps = publishing.rebuild_from_revision(sub.pk)
        assert ps.service_name == sub.service_name
        assert set(ps.service_categories.all()) == set(sub.service_categories.all())

    def test_rebuild_uses_the_latest_revision_not_the_working_copy(self):
        sub = _approved()
        publishing.publish(sub, reason="approval", actor="admin:jane")
        ServiceSubmission.objects.filter(pk=sub.pk).update(service_name="Edited later")
        ps = publishing.rebuild_from_revision(sub.pk)
        assert ps.service_name != "Edited later"

    def test_rebuild_keeps_state_and_pending_flags(self):
        sub = _approved()
        publishing.publish(sub, reason="approval", actor="admin:jane")
        publishing.on_status_change(sub, "deprecated", actor="admin:jane")
        publishing.on_status_change(sub, "submitted", actor="admin:jane")
        ps = publishing.rebuild_from_revision(sub.pk)
        assert (ps.state, ps.pending_update) == ("deprecated", True)

    def test_rebuild_recreates_a_missing_read_model(self):
        sub = _approved()
        publishing.publish(sub, reason="approval", actor="admin:jane")
        PublishedService.objects.filter(pk=sub.pk).delete()
        ps = publishing.rebuild_from_revision(sub.pk)
        assert ps.service_name == sub.service_name
        assert (ps.state, ps.pending_update) == ("active", False)

    def test_recreated_read_model_follows_the_current_status(self):
        sub = _approved()
        publishing.publish(sub, reason="approval", actor="admin:jane")
        set_status(sub, "deprecated", actor="admin:jane")
        PublishedService.objects.filter(pk=sub.pk).delete()
        assert publishing.rebuild_from_revision(sub.pk).state == "deprecated"

    def test_a_recreated_read_model_keeps_the_dates_of_the_service(self):
        sub = ServiceSubmissionFactory(biotools_url="")
        set_status(sub, "approved", actor="admin:jane")
        before = PublishedService.objects.get(pk=sub.pk)
        PublishedService.objects.filter(pk=sub.pk).delete()
        ps = publishing.rebuild_from_revision(sub.pk)
        assert ps.first_published_at == before.first_published_at is not None
        assert ps.public_modified_at == before.public_modified_at

    def test_a_recreated_read_model_keeps_the_date_given_to_the_copy(self):
        """A copy published for a change made long ago is not dated by the
        time of the rebuild nor by the time its revision was written."""
        sub = _approved()
        long_ago = timezone.now() - timedelta(days=400)
        publishing.publish(
            sub, reason="backfill", actor="system", public_modified_at=long_ago
        )
        PublishedService.objects.filter(pk=sub.pk).delete()
        ps = publishing.rebuild_from_revision(sub.pk)
        assert ps.public_modified_at == long_ago

    def test_a_recreated_read_model_takes_the_date_of_the_latest_revision(self):
        sub = _approved()
        long_ago = timezone.now() - timedelta(days=400)
        publishing.publish(
            sub, reason="backfill", actor="system", public_modified_at=long_ago
        )
        ServiceSubmission.objects.filter(pk=sub.pk).update(service_name="Example New")
        revision = publishing.publish(sub, reason="live_edit", actor="submitter")
        PublishedService.objects.filter(pk=sub.pk).delete()
        ps = publishing.rebuild_from_revision(sub.pk)
        assert ps.public_modified_at == revision.public_modified_at != long_ago

    def test_a_revision_records_the_date_given_to_the_copy(self):
        sub = _approved()
        long_ago = timezone.now() - timedelta(days=400)
        revision = publishing.publish(
            sub, reason="backfill", actor="system", public_modified_at=long_ago
        )
        assert revision.public_modified_at == long_ago
        assert PublishedService.objects.get(pk=sub.pk).public_modified_at == long_ago

    def test_rebuild_without_a_revision_fails(self):
        sub = _approved()
        with pytest.raises(PublishedRevision.DoesNotExist):
            publishing.rebuild_from_revision(sub.pk)

    def test_rebuild_refuses_a_newer_schema_version(self):
        sub = _approved()
        rev = publishing.publish(sub, reason="approval", actor="admin:jane")
        PublishedRevision.objects.filter(pk=rev.pk).update(
            schema_version=PublishedRevision.SCHEMA_VERSION + 1
        )
        with pytest.raises(ValueError, match="schema version"):
            publishing.rebuild_from_revision(sub.pk)

    def test_rebuild_skips_missing_reference_rows(self):
        cat = ServiceCategoryFactory()
        sub = _approved()
        sub.service_categories.add(cat)
        publishing.publish(sub, reason="approval", actor="admin:jane")
        sub.service_categories.remove(cat)
        cat.delete()
        ps = publishing.rebuild_from_revision(sub.pk)
        assert cat.pk not in {c.pk for c in ps.service_categories.all()}

    def test_rebuild_is_idempotent(self):
        sub = _approved()
        publishing.publish(sub, reason="approval", actor="admin:jane")
        first = publishing.rebuild_from_revision(sub.pk)
        modified = first.public_modified_at
        second = publishing.rebuild_from_revision(sub.pk)
        assert second.public_modified_at == modified
        assert PublishedRevision.objects.filter(submission=sub).count() == 1


class TestLocking:
    """SQLite ignores row locks, so these check that every writer asks for the
    submission row lock before it reads what it is about to write."""

    def test_publish_locks_the_submission(self, locked_models):
        sub = _approved()
        publishing.publish(sub, reason="approval", actor="admin:jane")
        assert "ServiceSubmission" in locked_models

    def test_republish_locks_before_it_reads_the_status(
        self, locked_models, monkeypatch
    ):
        sub = _approved()
        publishing.publish(sub, reason="approval", actor="admin:jane")
        del locked_models[:]
        monkeypatch.setattr(publishing, "publish", lambda *args, **kwargs: None)
        publishing.republish(sub, reason="live_edit", actor="submitter")
        assert locked_models == ["ServiceSubmission"]

    def test_set_publication_state_locks_the_submission(self, locked_models):
        sub = _approved()
        publishing.publish(sub, reason="approval", actor="admin:jane")
        del locked_models[:]
        publishing.set_publication_state(sub.pk, pending_update=True)
        assert locked_models == ["ServiceSubmission"]

    def test_rebuild_locks_the_submission(self, locked_models):
        sub = _approved()
        publishing.publish(sub, reason="approval", actor="admin:jane")
        del locked_models[:]
        publishing.rebuild_from_revision(sub.pk)
        assert "ServiceSubmission" in locked_models


# A value for every kind of public scalar field that differs from what the
# read model shows for a field a version does not have.
WORKING_VALUES = {
    "service_name": "Example Working Name",
    "service_description": "Example working description that is long enough.",
    "user_knowledge_required": "Example working knowledge.",
    "logo": "logos/working.png",
    "primary_maturity_tag": "mature",
    "host_institute": "Example Working Institute",
    "year_established": 2003,
    "is_toolbox": True,
    "toolbox_name": "Example Working Toolbox",
    "website_url": "https://example.org/working",
    "terms_of_use_url": "https://example.org/working-terms",
    "license_note": "Example working note",
    "public_contact_email": "jane.doe@example.org",
    "github_url": "https://github.com/example/working",
    "biotools_url": "https://bio.tools/example_working",
    "fairsharing_url": "https://fairsharing.org/working",
    "other_registry_url": "https://example.org/registry-working",
    "keywords_seo": "working, keywords",
    "publications_pmids": "12345",
}


def _with_a_value_in_every_public_field(n=1):
    """An approved working copy whose every public field holds a value other
    than what a missing field would be given."""
    sub = _approved()
    ServiceSubmission.objects.filter(pk=sub.pk).update(**WORKING_VALUES)
    sub = ServiceSubmission.objects.get(pk=sub.pk)
    sub.service_categories.set([ServiceCategoryFactory(), ServiceCategoryFactory()])
    sub.responsible_pis.set([PIFactory(), PIFactory()])
    sub.edam_topics.set([_topic(n)])
    sub.edam_operations.set(
        [
            EdamTerm.objects.create(
                uri=f"http://edamontology.org/operation_{9000 + n}",
                accession=f"operation_{9000 + n}",
                branch="operation",
                label=f"Example operation {n}",
            )
        ]
    )
    sub.licenses.set([_licence(f"Example-{n}.0")])
    return ServiceSubmission.objects.get(pk=sub.pk)


def _count_queries(func):
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    with CaptureQueriesContext(connection) as queries:
        result = func()
    return result, len(queries)


class TestBuildContentRelationQueries:
    def test_a_row_without_prefetched_relations_costs_one_query_per_relation(self):
        sub = _with_a_value_in_every_public_field()
        row = ServiceSubmission.objects.get(pk=sub.pk)
        _, queries = _count_queries(lambda: publishing.build_content(row))
        assert queries == len(PUBLIC_M2M_FIELDS)

    def test_prefetched_relations_are_read_without_queries(self):
        sub = _with_a_value_in_every_public_field()
        plain = publishing.build_content(ServiceSubmission.objects.get(pk=sub.pk))
        row = ServiceSubmission.objects.prefetch_related(*PUBLIC_M2M_FIELDS).get(
            pk=sub.pk
        )
        content, queries = _count_queries(lambda: publishing.build_content(row))
        assert queries == 0
        assert content == plain

    def test_the_publish_path_reads_the_relations_from_the_database(self):
        sub = _with_a_value_in_every_public_field()
        stale = ServiceSubmission.objects.prefetch_related(*PUBLIC_M2M_FIELDS).get(
            pk=sub.pk
        )
        sub.service_categories.add(ServiceCategoryFactory())
        revision = publishing.publish(stale, reason="approval", actor="admin:jane")
        assert len(revision.content["service_categories"]) == 3

    def test_publish_costs_the_same_with_or_without_a_prefetched_instance(self):
        first = _with_a_value_in_every_public_field()
        second = _with_a_value_in_every_public_field(2)
        plain = ServiceSubmission.objects.get(pk=first.pk)
        prefetched = ServiceSubmission.objects.prefetch_related(*PUBLIC_M2M_FIELDS).get(
            pk=second.pk
        )
        _, plain_queries = _count_queries(
            lambda: publishing.publish(plain, reason="approval", actor="admin:jane")
        )
        _, prefetched_queries = _count_queries(
            lambda: publishing.publish(
                prefetched, reason="approval", actor="admin:jane"
            )
        )
        assert plain_queries == prefetched_queries


def test_changelog_model_is_untouched_by_publishing():
    sub = _approved()
    publishing.publish(sub, reason="approval", actor="admin:jane")
    assert SubmissionChangeLog.objects.filter(submission=sub).count() == 0


class TestDeletion:
    def _published(self, **kw):
        sub = _approved(**kw)
        publishing.publish(sub, reason="approval", actor="admin:jane")
        return sub

    def test_delete_published_submission_writes_tombstone(self):
        sub = self._published()
        pk = sub.pk
        sub.delete()
        assert WithdrawnService.objects.filter(pk=pk).exists()
        assert not PublishedService.objects.filter(pk=pk).exists()
        assert not PublishedRevision.objects.filter(submission_id=pk).exists()

    def test_tombstone_records_when_the_service_was_withdrawn(self):
        sub = self._published()
        before = timezone.now()
        pk = sub.pk
        sub.delete()
        withdrawn = WithdrawnService.objects.get(pk=pk)
        assert before <= withdrawn.withdrawn_at <= timezone.now()

    def test_delete_with_several_revisions(self):
        sub = self._published()
        sub.website_url = "https://example.org/moved"
        sub.save()
        publishing.publish(sub, reason="live_edit", actor="submitter")
        pk = sub.pk
        sub.delete()
        assert WithdrawnService.objects.filter(pk=pk).exists()
        assert PublishedRevision.objects.filter(submission_id=pk).count() == 0

    def test_delete_deprecated_published_submission(self):
        sub = self._published()
        publishing.on_status_change(sub, "deprecated", actor="admin:jane")
        pk = sub.pk
        sub.delete()
        assert WithdrawnService.objects.filter(pk=pk).exists()

    def test_delete_locks_the_row_first(self, locked_models):
        """A publish running at the same time must finish before the rows
        that refer to the service are collected (checked on PostgreSQL in
        test_concurrency_postgres.py)."""
        sub = self._published()
        del locked_models[:]
        sub.delete()
        assert locked_models[:1] == ["ServiceSubmission"]

    def test_queryset_delete_locks_the_rows_first(self, locked_models):
        a, b = self._published(), self._published()
        del locked_models[:]
        ServiceSubmission.objects.filter(pk__in=[a.pk, b.pk]).delete()
        assert locked_models[:1] == ["ServiceSubmission"]

    def test_queryset_delete_returns_what_django_returns(self):
        a = self._published()
        ServiceSubmissionFactory(biotools_url="")  # not selected
        count, per_model = ServiceSubmission.objects.filter(pk=a.pk).delete()
        assert per_model["submissions.ServiceSubmission"] == 1
        assert count == sum(per_model.values())
        assert ServiceSubmission.objects.count() == 1

    def test_deleting_an_empty_selection_is_a_no_op(self):
        self._published()
        assert ServiceSubmission.objects.none().delete() == (0, {})
        assert ServiceSubmission.objects.count() == 1

    def test_delete_through_a_related_manager_writes_a_tombstone(self):
        sub = self._published()
        sub.service_center.submissions.all().delete()
        assert WithdrawnService.objects.filter(pk=sub.pk).exists()

    def test_managers_do_not_offer_a_bulk_delete(self):
        """Like any Django manager, neither the default manager nor a related
        manager deletes every row; only a filtered queryset does."""
        sub = self._published()
        assert not hasattr(ServiceSubmission.objects, "delete")
        assert not hasattr(sub.service_center.submissions, "delete")
        with pytest.raises(AttributeError):
            ServiceSubmission.objects.delete()
        assert ServiceSubmission.objects.filter(pk=sub.pk).exists()
        ServiceSubmission.objects.filter(pk=sub.pk).delete()
        assert WithdrawnService.objects.filter(pk=sub.pk).exists()

    def test_queryset_delete_writes_tombstones(self):
        a, b = self._published(), self._published()
        ServiceSubmission.objects.filter(pk__in=[a.pk, b.pk]).delete()
        assert WithdrawnService.objects.filter(pk__in=[a.pk, b.pk]).count() == 2

    def test_delete_with_revisions_but_no_read_model_writes_a_tombstone(self):
        """The read model can be missing (dropped before a rebuild); the
        service was still published."""
        sub = self._published()
        PublishedService.objects.filter(pk=sub.pk).delete()
        pk = sub.pk
        sub.delete()
        assert WithdrawnService.objects.filter(pk=pk).exists()

    def test_unpublished_delete_writes_no_tombstone(self):
        sub = ServiceSubmissionFactory(biotools_url="")
        pk = sub.pk
        sub.delete()
        assert not WithdrawnService.objects.filter(pk=pk).exists()

    def test_deleting_one_service_leaves_the_others(self):
        keep, gone = self._published(), self._published()
        gone.delete()
        assert PublishedService.objects.filter(pk=keep.pk).exists()
        assert WithdrawnService.objects.count() == 1

    def test_tombstone_is_not_written_twice(self):
        sub = self._published()
        pk = sub.pk
        WithdrawnService.objects.create(id=pk, withdrawn_at=timezone.now())
        sub.delete()
        assert WithdrawnService.objects.filter(pk=pk).count() == 1

    def test_removing_only_the_read_model_writes_no_tombstone(self):
        """Dropping the derived copy (for example before a rebuild) is not a
        withdrawal: the submission still exists."""
        sub = self._published()
        PublishedService.objects.filter(pk=sub.pk).delete()
        assert not WithdrawnService.objects.filter(pk=sub.pk).exists()

    def test_admin_single_delete(self, superuser_client):
        from django.urls import reverse

        sub = self._published()
        resp = superuser_client.post(
            reverse("admin:submissions_servicesubmission_delete", args=[sub.pk]),
            {"post": "yes"},
        )
        assert resp.status_code == 302
        assert WithdrawnService.objects.filter(pk=sub.pk).exists()

    def test_admin_bulk_delete(self, superuser_client):
        from django.urls import reverse

        a, b = self._published(), self._published()
        resp = superuser_client.post(
            reverse("admin:submissions_servicesubmission_changelist"),
            {
                "action": "delete_selected",
                "_selected_action": [str(a.pk), str(b.pk)],
                "post": "yes",
            },
        )
        assert resp.status_code == 302
        assert WithdrawnService.objects.filter(pk__in=[a.pk, b.pk]).count() == 2

    def test_admin_delete_keeps_the_audit_trail(self, superuser_client):
        from django.urls import reverse

        from apps.submissions.models import SubmissionDeletionAudit

        sub = self._published()
        superuser_client.post(
            reverse("admin:submissions_servicesubmission_delete", args=[sub.pk]),
            {"post": "yes"},
        )
        assert SubmissionDeletionAudit.objects.filter(submission_id=sub.pk).exists()

    def test_tombstone_holds_no_service_content(self):
        sub = self._published(service_name="Example Distinctive Name")
        pk = sub.pk
        sub.delete()
        row = WithdrawnService.objects.filter(pk=pk).values().get()
        assert set(row) == {"id", "withdrawn_at"}


class TestFactoryTrait:
    def test_published_trait_creates_the_public_copy(self):
        sub = ServiceSubmissionFactory(
            status="approved", biotools_url="", published=True
        )
        ps = PublishedService.objects.get(pk=sub.pk)
        assert ps.service_categories.count() == sub.service_categories.count() > 0
        assert ps.responsible_pis.count() == sub.responsible_pis.count() > 0
        assert ps.revision.reason == "approval"

    def test_default_does_not_publish(self):
        sub = ServiceSubmissionFactory(status="approved", biotools_url="")
        assert not PublishedService.objects.filter(pk=sub.pk).exists()

    def test_published_false_does_not_publish(self):
        sub = ServiceSubmissionFactory(
            status="approved", biotools_url="", published=False
        )
        assert not PublishedService.objects.filter(pk=sub.pk).exists()

    def test_published_trait_needs_an_approved_service(self):
        with pytest.raises(ValueError, match="approved"):
            ServiceSubmissionFactory(
                status="submitted", biotools_url="", published=True
            )

    def test_published_trait_with_explicit_relations(self):
        cat = ServiceCategoryFactory()
        sub = ServiceSubmissionFactory(
            status="approved",
            biotools_url="",
            service_categories=[cat],
            published=True,
        )
        assert list(
            PublishedService.objects.get(pk=sub.pk).service_categories.all()
        ) == [cat]
