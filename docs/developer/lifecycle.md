---
icon: material/history
---

# Change Tracking and Status Reset

## Field-level diff (`diff_utils`)

`apps/submissions/diff_utils.py` computes a human-readable before/after diff for any submission save. It is used by all three edit paths — the submitter web form (`views.py`), the admin backend (`admin.py`), and the REST API (`api/views.py`).

### Key functions

| Function                    | Purpose                                                                                                              |
| --------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `snapshot(instance)`        | Returns a `dict` of current scalar field values; **must be called before** `form.is_valid()` or admin `save_model()` |
| `snapshot_m2m(instance)`    | Returns a `dict` of current M2M field values as sorted lists of strings                                              |
| `build_diff(before, after)` | Compares two snapshots; returns `[{field, label, old, new}]` for changed fields only                                 |
| `filter_sanitization_artifacts(changes, form_changed_data, form_field_names)` | Removes false-positive diff entries where form sanitization (bleach, NFC normalisation, whitespace stripping) produced a raw difference that resolved to the same stored value. Always applied after `build_diff()` in the web-form edit path. |

### Snapshot timing

Django's `ModelForm._post_clean()` calls `construct_instance()` during `is_valid()`, which writes POST data onto `form.instance` **before** `form.save()` is called. Always take the `snapshot()` and `snapshot_m2m()` calls before constructing the form:

```python
before_scalar = snapshot(submission)          # ← before form construction
before_m2m    = snapshot_m2m(submission)
form = SubmissionForm(request.POST, instance=submission)
form.is_valid()                               # ← would corrupt before-state if snapshot taken here
form.save()
after_scalar = snapshot(submission)
after_m2m    = snapshot_m2m(submission)
changes = build_diff({**before_scalar, **before_m2m}, {**after_scalar, **after_m2m})
```

In the admin, `save_model()` receives `obj` already populated with new form values — re-fetch the original from the database for the before-snapshot:

```python
original = obj.__class__.objects.get(pk=obj.pk)
before_scalar = snapshot(original)
```

DRF serializers do **not** modify `self.instance` during `is_valid()`, so the snapshot can safely be taken before serializer construction.

### Adding a diffable field

1. Add an entry to `DIFFABLE_FIELDS` (scalar) or `DIFFABLE_M2M` in `diff_utils.py`
2. If the field uses `choices`, add it to `_CHOICE_FIELDS` so `get_FOO_display()` is used
3. Add a test to `tests/test_diff_utils.py`

### Sanitization artifacts

Form `clean_*` methods (bleach, `unicodedata.normalize`, `.strip()`) may produce a cleaned value identical to the stored value even when the raw POST string differed slightly (trailing spaces, NFC variants). `build_diff()` compares snapshots of the **stored model value** before and after save, so such non-changes do not appear in the diff.

`filter_sanitization_artifacts()` provides a second layer: it removes any diff entry where the field is managed by the form **and** is absent from `form.changed_data`. It is called after `build_diff()` in the web-form path before persisting `last_change_summary`.

**Status reset and sanitization:** `EditView` determines whether to reset status using a *prospective diff* — it compares `before_scalar` against `snapshot(updated)` taken from the in-memory instance after `form.save(commit=False)`. Because `_post_clean()` has already applied `clean_*` normalization at that point, sanitization artifacts never appear in the prospective diff and cannot trigger a false status reset.

---

## Submission lifecycle (status reset)

When a submitter edits an **approved** service via the web form or REST API, the platform determines whether to reset the status to `submitted` (triggering a re-review) based on which fields actually changed.

### How "changed" is determined

**Scalar fields** — a *prospective diff* compares the `before_scalar` snapshot (taken before form processing) with a snapshot of the in-memory instance after `form.save(commit=False)`. This uses the already-cleaned values, so sanitization artifacts (whitespace, NFC normalization) do not produce false positives.

**M2M fields** — `form.changed_data` filtered to the set of tracked M2M field names (`DIFFABLE_M2M`). M2M comparison is PK-based with no sanitization, so `form.changed_data` is reliable here.

### `no_reset_fields` exemption

Fields listed in `settings.SUBMISSION_NO_RESET_FIELDS` (configured via `site.toml [submission] no_reset_fields`) are exempt. If all actually-changed fields are exempt, the `approved` status is preserved. If any non-exempt field changed, status is reset and maturity tags are cleared (they are only valid on approved services).

The exemption is enforced identically in:

- `apps/submissions/views.py` `EditView.post()` — web form path
- `apps/api/views.py` `partial_update()` — REST API path

### `lifecycle.py`

`apps/submissions/lifecycle.py` provides `get_no_reset_fields()` — an `@lru_cache`-decorated function that reads `settings.SUBMISSION_NO_RESET_FIELDS`, validates each entry against `DIFFABLE_FIELDS`/`DIFFABLE_M2M`, rejects system-controlled fields, and returns a `frozenset` of valid exempt names. The cache is populated at startup via `SubmissionsConfig.ready()` so misconfigured entries are logged immediately.

Call `get_no_reset_fields.cache_clear()` in tests that override `settings.SUBMISSION_NO_RESET_FIELDS`.

### When status resets

On reset, both the web form and API:

1. Set `status = "submitted"`
2. Clear `primary_maturity_tag = None` and `secondary_maturity_tags = []`
3. Pass `status_reset=True` to `send_update_notification` so the submitter email includes a lifecycle notice

---
