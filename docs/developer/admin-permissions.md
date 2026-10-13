---
icon: material/shield-lock
---

# Admin Permission Enforcement

## Defence in depth: how permissions are enforced

The permission system has two enforcement layers that work together:

1. **Outer gate — `has_*_permission` methods on the `ModelAdmin`**
   Django calls these before rendering any view. A Viewer's `has_change_permission`
   returns `False`, so any HTTP POST to the change URL returns **403 Forbidden**
   before the code even processes the POST body.

2. **Inner gate — `_require_perm` guards in `response_change`**
   Each privileged POST action (approve, reject, issue key, revoke keys, …) is
   guarded individually inside `response_change`. This is defence-in-depth:
   even if a future refactor were to inadvertently widen the outer gate, the
   inner guards would still block the action and show a "Permission denied"
   message instead of silently performing it.

The conditional fieldset system (`get_fieldsets`) hides the **Status Actions**
panel and the **Key Management** panel from users who lack the relevant
permissions. This is a UI convenience, not a security boundary — the inner
guards are the security boundary.

Specifically: the **Status Actions** panel is shown to any user who has **either**
`change_servicesubmission` **or** `approve_servicesubmission`. A user with only
`change_servicesubmission` will see the buttons, but the Approve and Reject
buttons are still blocked by the inner `_require_perm` guard if that permission
is absent — only Under Review, Deprecate, and Undeprecate will succeed.
