"""Phase 7: persistence tests.

Two layers, one contract:

- the protocol **contract tests** run against BOTH ``FakeProjectionRepository``
  (always, in-memory, fast) and ``PostgresProjectionRepository`` (when a local
  PostgreSQL is available via pytest-postgresql). The fake being tested
  against the same assertions as the real adapter is what makes it safe to
  use in API tests;
- the **Postgres-only tests** prove what the fake can't: real upserts,
  transactional rollback, and the published_* views resolving the latest
  published run (including the DGW fixture-grain view).

Skips cleanly when psycopg / pytest-postgresql / PG binaries are absent.
"""

import pandas as pd
import pytest

from fpl_xpts.projection_repository import ProjectionRepository, RunStatus
from tests.fakes import (
    SEASON,
    FakeProjectionRepository,
    make_metadata,
    make_sample_results,
)

# The postgres fixtures (postgresql_local, pg_repo) live in tests/conftest.py
# -- shared with the public read-API tests.


@pytest.fixture(params=["fake", "postgres"])
def repo(request):
    """Every contract test runs against both implementations."""
    if request.param == "fake":
        return FakeProjectionRepository()
    return request.getfixturevalue("pg_repo")


def _count(conn, table: str) -> int:
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


# ----------------------------------------------------- protocol contract


def test_implementations_satisfy_protocol():
    assert isinstance(FakeProjectionRepository(), ProjectionRepository)
    from fpl_xpts.postgres_repository import PostgresProjectionRepository

    assert isinstance(
        PostgresProjectionRepository(conninfo="postgresql://unused"),
        ProjectionRepository,
    )


def test_save_returns_draft_and_metadata_round_trips(repo):
    run_id = repo.save_run(make_sample_results(), make_metadata(notes="weekly run"))
    record = repo.get_run(run_id)

    assert record is not None
    assert record.status is RunStatus.DRAFT
    assert record.published_at is None
    assert record.metadata.season == SEASON
    assert record.metadata.n_sim == 1000
    assert record.metadata.manual_minutes_layers == 1
    assert dict(record.metadata.inputs)["override_count"] == 1
    assert record.metadata.notes == "weekly run"


def test_publish_marks_run_and_latest_published_resolves(repo):
    run_id = repo.save_run(make_sample_results(), make_metadata())
    assert repo.latest_published_run() is None

    repo.publish_run(run_id)
    record = repo.get_run(run_id)
    assert record.status is RunStatus.PUBLISHED
    assert record.published_at is not None
    assert repo.latest_published_run().run_id == run_id


def test_latest_published_wins_and_rollback_by_republish(repo):
    first = repo.save_run(make_sample_results(), make_metadata())
    second = repo.save_run(make_sample_results(), make_metadata())

    repo.publish_run(first)
    repo.publish_run(second)
    assert repo.latest_published_run().run_id == second

    repo.publish_run(first)  # rollback = republish the earlier run
    assert repo.latest_published_run().run_id == first


def test_archived_runs_cannot_be_published(repo):
    run_id = repo.save_run(make_sample_results(), make_metadata())
    repo.archive_run(run_id)
    assert repo.get_run(run_id).status is RunStatus.ARCHIVED
    assert repo.get_run(run_id).published_at is None
    with pytest.raises(ValueError):
        repo.publish_run(run_id)


def test_unknown_run_raises_keyerror(repo):
    with pytest.raises(KeyError):
        repo.publish_run("00000000-0000-0000-0000-000000000000")
    with pytest.raises(KeyError):
        repo.archive_run("00000000-0000-0000-0000-000000000000")
    assert repo.get_run("00000000-0000-0000-0000-000000000000") is None


def test_list_runs_most_recent_first_with_limit(repo):
    ids = [repo.save_run(make_sample_results(), make_metadata()) for _ in range(3)]
    listed = repo.list_runs(limit=2)
    assert [record.run_id for record in listed] == [ids[2], ids[1]]


def test_load_run_tables_round_trips_preview_frames(repo):
    """Phase 10: what save_run persisted, load_run_tables re-hydrates --
    same results keys, same column names, same values (both adapters)."""
    run_id = repo.save_run(make_sample_results(), make_metadata())

    tables = repo.load_run_tables(run_id)
    assert tables is not None
    for key in ("weekly", "player_fixture", "monte_carlo", "fixtures_forecast"):
        assert key in tables, key

    weekly = tables["weekly"].sort_values("player_id").reset_index(drop=True)
    assert len(weekly) == 2
    assert list(weekly["player_id"]) == [101, 202]
    assert list(weekly["web_name"]) == ["Haaland", "Saka"]
    assert weekly.loc[0, "xPts"] == pytest.approx(10.0)
    assert weekly.loc[1, "P_haul"] == pytest.approx(0.12)

    fixture = tables["player_fixture"].sort_values(
        ["player_id", "fixture"]
    ).reset_index(drop=True)
    assert len(fixture) == 3
    assert list(fixture["fixture"]) == [10, 12, 11]
    assert fixture.loc[0, "xPts"] == pytest.approx(5.1)
    assert fixture.loc[0, "expected_minutes"] == pytest.approx(80.0)

    mc = tables["monte_carlo"].sort_values("player_id").reset_index(drop=True)
    assert len(mc) == 2
    assert mc.loc[0, "MC_MeanPts"] == pytest.approx(9.8)
    assert mc.loc[1, "Bracket_15_plus"] == pytest.approx(0.05)


