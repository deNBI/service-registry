"""
Permission System Tests
=======================
Comprehensive tests for the group-based access control system.

Coverage:
  1. setup_groups management command — group creation, idempotency,
     correct permission assignments, hierarchy invariants.
  2. ServiceSubmissionAdmin permission methods — has_view, has_add,
     has_change, has_delete for Viewer / Editor / Manager / Superuser.
  3. response_change security gates — direct POST attacks on privileged
     buttons (approve, reject, key-management) are rejected without the
     correct permission, even when the form is submitted manually.
  4. Bulk-action visibility — Viewer cannot see approve/reject actions;
     Editor can; Manager can.
  5. get_fieldsets — status_actions and key_management_panel panels are
     hidden for users who lack the relevant permissions; submission_ip_display
     is hidden for non-superusers.
  6. SubmissionAPIKeyAdmin — has_view / has_add / has_change / has_delete
     respect manage_apikeys and view_submissionapikey permissions.
  7. SubmissionChangeLogAdmin — read-only for all users, visible only to
     those with view_submissionchangelog.

Security invariants checked explicitly:
  • A Viewer who crafts a raw POST to the approve button is rejected.
  • A Viewer who crafts a raw POST to issue/reset/revoke keys is rejected.
  • An Editor who crafts a raw POST to _reject is accepted (they have the perm).
  • No privileged action silently succeeds without the correct permission.
"""

from __future__ import annotations

import pytest
from django.contrib.admin import site as admin_site
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import Client, RequestFactory
from django.urls import reverse

from apps.submissions.admin import ServiceSubmissionAdmin, SubmissionAPIKeyAdmin
from apps.submissions.models import ServiceSubmission, SubmissionChangeLog
from tests.factories import ServiceSubmissionFactory

User = get_user_model()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def setup_groups_fixture(db):
    """Run setup_groups so all three groups exist with correct permissions."""
    call_command("setup_groups", verbosity=0)


@pytest.fixture
def superuser(db):
    return User.objects.create_superuser(
        username="superuser", password="pass", email="super@example.com"
    )


@pytest.fixture
def viewer_user(db, setup_groups_fixture):
    u = User.objects.create_user(
        username="viewer", password="pass", email="viewer@example.com", is_staff=True
    )
    u.groups.add(Group.objects.get(name="Registry Viewer"))
    return u


@pytest.fixture
def editor_user(db, setup_groups_fixture):
    u = User.objects.create_user(
        username="editor", password="pass", email="editor@example.com", is_staff=True
    )
    u.groups.add(Group.objects.get(name="Registry Editor"))
    return u


@pytest.fixture
def manager_user(db, setup_groups_fixture):
    u = User.objects.create_user(
        username="manager", password="pass", email="manager@example.com", is_staff=True
    )
    u.groups.add(Group.objects.get(name="Registry Manager"))
    return u


@pytest.fixture
def submission(db):
    return ServiceSubmissionFactory(status="submitted")


def _client_for(user) -> Client:
    c = Client()
    c.force_login(user)
    return c


def _changelist_url():
    return reverse("admin:submissions_servicesubmission_changelist")


def _change_url(pk):
    return reverse("admin:submissions_servicesubmission_change", args=[pk])


