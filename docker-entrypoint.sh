#!/bin/sh
set -e

# Wait for Postgres, then bring the schema up to date before serving.
# Migrations run here rather than at app startup so multiple web workers can't
# race each other applying the same revision.

echo "Waiting for the database…"
python - <<'PY'
import os, sys, time
import psycopg

url = os.environ.get("DATABASE_URL", "")
for prefix in ("postgresql+psycopg://", "postgres://"):
    if url.startswith(prefix):
        url = "postgresql://" + url[len(prefix):]
        break

deadline = time.time() + 60
while True:
    try:
        with psycopg.connect(url, connect_timeout=5):
            print("Database is up.")
            break
    except Exception as exc:
        if time.time() > deadline:
            print(f"Database never became reachable: {exc}", file=sys.stderr)
            sys.exit(1)
        time.sleep(2)
PY

echo "Applying migrations…"
alembic upgrade head

echo "Starting: $*"
exec "$@"
