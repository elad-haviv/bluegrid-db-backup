# bluegrid-db-backup

Nightly logical backup of the Blue Grid Postgres instance (volume
`bluegrid-pgdata`, Coolify app `blue-grid-api`) to Garage S3
(`https://s3.haviv.pro`, bucket `bluegrid-backups`).

## Architecture

Two pieces, both Coolify-managed, no host access needed:

1. **This service** (`bluegrid-db-backup`, Coolify app in project "Blue Grid
   API"): a small HTTP ingest + uploader. `POST /ingest` (Bearer token) stores
   the request body as `s3://<bucket>/postgres/bluegrid/YYYY-MM-DD.dump`,
   prunes to the newest 14 daily dumps, and appends a run line to
   `/data/logs/backup.log` (persisted volume). `GET /health` for liveness.
2. **Coolify Scheduled Task** on the `blue-grid-api` application, container
   `postgres`, nightly at **02:30 UTC**. The task runs *inside* the postgres
   container (which already holds its own credentials), so no DB password is
   ever copied anywhere:

   ```sh
   apk add -q curl; pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB" |
     curl -sf -X POST --data-binary @- \
       -H "Authorization: Bearer $BACKUP_INGEST_TOKEN" \
       -H "Content-Type: application/octet-stream" \
       http://<backup-container-host>:8000/ingest
   ```

## Configuration (env vars on the Coolify app)

| Var | Purpose |
|---|---|
| `S3_ENDPOINT` | `https://s3.haviv.pro` |
| `S3_REGION` | `garage` |
| `GARAGE_BUCKET` | Garage bucket id (`bluegrid-backups`) |
| `GARAGE_KEY_ID` / `GARAGE_SECRET` | Garage S3 key `bluegrid-backup` (scoped to the backups bucket only) |
| `BACKUP_INGEST_TOKEN` | Bearer token required by `/ingest` |
| `KEEP_DAILY` | Retention, default 14 daily dumps |
| `PG_DB_NAME` | DB name, used in the S3 key path |

Secrets live only in Coolify env vars (and the Garage key in Coolify); nothing
is hardcoded in the image.

## Run log

`/data/logs/backup.log` inside the container (persisted Coolify volume), one
line per run: `ISO8601 OK|FAIL key=... size=... dur=...s pruned=N`.

Manual trigger: Coolify dashboard -> blue-grid-api -> Scheduled Tasks ->
Execute Now on the nightly task, or:

```sh
curl -X POST --data-binary @dump.dump \
  -H "Authorization: Bearer $BACKUP_INGEST_TOKEN" \
  http://<backup-container-host>:8000/ingest
```

## Restore

```sh
aws --endpoint-url https://s3.haviv.pro s3 cp s3://<bucket>/postgres/bluegrid/2026-10-09.dump -
pg_restore -h <host> -U bluegrid -d bluegrid --clean --if-exists dump.dump
```
