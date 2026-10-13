---
icon: material/backup-restore
---

# Backups

Two things hold data that cannot be recreated: the PostgreSQL database and the uploaded logos. Redis holds only transient Celery queue data and cache entries and does not need a backup.

## Database

With the bundled `db` container (development and staging):

```bash
# Backup
docker compose exec db pg_dump -U denbi denbi_registry > backup_$(date +%Y%m%d).sql

# Restore
docker compose exec -T db psql -U denbi denbi_registry < backup_20260306.sql
```

On production the database is an external PostgreSQL server; back it up with `pg_dump` against `DB_HOST`, or with the backup tooling of that server.

### Scheduled backups (`docker/docker-compose.backup.yml`)

The repository includes an optional overlay with a `backup` service that runs `pg_dump` against `DB_HOST` once every 24 hours (counted from when the container starts), writes gzipped dumps to `/opt/denbi-backups` on the host and keeps the last 7:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.prod.yml \
  -f docker/docker-compose.backup.yml \
  up -d
```

Adjust the host directory (`device:` under `backup_vol`) and the external network name in the file to match your host before using it.

## Uploaded logos

Logos are stored under `/app/mediafiles` in the `web` container. In `docker-compose.prod.yml` this is the named volume `media_data`; the production Ansible template can bind-mount a host path instead (see [Deployment](../deployment.md#persistent-storage-in-production)).

If using a bind mount (for example `/data/denbi-service-registry/media`), include the directory in your standard host backup. If using a named Docker volume, export it:

```bash
# Export named volume to a tar archive
docker run --rm \
  -v denbi_service_registry_media_data:/data \
  -v $(pwd):/backup \
  alpine tar czf /backup/media_backup_$(date +%Y%m%d).tar.gz -C /data .

# Restore
docker run --rm \
  -v denbi_service_registry_media_data:/data \
  -v $(pwd):/backup \
  alpine tar xzf /backup/media_backup_20260322.tar.gz -C /data
```

The volume name starts with the Compose project name (here `denbi_service_registry`); check it with `docker volume ls`.

Replaced logos are not deleted: when a logo is replaced, the previous file stays on disk. No cleanup task exists; remove unreferenced files by hand if disk space becomes a concern.
