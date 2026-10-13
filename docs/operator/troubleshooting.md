---
icon: material/wrench
---

# Troubleshooting

## Web service not starting

```bash
docker compose logs web
# Common causes: missing SECRET_KEY, DB_PASSWORD, or REDIS_PASSWORD in .env
# The startup error will name the missing variable explicitly
```

## Database authentication failure

```bash
# Show the database settings Django uses (password masked)
docker compose exec web python manage.py shell -c "
from django.conf import settings
d = settings.DATABASES['default'].copy()
d['PASSWORD'] = '***'
print(d)
"
# Production: DB_HOST points to the external PostgreSQL instance — verify DB_HOST,
# DB_PORT, DB_USER, and DB_PASSWORD in .env match the external server's credentials.
# Development: make sure DB_PASSWORD in .env matches the password the DB volume was
# initialised with. If unsure, wipe the volume and start fresh: make nuke
```

## Emails not sending

E-mails are sent by the `worker` container, so check that it is running and look at its log:

```bash
docker compose logs worker
docker compose exec worker python manage.py shell -c \
  "from django.core.mail import send_mail; send_mail('test','test',None,['to@example.com'])"
```

If `SUBMISSION_NOTIFY_OVERRIDE` is set, all admin e-mails go to that address and no submitter e-mail is sent (see [E-mail and Notifications](email.md#test-and-staging-environments)).

## Rate limit errors in testing

Once a client exceeds a limit, the web forms and the bio.tools and CAPTCHA endpoints answer with HTTP 403, and the REST API answers with HTTP 429. The description preview does not block; it shows "Too many previews" instead. Raise the limits in `.env` for a test environment:

```bash
RATE_LIMIT_SUBMIT=1000/h
RATE_LIMIT_UPDATE=1000/h
```

If every user is limited at once behind a reverse proxy, the proxy is probably not setting `X-Real-IP` (see [Configuration → Rate limiting](../configuration.md#rate-limiting)).

## A staff member is locked out of the admin

After 5 failed logins (`AXES_FAILURE_LIMIT`) the username and IP are locked for 30 minutes (`AXES_COOLOFF_MINUTES`). Wait for the cool-off, or clear the lockout:

```bash
docker compose exec web python manage.py axes_list_attempts
docker compose exec web python manage.py axes_reset_username <username>
```

## EDAM or licence pickers empty

```bash
docker compose exec web python manage.py sync_edam
docker compose exec web python manage.py sync_spdx_licenses
```

See [Reference Data and Ontology Sync](data-sync.md).

## Large logo uploads fail with HTTP 413

The reverse proxy refused the request before it reached the application. Raise `client_max_body_size` for the upload locations, see [Deployment → Request size for uploads](../deployment.md#request-size-for-uploads).

## CAPTCHA fails for everyone after a key change

Challenges signed with a previous `ALTCHA_HMAC_KEY` are rejected; users need to reload the form. See [Security Operations](security.md#rotating-the-altcha-hmac-key).

## API returns 401 or 403

- **401**: no key, an unknown key, a revoked key, or the wrong scheme (`ApiKey` instead of `AdminKey` or the other way round).
- **403**: the key is valid but not allowed to do this, for example a read-scope key sending `PATCH`, or a submission key calling an admin-only endpoint.

See [Using the API](../api-guide.md#errors) and the [Admin REST API](../admin/api.md).
