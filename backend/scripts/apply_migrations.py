"""Apply pending PostgreSQL schema migrations before starting the API.

The Postgres Docker image runs ``docker-entrypoint-initdb.d`` only when its
data directory is empty.  This runner gives persistent production volumes the
same upgrade path and records completed migrations in ``schema_migrations``.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

DATABASE_URL_ENV = "DATABASE_URL"
MIGRATIONS_DIRECTORY = Path(__file__).resolve().parents[1] / "db" / "migrations"
BASELINE_MIGRATION = "0001_initial_schema.sql"
MIGRATION_LOCK = "fpl_xpts_schema_migrations"


def migration_paths() -> list[Path]:
    """Return versioned migrations in deterministic application order."""
    return sorted(MIGRATIONS_DIRECTORY.glob("[0-9][0-9][0-9][0-9]_*.sql"))


def apply_migrations(
    database_url: str | None = None,
    *,
    connect: Callable[[str], Any] | None = None,
) -> list[str]:
    """Apply each unapplied migration once and return its filenames.

    Existing deployments predate the migration ledger.  A ``fixtures`` table
    is the durable marker that the initial schema is already present, so it is
    baselined before later migrations are considered.
    """
    url = database_url or os.environ.get(DATABASE_URL_ENV)
    if not url:
        return []

    if connect is None:
        import psycopg

        connect = psycopg.connect

    applied_now: list[str] = []
    with connect(url) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (MIGRATION_LOCK,))
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    name text PRIMARY KEY,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
                """
            )
            cursor.execute("SELECT to_regclass('public.fixtures')")
            if cursor.fetchone()[0] is not None:
                cursor.execute(
                    """
                    INSERT INTO schema_migrations (name)
                    VALUES (%s)
                    ON CONFLICT (name) DO NOTHING
                    """,
                    (BASELINE_MIGRATION,),
                )

            cursor.execute("SELECT name FROM schema_migrations")
            applied = {row[0] for row in cursor.fetchall()}
            for path in migration_paths():
                if path.name in applied:
                    continue
                cursor.execute(path.read_text(encoding="utf-8"))
                cursor.execute(
                    "INSERT INTO schema_migrations (name) VALUES (%s)",
                    (path.name,),
                )
                applied_now.append(path.name)

        connection.commit()

    return applied_now


def main() -> None:
    applied = apply_migrations()
    if applied:
        print(f"Applied database migrations: {', '.join(applied)}")
    else:
        print("Database migrations already current or DATABASE_URL is unset.")


if __name__ == "__main__":
    main()
