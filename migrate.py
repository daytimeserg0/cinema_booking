import hashlib
from pathlib import Path
import sys

import psycopg2

from db import get_db_connection

ROOT = Path(__file__).resolve().parent
MIGRATION_LOCK = 724981531


def run_migrations(connection=None):
    own_connection = connection is None
    conn = connection if connection is not None else get_db_connection()
    applied = []
    current_file = "schema.sql"
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK,))
                cur.execute((ROOT / "schema.sql").read_text(encoding="utf-8"))
                cur.execute(
                    """CREATE TABLE IF NOT EXISTS public.schema_migrations (
                        version text PRIMARY KEY,
                        checksum char(64) NOT NULL,
                        applied_at timestamptz NOT NULL DEFAULT now()
                    )"""
                )
                cur.execute("SELECT version, checksum FROM public.schema_migrations")
                recorded = dict(cur.fetchall())
                for path in sorted((ROOT / "migrations").glob("*.sql")):
                    current_file = path.name
                    content = path.read_text(encoding="utf-8").replace("\r\n", "\n")
                    checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
                    if path.name in recorded:
                        if recorded[path.name] != checksum:
                            raise RuntimeError(
                                f"Applied migration {path.name} has changed. "
                                "Restore its original contents and add a new migration."
                            )
                        continue
                    cur.execute(content)
                    cur.execute(
                        "INSERT INTO public.schema_migrations (version, checksum) VALUES (%s, %s)",
                        (path.name, checksum),
                    )
                    applied.append(path.name)
        return applied
    except psycopg2.Error as exc:
        detail = exc.diag.message_primary or "Database migration failed."
        raise RuntimeError(
            f"Migration {current_file} failed: {detail} All changes in this run were rolled back."
        ) from exc
    finally:
        if own_connection:
            conn.close()


def main():
    try:
        applied = run_migrations()
    except (psycopg2.Error, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Database ready. Applied migrations: {len(applied)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
