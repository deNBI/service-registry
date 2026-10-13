---
icon: material/shield-account
---

# Admin Guide

For registry staff who review submissions and manage reference data in the Django admin. Deploying, configuring and running the server (e-mail settings, scheduled tasks, key rotation, backups) is covered in the [Operator Guide](operator/index.md). API keys have their own page: [Managing API Keys](admin/api-keys.md).

## Accessing the Admin Portal

The admin is available at `/<ADMIN_URL_PREFIX>/`. The shipped `config/site.toml` sets the prefix to `admin-denbi` (so `/admin-denbi/`); operators can change it there or with the `ADMIN_URL_PREFIX` environment variable.

Log in with the staff account the registry operators or a superuser created for you.

---
## User Roles & Permissions

The admin uses Django's group-based access control. Three purpose-built groups
map directly onto the real-world roles in the de.NBI service-registry team.
The groups are created and kept up-to-date by a management command — no manual
permission checkbox work is needed.

### The three groups at a glance

| Group                | Intended for                                    | Can do                                                                                                                                                            |
| -------------------- | ----------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Registry Viewer**  | Auditors, new team members, read-only observers | View submissions, change logs, submission API keys, reference data, EDAM terms, bio.tools records and Celery task results                                        |
| **Registry Editor**  | Day-to-day curators                             | Everything a Viewer can, **plus** create/edit submissions, approve/reject submissions, mark under-review/deprecated, and issue/reset/revoke submission API keys   |
| **Registry Manager** | Team leads, data stewards                       | Everything an Editor can, **plus** delete submissions and full add/change/delete on reference data (Service Categories, Service Centres, Principal Investigators) |

**Superusers** bypass the group system entirely and have unrestricted access. Some sections are not part of any group, so only superusers see them: **Admin API Keys**, **SPDX Licenses** and **Deletion Audits**.

### Permissions in detail

#### Registry Viewer

| Resource                      | Permissions |
| ----------------------------- | ----------- |
| Service Submissions           | View        |
| Submission Change Logs        | View        |
| Submission API Keys           | View        |
| Service Categories            | View        |
| Service Centres               | View        |
| Principal Investigators       | View        |
| EDAM Terms                    | View        |
| bio.tools Records & Functions | View        |
| Celery Task Results           | View        |

#### Registry Editor

Everything a Viewer has, plus:

| Additional capability                       | Permission codename                    |
| ------------------------------------------- | -------------------------------------- |
| Create submissions                          | `add_servicesubmission`                |
| Edit submission content                     | `change_servicesubmission`             |
| Approve / reject submissions                | `approve_servicesubmission` _(custom)_ |
| Mark submissions under review or deprecated | `change_servicesubmission`             |
| Issue, reset, and revoke API keys           | `manage_apikeys` _(custom)_            |

#### Registry Manager

Everything an Editor has, plus:

| Additional capability                         | Permission codename                       |
| --------------------------------------------- | ----------------------------------------- |
| Delete submissions                            | `delete_servicesubmission`                |
| Add / change / delete Service Categories      | `add/change/delete_servicecategory`       |
| Add / change / delete Service Centres         | `add/change/delete_servicecenter`         |
| Add / change / delete Principal Investigators | `add/change/delete_principalinvestigator` |

### Custom permission codenames

Two semantic permissions are defined on `ServiceSubmission.Meta.permissions`
and are separate from the standard CRUD permissions:

| Codename                                | What it gates                                                                                                                                                                      |
| --------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `submissions.approve_servicesubmission` | Approve and reject status transitions; bulk approve/reject actions. Kept separate from `change_servicesubmission` so editors can fix data without having final-decision authority. |
| `submissions.manage_apikeys`            | Issue, reset, and revoke `SubmissionAPIKey` objects. Kept separate so auditors can view key metadata without being able to create credentials that grant submitters write access.  |

These two permissions apply only in the Django admin. The REST API does not use staff accounts or these permissions: it is accessed with API keys (see [Managing API Keys](admin/api-keys.md)).

### Setting up the groups