def test_load_run_tables_unknown_run_returns_none(repo):
    assert repo.load_run_tables("00000000-0000-0000-0000-000000000000") is None


# ------------------------------------------------------- postgres-only


def test_save_persists_every_table(pg_repo, postgresql_local):
    pg_repo.save_run(make_sample_results(), make_metadata())
    expected = {
        "projection_runs": 1,
        "gameweeks": 1,
        "teams": 2,
        "players": 2,
        "fixtures": 3,
        "fixture_forecasts": 3,
        "player_fixture_projections": 3,
        "player_gameweek_projections": 2,
        "player_gameweek_simulations": 2,
    }
    for table, count in expected.items():
        assert _count(postgresql_local, table) == count, table


def test_dimension_upserts_are_idempotent_and_refresh(pg_repo, postgresql_local):
    pg_repo.save_run(make_sample_results(), make_metadata())
    pg_repo.save_run(
        make_sample_results(web_names={101: "HAALAND-RENAMED", 202: "Saka"}),
        make_metadata(),
    )

    assert _count(postgresql_local, "projection_runs") == 2
    assert _count(postgresql_local, "players") == 2  # upserted, not duplicated
    assert _count(postgresql_local, "teams") == 2
    assert _count(postgresql_local, "fixtures") == 3

    name = postgresql_local.execute(
        "SELECT web_name FROM players WHERE season = %s AND id = 101", (SEASON,)
    ).fetchone()[0]
    assert name == "HAALAND-RENAMED"  # second save refreshed the dimension


def test_save_is_atomic_rolls_back_everything(pg_repo, postgresql_local):
    results = make_sample_results()
    bad_row = results["weekly"].iloc[[0]].assign(player_id=999)  # FK violation
    results["weekly"] = pd.concat([results["weekly"], bad_row], ignore_index=True)

    with pytest.raises(Exception):
        pg_repo.save_run(results, make_metadata())

    for table in [
        "projection_runs",
        "player_gameweek_projections",
        "player_fixture_projections",
        "fixture_forecasts",
        "player_gameweek_simulations",
    ]:
        assert _count(postgresql_local, table) == 0, table


def test_published_views_serve_latest_published_run(pg_repo, postgresql_local):
    first = pg_repo.save_run(make_sample_results(), make_metadata())
    second_results = make_sample_results()
    second_results["weekly"] = second_results["weekly"].assign(
        xPts=lambda f: f["xPts"] + 100.0
    )
    second = pg_repo.save_run(second_results, make_metadata())

    assert postgresql_local.execute(
        "SELECT count(*) FROM published_player_week"
    ).fetchone()[0] == 0  # nothing published yet

    pg_repo.publish_run(first)
    pg_repo.publish_run(second)
    run_ids = {
        str(row[0])
        for row in postgresql_local.execute(
            "SELECT DISTINCT run_id FROM published_player_week"
        ).fetchall()
    }
    assert run_ids == {second}
    top = postgresql_local.execute(
        "SELECT web_name, xpts FROM published_player_week ORDER BY xpts DESC LIMIT 1"
    ).fetchone()
    assert top[0] == "Haaland"
    assert top[1] == pytest.approx(110.0)

    pg_repo.publish_run(first)  # rollback-by-republish flips the views back
    run_ids = {
        str(row[0])
        for row in postgresql_local.execute(
            "SELECT DISTINCT run_id FROM published_player_week"
        ).fetchall()
    }
    assert run_ids == {first}


def test_weekly_view_carries_denormalized_player_state(pg_repo, postgresql_local):
    run_id = pg_repo.save_run(make_sample_results(), make_metadata())
    pg_repo.publish_run(run_id)
    row = postgresql_local.execute(
        """
        SELECT team_name, price, fpl_status, selected_by_pct
        FROM published_player_week WHERE player_id = 101
        """
    ).fetchone()
    assert row[0] == "Manchester City"
    assert row[1] == pytest.approx(14.1)  # now_cost 141 -> price 14.1
    assert row[2] == "a"
    assert row[3] == pytest.approx(55.3)


def test_dgw_breakdown_view_returns_fixture_grain(pg_repo, postgresql_local):
    run_id = pg_repo.save_run(make_sample_results(), make_metadata())
    pg_repo.publish_run(run_id)
    rows = postgresql_local.execute(
        """
        SELECT fixture_id, team_short, opponent_short, kickoff_time
        FROM published_fixture_projections
        WHERE player_id = 101
        ORDER BY kickoff_time
        """
    ).fetchall()
    assert [row[0] for row in rows] == [10, 12]  # both DGW fixtures, in order
    assert rows[0][1] == "MCI"
    assert rows[0][2] == "ARS"


def test_simulations_view_serves_published_mc(pg_repo, postgresql_local):
    run_id = pg_repo.save_run(make_sample_results(), make_metadata())
    pg_repo.publish_run(run_id)
    row = postgresql_local.execute(
        """
        SELECT web_name, mean_pts, bracket_15_plus, n_sim
        FROM published_player_week_simulations WHERE player_id = 101
        """
    ).fetchone()
    assert row[0] == "Haaland"
    assert row[1] == pytest.approx(9.8)
    assert row[2] == pytest.approx(0.05)
    assert row[3] == 1000
