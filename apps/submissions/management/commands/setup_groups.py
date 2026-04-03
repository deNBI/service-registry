"""
setup_groups — create or refresh the standard admin role groups
===============================================================

Usage
-----
    python manage.py setup_groups           # create/update all three groups
    python manage.py setup_groups --dry-run # print what would change, touch nothing
    python manage.py setup_groups --list    # list groups + their current permissions

Background
----------
Django's permission system ships with four auto-generated permissions per
model (add_, change_, delete_, view_) plus any custom permissions declared
in Meta.permissions.  On their own these are granular but overwhelming —
a fresh admin sees dozens of unrelated checkboxes.

This command assembles them into three purpose-built groups that map
directly onto the real-world roles in the de.NBI service-registry team:

  Registry Viewer
    Read-only access to every admin section.  Suitable for auditors,
    new team members, or anyone who needs visibility without write rights.

  Registry Editor
    Can view everything a Viewer can, plus:
      • Create and edit service submissions
      • Approve or reject submissions (status → Approved / Rejected)
      • Issue, reset, and revoke submission API keys

  Registry Manager
    Everything an Editor can, plus:
      • Delete service submissions
      • Full add / change / delete on reference data:
        Service Categories, Service Centres, Principal Investigators

Superusers have unrestricted access regardless of group membership.

API key management (AdminAPIKey) is intentionally excluded from
all groups — API key management remains superuser-only.

Idempotency
-----------
The command is safe to run multiple times.  It brings each group's
permission set exactly in line with the specification below, adding
missing permissions and removing any that are no longer in the spec.
Existing user → group memberships are never touched.

Running after every deployment is recommended to pick up any new
permissions added to the model in code.
"""

from __future__ import annotations

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand


# ---------------------------------------------------------------------------
# Permission specification
# ---------------------------------------------------------------------------
# Each entry is (app_label, codename).  The codename is the bare name without
# the app prefix — exactly what appears in Permission.codename in the DB.

_VIEWER: list[tuple[str, str]] = [
    # Submissions
    ("submissions", "view_servicesubmission"),
    ("submissions", "view_submissionchangelog"),
    ("submissions", "view_submissionapikey"),
    # Reference data
    ("registry", "view_servicecategory"),
    ("registry", "view_servicecenter"),
    ("registry", "view_principalinvestigator"),
    # Celery task results (read-only; Add/Change already blocked in admin)
    ("django_celery_results", "view_taskresult"),
    # EDAM ontology (sync-managed; Add/Delete already blocked in admin)
    ("edam", "view_edamterm"),
    # bio.tools records (sync-managed; Add already blocked in admin)
    ("biotools", "view_biotoolsrecord"),
    ("biotools", "view_biotoolsfunction"),
]

_EDITOR: list[tuple[str, str]] = _VIEWER + [
    # Submission content
    ("submissions", "add_servicesubmission"),
    ("submissions", "change_servicesubmission"),
    # Semantic custom permissions (defined in ServiceSubmission.Meta.permissions)
    ("submissions", "approve_servicesubmission"),  # approve / reject
    ("submissions", "manage_apikeys"),  # issue / reset / revoke keys
]

_MANAGER: list[tuple[str, str]] = _EDITOR + [
    # Destructive submission operations
    ("submissions", "delete_servicesubmission"),
    # Reference data — full CRUD
    ("registry", "add_servicecategory"),
    ("registry", "change_servicecategory"),
    ("registry", "delete_servicecategory"),
    ("registry", "add_servicecenter"),
    ("registry", "change_servicecenter"),
    ("registry", "delete_servicecenter"),
    ("registry", "add_principalinvestigator"),
    ("registry", "change_principalinvestigator"),
    ("registry", "delete_principalinvestigator"),
]

