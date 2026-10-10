"""bluegrid-db-backup: HTTP ingest + Garage S3 uploader.

POST /ingest (Bearer token, application/octet-stream) stores the request body
as s3://<GARAGE_BUCKET>/postgres/<PG_DB_NAME>/YYYY-MM-DD.dump, prunes the
oldest dumps beyond KEEP_DAILY, and appends a run line to /data/logs/backup.log.
GET /health liveness endpoint. Designed to receive a pg_dump -Fc stream from a
Coolify Scheduled Task running inside the postgres container (see README.md).
"""
import datetime as dt
import logging
import os
import threading
import time

import boto3
from botocore.config import Config
from flask import Flask, request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backup")

app = Flask(__name__)

S3_ENDPOINT = os.environ["S3_ENDPOINT"].rstrip("/")
S3_REGION = os.environ.get("S3_REGION", "garage")
GARAGE_BUCKET = os.environ["GARAGE_BUCKET"]
GARAGE_KEY_ID = os.environ["GARAGE_KEY_ID"]
GARAGE_SECRET = os.environ["GARAGE_SECRET"]
INGEST_TOKEN = os.environ["BACKUP_INGEST_TOKEN"]
PG_DB_NAME = os.environ.get("PG_DB_NAME", "bluegrid")
KEEP_DAILY = int(os.environ.get("KEEP_DAILY", "14"))
LOG_PATH = os.environ.get("BACKUP_LOG_PATH", "/data/logs/backup.log")

s3 = boto3.client(
    "s3",
    endpoint_url=S3_ENDPOINT,
    aws_access_key_id=GARAGE_KEY_ID,
    aws_secret_access_key=GARAGE_SECRET,
    region_name=S3_REGION,
    config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 3}),
)

_lock = threading.Lock()


def _prefix() -> str:
    return f"postgres/{PG_DB_NAME}/"


def _today_key() -> str:
    return f"{_prefix()}{dt.datetime.now(dt.timezone.utc):%Y-%m-%d}.dump"


def _prune_locked() -> int:
    """Keep the newest KEEP_DAILY dumps; delete older objects. Returns deleted count."""
    resp = s3.list_objects_v2(Bucket=GARAGE_BUCKET, Prefix=_prefix())
    objs = sorted(resp.get("Contents", []), key=lambda o: o["LastModified"], reverse=True)
    deleted = 0
    for old in objs[KEEP_DAILY:]:
        s3.delete_object(Bucket=GARAGE_BUCKET, Key=old["Key"])
        deleted += 1
        log.info("pruned %s", old["Key"])
    return deleted


def _append_log(line: str) -> None:
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
    except OSError as e:
        log.warning("could not append run log: %s", e)


@app.get("/health")
def health():
    return {"ok": True, "db": PG_DB_NAME}


@app.post("/ingest")
def ingest():
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {INGEST_TOKEN}":
        return {"error": "unauthorized"}, 401
    body = request.get_data()
    if not body:
        return {"error": "empty body"}, 400
    key = _today_key()
    started = time.monotonic()
    try:
        s3.put_object(
            Bucket=GARAGE_BUCKET,
            Key=key,
            Body=body,
            ContentType="application/octet-stream",
        )
    except Exception as e:  # noqa: BLE001 — log and report, run line records failure
        line = f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ} FAIL size={len(body)} err={e}"
        log.error(line)
        with _lock:
            _append_log(line)
        return {"error": "upload failed"}, 502
    duration = time.monotonic() - started
    with _lock:
        deleted = _prune_locked()
        _append_log(
            f"{dt.datetime.now(dt.timezone.utc):%Y-%m-%dT%H:%M:%SZ} OK key={key} "
            f"size={len(body)} duration={duration:.1f}s pruned={deleted}"
        )
    log.info("stored %s (%d bytes, %.1fs, pruned %d)", key, len(body), duration, deleted)
    return {"ok": True, "key": key, "size": len(body), "pruned": deleted}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)