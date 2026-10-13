---
icon: material/format-text
---

# Customising Texts

The registration form texts and the e-mail texts live in two YAML files in the repository. Both are read when the application starts, so a change needs a restart; images built for production contain the files, so on production rebuild and redeploy the image.

## Form text and section descriptions

The registration form text is controlled entirely from a single YAML file:
`apps/submissions/form_texts.yaml`

The file has two parts:

### Section descriptions

Each of the seven form sections (A–G) can show an introductory paragraph at the
top of its card. Edit the `sections` block:

```yaml
sections:
  a:
    description: '' # leave empty to show no description
  b:
    description: 'Provide accurate information about your service.'
  # ... c through g
```

- Leave `description: ""` to hide the paragraph for that section.
- No raw HTML — use the supported syntax below instead.

#### Supported text formatting

**Named hyperlinks** — use `[link text](https://...)` to display a clickable word or phrase
instead of the full URL:

```yaml
e:
  description: >-
    KPI requirements depend on your service category. See the
    [de.NBI KPI Compass](https://www.denbi.de/images/Service/20210624_KPI_Cheat_Sheet_doi.pdf)
    for guidance.
```

**Bare URLs** — plain `https://` links are also automatically converted to clickable links:

```yaml
b:
  description: 'For examples see https://www.denbi.de/services'
```

**Multiple paragraphs** — use a YAML [literal block scalar](https://yaml.org/spec/1.2/spec.html#id2795688)
(`|`) and leave a blank line between paragraphs:

```yaml
f:
  description: |
    This section collects keywords and consent information.

    The information helps us improve visibility in search engines and outreach activities.
```

**Line breaks within a paragraph** — also use the `|` block style; each newline becomes a `<br>`:

```yaml
c:
  description: |
    Please name the PI responsible for this service.
    Use the associated partner option if your PI is not listed.
```

!!! note "Folded (`>-`) vs literal (`|`) block scalars"
    The `>-` style collapses line breaks into spaces — useful for long single-paragraph
    descriptions that you want to wrap neatly in the file. Use `|` when you need actual
    line breaks or blank-line paragraph splits to appear in the rendered output.

For the full technical details of the rendering filter, see
[`linkify_description`](../developer/frontend.md#linkify_description) in the
Developer Guide.

### Field help text and tooltips

Each field shows two types of guidance:

- **Help text** — a short hint shown below the input field.
- **Tooltip** — a detailed explanation shown when hovering or clicking the info icon.

```yaml
service_name:
  help: 'Official name of the service.'
  tooltip: 'Use the canonical name as it appears on your website.'
  locked_note: 'The service name cannot be changed after submission. Contact the registry team if it must be corrected.'
```

- `label` (optional) replaces the field's label; the shipped file uses it for a few fields.
- Set `help: ""` to hide the help text for a field.
- Set `tooltip: ""` to hide the info icon for a field.
- `locked_note` (currently used only for `service_name`) is appended to the field's help text on the **edit** form, where the field is locked. It is not shown on the registration form. Edit it to reword the lock explanation shown to submitters.
- The logo field text can include the `{logo_limits}` placeholder, which is replaced with the upload limits configured in [`[uploads]`](../configuration.md#uploads).

### Deploying form text changes

The file is read when the `web` process starts. In the development stack (repository mounted into the container) restart it:

```bash
docker compose restart web
```

On production the file is part of the image: rebuild and redeploy it (see [Releases and Rollback](../rollout.md)). No code changes, no migrations, no template edits required.

---

## Email notification texts

Email subject lines and status messages sent to submitters are controlled from a
single YAML file:

```
apps/submissions/email_texts.yaml
```

### Subjects

The `subjects` section defines the subject line for each email type.
Placeholders `{service_name}` and `{status}` are replaced automatically. The shipped file:

```yaml
subjects:
  created: "[de.NBI Service Registration Platform] New service submitted: {service_name}"
  status_changed: "[de.NBI Service Registration Platform] Status changed to '{status}': {service_name}"
  updated: "[de.NBI Service Registration Platform] Update: {service_name}"
  submitter_created: "Your service registration has been received: {service_name}"
  submitter_status: "Your service registration status: {status} — {service_name}"
  submitter_updated: "Your service registration has been updated: {service_name}"
```

| Key                 | Event                                                   | Placeholders                 |
| ------------------- | ------------------------------------------------------- | ---------------------------- |
| `created`           | New submission received (admin notification)            | `{service_name}`             |
| `status_changed`    | Status changed (admin notification)                     | `{service_name}`, `{status}` |
| `updated`           | Submitter edited their service (admin notification)     | `{service_name}`             |
| `submitter_created` | New submission received (submitter confirmation)        | `{service_name}`             |
| `submitter_status`  | Status changed (submitter notification)                 | `{service_name}`, `{status}` |
| `submitter_updated` | Submitter edited their service (submitter notification) | `{service_name}`             |

The subject prefix is part of each line here; the `[email] subject_prefix` key in `site.toml` is not used.

### Status messages

The `status_messages` section provides the body text included in the submitter
notification when the submission status changes (by an admin, or by the owner deprecating the service).
A `default` fallback is used for any status not explicitly listed:

```yaml
status_messages:
  approved: "Your service has been approved and is now registered …"
  rejected: "Your service registration was not approved at this time …"
  under_review: "Your submission is currently under review …"
  deprecated: "Your service has been marked as deprecated …"
  default: "If you have questions about your submission, please contact us."
```

Who receives each e-mail is described in [E-mail and Notifications](email.md).

### Deploying email text changes

E-mails are rendered by the Celery `worker`, which reads the file when it starts. In the development stack restart it:

```bash
docker compose restart worker
```

On production rebuild and redeploy the image, as for form text changes.

No code changes, no migrations, no template edits required.
