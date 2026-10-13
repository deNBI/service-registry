---
icon: material/language-javascript
---

# Frontend Templates, Widgets and Drafts

## Custom template tags

Custom template tags and filters live in
`apps/submissions/templatetags/registry_tags.py` and are loaded in templates with
`{% load registry_tags %}`.

### Available filters

#### `linkify_description`

Renders a section description string from `form_texts.yaml` as safe HTML.
Use this filter (not Django's built-in `urlize`) for all section description output.

| Input syntax                                 | Output                                        |
| -------------------------------------------- | --------------------------------------------- |
| `[link text](https://example.com)`           | `<a href="https://example.com">link text</a>` |
| `https://example.com`                        | auto-linked anchor                            |
| Blank line (`\n\n`)                          | paragraph break — wraps each block in `<p>`   |
| Single newline (`\n`, using YAML `\|` block) | `<br>`                                        |
| Raw `<html>`                                 | escaped — never rendered as markup            |

Only `http://` and `https://` schemes are accepted for links. `javascript:` and other
schemes in `[text](...)` syntax are not matched and pass through as escaped plain text.

**Template usage:**

```django
{% load registry_tags %}
<div class="section-description">{{ desc|linkify_description }}</div>
```

**Extending or testing the filter:**

The filter is unit-tested in `tests/test_template_tags.py` (`TestLinkifyDescriptionFilter`).
Add a new test there whenever you extend the filter's behaviour.
Integration tests that render `form_body.html` or `register.html` with patched YAML
data live in `tests/test_forms.py` (`TestSectionDescriptionsYAML`).

**Styling:**

`.section-description` is styled in `static/css/registry.css` as a light tinted callout
box — subtle `rgba(0,0,0,0.03)` background with rounded corners and muted text — to
visually distinguish it from form input labels and fields. If you change the style, keep
the distinction clear: the description is contextual guidance, not an actionable form
element.

---

### Available simple tags

| Tag                              | Purpose                                                               |
| -------------------------------- | --------------------------------------------------------------------- |
| `{% site_logo_url %}`            | Returns logo URL from `site.toml`, or empty string                    |
| `{% site_favicon_url %}`         | Returns favicon URL (auto-detects `static/img/favicon.*` as fallback) |
| `{% site_setting section key %}` | Generic accessor for any `site.toml` value                            |

---

## Form draft (localStorage)

The registration form (`/register/`) and the edit form (`/update/edit/<submission_id>/`) auto-save field values to the browser's `localStorage` so that a partially completed form survives a tab close or accidental navigation.

The API-key entry page (`/update/`) is deliberately **not** draft-enabled — it carries no draft key and does not load the draft script — so a typed API key is never written to `localStorage`. The key is also never a field on the register/edit forms, so it cannot enter a draft by that route either.

### Draft key scheme

| Form | Key pattern | Invalidated when |
|---|---|---|
| Register | `denbi_draft_register` | Form is submitted (cleared in `submit` event handler) |
| Edit | `denbi_draft_edit_{submission_uuid}_{updated_at_unix}` | Submission `updated_at` changes (any server-side save) **or** form is submitted |

The edit form embeds `updated_at` as a Unix timestamp in the key. Any server-side modification (admin edit, status change, API PATCH) advances `updated_at`, making the old draft key unreachable on the next page load — the user always sees the current DB values.

### Draft payload structure

```json
{
  "_savedAt": 1748000000000,
  "service_name": "MyTool",
  "service_description": "..."
}
```

`_savedAt` is a `Date.now()` millisecond timestamp written by `saveDraft()`. It is used exclusively for TTL enforcement and is not a form field name.

### Fields excluded from drafts (`SKIP_NAMES`)

| Field | Reason |
|---|---|
| `csrfmiddlewaretoken` | Security — must come from the server |
| `data_protection_consent` | Must be re-checked deliberately on every submission |
| `altcha` | Proof-of-work challenges expire; stale values are rejected server-side |
| `logo` | File inputs cannot be restored from localStorage |

### TTL and global purge

Every form load calls `purgeStaleDrafts()` before `restoreDraft()`. It:

1. Iterates all `localStorage` keys starting with `denbi_draft_`.
2. Removes any entry whose `_savedAt` is older than the configured TTL, or whose `_savedAt` is absent.
3. For the edit form specifically: also removes any `denbi_draft_edit_{uuid}_*` key that does not match the current draft key (old-version cleanup).

The TTL is configured in `site.toml` under `[submission] draft_ttl_days` (default 7). It is exposed to JavaScript as `data-draft-ttl-days` on the `<form>` element.

### Restoring a draft

`restoreDraft()` is called after `purgeStaleDrafts()`. It reads `DRAFT_KEY` from `localStorage`, verifies the TTL, then restores each field. A "Draft restored" banner with a **Clear draft** button is shown if any fields were actually restored. Server-side validation errors suppress draft restoration so the user sees the real error rather than a stale draft overwriting the submitted values.

### Testing

JavaScript logic is not covered by the Python test suite. Python tests cover:

- `FORM_DRAFT_TTL_DAYS` is present in the template context (`tests/test_context_processors.py`)
- `data-draft-ttl-days` attribute is rendered in both form pages (`tests/test_views.py`)

Manual browser verification: use DevTools → Application → Local Storage to inspect and manipulate `_savedAt` values.

---

## Frontend JavaScript widgets

`static/js/edam-autocomplete.js` is loaded on every page via `base.html`. It defines the EDAM picker (`buildEdamPicker`), the compact select widgets (`buildCompactSelect`, `buildCompactSelectSingle`) and the bio.tools prefill handling. The Tom Select combobox described below is initialised separately, in `templates/submissions/partials/form_scripts.html`.

### `buildEdamPicker(selectEl)`

Enhances any `<select class="edam-autocomplete">` into a pill-zone + search + dropdown
picker for EDAM ontology terms. Applied automatically to all matching elements on
`DOMContentLoaded`. Configure via data attributes on the `<select>`:

| Attribute          | Default                | Purpose                                         |
| ------------------ | ---------------------- | ----------------------------------------------- |
| `data-max-items`   | `6`                    | Maximum selectable terms                        |
| `data-placeholder` | `"Search EDAM terms…"` | Search input placeholder                        |
| `data-branch`      | `""`                   | EDAM branch filter (`topic`, `operation`, etc.) |

### `buildCompactSelect(selectEl, label)`

Enhances any `<select multiple data-compact-select="label">` into a searchable
checkbox list with selected pills shown at the top (matching the EDAM picker layout).

**To apply to a new field**, add the data attribute to the widget in `forms.py`:

```python
"my_field": forms.SelectMultiple(
    attrs={"class": "form-select", "data-compact-select": "items"}
),
```

No JS changes needed — the boot code auto-discovers all `[data-compact-select]` elements.

Currently used by: `responsible_pis` (`"PIs"`), `service_categories` (`"categories"`).

### Tom Select combobox (`data-affiliation-combobox`)

The `submitter_affiliation` and `host_institute` fields use Tom Select (vendored at
`static/js/tom-select.complete.min.js`) through `AffiliationComboboxWidget`. The
library is loaded and initialised in `templates/submissions/partials/form_scripts.html`,
which the registration and edit pages include. It provides a single-value searchable
combobox with `create: true` (free-text entry). To use it on another field, give the
field an `AffiliationComboboxWidget` (see the next section).

---

## Reusable form widgets

**Four reusable widget classes** in `apps/submissions/widgets.py` provide consistent UX across form fields:

| Widget                      | Use case                                                                       | Output                                                                                 |
| --------------------------- | ------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------- |
| `EdamAutocompleteWidget`    | EDAM term fields (`edam_topics`, `edam_operations`)                            | Searchable multi-select with pills, max 6 items                                        |
| `AffiliationComboboxWidget` | Institute/affiliation autocomplete (`submitter_affiliation`, `host_institute`) | Tom Select single-select combobox with free-text entry                                 |
| `CompactSelectWidget`       | Multi-select fields (`service_categories`, `responsible_pis`)                  | Searchable list with checkboxes, selected items shown as pills                         |
| `CompactSelectSingleWidget` | Single-select fields (`service_center`)                                        | Searchable list with **radio buttons** (not checkboxes), clarifies "pick one" to users |

**To add a new instance of these**, just use them in `forms.py`:

```python
"my_instit_field": AffiliationComboboxWidget(placeholder="e.g. Max-Planck-Institut"),
"my_multi_field": CompactSelectWidget(label="Items"),
"my_single_field": CompactSelectSingleWidget(label="Choice"),
```

No JS changes needed — widgets declare their Media (CSS/JS dependencies) and register data attributes that the boot code auto-discovers.

**Implementation details** — see `apps/submissions/widgets.py` docstrings for architectural rationale (progressive enhancement, accessibility, no CDN for vendored assets).
