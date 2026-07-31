"""Regression coverage for persistent-database schema upgrades."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("psycopg")

from scripts.apply_migrations import apply_migrations


MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"
INITIAL_SCHEMA = (MIGRATIONS / "0001_initial_schema.sql").read_text(encoding="utf-8")


class _ConnectionContext:
    """Keep pytest-postgresql's shared connection open after migration runs."""

    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc_value, traceback):
        return False


def test_startup_migrator_upgrades_preexisting_initial_schema(postgresql_local):
    """A database initialized before FDR support receives 0002 at startup."""
    with postgresql_local.cursor() as cursor:
        cursor.execute(INITIAL_SCHEMA)
    postgresql_local.commit()

    connector = lambda _url: _ConnectionContext(postgresql_local)
    assert apply_migrations("postgresql://ignored", connect=connector) == [
        "0002_add_fixture_fdr.sql"
    ]
    assert apply_migrations("postgresql://ignored", connect=connector) == []

    columns = {
        row[0]
        for row in postgresql_local.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'fixtures'
            """
        ).fetchall()
    }
    assert {
        "finished",
        "team_h_fdr_fpl",
        "team_a_fdr_fpl",
        "team_h_fdr_override",
        "team_a_fdr_override",
    } <= columns