# ---------------------------------------------------------------------------
# 1. setup_groups — group creation and permission correctness
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSetupGroups:
    def test_creates_all_three_groups(self, setup_groups_fixture):
        names = set(Group.objects.values_list("name", flat=True))
        assert "Registry Viewer" in names
        assert "Registry Editor" in names
        assert "Registry Manager" in names

    def test_idempotent_second_run(self, setup_groups_fixture):
        """Running setup_groups a second time must not raise or duplicate groups."""
        call_command("setup_groups", verbosity=0)
        assert Group.objects.filter(name="Registry Viewer").count() == 1
        assert Group.objects.filter(name="Registry Editor").count() == 1
        assert Group.objects.filter(name="Registry Manager").count() == 1

    def test_viewer_has_view_servicesubmission(self, setup_groups_fixture):
        group = Group.objects.get(name="Registry Viewer")
        codes = set(group.permissions.values_list("codename", flat=True))
        assert "view_servicesubmission" in codes

    def test_viewer_does_not_have_change_servicesubmission(self, setup_groups_fixture):
        group = Group.objects.get(name="Registry Viewer")
        codes = set(group.permissions.values_list("codename", flat=True))
        assert "change_servicesubmission" not in codes

    def test_viewer_does_not_have_approve_or_manage_keys(self, setup_groups_fixture):
        group = Group.objects.get(name="Registry Viewer")
        codes = set(group.permissions.values_list("codename", flat=True))
        assert "approve_servicesubmission" not in codes
        assert "manage_apikeys" not in codes

    def test_editor_has_change_and_approve_and_manage_keys(self, setup_groups_fixture):
        group = Group.objects.get(name="Registry Editor")
        codes = set(group.permissions.values_list("codename", flat=True))
        assert "change_servicesubmission" in codes
        assert "approve_servicesubmission" in codes
        assert "manage_apikeys" in codes

    def test_editor_does_not_have_delete_servicesubmission(self, setup_groups_fixture):
        group = Group.objects.get(name="Registry Editor")
        codes = set(group.permissions.values_list("codename", flat=True))
        assert "delete_servicesubmission" not in codes

    def test_manager_has_delete_servicesubmission(self, setup_groups_fixture):
        group = Group.objects.get(name="Registry Manager")
        codes = set(group.permissions.values_list("codename", flat=True))
        assert "delete_servicesubmission" in codes

    def test_manager_has_reference_data_permissions(self, setup_groups_fixture):
        group = Group.objects.get(name="Registry Manager")
        codes = set(group.permissions.values_list("codename", flat=True))
        for codename in (
            "add_servicecategory",
            "change_servicecategory",
            "delete_servicecategory",
            "add_servicecenter",
            "change_servicecenter",
            "delete_servicecenter",
            "add_principalinvestigator",
            "change_principalinvestigator",
            "delete_principalinvestigator",
        ):
            assert codename in codes, f"Manager missing: {codename}"

    def test_viewer_permissions_are_subset_of_editor(self, setup_groups_fixture):
        viewer_codes = set(
            Group.objects.get(name="Registry Viewer").permissions.values_list(
                "codename", flat=True
            )
        )
        editor_codes = set(
            Group.objects.get(name="Registry Editor").permissions.values_list(
                "codename", flat=True
            )
        )
        assert viewer_codes.issubset(editor_codes), (
            f"Viewer has permissions Editor lacks: {viewer_codes - editor_codes}"
        )

    def test_editor_permissions_are_subset_of_manager(self, setup_groups_fixture):
        editor_codes = set(
            Group.objects.get(name="Registry Editor").permissions.values_list(
                "codename", flat=True
            )
        )
        manager_codes = set(
            Group.objects.get(name="Registry Manager").permissions.values_list(
                "codename", flat=True
            )
        )
        assert editor_codes.issubset(manager_codes), (
            f"Editor has permissions Manager lacks: {editor_codes - manager_codes}"
        )

    def test_dry_run_does_not_create_groups(self, db):
        """--dry-run must not touch the database."""
        assert not Group.objects.filter(name="Registry Viewer").exists()
        call_command("setup_groups", dry_run=True, verbosity=0)
        assert not Group.objects.filter(name="Registry Viewer").exists()

    def test_no_auth_token_permissions_in_any_group(self, setup_groups_fixture):
        """DRF TokenProxy must remain superuser-only — absent from all groups."""
        for name in ("Registry Viewer", "Registry Editor", "Registry Manager"):
            group = Group.objects.get(name=name)
            codes = set(group.permissions.values_list("codename", flat=True))
            token_perms = {c for c in codes if "token" in c.lower()}
            assert not token_perms, (
                f"Group '{name}' has token permissions: {token_perms}"
            )


