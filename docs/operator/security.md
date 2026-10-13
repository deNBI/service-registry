---
icon: material/shield-key
---

# Security Operations

Secrets and keys an operator sets or rotates. All of them live in `.env` (managed by Ansible vault on the production host). Their reference entries are in [Configuration](../configuration.md#secrets-and-connection-strings); the pre-launch checklist is in [Deployment](../deployment.md#security-checklist-pre-launch).

## ALTCHA CAPTCHA

The registration and edit forms use [ALTCHA](https://altcha.org/), a proof-of-work CAPTCHA that runs on this server. The widget script is `static/js/altcha.min.js` and challenges come from `GET /captcha/`; no third-party service is contacted.

How it works:

1. The form shows the ALTCHA checkbox.
2. When the user ticks it, the browser fetches a signed challenge from `/captcha/` and solves a small SHA-256 puzzle.
3. The solution is posted with the form in the `altcha` field.
4. The server checks the signature and the expiry (10 minutes) before it accepts the form.

### Setting the HMAC key

`ALTCHA_HMAC_KEY` signs and verifies the challenges. When it is empty, the check is skipped and the widget is not shown, which is meant for local development only. Set it before the site is public.

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

```bash
ALTCHA_HMAC_KEY=<your-generated-key>
```

Restart the web service to apply: `docker compose restart web`

### Rotating the ALTCHA HMAC key

1. Generate a new key: `python -c "import secrets; print(secrets.token_hex(32))"`
2. Update `ALTCHA_HMAC_KEY` in `.env`.
3. Restart the web service: `docker compose restart web`

Challenges signed with the old key stop working. Users who opened the form before the rotation get a CAPTCHA error on submit and need to reload the page.

## Rotating the SECRET_KEY

1. Generate a new key: `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`
2. Update `SECRET_KEY` in `.env` (or your deployment environment / CI secret store).
3. Restart the web and worker services: `docker compose restart web worker`
4. All existing sessions end: admins log in again, and submitters re-enter their API key to edit.

## Rotating the database password

For the bundled `db` container (development and staging):

1. Update the PostgreSQL password: `docker compose exec db psql -U denbi -d postgres -c "ALTER USER denbi PASSWORD 'new-password';"`
2. Update `DB_PASSWORD` in `.env` to the new password.
3. Restart services: `docker compose restart web worker beat`

On production the database is an external PostgreSQL server: change the password there, then update `DB_PASSWORD` and restart the same services.

## API keys

Submission API keys and Admin API keys are stored as SHA-256 hashes; the plaintext is shown once when a key is created. Keys are created, reset and revoked by registry staff in the admin, see [Managing API Keys](../admin/api-keys.md).
