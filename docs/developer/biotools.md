---
icon: material/tools
---

# bio.tools Integration Internals

`apps/biotools/` keeps a local copy of the [bio.tools](https://bio.tools) entry of every submission that has a `biotools_url`, and serves the form prefill. The admin side is in the [Admin Guide](../admin-guide.md#biotools-records); command-line syncs are in [Reference Data and Ontology Sync](../operator/data-sync.md#biotools-records).

## Modules

| Module       | Purpose                                                                                                   |
| ------------ | --------------------------------------------------------------------------------------------------------- |
| `client.py`  | `BioToolsClient`: a small HTTP client (standard library `urllib`, 15-second timeout) for `GET https://bio.tools/api/tool/{id}?format=json` and the name search. Raises `BioToolsNotFound` or `BioToolsError`. |
| `sync.py`    | `sync_tool(biotools_id, submission_id, ...)`: fetches one tool and creates or updates `BioToolsRecord` and its `BioToolsFunction` rows. Used by the task, the management command and the admin action. |
| `tasks.py`   | `biotools.sync_record` (one submission; 3 retries, 2 minutes apart) and `biotools.sync_all` (every submission with a `biotools_url`, run daily by beat). |
| `signals.py` | `post_save` on `ServiceSubmission`: schedules `biotools.sync_record` two seconds after a save when the submission has a `biotools_url` and no record for that URL's tool; removes the record when the URL is empty. |
| `views.py`   | The form endpoints below.                                                                                 |
| `admin.py`   | Record list with sync status, the **Functions** inline and the "Sync selected records from bio.tools now" action. |

## Form endpoints

Both are `GET` and rate-limited by `RATE_LIMIT_BIOTOOLS` per client IP. The registration form calls the prefill endpoint from `initBioToolsPrefill()` in `static/js/edam-autocomplete.js`; no template or script calls the search endpoint at the moment.

| URL                                    | Returns                                                                                       |
| -------------------------------------- | --------------------------------------------------------------------------------------------- |
| `/biotools/prefill/?id=<biotools_id>`  | JSON with suggested form values (name, description, homepage, licence, up to 10 publication ids, GitHub URL, EDAM topics and operations) |
| `/biotools/search/?q=<name>`           | A short list of bio.tools candidates matching a name                                          |

The form shows the "bio.tools data found" banner and fills fields only when the user clicks **Apply prefill** (see the [User Guide](../user-guide.md#biotools-prefill)).

## When a record is synced

The bio.tools id is always taken from the submission's current `biotools_url` (`biotools_id_from_url()` in `sync.py`). A submission has at most one record, so syncing a changed URL replaces the old tool's data.

- **On save:** the signal schedules a sync when the submission has a `biotools_url` and no record for the tool in that URL exists (new submission, changed URL, or an earlier sync that never created a record). Other saves, such as status changes, do not trigger a sync. When the URL is empty, the submission's record is removed.
- **Daily:** `biotools.sync_all` first removes records of submissions without a `biotools_url`, then syncs every submission that has one, creating missing records.
- **By hand:** the admin action queues `biotools.sync_record` for the selected records' submissions. `manage.py sync_biotools --submission <uuid>` syncs one submission; without `--submission` it removes records of submissions without a URL (not on `--dry-run`) and refreshes every existing record.

## Tests

`tests/test_biotools.py` covers the client (HTTP mocked), the sync logic, the tasks, the signal and the views.
