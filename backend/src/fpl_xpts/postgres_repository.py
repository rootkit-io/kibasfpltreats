"""PostgreSQL implementation of the ProjectionRepository protocol (Phase 7).

Driver: psycopg 3 (install the ``db`` extra). Deliberately no ORM -- the run
output is already flat DataFrames, so the right tools are:

- ``executemany`` with ``ON CONFLICT`` upserts for the (small) dimensions;
- ``COPY FROM STDIN`` for the (large) fact tables -- pure inserts keyed by a
  fresh run id, so no conflict handling is needed and COPY is the fastest
  path Postgres offers.

Everything in ``save_run`` happens inside one transaction: any failure rolls
back the run header, dimension upserts, and all facts together.

Schema: ``db/migrations/0001_initial_schema.sql``. All dimensions are keyed
by ``(season, fpl_id)``; the season comes from ``RunMetadata.season``.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Mapping, Sequence

import pandas as pd

from .projection_repository import (
    RunId,
    RunMetadata,
    RunRecord,
    RunSource,
    RunStatus,
)

# ---------------------------------------------------------------------------
# Column maps: db_column -> results-frame column. Missing frame columns are
# persisted as NULL (the schema is the contract; frames may omit optionals
# like ml_xpts or prior_source).
# ---------------------------------------------------------------------------

FIXTURE_FORECAST_MAP = {
    "fixture_id": "id",
    "gameweek_id": "event",
    "home_goals_lambda": "home_xg",
    "away_goals_lambda": "away_xg",
    "home_cs_prob": "home_cs_prob",
    "away_cs_prob": "away_cs_prob",
    "projection_source": "projection_source",
}

PLAYER_FIXTURE_MAP = {
    "player_id": "player_id",
    "fixture_id": "fixture",
    "gameweek_id": "event",
    "team_id": "team",
    "opponent_id": "opponent",
    "was_home": "was_home",
    "expected_minutes": "expected_minutes",
    "likely_minutes": "likely_minutes",
    "start_probability": "start_probability",
    "play_probability": "play_probability",
    "minutes_source": "minutes_model_source",
    "xg": "xG",
    "xa": "xA",
    "xga_expected": "xGA_exp",
    "cs_prob": "cs_prob",
    "p1_ga": "P1_GA",
    "xpts": "xPts",
    "appearance_pts": "AppPts",
    "goal_pts": "GoalPts",
    "assist_pts": "AssistPts",
    "cs_pts": "CSPts",
    "save_pts": "SavePts",
    "defcon_pts": "DefconPts",
    "card_pts": "CardPts",
    "pen_miss_pts": "PenMissPts",
    "concede_pts": "ConcedePts",
    "prior_based": "prior_based",
    "prior_source": "prior_source",
}

PLAYER_GAMEWEEK_MAP = {
    "gameweek_id": "event",
    "player_id": "player_id",
    "team_id": "team",
    "position": "position",
    "now_cost": "now_cost",
    "selected_by_pct": "selected_by_percent",
    "fpl_status": "status",
    "chance_of_playing": "chance_of_playing_this_round",
    "news": "news",
    "prior_based": "prior_based",
    "prior_source": "prior_source",
    "fixtures_in_week": "fixtures",
    "expected_minutes": "expected_minutes",
    "start_probability": "start_probability",
    "play_probability": "play_probability",
    "minutes_source": "minutes_model_source",
    "xg": "xG",
    "xa": "xA",
    "xga_expected": "xGA_exp",
    "xpts": "xPts",
    "ml_xpts": "ml_xpts",
    "p1_ga": "P1_GA",
    "p_return": "P_return",
    "p_haul": "P_haul",
    "appearance_pts": "AppPts",
    "goal_pts": "GoalPts",
    "assist_pts": "AssistPts",
    "cs_pts": "CSPts",
    "save_pts": "SavePts",
    "defcon_pts": "DefconPts",
    "card_pts": "CardPts",
    "pen_miss_pts": "PenMissPts",
    "concede_pts": "ConcedePts",
}

SIMULATION_MAP = {
    "gameweek_id": "event",
    "player_id": "player_id",
    "mean_pts": "MC_MeanPts",
    "std_pts": "MC_StdPts",
    "min_pts": "MC_MinPts",
    "max_pts": "MC_MaxPts",
    "floor_p10": "MC_Floor",
    "p25": "MC_P25",
    "p75": "MC_P75",
    "upside_p90": "MC_Upside",
    "p1_return": "MC_P1_Return",
    "p2_return": "MC_P2_Return",
    "p_return": "P_return",
    "p_haul": "P_haul",
    "bracket_le_2": "Bracket_LE_2",
    "bracket_3_6": "Bracket_3_to_6",
    "bracket_7_9": "Bracket_7_to_9",
    "bracket_10_14": "Bracket_10_to_14",
    "bracket_15_plus": "Bracket_15_plus",
}

#: Read-path specs for ``load_run_tables``: results key -> (fact table,
#: write-path column map). The maps are reversed on read so frames come back
#: with their original column names.
_RUN_TABLE_SPECS = (
    ("weekly", "player_gameweek_projections", PLAYER_GAMEWEEK_MAP),
    ("player_fixture", "player_fixture_projections", PLAYER_FIXTURE_MAP),
    ("monte_carlo", "player_gameweek_simulations", SIMULATION_MAP),
    ("fixtures_forecast", "fixture_forecasts", FIXTURE_FORECAST_MAP),
)

#: player_fixture columns folded into the weekly grain (first per player-gw)
#: because the weekly frame does not carry them itself.
_WEEKLY_FROM_FIXTURE = [
    "start_probability",
    "play_probability",
    "minutes_model_source",
    "prior_based",
    "prior_source",
]

#: bootstrap player-state columns denormalized into the weekly grain.
_WEEKLY_FROM_PLAYERS = [
    "now_cost",
    "selected_by_percent",
    "status",
    "chance_of_playing_this_round",
    "news",
]


def _py(value: Any) -> Any:
    """Convert a pandas/numpy cell to a psycopg-friendly Python scalar."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if hasattr(value, "item"):  # numpy scalar
        return value.item()
    return value


