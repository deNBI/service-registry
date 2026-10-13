---
icon: material/server
---

# Operator Guide Overview

For the people who deploy and run the registry: they have shell access to the host and edit `.env` and `config/site.toml`. Reviewing submissions and managing reference data in the Django admin is covered in the [Admin Guide](../admin-guide.md).

| Page                                                  | Covers                                                                         |
| ----------------------------------------------------- | ------------------------------------------------------------------------------ |
| [Deployment](../deployment.md)                        | Production setup, reverse proxy, uploads, updates, pre-launch checklist        |
| [Configuration](../configuration.md)                  | The `site.toml` and `.env` settings                                            |
| [E-mail and Notifications](email.md)                  | SMTP settings, who receives which e-mail, test environments                    |
| [Customising Texts](customising-texts.md)             | Form texts and e-mail texts (`form_texts.yaml`, `email_texts.yaml`)            |
| [Reference Data and Ontology Sync](data-sync.md)      | EDAM, SPDX and bio.tools sync from the command line, bulk import of reference data |
| [Operations and Monitoring](operations.md)            | Health checks, logs, Celery, scheduled tasks, admin role groups                |
| [Security Operations](security.md)                    | ALTCHA key, `SECRET_KEY` and database password rotation                        |
| [Backups](backups.md)                                 | Database and uploaded logos                                                    |
| [Releases and Rollback](../rollout.md)                | Building images, deploying, rolling back, the Markdown rollout                 |
| [Troubleshooting](troubleshooting.md)                 | Common problems and where to look                                              |