# Canonical group → permission-spec mapping (order matters for --list output).
GROUP_SPECS: dict[str, list[tuple[str, str]]] = {
    "Registry Viewer": _VIEWER,
    "Registry Editor": _EDITOR,
    "Registry Manager": _MANAGER,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_permissions(
    spec: list[tuple[str, str]],
) -> tuple[set[Permission], list[str]]:
    """
    Look up Permission objects for every (app_label, codename) pair.

    Queries Permission directly rather than via ContentType so we never
    run into MultipleObjectsReturned (an app can have many content types).

    Returns:
        (found, missing)
        found   — set of Permission instances
        missing — list of human-readable strings for any that don't exist yet
    """
    found: set[Permission] = set()
    missing: list[str] = []
    for app_label, codename in spec:
        try:
            perm = Permission.objects.select_related("content_type").get(
                codename=codename,
                content_type__app_label=app_label,
            )
            found.add(perm)
        except Permission.DoesNotExist:
            missing.append(f"{app_label}.{codename}")
    return found, missing


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


class Command(BaseCommand):
    help = (
        "Create or refresh the three standard admin role groups "
        "(Registry Viewer, Registry Editor, Registry Manager). "
        "Safe to run multiple times — existing user memberships are preserved."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be added / removed without touching the database.",
        )
        parser.add_argument(
            "--list",
            action="store_true",
            dest="list_perms",
            help="List each group and its current permissions, then exit.",
        )

    def handle(self, *args, **options):
        dry_run: bool = options["dry_run"]
        list_perms: bool = options["list_perms"]

        if list_perms:
            self._list_groups()
            return

        if dry_run:
            self.stdout.write(
                self.style.WARNING("DRY RUN — no changes will be written.\n")
            )

        any_error = False
        for group_name, spec in GROUP_SPECS.items():
            error = self._sync_group(group_name, spec, dry_run=dry_run)
            if error:
                any_error = True

        if any_error:
            self.stderr.write(
                self.style.ERROR(
                    "\nSome permissions could not be resolved.  "
                    "Run 'manage.py migrate' first to ensure all Permission "
                    "rows exist, then re-run setup_groups."
                )
            )
        elif dry_run:
            self.stdout.write(
                self.style.SUCCESS("\nDry run complete — no changes made.")
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "\nAll groups are up-to-date.  "
                    "Assign staff users to these groups via the admin "
                    "or with: manage.py shell"
                )
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _sync_group(
        self, group_name: str, spec: list[tuple[str, str]], *, dry_run: bool
    ) -> bool:
        """
        Bring one group's permissions in line with spec.

        Returns True if any unresolved permissions were found.
        """
        self.stdout.write(f"\n{self.style.MIGRATE_HEADING(group_name)}")

        target_perms, missing = _resolve_permissions(spec)

        if missing:
            for m in missing:
                self.stderr.write(
                    self.style.ERROR(f"  ✗ Could not resolve permission: {m}")
                )

        if dry_run:
            # In dry-run mode, check without creating the group.
            try:
                group = Group.objects.get(name=group_name)
                self.stdout.write("  Group already exists.")
            except Group.DoesNotExist:
                self.stdout.write("[DRY RUN] Would create group.")
                group = None
        else:
            group, created = Group.objects.get_or_create(name=group_name)
            if created:
                self.stdout.write("  Created group.")
            else:
                self.stdout.write("  Group already exists.")

        current_perms: set[Permission] = (
            set(group.permissions.all()) if group else set()
        )
        to_add = target_perms - current_perms
        to_remove = current_perms - target_perms

        if not to_add and not to_remove:
            self.stdout.write(
                self.style.SUCCESS(
                    "  ✓ Permissions already match spec — nothing to do."
                )
            )
        else:
            prefix = "[DRY RUN] " if dry_run else ""
            for perm in sorted(to_add, key=lambda p: p.codename):
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  {prefix}+ {perm.content_type.app_label}.{perm.codename}"
                    )
                )
            for perm in sorted(to_remove, key=lambda p: p.codename):
                self.stdout.write(
                    self.style.ERROR(
                        f"  {prefix}- {perm.content_type.app_label}.{perm.codename}"
                    )
                )

            if not dry_run:
                if to_add:
                    group.permissions.add(*to_add)
                if to_remove:
                    group.permissions.remove(*to_remove)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  ✓ Applied: +{len(to_add)} added, -{len(to_remove)} removed."
                    )
                )

        return bool(missing)

    def _list_groups(self) -> None:
        """Print each group and its current permissions."""
        for group_name in GROUP_SPECS:
            try:
                group = Group.objects.get(name=group_name)
            except Group.DoesNotExist:
                self.stdout.write(
                    self.style.WARNING(
                        f"\n{group_name}: NOT CREATED (run setup_groups)"
                    )
                )
                continue

            perms = list(
                group.permissions.select_related("content_type").order_by(
                    "content_type__app_label", "codename"
                )
            )
            self.stdout.write(
                f"\n{self.style.MIGRATE_HEADING(group_name)} ({len(perms)} permissions)"
            )
            for p in perms:
                self.stdout.write(f"  {p.content_type.app_label}.{p.codename}")
