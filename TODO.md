# Future Improvements

Ordered roughly by priority.

---

## Security

### Admin Two-Factor Authentication
Add TOTP-based 2FA for admin users via `django-otp` + `django-two-factor-auth`.

**Why:** The admin holds all service submission data including internal contact emails and IP logs. Password-only auth is a single point of failure.

**Approach:**
1. Add `django-otp` and `django-two-factor-auth` to `requirements/base.txt`
2. Add `django_otp` and `two_factor` to `INSTALLED_APPS`
3. Replace `path(f"{admin_prefix}/", admin.site.urls)` with the two-factor-aware admin URLs
4. Document the TOTP setup flow in `docs/admin-guide.md`

---

## User Experience

### API Key Recovery (Self-Service)
If a submitter loses their API key, they are currently locked out with no self-service option. An admin must manually issue a replacement key.

**Why:** Only the internal_contact_email is stored — if they lose the key there is no way to authenticate a recovery request other than manually.

**Approach:**
1. Add a `/update/recover/` view that accepts a service name + internal contact email
2. If the combination matches a submission, send a one-time recovery link to `internal_contact_email`
3. The recovery link (signed token, short TTL) issues a new API key and invalidates old ones
4. Document in `docs/user-guide.md`

---

## Code Quality

### Pre-Commit Hooks
Prevent unlinted or unformatted code from being committed.

**Why:** Currently ruff only runs in CI. A pre-commit hook catches issues before push.

**Setup:**
```bash
pip install pre-commit
```

`.pre-commit-config.yaml`:
```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.9.0
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
```

```bash
pre-commit install
```

Add `pre-commit` to `requirements/development.txt`.

---

## Testing

### Increase Coverage for views.py and admin.py
Current coverage: `views.py` ~72%, `admin.py` ~39%.

**Untested in views.py:**
- `validate_field` HTMX endpoint
- `update_success` view
- Rate limiting behaviour (`@ratelimit` decorators)
- `_get_client_ip` with `X-Real-IP` / `X-Forwarded-For` headers

**Untested in admin.py:**
- Status change actions
- Export CSV/JSON actions
- `key_management_panel` inline rendering

Target: bring both modules above 80%.

---

## Operations

### Automated Database Backup
No automated backup currently exists. PostgreSQL is externally managed but the application has no backup task.

**Why:** If the external DB host does not have its own backup policy configured, data loss is possible.

**Approach:**
1. Add a Celery beat task `backup_database` that runs `pg_dump` via subprocess and writes to a configurable path (e.g. a mounted volume or S3)
2. Add `BACKUP_PATH` and `BACKUP_RETENTION_DAYS` to `.env.example` and `docs/configuration.md`
3. Schedule daily via `CELERY_BEAT_SCHEDULE`

**Alternative:** Configure pg_basebackup or a dedicated backup tool (pgBackRest, Barman) at the infrastructure level — this is often preferable to application-level backups.