# ---------------------------------------------------------------------------
# 2. ServiceSubmissionAdmin — has_*_permission methods
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSubmissionAdminPermissionMethods:
    """
    Call the ModelAdmin permission methods directly using a fake request.

    This is the lowest-level unit test — no HTTP, no DB queries beyond
    the user/permission setup done by fixtures.
    """

    def _make_request(self, user):
        rf = RequestFactory()
        req = rf.get("/")
        req.user = user
        return req

    def _admin(self):
        return ServiceSubmissionAdmin(ServiceSubmission, admin_site)

    def test_viewer_has_view(self, viewer_user):
        req = self._make_request(viewer_user)
        assert self._admin().has_view_permission(req) is True

    def test_viewer_no_add(self, viewer_user):
        req = self._make_request(viewer_user)
        assert self._admin().has_add_permission(req) is False

    def test_viewer_no_change(self, viewer_user):
        req = self._make_request(viewer_user)
        assert self._admin().has_change_permission(req) is False

    def test_viewer_no_delete(self, viewer_user):
        req = self._make_request(viewer_user)
        assert self._admin().has_delete_permission(req) is False

    def test_editor_has_view_add_change(self, editor_user):
        req = self._make_request(editor_user)
        a = self._admin()
        assert a.has_view_permission(req) is True
        assert a.has_add_permission(req) is True
        assert a.has_change_permission(req) is True

    def test_editor_no_delete(self, editor_user):
        req = self._make_request(editor_user)
        assert self._admin().has_delete_permission(req) is False

    def test_manager_has_delete(self, manager_user):
        req = self._make_request(manager_user)
        assert self._admin().has_delete_permission(req) is True

    def test_superuser_has_all_permissions(self, superuser):
        req = self._make_request(superuser)
        a = self._admin()
        assert a.has_view_permission(req) is True
        assert a.has_add_permission(req) is True
        assert a.has_change_permission(req) is True
        assert a.has_delete_permission(req) is True

    def test_viewer_no_approve_permission(self, viewer_user):
        req = self._make_request(viewer_user)
        assert self._admin().has_approve_servicesubmission_permission(req) is False

    def test_editor_has_approve_permission(self, editor_user):
        req = self._make_request(editor_user)
        assert self._admin().has_approve_servicesubmission_permission(req) is True

    def test_viewer_no_manage_apikeys_permission(self, viewer_user):
        req = self._make_request(viewer_user)
        assert self._admin().has_manage_apikeys_permission(req) is False

    def test_editor_has_manage_apikeys_permission(self, editor_user):
        req = self._make_request(editor_user)
        assert self._admin().has_manage_apikeys_permission(req) is True


# ---------------------------------------------------------------------------
# 3. response_change security gates (direct admin-method tests)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestResponseChangePermissionGates:
    """
    Test the permission guards in ServiceSubmissionAdmin.response_change.

    Strategy: call the admin method directly with a crafted request object
    rather than through the full HTTP cycle.  This isolates the permission
    logic in response_change from Django admin's own has_change_permission
    check (which would return 403 before reaching our code for Viewers).

    These tests prove defence-in-depth: even if someone somehow bypassed
    the outer has_change_permission gate (e.g. a future refactor), the
    inner _require_perm guards in response_change would still block them.
    """

    def _make_post_request(self, user, **post_data):
        """Build a POST request whose user has the given user object."""
        rf = RequestFactory()
        req = rf.post("/fake/", post_data)
        req.user = user
        # Use CookieStorage — it does not require session middleware and works
        # fine with a bare RequestFactory request.
        from django.contrib.messages.storage.cookie import CookieStorage

        req._messages = CookieStorage(req)
        return req

    def _admin(self):
        return ServiceSubmissionAdmin(ServiceSubmission, admin_site)

    def _call_response_change(self, user, submission, **post_data):
        """
        Call response_change directly and return (response, refreshed_submission).
        """
        req = self._make_post_request(user, **post_data)
        self._admin().response_change(req, submission)
        submission.refresh_from_db()
        return submission

    # ── Approve / Reject — require approve_servicesubmission ─────────────────

    def test_user_without_approve_perm_cannot_approve(self, viewer_user, submission):
        """Without approve_servicesubmission the submission status must not change."""
        sub = self._call_response_change(viewer_user, submission, _approve="1")
        assert sub.status == "submitted"

    def test_user_without_approve_perm_cannot_reject(self, viewer_user, submission):
        sub = self._call_response_change(viewer_user, submission, _reject="1")
        assert sub.status == "submitted"

    def test_user_with_approve_perm_can_approve(self, editor_user, submission):
        sub = self._call_response_change(editor_user, submission, _approve="1")
        assert sub.status == "approved"

    def test_user_with_approve_perm_can_reject(self, editor_user, submission):
        sub = self._call_response_change(editor_user, submission, _reject="1")
        assert sub.status == "rejected"

    # ── Under-review / Deprecate — require change_servicesubmission ──────────

    def test_viewer_cannot_mark_under_review(self, viewer_user, submission):
        sub = self._call_response_change(viewer_user, submission, _under_review="1")
        assert sub.status == "submitted"

    def test_editor_can_mark_under_review(self, editor_user, submission):
        sub = self._call_response_change(editor_user, submission, _under_review="1")
        assert sub.status == "under_review"

    def test_viewer_cannot_deprecate(self, viewer_user, submission):
        sub = self._call_response_change(viewer_user, submission, _deprecate="1")
        assert sub.status == "submitted"

    def test_editor_can_deprecate(self, editor_user, submission):
        sub = self._call_response_change(editor_user, submission, _deprecate="1")
        assert sub.status == "deprecated"

    # ── API key operations — require manage_apikeys ───────────────────────────

    def test_viewer_cannot_issue_key(self, viewer_user, submission):
        from apps.submissions.models import SubmissionAPIKey

        before = SubmissionAPIKey.objects.filter(submission=submission).count()
        self._call_response_change(viewer_user, submission, _issue_new_key="1")
        after = SubmissionAPIKey.objects.filter(submission=submission).count()
        assert after == before, "Viewer must not be able to issue API keys"

    def test_viewer_cannot_revoke_keys(self, viewer_user, submission):
        from tests.factories import APIKeyFactory
        from apps.submissions.models import SubmissionAPIKey

        APIKeyFactory.create_with_plaintext(submission=submission)
        self._call_response_change(viewer_user, submission, _revoke_all_keys="1")
        assert SubmissionAPIKey.objects.filter(
            submission=submission, is_active=True
        ).exists(), "Viewer must not be able to revoke keys"

    def test_viewer_cannot_reset_key(self, viewer_user, submission):
        from tests.factories import APIKeyFactory
        from apps.submissions.models import SubmissionAPIKey

        APIKeyFactory.create_with_plaintext(submission=submission)
        original_count = SubmissionAPIKey.objects.filter(submission=submission).count()
        self._call_response_change(viewer_user, submission, _reset_key="1")
        # Count must be unchanged (no new key issued).
        assert (
            SubmissionAPIKey.objects.filter(submission=submission).count()
            == original_count
        )

    def test_editor_can_revoke_keys(self, editor_user, submission):
        from tests.factories import APIKeyFactory
        from apps.submissions.models import SubmissionAPIKey

        APIKeyFactory.create_with_plaintext(submission=submission)
        self._call_response_change(editor_user, submission, _revoke_all_keys="1")
        assert not SubmissionAPIKey.objects.filter(
            submission=submission, is_active=True
        ).exists()

    def test_editor_can_issue_key(self, editor_user, submission):
        from apps.submissions.models import SubmissionAPIKey

        before = SubmissionAPIKey.objects.filter(submission=submission).count()
        self._call_response_change(editor_user, submission, _issue_new_key="1")
        after = SubmissionAPIKey.objects.filter(submission=submission).count()
        assert after == before + 1