#: db columns that must arrive as integers. Pandas upcasts mixed rows and
#: NaN-bearing merges to float64 ("1.0" is not a valid smallint in COPY), so
#: integral floats are coerced back for these columns.
_INT_COLUMNS = {
    "gameweek_id",
    "player_id",
    "fixture_id",
    "team_id",
    "opponent_id",
    "now_cost",
    "chance_of_playing",
    "fixtures_in_week",
    "n_sim",
}


def _cell(db_col: str, value: Any) -> Any:
    value = _py(value)
    if value is None:
        return None
    if db_col in _INT_COLUMNS and isinstance(value, float):
        return int(value) if value.is_integer() else value
    return value


def _copy_rows(
    frame: pd.DataFrame,
    mapping: Mapping[str, str],
    extra: Mapping[str, Any],
) -> Iterator[tuple]:
    # Column-wise extraction preserves per-column dtypes (iterrows would
    # upcast whole mixed-numeric rows to float64).
    columns: list[list[Any]] = []
    for db_col, frame_col in mapping.items():
        if frame_col in frame.columns:
            columns.append([_cell(db_col, v) for v in frame[frame_col].tolist()])
        else:
            columns.append([None] * len(frame))
    extra_values = tuple(extra.values())
    for i in range(len(frame)):
        yield extra_values + tuple(column[i] for column in columns)