The groups are created and kept in sync automatically each time the application starts (`manage.py setup_groups`); no permission checkboxes need to be ticked by hand. To preview or list the groups from the command line, see [Operations → Admin role groups](operator/operations.md#admin-role-groups).

### Creating a staff user and assigning a group

Only superusers can create users and assign groups; the role groups have no permissions on user accounts.

1. **Create the user**
   - Go to **Admin → Authentication and Authorization → Users → Add User**
   - Enter a username and a strong initial password, then click **Save and continue editing**
   - Tick **Staff status** (required to log in to the admin)
   - Leave **Superuser status** unticked unless full, unrestricted access is needed

2. **Assign a group**
   - In the **Groups** field, move the appropriate group
     (`Registry Viewer`, `Registry Editor`, or `Registry Manager`)
     from "Available groups" to "Chosen groups"
   - Click **Save**

3. **Inform the user** — they can log in at `/<ADMIN_URL_PREFIX>/` and should
   change their password immediately via the account menu (top right)

!!! note "A user can belong to multiple groups"
    Permissions are additive. A user in both `Registry Viewer` and
    `Registry Editor` has the union of both groups' permissions, but in
    practice it is cleaner to assign exactly one group per user.

### Superuser vs. staff user behaviour

|                                     | Superuser | Registry Manager | Registry Editor | Registry Viewer |
| ----------------------------------- | --------- | ---------------- | --------------- | --------------- |
| Log in to admin                     | ✓         | ✓                | ✓               | ✓               |
| View submissions, change logs, keys, reference data, EDAM, bio.tools | ✓ | ✓        | ✓               | ✓               |
| Edit submissions                    | ✓         | ✓                | ✓               | —               |
| Approve / reject                    | ✓         | ✓                | ✓               | —               |
| Manage submission API keys          | ✓         | ✓                | ✓               | —               |
| Delete submissions                  | ✓         | ✓                | —               | —               |
| Edit reference data                 | ✓         | ✓                | —               | —               |
| Manage Admin API keys               | ✓         | —                | —               | —               |
| View SPDX licences and deletion audits | ✓      | —                | —               | —               |
| See submission IP address           | ✓         | —                | —               | —               |
| Django admin settings & log entries | ✓         | —                | —               | —               |

### How permissions are enforced

Buttons and panels a user has no permission for are hidden: the **Status Actions** panel needs `change_servicesubmission` or `approve_servicesubmission`, and the **API Key Management** panel needs `manage_apikeys`. Hiding is only for convenience; the server checks the permission again for every action. A user with only `change_servicesubmission` sees the status buttons, but **Approve** and **Reject** are refused; only **Mark Under Review**, **Deprecate** and **Undeprecate** succeed. Implementation details are in [Admin Permission Enforcement](developer/admin-permissions.md).

---

## Managing Submissions

### Submission List View

**Columns:** Service name (link), Submitter (`LAST, First — Affiliation`), Status badge (colour-coded pill), **Maturity** tag (primary bold + secondary muted), Service Centre, **License(s)**, ELIXIR-DE flag, Submission date, **API Keys** (active/total count), **Keys** (🔑 Manage link to the key list filtered for that submission).

**Filters** (right sidebar): Status, Primary maturity tag, ELIXIR-DE flag, Service Centre, Service Categories, Responsible PIs, Submission date (date-drill-down calendar).

**Search** (top): service name, submitter first/last name, submitter affiliation, host institute, responsible PI name, licence id or name, and licence note.

**Default sort:** newest first. **Page size:** 30.

### Creating a submission in the admin

Staff with `add_servicesubmission` can create a submission directly in the admin. Such a submission gets no API key and sends no e-mail. To let the owner edit it later, issue a key on its change page ([Managing API Keys](admin/api-keys.md)) and send it to them.

### Submission Detail View

The detail view is organised into collapsible fieldsets. The fieldset layout is:

| Fieldset                           | Collapsed by default | Contents                                                                                                                                                                                     |
| ---------------------------------- | -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Status & Metadata**              | No                   | ID, Status (read-only display), submitted/updated timestamps, Submission IP (superusers only), **Status action buttons**, Primary maturity tag (radio), Secondary maturity tags (checkboxes) |
| **Last Change Summary**            | Yes                  | Most recent field-level diff                                                                                                                                                                 |
| **Change History**                 | Yes                  | Full diff log (most recent 50 entries, each collapsible); a footer line shows the total count when the log exceeds 50 entries                                                                |
| **A — General**                    | No                   | Date of entry, submitter name/affiliation, ELIXIR-DE flag                                                                                                                                    |
| **B — Service Master Data**        | No                   | Service name, description, year, categories, toolbox, EDAM, publications, logo + preview                                                                                                     |
| **C — Responsibilities**           | No                   | Responsible PIs, host institute, service centre, contact details                                                                                                                             |
| **D — Websites & Links**           | No                   | All URL fields                                                                                                                                                                               |
| **E — KPIs**                       | No                   | KPI monitoring, start year                                                                                                                                                                   |
| **F — Discoverability & Outreach** | No                   | Keywords, survey, comments                                                                                                                                                                   |
| **G — Consent**                    | No                   | Data protection consent (read-only)                                                                                                                                                          |
| **🔑 API Key Management**          | No                   | Inline key list + Revoke / Reset / Issue controls (hidden if user lacks `manage_apikeys`)                                                                                                    |

!!! note "Admins can change a locked service name"
    Submitters cannot change the service name after submission (on the edit form and in the REST API). In this admin change form the **Service name** field (fieldset **B**) stays fully editable, so an administrator can correct a name on a submitter's behalf.

!!! note "Status is read-only"
    The `Status` field is displayed for information only — it cannot be edited directly. All status transitions are made via the **Status action buttons** panel in the same fieldset (see below).

### Markdown descriptions

When `[features] markdown_descriptions = true` (see [Configuration](configuration.md#feature-flags)), **Service description** in fieldset **B** of the change form uses the same **Write** | **Preview** editor as the public forms (see the [user guide](user-guide.md#formatting-the-description) for the tabs, formatting help and counter). **Preview** renders the current text through the same sanitizer used by the catalogue and the API, and shows a notice when content was removed (images, code, horizontal rules, lists nested more than nine levels deep (or quotes about twenty), or links other than `http`, `https` and `mailto`, including relative links).

Staff who can view a submission but not change it see the description read-only, with an extra **Description (rendered)** row below it that shows the saved description as the catalogue list view displays it. Staff who can change the submission get the editor instead and do not see that row.

With the flag off, the editor and the rendered row are not shown and the description is edited as a plain text box. Descriptions are always stored as the raw text that was typed; rendering happens on output.

Notification emails never render Markdown: plain-text bodies show the description source without HTML escaping, and HTML bodies (such as the change table) escape it once. In both, HTML entities stored by older versions of the web form, such as `&gt;`, are decoded once, so they read as `>`, as they do in the catalogue and the API. The change form's text box, the change history and the CSV/JSON exports show the stored value unchanged.

**Before the flag is switched on**, operators run a read-only audit command that lists the descriptions whose display would change (see [Markdown descriptions rollout](rollout.md#markdown-descriptions-rollout)). Each flagged row has a reason: `text_changed` (the visible text would change, for example `*word*` becomes emphasis or a line starting with `# ` becomes a heading) or `content_removed` (code, images, horizontal rules, deeply nested lists, or links that are not full `https://`, `http://` or `mailto:` addresses would be dropped).

Fix flagged rows by editing the description in this change form. **Admin edits do not reset the submission status**, so approved services stay approved. See the [Markdown descriptions rollout](rollout.md#markdown-descriptions-rollout) for the full sequence.

### Changing Submission Status

**On new submission:** the registry address receives a notification with a link to the admin change view, and the submitter receives a receipt confirmation. Who receives which e-mail is listed in [E-mail and Notifications](operator/email.md).

**Individual status change (change view):** Open a submission. The **Status & Metadata** fieldset contains a row of action buttons — the current status is highlighted and its button is disabled. Click any other button to trigger the transition:

| Button                      | Required permission         | Result                                 |
| --------------------------- | --------------------------- | -------------------------------------- |
| **Approve**                 | `approve_servicesubmission` | → `Approved`                           |
| **Reject**                  | `approve_servicesubmission` | → `Rejected`                           |
| **Mark Under Review**       | `change_servicesubmission`  | → `Under Review`                       |
| **Deprecate**               | `change_servicesubmission`  | → `Deprecated`                         |
| **Undeprecate → Submitted** | `change_servicesubmission`  | → `Submitted` (re-enters review queue) |

!!! note "Status changes are recorded"
    Each status change (button or bulk action) is recorded as a `status` change, with `admin:<username>`, in the **Change History** and the **Last Change Summary**, and also in the **History** tab. Choosing the status a submission already has records nothing.

Every status transition sends the registry a notification and the submitter a plain-language status message (for example "Your service has been approved"). The submitter message is a separate e-mail and contains no admin link.

**Bulk status changes:** Select submissions in the list view, then choose an action from the dropdown:

| Action                        | Required permission         | Result                                  |
| ----------------------------- | --------------------------- | --------------------------------------- |
| Approve selected              | `approve_servicesubmission` | → `Approved`                            |
| Reject selected               | `approve_servicesubmission` | → `Rejected`                            |
| Mark as Under Review          | `change_servicesubmission`  | → `Under Review`                        |
| Deprecate selected            | `change_servicesubmission`  | → `Deprecated`                          |
| Undeprecate selected (→ Submitted) | `change_servicesubmission` | → `Submitted` (returns to review queue) |

All bulk transitions fire the admin + submitter email notifications via Celery and are logged to Django's LogEntry.

!!! note "Maturity tags are auto-cleared on any non-approved transition"
    Both individual buttons and bulk actions call the same underlying `_change_status` function, which clears maturity tags whenever moving away from `Approved`. See [Assigning Maturity Tags](#assigning-maturity-tags) for details.

!!! note "Deprecation is owner-reversible only by admins"
    Service owners can mark their own service as deprecated via the edit form. Only admins can reverse a deprecation (via bulk action or the change view button), which resets status to `Submitted` for re-review.

!!! note "Draft status"
    `draft` exists in the data model but nothing sets it: not the forms, the API, the status buttons or any automated workflow. Unsubmitted forms exist only as drafts in the submitter's browser.

#### Submitter edits on approved services

When a submitter edits an approved service (via the update form or REST API), the platform determines whether to reset the status to `submitted`:

- **Exempt fields** (configured in `site.toml [submission] no_reset_fields`) do not trigger a reset. The shipped default set covers supplementary fields: the external links, EDAM annotations, keywords, publications, contact fields, KPI fields, and `comments`. See [`no_reset_fields`](configuration.md#no_reset_fields) for how the list is configured.
- **Any non-exempt field change** resets status to `submitted` and clears maturity tags. The submitter's update email includes a lifecycle notice: *"Because one or more core service fields were modified, your service registration has been reset to Submitted…"*
- **Exempt-only edits** (e.g. uploading a logo, updating EDAM annotations) preserve the `approved` status and maturity tags. The submitter receives the normal diff email with no lifecycle notice.

Sanitization artifacts (trailing whitespace stripped by form cleaning, Unicode normalization) are excluded from the reset decision — only genuine value changes count.

---

## Audit Logging

The system maintains three complementary audit trails for tracking changes to submissions.

### Submission Change Log

**Location 1:** Admin → Service Submissions → Open submission → **Change History** fieldset (collapsed by default)

**Location 2:** Admin → Service Submissions → **Change Log** (sidebar menu)

A dedicated append-only table (`SubmissionChangeLog`) that captures **all** field-level changes to a submission, regardless of source. The Change Log admin list is **read-only** — add, edit, and delete are all blocked at the admin level. Entries can never be modified or removed through the admin UI.

| Source                  | `changed_by` value   |
| ----------------------- | -------------------- |
| Submitter web form edit | `"submitter"`        |
| Admin backend save      | `"admin:<username>"` |
| API PATCH               | `"api:<key_label>"`  |

**Structure:**

- `submission` → ServiceSubmission (FK)
- `changed_by` → Actor identifier
- `changed_at` → UTC timestamp
- `changes` → JSON array of `{"field", "label", "old", "new"}`

**Features:**

- One row per edit event (never updated or deleted)
- **Change History fieldset** (on each submission page) shows entries collapsibly per submission
- **Dedicated Change Log view** (sidebar menu) shows a list of all entries system-wide with:
  - **Submission link** — click the service name to filter the Change Log to all entries for that submission
  - Who changed it (submitter/admin/API)
  - **Changed at** — click the timestamp to open that specific entry's read-only detail view showing the full before/after diff
  - Number of fields changed

### Django LogEntry (History tab)

**Location:** Admin → Service Submissions → Open submission → **History** tab (top right)

Django's built-in `LogEntry` table that records **admin-initiated actions only**:

- Status changes (approve/reject/under_review/deprecate); these also appear in the Submission Change Log
- API key operations (issue/reset/revoke)
- Direct admin saves via the backend

**Not covered:** Submitter edits or API PATCH requests.

### Deletion Audit Log

**Location:** Admin → Service Submissions → **Deletion Audits** (sidebar menu; superusers only)

When a `ServiceSubmission` is hard-deleted, a `SubmissionDeletionAudit` record is written **before** the cascade. This record is never linked by FK to the submission so it is never cascade-deleted itself — it persists indefinitely.

Each record captures:

- Key submission fields at time of deletion: service name, status, submitter info, contact email
- Who deleted it (`admin:<username>`)
- When it was deleted
- The number of change log entries that were cascade-deleted
- A full snapshot of every `SubmissionChangeLog` entry (changed_by, changed_at, fields changed)

**Delete confirmation warning:** When deleting a submission that has change log entries, the admin confirmation page displays a warning showing how many entries will be lost and suggests marking the submission as **Deprecated** instead (which preserves all history while hiding it from active listings).

### Which to use?

| Use Case                                                          | Recommended                       |
| ----------------------------------------------------------------- | --------------------------------- |
| Track ALL changes to a submission (submitter + admin + API)       | **Submission Change Log**         |
| Quick audit of admin actions only                                 | **Django LogEntry (History tab)** |
| Query changes programmatically (e.g., "who changed X on date Y?") | **Submission Change Log**         |
| Simple overview of admin activity                                 | **Django LogEntry**               |
| Audit trail for hard-deleted submissions                          | **Deletion Audit Log**            |

---

### Last Change Summary

Every submission change view includes a collapsible **Last Change Summary** section (collapsed by default — click to expand). It shows the most recent edit only.

| Column         | Description                                                                                        |
| -------------- | -------------------------------------------------------------------------------------------------- |
| **Changed by** | `submitter` (edit form), `admin:<username>` (admin backend), or `api:<key_label>` (REST API PATCH) |
| **Changed at** | UTC timestamp of the edit                                                                          |
| **Field**      | Human-readable field name                                                                          |
| **Before**     | Previous value (shown in red)                                                                      |
| **After**      | New value (shown in green)                                                                         |

!!! note "Status changes appear here too"
    Status changes made with the buttons or bulk actions, and a submitter deprecating their own service, are recorded as a change of the `status` field, so they appear in the **Last Change Summary** and the **Change History**. API key operations do not; they appear only in the **History** tab.

!!! info "E-mails on submitter edits"
    A submitter edit (edit form or API `PATCH`) sends the registry a report with a **what changed** table and a link to the change view; the submitter gets a confirmation with the same table. See [E-mail and Notifications](operator/email.md).

---

## Exporting Submissions

The list view sidebar filters by **status**, making it easy to find submissions in a specific state:
`Draft` · `Submitted` · `Under Review` · `Approved` · `Rejected` · `Deprecated`

Combine status with the category, service centre, or date filters to narrow down the selection, then select submissions → choose **Export selected as CSV** or **Export selected as JSON**. All admin groups, including Viewers, can export.

Both formats contain these fields:

| Category               | Fields included                                                                                                                                                                                  |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Identity               | `id`, `status`, `primary_maturity_tag`, `secondary_maturity_tags`, `date_of_entry`, `submitted_at`, `updated_at`                                                                                 |
| Submitter              | `submitter_first_name/last_name/affiliation` (JSON: nested `submitter` object), `host_institute`, `public_contact_email`, `internal_contact_name/email`, `associated_partner_note`               |
| Service                | `service_name`, `service_description`, `year_established`, `is_toolbox`, `toolbox_name`, `user_knowledge_required`, `publications_pmids`                                                         |
| Relations              | `service_center` (name), `service_categories`, `responsible_pis` (semicolons in CSV, arrays in JSON)                                                                                              |
| EDAM                   | `edam_topics`, `edam_operations` — label + URI (semicolons in CSV, objects in JSON)                                                                                                              |
| Links and licences     | `website_url`, `terms_of_use_url`, `licenses` (SPDX ids), `license_note`, `github_url`, `biotools_url`, `fairsharing_url`, `other_registry_url`                                                  |
| KPIs                   | `kpi_monitoring`, `kpi_start_year`                                                                                                                                                               |
| Discovery              | `keywords_uncited`, `keywords_seo`, `register_as_elixir`, `survey_participation`, `comments`                                                                                                     |
| Logo                   | `logo_url` — absolute URL, or empty if no logo uploaded                                                                                                                                          |
| bio.tools              | `biotools_id`, `biotools_name`, `biotools_description`, `biotools_homepage`, `biotools_version`, `biotools_license`, `biotools_maturity`, `biotools_cost`, `biotools_tool_type`, `biotools_operating_system`, `biotools_edam_topic_uris`, `biotools_edam_operation_uris`, `biotools_functions`, `biotools_publications`, `biotools_documentation`, `biotools_download`, `biotools_links`, `biotools_last_synced_at` — empty if the service has no bio.tools record |

In the CSV each bio.tools field is its own column (lists joined with semicolons, structured values as JSON strings). In the JSON export they are grouped in one `biotools` object with the same key names.

!!! note "CSV cells that start like a formula"
    In the CSV export, any text cell that starts with `=`, `+`, `-`, `@`, a tab or a carriage return gets a leading `'` so spreadsheet applications treat it as text instead of a formula. This applies to every column: a description that starts with a Markdown bullet appears as `'- item`, and a phone number written as `+49 ...` appears as `'+49 ...`. The JSON export and the API return the raw values unchanged.

!!! note "JSON export uses a nested submitter object"
    In the JSON export, submitter fields are grouped under a `"submitter"` key:
    `{"submitter": {"first_name": "…", "last_name": "…", "affiliation": "…"}, …}`.
    The CSV export keeps these fields flat (`submitter_first_name`, `submitter_last_name`, `submitter_affiliation`).

---

## Assigning Maturity Tags

Maturity tags categorize services by lifecycle state. Tags can **only be assigned to approved services**.

### Tag types

| Tag Type                 | Values                   | Notes                               |
| ------------------------ | ------------------------ | ----------------------------------- |
| **Primary** (optional)   | Mature, Emerging, Legacy | Mutually exclusive; one per service |
| **Secondary** (optional) | Unstable, etc.           | Zero or more per service            |

### Tag lifecycle and auto-clear on unapproval

Tags are bound to approved status. **Any status transition away from Approved automatically clears all maturity tags** — this covers single-service status buttons and bulk actions (Reject, Mark Under Review, Deprecate, Undeprecate → Submitted).

When a tagged service is about to be unapproved, the change form shows a yellow warning banner listing which tags will be cleared. An info message is also shown after the action confirms how many services had tags cleared.

To re-tag a service after re-approval, use the change form or bulk action again.

### Single service: Edit in change form

1. Open an approved submission's detail page
2. Scroll to the **Status & Metadata** section
3. Select a primary tag (radio buttons) or leave blank
4. Check secondary tags (checkboxes) as needed
5. Save

!!! note
    Using a status button (Reject, Deprecate, etc.) in the same section will automatically clear any selected tags — the warning banner indicates this before you act.

### Bulk assignment: List action

1. Filter the submission list by status = Approved (optional)
2. Select multiple approved submissions
3. From the **Action** dropdown, select **Assign maturity tags to selected submissions**
4. In the popup form, select tags
5. Click **Assign Tags**

Non-approved submissions are filtered out automatically with a warning. Mixed selections (approved + non-approved) result in tags applied only to the approved subset.

---

## API Key Management

Issuing, resetting and revoking submission API keys, and the Admin API keys used by integrations, are described in [Managing API Keys](admin/api-keys.md).

---

## Managing Reference Data

Reference data (PIs, service centres, categories) can be managed in two ways:

- **Admin UI** — the Django admin portal (see below)
- **REST API** — `POST/PATCH/DELETE /api/v1/pis/`, `/api/v1/service-centers/`, `/api/v1/categories/` with an Admin API key — useful for bulk onboarding or automation (see [Admin REST API](admin/api.md#reference-data-categories-service-centres-pis) and the [bulk import script](operator/data-sync.md#bulk-import-of-pis-categories-and-service-centres))

Both interfaces support soft-delete: `DELETE` via the API (or setting `is_active = False` in the admin) hides the record from the registration form but keeps it linked to existing submissions.

### Deletion guard

Hard deletion of any PI, service centre, or service category is **blocked** whenever the record is referenced by at least one submission (in any status — draft, submitted, approved, etc.).

| Scenario                                                              | Behaviour                                                                                                                                                                                                                            |
| --------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Single delete — record **in use**                                     | **Blocked.** The confirmation screen is never shown. The admin is redirected straight back to the list page with an error message stating how many submissions are affected and instructing them to use `is_active = False` instead. |
| Single delete — record **not in use**                                 | Allowed. The normal Django confirmation page is shown and deletion proceeds only after the admin confirms.                                                                                                                           |
| Bulk "Delete selected" — **any** selected record in use               | **Blocked entirely.** No records in the selection are deleted. A detailed error lists each blocked record and its submission count.                                                                                                  |
| Bulk "Delete selected" — **all** selected records have no submissions | Allowed. All selected records are deleted.                                                                                                                                                                                           |

!!! warning "Use `is_active = False` to retire records, not Delete"
    Setting `is_active = False` hides the record from the submission form dropdown
    while preserving all existing submission links. This is always the correct
    operation for records that are no longer in use. Hard deletion is only
    appropriate for records that were added by mistake and have never been
    referenced by any submission.

The **Submissions** column in each list view shows how many submissions currently
reference the record. For Service Categories and Service Centres the count is a
hyperlink that opens the submission changelist pre-filtered to that record, making
it easy to see exactly which submissions are affected before deciding whether to
deactivate or delete.

### Principal Investigators (PIs)

**Location:** Admin → Reference Data → Principal Investigators

- Add new PIs who are not yet listed.
- Set `is_active = False` to hide a PI from the form dropdown without removing them from existing submissions.
- The `is_associated_partner` flag should be `True` for exactly one entry (the generic "Associated partner" option).
- ORCID iDs are validated on save.
- The **Submissions** column shows the total number of submissions that list this PI as responsible (plain count — no hyperlink, as the submission list does not have a per-PI filter).

!!! info "PI institutes populate the affiliation combobox"
    The **Institute** field on each PI record feeds directly into the affiliation
    autocomplete shown to submitters in Section A of the registration form.
    Keeping PI institute names consistent and up-to-date here helps submitters
    find and reuse the correct spelling, reducing data inconsistencies.

### Service Centres

**Location:** Admin → Reference Data → Service Centers

- Each centre has a short name (e.g. "HD-HuB"), full name, and optional website.
- `is_active = False` hides from the form but keeps existing submission links intact.
- The **Submissions** column links to the filtered submission changelist for that centre.

### Service Categories

**Location:** Admin → Reference Data → Service Categories

- Add new category types as needed.
- `is_active = False` hides from the form.
- The **Submissions** column links to the filtered submission changelist for that category.

---

## Service Logos

Submitters can optionally upload a logo for their service during registration or when editing their submission. Logos can also be uploaded from the admin detail view.

Accepted formats are PNG, JPEG and SVG, within the limits set in [`[uploads]`](configuration.md#uploads) (by default PNG/JPEG up to 10 MB and 25 megapixels, SVG up to 1 MB). An upload from the admin is checked and processed exactly like one from the forms or the API: JPEG and PNG are re-encoded without metadata, SVGs are reduced to standard drawing content, and files that break the rules are rejected with a message. The rules are listed in the [User Guide](user-guide.md#logo); the implementation is in [Logo Upload Pipeline](developer/logo-pipeline.md).

Saving a submission without choosing a new file keeps the stored logo exactly
as it is: stored logos are never re-processed, so rule changes do not alter
logos that are already in place.

### Admin usage

Open a submission's detail view. The **B — Service Master Data** section shows:

- **Logo** — file upload widget to add or replace the current logo
- **Logo preview** — inline image display of the currently stored logo (or "—" if none)

A replaced logo file stays on disk; nothing deletes it automatically. If a large admin upload fails with a bare HTTP 413 page, the reverse proxy limit is too low: see [Deployment → Request size for uploads](deployment.md#request-size-for-uploads).

---

## EDAM Ontology Management {#edam-management}

**Location:** Admin → EDAM Ontology → EDAM Terms

EDAM terms are imported from the official EDAM ontology release and are read-only in the admin.
Terms cannot be added or deleted manually — all changes come through a sync.

### How EDAM terms are kept current

The terms are loaded automatically on the first deployment and refreshed every 30 days. You can also start a sync from the admin (below). Command-line options, air-gapped servers and the full schedule are in [Reference Data and Ontology Sync](operator/data-sync.md#edam-ontology).

### Viewing Terms

The list shows: accession (e.g. `topic_0121`), label, branch, obsolete flag, EDAM version.

Filter by **branch** to see only topics, operations, data types, formats, or identifiers.
Search by label or definition text.

### Checking the Loaded Version

The **EDAM version** column shows which release each term was last loaded from (for example `1.25-20200618T0915Z`).
All terms should show the same version after a successful sync.

### Triggering a manual EDAM sync

**From the admin UI** (recommended — no shell access needed):

1. Go to **EDAM Ontology → EDAM Terms**
2. Click **↻ Sync EDAM from upstream** in the top-right toolbar
3. A green message confirms the task was queued
4. Refresh the page after ~30 seconds to see the updated term count and version

New terms appear immediately in the form. Obsolete terms are hidden from the form but
retained in the database so existing submissions referencing them are not broken.

If the form shows no EDAM terms, ask an operator to check the sync (see [Data sync → If the form shows no EDAM terms](operator/data-sync.md#if-the-form-shows-no-edam-terms)).

---

## SPDX License Management {#spdx-management}

**Location:** Admin → SPDX Licenses → SPDX Licenses (superusers only)

SPDX licenses are imported from the canonical [SPDX License List](https://spdx.org/licenses/) and are read-only in the admin. Entries cannot be added or deleted manually — all changes come through a sync. The list is loaded on the first deployment and refreshed every 30 days; command-line options are in [Reference Data and Ontology Sync](operator/data-sync.md#spdx-licences).

### Viewing Licenses

The list shows: SPDX id (e.g. `MIT`, `Apache-2.0`), name, OSI approval, FSF libre, deprecated flag, SPDX version. Filter by **OSI-approved**, **deprecated**, or search by id/name.

### Deprecated entries

Licenses that are either deprecated upstream in SPDX, or that disappear from upstream in a subsequent sync, are marked `is_deprecated=True`. Deprecated entries are hidden from new submission pickers but **retained in the database** so existing submissions referencing them are not broken.

### Triggering a manual SPDX sync

**From the admin UI**:

1. Go to **SPDX Licenses → SPDX Licenses**
2. Click **↻ Sync Licenses from SPDX** in the top-right toolbar
3. A green message confirms the task was queued
4. Refresh the page after a few seconds to see the updated license count and version

---

## bio.tools Record Management {#biotools-records}

When a submitter enters a bio.tools URL, the system automatically fetches and stores a local
copy of the tool's bio.tools entry. This is displayed in the submission detail view and
exposed in the API.

### Viewing bio.tools Records

**Location:** Admin → bio.tools Integration → bio.tools Records

Each record shows:

- The linked submission
- The bio.tools ID and link to bio.tools
- Extracted metadata: name, description, license, tool types, maturity
- EDAM topic URIs sourced from bio.tools
- Last sync timestamp and any sync error

The **Functions** inline shows all EDAM Operation/Input/Output annotations from bio.tools,
structured as one row per function block.

### Sync Status

The list view shows a green ✓ or red ✗ for each record's last sync status.
A red ✗ means the last sync failed — check the **sync_error** field on the record.

Common sync errors:

- `bio.tools tool not found (HTTP 404)` — the bio.tools ID in the submission URL is wrong
- `bio.tools network error` — the server could not reach bio.tools (check firewall/proxy)
- `bio.tools API error (HTTP 5xx)` — bio.tools is temporarily unavailable; the next sync tries again

### Manually Triggering a Sync

From the admin list, select records and choose **Sync selected records from bio.tools now**.
This queues a background Celery task; the record refreshes within a few seconds.

The command-line sync, and creating a missing record for a submission, are described in [Reference Data and Ontology Sync](operator/data-sync.md#biotools-records). Records are refreshed automatically every 24 hours.