# ---------------------------------------------------------------------------
# 3b. HTTP-level gate — Viewer POST to change URL returns 403
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestHTTPPermissionGates:
    """
    Verify the HTTP-level gate: Django admin returns 403 when a user with
    only view_servicesubmission tries to POST to the change-form URL.
    This is the outer has_change_permission check, not the inner guards.
    """

    def test_viewer_post_to_change_url_is_forbidden(self, viewer_user, submission):
        c = _client_for(viewer_user)
        resp = c.post(_change_url(submission.pk), {"_approve": "1"})
        assert resp.status_code == 403

    def test_viewer_get_change_url_is_allowed(self, viewer_user, submission):
        c = _client_for(viewer_user)
        resp = c.get(_change_url(submission.pk))
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 4. Bulk-action visibility
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBulkActionVisibility:
    """
    Django only shows bulk actions whose permissions= requirement is met.
    Verify the approve/reject actions are hidden from Viewers.

    A submission record is always created (autouse) because Django admin
    only renders the action dropdown when the changelist has at least one row.
    """

    @pytest.fixture(autouse=True)
    def _ensure_row(self, db):
        """Ensure at least one submission exists so the action form is rendered."""
        ServiceSubmissionFactory(status="submitted")

    def _get_action_names(self, client):
        resp = client.get(_changelist_url())
        assert resp.status_code == 200
        # The action names are rendered in <option value="..."> elements.
        content = resp.content.decode()
        import re

        return set(re.findall(r'<option value="([^"]+)"', content))

    def test_viewer_does_not_see_approve_action(self, viewer_user):
        c = _client_for(viewer_user)
        actions = self._get_action_names(c)
        assert "action_approve" not in actions

    def test_viewer_does_not_see_reject_action(self, viewer_user):
        c = _client_for(viewer_user)
        actions = self._get_action_names(c)
        assert "action_reject" not in actions

    def test_viewer_does_not_see_change_status_actions(self, viewer_user):
        c = _client_for(viewer_user)
        actions = self._get_action_names(c)
        for act in (
            "action_mark_under_review",
            "action_deprecate",
            "action_undeprecate",
        ):
            assert act not in actions

    def test_viewer_sees_export_actions(self, viewer_user):
        c = _client_for(viewer_user)
        actions = self._get_action_names(c)
        assert "action_export_csv" in actions
        assert "action_export_json" in actions

    def test_editor_sees_approve_and_reject(self, editor_user):
        c = _client_for(editor_user)
        actions = self._get_action_names(c)
        assert "action_approve" in actions
        assert "action_reject" in actions

    def test_editor_sees_change_status_actions(self, editor_user):
        c = _client_for(editor_user)
        actions = self._get_action_names(c)
        for act in (
            "action_mark_under_review",
            "action_deprecate",
            "action_undeprecate",
        ):
            assert act in actions

    def test_manager_sees_all_actions(self, manager_user):
        c = _client_for(manager_user)
        actions = self._get_action_names(c)
        for act in (
            "action_approve",
            "action_reject",
            "action_mark_under_review",
            "action_deprecate",
            "action_undeprecate",
            "action_export_csv",
            "action_export_json",
        ):
            assert act in actions


