"""
Container prestart: wait for Postgres, then bring the schema up to date.

Run as `python -m app.prestart` before uvicorn. Migrations happen here rather
than at app startup so multiple web workers can't race each other applying the
same revision. This lives inside `app/` rather than in a shell script because
some build hosts drop loose `.sh` files from the Docker build context.
"""
from __future__ import annotations

import sys
import time

import psycopg
from alembic import command
from alembic.config import Config

from app.config import settings


def wait_for_db(timeout: float = 60.0) -> None:
    # psycopg wants a plain libpq URL, not SQLAlchemy's `+psycopg` dialect form.
    url = settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    deadline = time.time() + timeout
    print("Waiting for the database…", flush=True)
    while True:
        try:
            with psycopg.connect(url, connect_timeout=5):
                print("Database is up.", flush=True)
                return
        except Exception as exc:  # noqa: BLE001 - any failure means "not yet"
            if time.time() > deadline:
                print(f"Database never became reachable: {exc}", file=sys.stderr)
                sys.exit(1)
            time.sleep(2)


def main() -> None:
    wait_for_db()
    print("Applying migrations…", flush=True)
    command.upgrade(Config("alembic.ini"), "head")


if __name__ == "__main__":
    main()
