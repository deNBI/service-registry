"""Rebuild public copies from their latest revision (never the working copy)."""

import uuid

from django.core.management.base import BaseCommand, CommandError

from apps.catalogue import publishing
from apps.catalogue.models import PublishedRevision


class Command(BaseCommand):
    help = (
        "Rebuild PublishedService rows from their latest PublishedRevision. "
        "Reference rows that no longer exist are skipped."
    )

    def add_arguments(self, parser):
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument(
            "--all", action="store_true", help="Rebuild every published service."
        )
        group.add_argument(
            "--id",
            action="append",
            dest="ids",
            default=[],
            help="Rebuild one service by id (repeatable).",
        )

    def handle(self, *args, all=False, ids=(), **options):
        if all:
            ids = list(
                PublishedRevision.objects.order_by("submission_id")
                .values_list("submission_id", flat=True)
                .distinct()
            )
        else:
            parsed = []
            for raw in ids:
                try:
                    parsed.append(uuid.UUID(str(raw)))
                except ValueError as exc:
                    raise CommandError(f"{raw!r} is not a valid id") from exc
            ids = parsed
        count = 0
        for pk in ids:
            try:
                publishing.rebuild_from_revision(pk)
            except PublishedRevision.DoesNotExist as exc:
                raise CommandError(f"no revision for {pk}") from exc
            count += 1
        self.stdout.write(f"rebuilt: {count}")