# ---------------------------------------------------------------------------
# 5. get_fieldsets — conditional panel visibility
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestGetFieldsets:
    """
    Verify that status_actions, key_management_panel, and
    submission_ip_display are conditionally shown/hidden.
    """

    def _flat_fields(self, fieldsets) -> set[str]:
        """Extract all field names from a fieldsets structure."""
        names: set[str] = set()
        for _title, options in fieldsets:
            for item in options.get("fields", ()):
                if isinstance(item, str):
                    names.add(item)
                else:
                    names.update(item)
        return names

    def _admin(self):
        return ServiceSubmissionAdmin(ServiceSubmission, admin_site)

    def _make_request(self, user):
        rf = RequestFactory()
        req = rf.get("/")
        req.user = user
        return req

    def test_viewer_missing_status_actions(self, viewer_user):
        req = self._make_request(viewer_user)
        fields = self._flat_fields(self._admin().get_fieldsets(req))
        assert "status_actions" not in fields

    def test_viewer_missing_key_management_panel(self, viewer_user):
        req = self._make_request(viewer_user)
        fields = self._flat_fields(self._admin().get_fieldsets(req))
        assert "key_management_panel" not in fields

    def test_viewer_missing_submission_ip_display(self, viewer_user):
        req = self._make_request(viewer_user)
        fields = self._flat_fields(self._admin().get_fieldsets(req))
        assert "submission_ip_display" not in fields

    def test_editor_has_status_actions(self, editor_user):
        req = self._make_request(editor_user)
        fields = self._flat_fields(self._admin().get_fieldsets(req))
        assert "status_actions" in fields

    def test_editor_has_key_management_panel(self, editor_user):
        req = self._make_request(editor_user)
        fields = self._flat_fields(self._admin().get_fieldsets(req))
        assert "key_management_panel" in fields

    def test_editor_missing_submission_ip_display(self, editor_user):
        """IP display is superuser-only even for editors."""
        req = self._make_request(editor_user)
        fields = self._flat_fields(self._admin().get_fieldsets(req))
        assert "submission_ip_display" not in fields

    def test_superuser_has_all_panels(self, superuser):
        req = self._make_request(superuser)
        fields = self._flat_fields(self._admin().get_fieldsets(req))
        assert "status_actions" in fields
        assert "key_management_panel" in fields
        assert "submission_ip_display" in fields


