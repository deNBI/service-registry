---
icon: material/key-variant
---

# Managing API Keys

The registry has two kinds of API key:

| Key                    | Header                         | Belongs to                       | Who manages it                                                    |
| ---------------------- | ------------------------------ | -------------------------------- | ----------------------------------------------------------------- |
| **Submission API key** | `Authorization: ApiKey <key>`  | One submission                   | Issued automatically at registration; staff with `manage_apikeys` can issue, reset and revoke more |
| **Admin API key**      | `Authorization: AdminKey <key>`| No submission, no user account   | Superusers only                                                   |

Both are stored as SHA-256 hashes. The plaintext is shown **once**, when the key is created, and cannot be shown again: if it is lost, issue a new key.

---

## Submission API keys

A submitter receives one key with **write** scope when they register. It lets them open the web edit form and use the REST API for their own submission only. How submitters use it is described in the [User Guide](../user-guide.md#your-api-key) and in [Using the API](../api-guide.md).

| Scope   | REST API    | Web edit form (`/update/edit/<submission_id>/`) |
| ------- | ----------- | ----------------------------------------------- |
| `read`  | GET only    | View the form, cannot save                      |
| `write` | GET + PATCH | View and save changes                           |

### On the submission change page

Each submission's change page has an **API Key Management** section (shown only to staff with the `manage_apikeys` permission). It lists every key ever issued for the submission with its label, scope, creation date, last use and whether it is active.

| Action                   | What it does                                                                                                                                                          |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Revoke all keys**                  | Deactivates all active keys. The submitter can no longer edit their submission until a new key is issued.                                                             |
| **Reset (revoke all + issue one)**   | Revokes all keys and issues one new **write** key. The new plaintext key is shown **once** in a banner. Communicate it to the submitter securely (e.g. encrypted email, phone). |
| **Issue new key**                    | Creates a new active **write** key alongside existing ones, with the label you enter. Useful for CI/CD pipelines or team members.                                       |

**Auto-generated labels:** If the label field is left blank, the system fills it automatically — `"Admin key YYYY-MM-DD by <username>"` for Issue, `"Admin reset YYYY-MM-DD by <username>"` for Reset.

Keys issued on this page always have **write** scope. A **read-only** key is created from the Submission API Keys list (below): either with **Add Submission API Key**, or with the scope selector in the key panel on an existing key's page. A key's scope is fixed when it is created; to change it, issue a new key and revoke the old one.

!!! note "Key operations do not email the submitter"
    Revoke, Reset, and Issue actions are silent — no notification is sent to the submitter. Communicate the new key manually (e.g. encrypted email, phone).

All key operations are logged in Django's admin audit log (**History** tab, top right of the submission change view).

### The Submission API Keys list

**Location:** Admin → Service Submissions → Submission API Keys

This view lists **all keys across all submissions** in one place. Viewers can look; staff with `manage_apikeys` (Editors, Managers) can also change keys here. Use it to:

- Filter by active/inactive status or by submission status
- Search by service name, key label, or the username of the staff member who created the key
- Add a key for a submission (**Add Submission API Key**: pick the submission, a label and the scope)
- Open a key to deactivate it, or to issue, reset or revoke keys for its submission

### When a submitter has lost their key

Submitters are told to contact the registry office (user guide: [Lost your API key?](../user-guide.md#lost-your-api-key)). Before issuing a new key, check that the request comes from the submission's internal contact, for example by replying to the internal contact address stored on the submission. Then use **Reset key** (or **Issue additional key** if the old key should keep working) and send the new key over a secure channel.

---

## Admin API keys

Admin API keys give machine-to-machine access to the [Admin REST API](api.md): listing all submissions, managing categories, service centres and PIs, and the bio.tools list. They are not tied to a staff account. Only superusers can manage them: the admin role groups do not include this permission.

**Location:** Admin → API Keys → Admin API Keys

### Scopes

| Scope  | Allowed HTTP methods       | When to use                                                    |
| ------ | -------------------------- | -------------------------------------------------------------- |
| `read` | GET / HEAD / OPTIONS only  | Read-only dashboards, reporting, scripts that only read        |
| `full` | All methods (read + write) | Trusted internal integration that needs to create, change or deactivate records |

New keys default to `full` in the form. Choose `read` unless the consumer needs to write.

### What a read key can see

A `read` key cannot change anything, but it can read everything the admin API returns:

- every submission in every status (submitted, under review, rejected, deprecated as well as approved), including submitter names and affiliations, `public_contact_email` and `comments`;
- all PIs, including their e-mail addresses (`/api/v1/pis/`);
- all categories, service centres and bio.tools records.

It never returns the internal contact fields, the submission IP or the user-agent hash. Treat every Admin API key, including `read` keys, as confidential, and do not embed one in a public web page.

### Creating a key

1. Go to **API Keys → Admin API Keys → Add Admin API Key**.
2. Enter a descriptive **Label** (e.g. `Reporting dashboard`, `Reference data import`).
3. Select the **Scope**.
4. Click **Save**.
5. The full plaintext key appears once in a warning banner — copy it immediately.

### Using the key

```bash
curl https://service-registry.bi.denbi.de/api/v1/submissions/ \
  -H "Authorization: AdminKey <plaintext-key>"
```

A write request with a `read` key returns 403:

```json
{
  "error": {"detail": "This key is read-only. Use a full-access Admin API Key to modify data."},
  "request_id": "…"
}
```

### Revoking a key

Uncheck **Is active** in the admin change view and save. The key stops working
immediately. The record is kept for audit purposes — hard deletion is intentionally
disabled. The change view also shows when the key was last used.
