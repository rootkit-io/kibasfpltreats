"""Shared test fixtures.

The local-PostgreSQL fixtures live here (not in test_postgres_repository)
because two suites need a real database: the repository adapter tests and
the public read-API tests (published_* views). Skips cleanly when psycopg /
pytest-postgresql / PG binaries are absent.
"""

from pathlib import Path

import pytest

# ------------------------------------------------------------ pg availability

try:
    import psycopg  # noqa: F401
    from pytest_postgresql import factories

    _PG_AVAILABLE = True
    postgresql_local_proc = factories.postgresql_proc()
    postgresql_local = factories.postgresql("postgresql_local_proc")
except Exception:  # pragma: no cover - environment-dependent
    _PG_AVAILABLE = False

    @pytest.fixture
    def postgresql_local():
        pytest.skip("psycopg / pytest-postgresql not available")


MIGRATION_SQL = (
    Path(__file__).resolve().parents[1] / "db" / "migrations" / "0001_initial_schema.sql"
).read_text(encoding="utf-8")


@pytest.fixture
def pg_repo(postgresql_local):
    """A PostgresProjectionRepository on a migrated throwaway database."""
    from fpl_xpts.postgres_repository import PostgresProjectionRepository

    with postgresql_local.cursor() as cur:
        cur.execute(MIGRATION_SQL)
    postgresql_local.commit()
    return PostgresProjectionRepository(connection=postgresql_local)
