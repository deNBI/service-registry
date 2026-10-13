---
icon: material/email-outline
---

# E-mail and Notifications

This is the single reference for who receives which e-mail. E-mails are sent asynchronously by the Celery `worker` container, so no e-mail goes out while the worker is down.

## SMTP settings

Set these in `.env`:

```bash
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.example.org
EMAIL_PORT=587
EMAIL_USE_TLS=true
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
EMAIL_FROM=no-reply@denbi.de    # Overrides [email] from_address in site.toml

# Optional CC address on every admin notification email (never added to submitter emails):
# SUBMISSION_NOTIFY_CC=admin@denbi.de

# Override ALL notification recipients for testing (sends every email here instead).
# When set, submitter-facing emails (status update, edit confirmation) are suppressed
# to prevent accidentally emailing real submitters from staging/test environments.
# SUBMISSION_NOTIFY_OVERRIDE=test-inbox@denbi.de
```

With `DEBUG=true` and no `EMAIL_BACKEND` set, e-mails are printed to the console instead of being sent.

## Who receives what

Admin e-mails go to the registry address (`[contact] email` in `site.toml`), with `SUBMISSION_NOTIFY_CC` in CC. Submitter e-mails go to the submission's internal contact address (`internal_contact_email`). The submitter is not CC'd on admin e-mails. Submitter messages are separate e-mails and do not contain the admin portal link.

| Event                                                                 | Admin e-mail                                                   | Submitter e-mail                                                    |
| --------------------------------------------------------------------- | -------------------------------------------------------------- | ------------------------------------------------------------------- |
| New registration (web form or `POST /api/v1/submissions/`)            | Full report with a link to the admin change view (the link is built from `[site] url` and left out when that is empty) | Receipt confirmation, no admin URL                                  |
| Submitter edit (web edit form or API `PATCH`)                         | Full report, **what changed** table (if anything changed) and admin link | Only if something changed: the same table, a security notice, and a lifecycle notice when the edit sent an approved service back to review |
| Status change by an admin (buttons or bulk actions) or by the owner deprecating the service | Status report                                  | Plain-language status message from `email_texts.yaml`               |
| Admin edits a submission's content in the Django admin                | None                                                           | None                                                                |
| Admin creates a submission in the Django admin                        | None                                                           | None                                                                |
| Admin issues, resets or revokes an API key                            | None                                                           | None                                                                |

## Test and staging environments

When `SUBMISSION_NOTIFY_OVERRIDE` is set:

- every admin e-mail goes to that address instead of the registry address (`SUBMISSION_NOTIFY_CC` still applies), and it carries no admin portal link;
- no submitter e-mail is sent at all (receipt, status message or edit confirmation).

## Changing the texts

Subject lines and the submitter status messages are edited in `apps/submissions/email_texts.yaml`; see [Customising Texts](customising-texts.md#email-notification-texts). The `[email] subject_prefix` key in `site.toml` is not used: the prefixes are part of the subject lines in `email_texts.yaml`.

## When e-mails do not arrive

See [Troubleshooting](troubleshooting.md#emails-not-sending).