class PostgresProjectionRepository:
    """ProjectionRepository backed by PostgreSQL via psycopg 3.

    Pass either a libpq ``conninfo`` string (a connection is opened per
    operation) or an existing ``connection`` (used as-is, never closed --
    the test harness route).
    """

    def __init__(self, conninfo: str | None = None, connection: Any | None = None):
        if (conninfo is None) == (connection is None):
            raise ValueError("pass exactly one of conninfo or connection")
        self._conninfo = conninfo
        self._connection = connection

    # ---------------------------------------------------------- plumbing

    @contextmanager
    def _txn(self):
        if self._connection is not None:
            with self._connection.transaction():
                yield self._connection
        else:
            import psycopg

            with psycopg.connect(self._conninfo) as conn:
                with conn.transaction():
                    yield conn

    @contextmanager
    def _read(self):
        if self._connection is not None:
            yield self._connection
        else:
            import psycopg

            with psycopg.connect(self._conninfo) as conn:
                yield conn

    # ---------------------------------------------------------- protocol

    def save_run(
        self,
        results: Mapping[str, pd.DataFrame],
        metadata: RunMetadata,
    ) -> RunId:
        from psycopg.types.json import Jsonb

        run_id = str(uuid.uuid4())
        season = metadata.season

        with self._txn() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO projection_runs
                    (id, season, source, gw_start, gw_end, n_sim, include_mc,
                     minutes_model_loaded, manual_minutes_layers,
                     override_count, inputs, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    run_id,
                    season,
                    metadata.source.value,
                    metadata.gw_start,
                    metadata.gw_end,
                    metadata.n_sim,
                    metadata.include_mc,
                    metadata.minutes_model_loaded,
                    metadata.manual_minutes_layers,
                    metadata.override_count,
                    Jsonb(dict(metadata.inputs)),
                    metadata.notes,
                ),
            )

            self._upsert_gameweeks(cur, season, results.get("events"))
            self._upsert_teams(cur, season, results.get("teams"))
            self._upsert_players(cur, season, results.get("players"))
            self._upsert_fixtures(cur, season, results.get("fixtures_forecast"))

            extra = {"run_id": run_id, "season": season}
            self._copy_facts(
                cur,
                "fixture_forecasts",
                FIXTURE_FORECAST_MAP,
                self._dedupe(results.get("fixtures_forecast"), ["id"]),
                extra,
            )
            self._copy_facts(
                cur,
                "player_fixture_projections",
                PLAYER_FIXTURE_MAP,
                results.get("player_fixture"),
                extra,
            )
            self._copy_facts(
                cur,
                "player_gameweek_projections",
                PLAYER_GAMEWEEK_MAP,
                self._enriched_weekly(results),
                extra,
            )
            monte_carlo = results.get("monte_carlo")
            if monte_carlo is not None and not monte_carlo.empty:
                self._copy_facts(
                    cur,
                    "player_gameweek_simulations",
                    SIMULATION_MAP,
                    monte_carlo,
                    {**extra, "n_sim": metadata.n_sim},
                )
        return RunId(run_id)

    def publish_run(self, run_id: RunId) -> None:
        with self._txn() as conn:
            status = self._status(conn, run_id)
            if status == RunStatus.ARCHIVED.value:
                raise ValueError(f"run {run_id} is archived and cannot be published")
            conn.execute(
                """
                UPDATE projection_runs
                SET status = 'published', published_at = clock_timestamp()
                WHERE id = %s
                """,
                (run_id,),
            )

    def archive_run(self, run_id: RunId) -> None:
        with self._txn() as conn:
            self._status(conn, run_id)  # KeyError if missing
            conn.execute(
                """
                UPDATE projection_runs
                SET status = 'archived', published_at = NULL
                WHERE id = %s
                """,
                (run_id,),
            )

    def get_run(self, run_id: RunId) -> RunRecord | None:
        with self._read() as conn:
            row = conn.execute(
                "SELECT * FROM projection_runs WHERE id = %s", (run_id,)
            ).fetchone()
        return self._record(conn, row) if row else None

    def latest_published_run(self) -> RunRecord | None:
        with self._read() as conn:
            row = conn.execute("SELECT * FROM current_published_run").fetchone()
            return self._record(conn, row) if row else None

    def list_runs(self, limit: int = 20) -> Sequence[RunRecord]:
        with self._read() as conn:
            rows = conn.execute(
                "SELECT * FROM projection_runs ORDER BY created_at DESC LIMIT %s",
                (limit,),
            ).fetchall()
            return [self._record(conn, row) for row in rows]

    def load_run_tables(self, run_id: RunId) -> Mapping[str, pd.DataFrame] | None:
        """Reverse of ``save_run``'s fact writes: facts back to result frames.

        Each fact table is read through the same column map used on the write
        path (db column -> frame column), so the frames round-trip with their
        original names and the preview grids re-hydrate unchanged. ``weekly``
        additionally re-joins ``players.web_name`` (a dimension attribute the
        fact row does not carry).
        """
        with self._read() as conn:
            exists = conn.execute(
                "SELECT 1 FROM projection_runs WHERE id = %s", (run_id,)
            ).fetchone()
            if exists is None:
                return None

            tables: dict[str, pd.DataFrame] = {}
            for name, table, mapping in _RUN_TABLE_SPECS:
                db_cols = ", ".join(f"f.{col}" for col in mapping)
                frame_cols = list(mapping.values())
                if name == "weekly":
                    sql = f"""
                        SELECT {db_cols}, p.web_name
                        FROM {table} f
                        LEFT JOIN players p
                            ON p.season = f.season AND p.id = f.player_id
                        WHERE f.run_id = %s
                        ORDER BY f.gameweek_id, f.player_id
                    """
                    frame_cols = frame_cols + ["web_name"]
                else:
                    order = (
                        "f.gameweek_id, f.player_id"
                        if "player_id" in mapping
                        else "f.gameweek_id, f.fixture_id"
                    )
                    sql = f"""
                        SELECT {db_cols}
                        FROM {table} f
                        WHERE f.run_id = %s
                        ORDER BY {order}
                    """
                rows = conn.execute(sql, (run_id,)).fetchall()
                tables[name] = pd.DataFrame(rows, columns=frame_cols)
            return tables

    # ----------------------------------------------------------- internals

    @staticmethod
    def _dedupe(frame: pd.DataFrame | None, keys: list[str]) -> pd.DataFrame | None:
        if frame is None or frame.empty:
            return frame
        return frame.drop_duplicates(subset=[k for k in keys if k in frame.columns])

    @staticmethod
    def _enriched_weekly(results: Mapping[str, pd.DataFrame]) -> pd.DataFrame | None:
        """weekly + player-state block + first-per-gw minutes/prior columns."""
        weekly = results.get("weekly")
        if weekly is None or weekly.empty:
            return weekly
        out = weekly.copy()

        players = results.get("players")
        if players is not None and not players.empty and "id" in players.columns:
            cols = [c for c in _WEEKLY_FROM_PLAYERS if c in players.columns]
            if cols:
                out = out.merge(
                    players[["id"] + cols].rename(columns={"id": "player_id"}),
                    on="player_id",
                    how="left",
                    suffixes=("", "_players"),
                )

        player_fixture = results.get("player_fixture")
        if player_fixture is not None and not player_fixture.empty:
            cols = [c for c in _WEEKLY_FROM_FIXTURE if c in player_fixture.columns]
            if cols:
                firsts = player_fixture.drop_duplicates(["event", "player_id"])
                out = out.merge(
                    firsts[["event", "player_id"] + cols],
                    on=["event", "player_id"],
                    how="left",
                    suffixes=("", "_pf"),
                )
        return out

    @staticmethod
    def _copy_facts(cur, table, mapping, frame, extra) -> None:
        if frame is None or frame.empty:
            return
        columns = list(extra) + list(mapping)
        with cur.copy(
            f"COPY {table} ({', '.join(columns)}) FROM STDIN"
        ) as copy:
            for row in _copy_rows(frame, mapping, extra):
                copy.write_row(row)

    @staticmethod
    def _upsert(cur, sql: str, rows: list[tuple]) -> None:
        if rows:
            cur.executemany(sql, rows)

    def _upsert_gameweeks(self, cur, season, events) -> None:
        if events is None or events.empty or "id" not in events.columns:
            return
        rows = [
            (
                season,
                _py(r["id"]),
                _py(r.get("deadline_time")),
                bool(_py(r.get("finished")) or False),
            )
            for _, r in events.drop_duplicates("id").iterrows()
            if _py(r["id"]) is not None
        ]
        self._upsert(
            cur,
            """
            INSERT INTO gameweeks (season, id, deadline_time, finished)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (season, id) DO UPDATE
            SET deadline_time = EXCLUDED.deadline_time,
                finished = EXCLUDED.finished
            """,
            rows,
        )

    def _upsert_teams(self, cur, season, teams) -> None:
        if teams is None or teams.empty or "id" not in teams.columns:
            return
        rows = [
            (season, _py(r["id"]), _py(r.get("name")), _py(r.get("short_name")))
            for _, r in teams.drop_duplicates("id").iterrows()
            if _py(r["id"]) is not None
        ]
        self._upsert(
            cur,
            """
            INSERT INTO teams (season, id, name, short_name)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (season, id) DO UPDATE
            SET name = EXCLUDED.name, short_name = EXCLUDED.short_name
            """,
            rows,
        )

    def _upsert_players(self, cur, season, players) -> None:
        if players is None or players.empty or "id" not in players.columns:
            return
        rows = [
            (
                season,
                _py(r["id"]),
                _py(r.get("first_name")),
                _py(r.get("second_name")),
                _py(r.get("web_name")) or "",
            )
            for _, r in players.drop_duplicates("id").iterrows()
            if _py(r["id"]) is not None
        ]
        self._upsert(
            cur,
            """
            INSERT INTO players (season, id, first_name, second_name, web_name)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (season, id) DO UPDATE
            SET first_name = EXCLUDED.first_name,
                second_name = EXCLUDED.second_name,
                web_name = EXCLUDED.web_name
            """,
            rows,
        )

    def _upsert_fixtures(self, cur, season, forecasts) -> None:
        if forecasts is None or forecasts.empty or "id" not in forecasts.columns:
            return
        rows = [
            (
                season,
                _py(r["id"]),
                _py(r.get("event")),
                _py(r.get("team_h")),
                _py(r.get("team_a")),
                _py(r.get("kickoff_time")),
            )
            for _, r in forecasts.drop_duplicates("id").iterrows()
            if _py(r["id"]) is not None
        ]
        self._upsert(
            cur,
            """
            INSERT INTO fixtures
                (season, id, gameweek_id, home_team_id, away_team_id, kickoff_time)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (season, id) DO UPDATE
            SET gameweek_id = EXCLUDED.gameweek_id,
                home_team_id = EXCLUDED.home_team_id,
                away_team_id = EXCLUDED.away_team_id,
                kickoff_time = EXCLUDED.kickoff_time
            """,
            rows,
        )

    @staticmethod
    def _status(conn, run_id: RunId) -> str:
        row = conn.execute(
            "SELECT status FROM projection_runs WHERE id = %s", (run_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown run: {run_id}")
        return row[0]

    @staticmethod
    def _record(conn, row) -> RunRecord:
        # Rows come back as tuples in table order; map by cursor description
        # is avoided by selecting through a dict here.
        if not isinstance(row, dict):
            columns = [
                "id", "season", "created_at", "status", "published_at",
                "source", "gw_start", "gw_end", "n_sim", "include_mc",
                "minutes_model_loaded", "manual_minutes_layers",
                "override_count", "inputs", "notes",
            ]
            row = dict(zip(columns, row))
        metadata = RunMetadata(
            season=row["season"],
            source=RunSource(row["source"]),
            gw_start=row["gw_start"],
            gw_end=row["gw_end"],
            n_sim=row["n_sim"],
            include_mc=row["include_mc"],
            minutes_model_loaded=row["minutes_model_loaded"],
            manual_minutes_layers=row["manual_minutes_layers"],
            override_count=row["override_count"],
            inputs=row["inputs"] or {},
            notes=row["notes"],
        )
        return RunRecord(
            run_id=RunId(str(row["id"])),
            status=RunStatus(row["status"]),
            created_at=row["created_at"],
            published_at=row["published_at"],
            metadata=metadata,
        )