# ---------------------------------------------------------------------------
# 6. SubmissionAPIKeyAdmin — permission methods
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAPIKeyAdminPermissions:
    def _admin(self):
        from apps.submissions.models import SubmissionAPIKey

        return SubmissionAPIKeyAdmin(SubmissionAPIKey, admin_site)

    def _make_request(self, user):
        rf = RequestFactory()
        req = rf.get("/")
        req.user = user
        return req

    def test_viewer_can_view_keys(self, viewer_user):
        req = self._make_request(viewer_user)
        assert self._admin().has_view_permission(req) is True

    def test_viewer_cannot_add_keys(self, viewer_user):
        req = self._make_request(viewer_user)
        assert self._admin().has_add_permission(req) is False

    def test_viewer_cannot_change_keys(self, viewer_user):
        req = self._make_request(viewer_user)
        assert self._admin().has_change_permission(req) is False

    def test_viewer_cannot_delete_keys(self, viewer_user):
        req = self._make_request(viewer_user)
        assert self._admin().has_delete_permission(req) is False

    def test_editor_can_add_change_delete_keys(self, editor_user):
        req = self._make_request(editor_user)
        a = self._admin()
        assert a.has_view_permission(req) is True
        assert a.has_add_permission(req) is True
        assert a.has_change_permission(req) is True
        assert a.has_delete_permission(req) is True

    def test_superuser_has_all_key_permissions(self, superuser):
        req = self._make_request(superuser)
        a = self._admin()
        assert a.has_add_permission(req) is True
        assert a.has_change_permission(req) is True
        assert a.has_delete_permission(req) is True


# ---------------------------------------------------------------------------
# 7. SubmissionChangeLogAdmin — read-only for everyone
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestChangeLogAdmin:
    def _admin(self):
        from apps.submissions.admin import SubmissionChangeLogAdmin

        return SubmissionChangeLogAdmin(SubmissionChangeLog, admin_site)

    def _make_request(self, user):
        rf = RequestFactory()
        req = rf.get("/")
        req.user = user
        return req

    def test_nobody_can_add_log_entries(self, superuser, editor_user):
        for user in (superuser, editor_user):
            req = self._make_request(user)
            assert self._admin().has_add_permission(req) is False

    def test_nobody_can_change_log_entries(self, superuser, editor_user):
        for user in (superuser, editor_user):
            req = self._make_request(user)
            assert self._admin().has_change_permission(req) is False

    def test_nobody_can_delete_log_entries(self, superuser, editor_user):
        for user in (superuser, editor_user):
            req = self._make_request(user)
            assert self._admin().has_delete_permission(req) is False

    def test_viewer_can_view_log(self, viewer_user):
        req = self._make_request(viewer_user)
        assert self._admin().has_view_permission(req) is True

    def test_user_without_view_perm_cannot_see_log(self, db):
        """An is_staff user with no group at all cannot view the change log."""
        bare_staff = User.objects.create_user(
            username="bare", password="pass", email="bare@test.com", is_staff=True
        )
        req = self._make_request(bare_staff)
        assert self._admin().has_view_permission(req) is False

    def test_changelist_url_accessible_for_viewer(self, viewer_user):
        url = reverse("admin:submissions_submissionchangelog_changelist")
        c = _client_for(viewer_user)
        resp = c.get(url)
        assert resp.status_code == 200

    def test_changelist_url_denied_for_bare_staff(self, db):
        bare_staff = User.objects.create_user(
            username="bare2", password="pass", email="bare2@test.com", is_staff=True
        )
        c = _client_for(bare_staff)
        url = reverse("admin:submissions_submissionchangelog_changelist")
        resp = c.get(url)
        # Django admin returns 403 or redirects to login when no view perm.
        assert resp.status_code in (302, 403)


# ---------------------------------------------------------------------------
# 8. Admin landing page — Viewer can access admin
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestAdminAccessibility:
    def test_viewer_can_reach_admin_index(self, viewer_user):
        from django.conf import settings

        prefix = getattr(settings, "ADMIN_URL_PREFIX", "admin-denbi")
        c = _client_for(viewer_user)
        resp = c.get(f"/{prefix}/")
        assert resp.status_code == 200

    def test_viewer_can_reach_submission_changelist(self, viewer_user, submission):
        c = _client_for(viewer_user)
        resp = c.get(_changelist_url())
        assert resp.status_code == 200

    def test_viewer_can_view_submission_detail(self, viewer_user, submission):
        c = _client_for(viewer_user)
        resp = c.get(_change_url(submission.pk))
        assert resp.status_code == 200

    def test_viewer_cannot_reach_add_submission(self, viewer_user):
        add_url = reverse("admin:submissions_servicesubmission_add")
        c = _client_for(viewer_user)
        resp = c.get(add_url)
        # Django admin returns 403 when has_add_permission is False.
        assert resp.status_code == 403

    def test_unauthenticated_redirected_to_login(self, db, submission):
        c = Client()  # no login
        resp = c.get(_changelist_url())
        assert resp.status_code == 302
        assert "login" in resp["Location"]
