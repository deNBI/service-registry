"""Check the public copy against its sources. Prints ids and counts only."""

from django.core.management.base import BaseCommand, CommandError
from django.db.models import OuterRef, Subquery

from apps.catalogue import publishing
from apps.catalogue.models import PublishedRevision, PublishedService
from apps.catalogue.public_fields import PUBLIC_M2M_FIELDS
from apps.submissions.models import ServiceSubmission, SubmissionStatus
from apps.submissions.selectors import (
    latest_public_decisions,
    status_history_broken,
    status_history_drift,
    was_ever_approved,
)


def _without_missing_references(ps: PublishedService, content: dict) -> dict:
    """The revision content as the read model can hold it: fields that became
    public after the revision was written get their default, and reference
    rows that no longer exist are dropped (and reported separately)."""
    content = publishing.complete_content(content)
    for name in PUBLIC_M2M_FIELDS:
        model = getattr(ps, name).model
        wanted = content.get(name, [])
        found = {
            str(pk)
            for pk in model.objects.filter(pk__in=wanted).values_list("pk", flat=True)
        }
        content[name] = sorted(v for v in wanted if str(v) in found)
    return content


class Command(BaseCommand):
    help = (
        "Verify the publishing layer; exits non-zero when problems are found. "
        "Output lists problem classes with submission ids, never field values."
    )

    def handle(self, *args, **options):
        problems: dict[str, list[str]] = {
            "stale": [],  # approved, nothing pending, working copy differs
            "unpublished_approved": [],  # approved but never published
            "drift": [],  # public copy differs from its latest revision
            "revision_without_copy": [],  # revisions exist, the public copy does not
            "no_approval_event": [],  # published without any approval event
            "missing_reference": [],  # revision names a deleted reference row
            "state_mismatch": [],  # state / pending flag do not fit the status
            # latest status event differs from the status (or there is none)
            "status_history_drift": [pk for pk, _, _ in status_history_drift()],
            # events that do not follow on from each other
            "status_history_broken": status_history_broken(),
        }
        newest_number = (
            PublishedRevision.objects.filter(submission=OuterRef("submission"))
            .order_by("-number")
            .values("number")[:1]
        )
        latest = {
            rev.submission_id: rev
            for rev in PublishedRevision.objects.filter(
                number=Subquery(newest_number)
            ).only("submission_id", "number", "content", "content_hash")
        }
        published = {
            ps.pk: ps for ps in PublishedService.objects.select_related("revision")
        }
        statuses = dict(ServiceSubmission.objects.values_list("pk", "status"))
        decisions = latest_public_decisions(PublishedService.objects.values("pk"))

        for sub in ServiceSubmission.objects.filter(status=SubmissionStatus.APPROVED):
            ps = published.get(sub.pk)
            if ps is None:
                problems["unpublished_approved"].append(str(sub.pk))
            elif not ps.pending_update:
                rev = latest.get(sub.pk)
                if rev is None or (
                    publishing.content_hash(publishing.build_content(sub))
                    != rev.content_hash
                ):
                    problems["stale"].append(str(sub.pk))

        problems["revision_without_copy"] = sorted(
            str(pk) for pk in latest if pk not in published
        )
        for pk, ps in published.items():
            rev = latest.get(pk)
            if rev is None or ps.revision_id != rev.pk:
                problems["drift"].append(str(pk))
            else:
                expected = _without_missing_references(ps, rev.content)
                if publishing.content_hash(
                    publishing.build_content(ps)
                ) != publishing.content_hash(expected):
                    problems["drift"].append(str(pk))
                if expected != publishing.complete_content(rev.content):
                    problems["missing_reference"].append(str(pk))
            if not was_ever_approved(pk):
                problems["no_approval_event"].append(str(pk))
            expected_flags = publishing.expected_flags(statuses[pk], decisions.get(pk))
            if (ps.state, ps.pending_update) != expected_flags:
                problems["state_mismatch"].append(str(pk))

        total = sum(len(ids) for ids in problems.values())
        for name, ids in problems.items():
            self.stdout.write(f"{name}: {len(ids)}")
            for pk in ids:
                self.stdout.write(f"  {pk}")
        if total:
            raise CommandError(
                f"{total} problem(s): "
                + ", ".join(f"{k}={len(v)}" for k, v in problems.items() if v)
            )
        self.stdout.write("0 problems")
