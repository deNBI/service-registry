---
icon: material/monitor-dashboard
---

# Operations and Monitoring

Day-to-day running of a deployed registry: health checks, logs, the task queue, scheduled tasks and the admin role groups. Commands assume the Docker Compose stack; on the Ansible-managed production host run them in the corresponding containers.

## Health checks

- `GET /health/live/` — 200 if the process is running (no DB check)
- `GET /health/ready/` — 200 with `{"status": "ok"}` only if DB and Redis are reachable; 503 otherwise

Set up an alert if `/health/ready/` returns non-200 for more than 60 seconds
(for example Uptime Kuma, Healthchecks.io, or your institution's monitoring stack).

## Logs

With `DEBUG=false` logs are JSON lines on stdout (captured by Docker) with the fields
`asctime`, `levelname`, `name`, `message`, `request_id`; with `DEBUG=true` they are plain text. Every response also carries the request id in the `X-Request-ID` header, and API error bodies include it, so a user report can be matched to the log line.

View live logs:

```bash
make logs
# or
docker compose logs -f web
docker compose logs -f worker
```

## Celery task queue

Check task queue health:

```bash
docker compose exec worker celery -A config inspect active
docker compose exec worker celery -A config inspect stats

# Check that beat is sending its scheduled tasks
docker compose logs beat --tail 50

# Ping the worker directly (same check used by the Docker healthcheck)
docker compose exec worker celery -A config inspect ping
```

The `worker` container reports a Docker health status based on `celery inspect ping`. The `beat` container has no inspection API so its healthcheck is disabled — liveness is inferred from the process staying up.

Task results are stored in the database and can be viewed in the admin under **Celery Results**.

## Scheduled tasks

Celery beat runs these tasks. Beat keeps its last-run times in a schedule file in the `celery_beat` volume, so intervals are counted from the last recorded run:

| Task                               | Interval       | What it does                                                         |
| ---------------------------------- | -------------- | -------------------------------------------------------------------- |
| `biotools.sync_all`                | Every 24 hours | Syncs every submission with a bio.tools URL                          |
| `edam.sync`                        | Every 30 days  | Refreshes the EDAM ontology                                          |
| `licenses.sync`                    | Every 30 days  | Refreshes the SPDX licence list                                      |
| `submissions.cleanup_stale_drafts` | Every 24 hours | Deletes Django sessions that expired more than 24 hours ago          |

The sync tasks are described in [Reference Data and Ontology Sync](data-sync.md).

`cleanup_stale_drafts` only removes expired login and edit sessions from the database session table; despite its name it does not touch submissions, and the browser-side form drafts live in the submitter's browser (`localStorage`), not on the server. To run it once by hand:

```bash
docker compose exec web python manage.py shell -c \
  "from apps.submissions.tasks import cleanup_stale_drafts; print(cleanup_stale_drafts(), 'expired session(s) removed')"
```

## Admin role groups

The three admin groups (`Registry Viewer`, `Registry Editor`, `Registry Manager`, described in the [Admin Guide](../admin-guide.md#user-roles-permissions)) are created and updated by `manage.py setup_groups`. The `web` container entrypoint runs it after every `migrate`, so the groups always match the code by the time Gunicorn starts.

The command is **idempotent** — safe to run repeatedly. It brings each
group's permission set exactly in line with the specification, adding missing
permissions and removing any that are no longer in the spec. Existing
user → group memberships are never touched.

```bash
# Preview changes without writing to the database:
docker compose exec web python manage.py setup_groups --dry-run

# List each group and its current permissions:
docker compose exec web python manage.py setup_groups --list

# Force a sync without restarting the container (rarely needed):
docker compose exec web python manage.py setup_groups
```

## Runtime sizing

The compose files set: Gunicorn with 4 sync workers and a 60-second timeout (`docker-compose.prod.yml`), Celery worker concurrency 4 in production and 2 in development, and Redis with `maxmemory 256mb` and the `allkeys-lru` eviction policy (`docker-compose.yml`). Redis holds both the Celery queue and the cache.

## Staging and CI overlays

- `docker/docker-compose.staging.yml`: a production-like overlay (on top of `docker-compose.yml` and `docker-compose.prod.yml`) that reads `.env.staging` and writes e-mails to files in `/tmp/staging-emails` instead of sending them. The usage is in the file header.
- `docker/docker-compose.ci.yml`: runs the test suite, lint, `pip-audit` and type check in containers (`docker compose -f docker/docker-compose.ci.yml run --rm test`, `lint`, `audit`, `typecheck`).
- `docker/docker-compose.backup.yml`: scheduled database dumps, see [Backups](backups.md).

## First admin account

```bash
docker compose exec web python manage.py createsuperuser
```

Further staff accounts are created in the admin; see [Creating a staff user](../admin-guide.md#creating-a-staff-user-and-assigning-a-group).
