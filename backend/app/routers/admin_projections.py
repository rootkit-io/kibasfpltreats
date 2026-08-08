"""Precomputed-run ingestion endpoint.

``POST /api/v1/admin/projections/ingest-csvs``

Accepts the two native local-model exports as multipart form data, validates
them, resolves identity against the season's dimension tables, and stages the
result as a DRAFT run so the existing preview / publish workflow applies
unchanged.

This is the *ingest* counterpart to ``/admin/projections/run``, which computes
projections in-process. Nothing here imports the modelling pipeline beyond the
key-normalisation helpers; heavy inference is the local machine's job.

Scope note: these CSVs are an export view and carry only the weekly and Monte
Carlo grains. The fixture-grain frames are not present, so
``player_fixture_projections`` / ``fixture_forecasts`` are not written by this
path and the columns the weekly table normally folds in from the fixture grain
(``start_probability`` / ``play_probability``) stay NULL.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from .ingest_csv import (
    FIXTURE_DIMENSION_MAP,
    FIXTURE_FORECAST_MAP,
    FIXTURE_REQUIRED,
    MC_COLUMN_MAP,
    PLAYERS_REQUIRED,
    TEAMS_REQUIRED,
    MC_REQUIRED,
    UNMAPPED_FIXTURE,
    UNMAPPED_MC,
    UNMAPPED_WEEKLY,
    WEEKLY_COLUMN_MAP,
    WEEKLY_REQUIRED,
    IngestValidationError,
    build_identity_index,
    check_fixture_frame,
    check_positions,
    check_symmetry,
    check_value_ranges,
    player_dimension_tuples,
    read_projection_csv,
    require_columns,
    team_dimension_tuples,
    resolve_identities,
    unknown_fixture_teams,
)

try:
    from fpl_xpts.api import require_admin_token
except ModuleNotFoundError:  # pragma: no cover - source checkout without install
    import sys
    from pathlib import Path

    _SRC = Path(__file__).resolve().parents[2] / "src"
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    from fpl_xpts.api import require_admin_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["admin-ingest"])

WEEKLY_FILENAME = "weekly_player_week.csv"
MC_FILENAME = "mc_brackets_full_player_week.csv"
FIXTURES_FILENAME = "fixtures_forecast.csv"
PLAYERS_FILENAME = "players.csv"
TEAMS_FILENAME = "teams.csv"


class UnmatchedPlayer(BaseModel):
    player: str
    team: str
    name_key: str
    team_key: str
    reason: str


class DroppedColumns(BaseModel):
    weekly: list[str]
    monte_carlo: list[str]
    fixtures: list[str]


class IngestResult(BaseModel):
    """Response contract for a precomputed-run ingest."""

    run_id: str
    season: str
    status: str = "draft"
    gameweeks: list[int]
    weekly_rows: int
    simulation_rows: int
    #: 0 when no fixtures file was supplied -- the grain is optional.
    fixture_rows: int = 0
    fixtures_upserted: int = 0
    fixture_gameweeks: list[int] = Field(default_factory=list)
    #: 0 when the corresponding dimension file was not supplied.
    teams_upserted: int = 0
    players_upserted: int = 0
    players: int
    unmatched_players: list[UnmatchedPlayer]
    dropped_columns: DroppedColumns

#: Refuse to stage a run when identity resolution is this bad -- a wholesale
#: failure means the wrong season or an unpopulated dimension table, and
#: staging a mostly-empty run would be worse than refusing.
MAX_UNMATCHED_FRACTION = 0.02


def _fail(message: str, errors: list[dict] | None = None, status: int = 400):
    raise HTTPException(status_code=status, detail={"message": message, "errors": errors or []})


@router.post(
    "/admin/projections/ingest-csvs",
    dependencies=[Depends(require_admin_token)],
    response_model=IngestResult,
)
async def ingest_precomputed_run(
    request: Request,
    season: str = Form(..., description="e.g. '2627' -- absent from the CSVs"),
    weekly_file: UploadFile = File(..., description=WEEKLY_FILENAME),
    mc_file: UploadFile = File(..., description=MC_FILENAME),
    # OPTIONAL on purpose. The admin panel lives outside this service and still
    # posts two files; making the fixture grain mandatory would break that flow
    # the moment this deploys. Runs ingested without it behave exactly as
    # before -- the dashboard's ticker simply stays disabled for them.
    fixtures_csv: UploadFile | None = File(default=None, description=FIXTURES_FILENAME),
    # Dimension seeding. Optional, but supplying them is the only way to fix a
    # stale squad: identity resolution reads `players`/`teams`, so a DB missing
    # late-window transfers fails wholesale no matter how good the matching is.
    players_csv: UploadFile | None = File(default=None, description=PLAYERS_FILENAME),
    teams_csv: UploadFile | None = File(default=None, description=TEAMS_FILENAME),
    notes: str | None = Form(default=None),
) -> dict[str, Any]:
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        _fail("persistence not configured; set DATABASE_URL", status=503)

    season = season.strip()
    if not season:
        _fail("season is required")

    # ---------------------------------------------------------- parse + preflight
    try:
        weekly = read_projection_csv(await weekly_file.read(), label=WEEKLY_FILENAME)
        mc = read_projection_csv(await mc_file.read(), label=MC_FILENAME)
        require_columns(weekly, WEEKLY_REQUIRED, label=WEEKLY_FILENAME)
        require_columns(mc, MC_REQUIRED, label=MC_FILENAME)
        report = check_symmetry(weekly, mc)
        check_value_ranges(mc)
        check_positions(weekly)

        teams_frame = None
        players_frame = None
        if teams_csv is not None and teams_csv.filename:
            teams_frame = read_projection_csv(await teams_csv.read(), label=TEAMS_FILENAME)
            require_columns(teams_frame, TEAMS_REQUIRED, label=TEAMS_FILENAME)
        if players_csv is not None and players_csv.filename:
            players_frame = read_projection_csv(await players_csv.read(), label=PLAYERS_FILENAME)
            require_columns(players_frame, PLAYERS_REQUIRED, label=PLAYERS_FILENAME)

        fixtures = None
        fixture_report = None
        if fixtures_csv is not None and fixtures_csv.filename:
            fixtures = read_projection_csv(await fixtures_csv.read(), label=FIXTURES_FILENAME)
            require_columns(fixtures, FIXTURE_REQUIRED, label=FIXTURES_FILENAME)
            fixture_report = check_fixture_frame(fixtures, report.gameweeks)
    except IngestValidationError as exc:
        logger.info("ingest preflight rejected: %s", exc.message)
        raise HTTPException(status_code=400, detail=exc.as_detail()) from exc

    with pool.connection() as conn:  # one transaction: commit on exit, rollback on raise
        cur = conn.cursor()

        # Dimension seeding runs FIRST, inside this same transaction, so the
        # identity index below is built from the freshly-upserted squad rather
        # than the stale one. Teams precede players only for readability; there
        # is no FK between them.
        team_dimension_rows: list[tuple] = []
        player_dimension_rows: list[tuple] = []
        try:
            if teams_frame is not None:
                team_dimension_rows = team_dimension_tuples(teams_frame, season)
                cur.executemany(_teams_upsert_sql(), team_dimension_rows)
            if players_frame is not None:
                player_dimension_rows = player_dimension_tuples(players_frame, season)
                cur.executemany(_players_upsert_sql(), player_dimension_rows)
        except IngestValidationError as exc:
            logger.info("dimension seeding rejected: %s", exc.message)
            raise HTTPException(status_code=400, detail=exc.as_detail()) from exc

        cur.execute(
            "SELECT id, first_name, second_name, web_name FROM players WHERE season = %s",
            (season,),
        )
        player_rows = cur.fetchall()
        cur.execute("SELECT id, name FROM teams WHERE season = %s", (season,))
        team_rows = cur.fetchall()

        index = build_identity_index(player_rows, team_rows)
        if index.is_empty:
            _fail(
                f"no players/teams are loaded for season {season!r}; ingest "
                "requires the dimension tables to be populated first",
                [{"players": len(player_rows), "teams": len(team_rows)}],
                status=409,
            )

        if fixture_report is not None:
            missing_teams = unknown_fixture_teams(
                fixture_report, (int(t[0]) for t in team_rows)
            )
            if missing_teams:
                _fail(
                    f"fixtures reference team id(s) {missing_teams} that are not "
                    f"loaded for season {season!r}",
                    [{"missing_team_ids": missing_teams}],
                    status=409,
                )

        cur.execute("SELECT id FROM gameweeks WHERE season = %s", (season,))
        known_gws = {int(r[0]) for r in cur.fetchall()}
        missing_gws = [gw for gw in report.gameweeks if gw not in known_gws]
        if missing_gws:
            _fail(
                f"gameweek(s) {missing_gws} are not loaded for season {season!r}",
                [{"missing_gameweeks": missing_gws}],
                status=409,
            )

        # ------------------------------------------------------------- identity
        resolution = resolve_identities(weekly, index)
        total_players = int(weekly["player"].nunique())
        unmatched_fraction = len(resolution.unmatched) / max(total_players, 1)
        if unmatched_fraction > MAX_UNMATCHED_FRACTION:
            logger.warning(
                "ingest rejected: %d/%d players unresolved for season %s",
                len(resolution.unmatched), total_players, season,
            )
            _fail(
                f"{len(resolution.unmatched)} of {total_players} players could not "
                f"be matched to season {season!r} (limit "
                f"{MAX_UNMATCHED_FRACTION:.0%}); nothing was written",
                resolution.unmatched[:50],
                status=422,
            )

        resolved = resolution.resolved
        staged = resolved[resolved["player_id"].notna()]
        if staged.empty:
            _fail("no rows survived identity resolution; nothing was written", status=422)

        # ---------------------------------------------------------- draft run
        run_id = str(uuid.uuid4())
        cur.execute(
            """
            INSERT INTO projection_runs
                (id, season, status, source, gw_start, gw_end, include_mc,
                 minutes_model_loaded, inputs, notes)
            VALUES (%s, %s, 'draft', 'admin_api', %s, %s, true, false, %s, %s)
            """,
            (
                run_id, season, min(report.gameweeks), max(report.gameweeks),
                _inputs_json(report, resolution, weekly, mc), notes,
            ),
        )

        weekly_rows = _weekly_tuples(staged, run_id, season)
        cur.executemany(_weekly_insert_sql(), weekly_rows)

        mc_indexed = _join_mc(mc, staged)
        mc_rows = _mc_tuples(mc_indexed, run_id, season)
        cur.executemany(_mc_insert_sql(), mc_rows)

        # Fixture grain. `fixtures` is a season-scoped dimension upserted
        # first, because fixture_forecasts carries an FK onto it. Both run
        # inside this same `with pool.connection()` block, so a failure here
        # rolls back the run row and the player grains with it.
        fixture_rows: list[tuple] = []
        if fixtures is not None and fixture_report is not None:
            dimension_rows = _fixture_dimension_tuples(fixtures, season)
            cur.executemany(_fixture_upsert_sql(), dimension_rows)
            fixture_rows = _fixture_forecast_tuples(fixtures, run_id, season)
            cur.executemany(_fixture_forecast_insert_sql(), fixture_rows)

    logger.info(
        "ingested draft run %s season=%s gws=%s weekly=%d sims=%d fixtures=%d "
        "teams=%d players=%d unmatched=%d",
        run_id, season, report.gameweeks, len(weekly_rows), len(mc_rows),
        len(fixture_rows), len(team_dimension_rows), len(player_dimension_rows),
        len(resolution.unmatched),
    )
    return {
        "run_id": run_id,
        "season": season,
        "status": "draft",
        "gameweeks": report.gameweeks,
        "weekly_rows": len(weekly_rows),
        "simulation_rows": len(mc_rows),
        "players": report.player_count,
        "fixture_rows": len(fixture_rows),
        "fixtures_upserted": len(fixture_rows),
        "fixture_gameweeks": fixture_report.gameweeks if fixture_report else [],
        "teams_upserted": len(team_dimension_rows),
        "players_upserted": len(player_dimension_rows),
        "unmatched_players": resolution.unmatched,
        "dropped_columns": {
            "weekly": list(UNMAPPED_WEEKLY),
            "monte_carlo": list(UNMAPPED_MC),
            "fixtures": list(UNMAPPED_FIXTURE),
        },
    }


# ------------------------------------------------------------------ helpers
def _inputs_json(report, resolution, weekly, mc):
    from psycopg.types.json import Jsonb

    return Jsonb({
        "ingest": "precomputed_csv",
        "weekly_filename": WEEKLY_FILENAME,
        "mc_filename": MC_FILENAME,
        "weekly_rows": int(len(weekly)),
        "mc_rows": int(len(mc)),
        "gameweeks": report.gameweeks,
        "unmatched_players": len(resolution.unmatched),
        "dropped_columns": {"weekly": list(UNMAPPED_WEEKLY), "monte_carlo": list(UNMAPPED_MC)},
    })


_WEEKLY_DB_COLUMNS = [
    "run_id", "season", "gameweek_id", "player_id", "team_id", "position",
    *WEEKLY_COLUMN_MAP.values(),
]
_MC_DB_COLUMNS = ["run_id", "season", "gameweek_id", "player_id", *MC_COLUMN_MAP.values()]


def _weekly_insert_sql() -> str:
    cols = ", ".join(_WEEKLY_DB_COLUMNS)
    ph = ", ".join(["%s"] * len(_WEEKLY_DB_COLUMNS))
    return f"INSERT INTO player_gameweek_projections ({cols}) VALUES ({ph})"


def _mc_insert_sql() -> str:
    cols = ", ".join(_MC_DB_COLUMNS)
    ph = ", ".join(["%s"] * len(_MC_DB_COLUMNS))
    return f"INSERT INTO player_gameweek_simulations ({cols}) VALUES ({ph})"


def _clean(value):
    """NaN/NaT -> None so psycopg writes SQL NULL instead of the float nan."""
    import pandas as pd

    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return value.item() if hasattr(value, "item") else value


def _weekly_tuples(frame, run_id: str, season: str) -> list[tuple]:
    rows = []
    for record in frame.to_dict("records"):
        rows.append((
            run_id, season, int(record["GW"]), int(record["player_id"]),
            # team_id is NULLABLE in the schema. The ID-first resolver can
            # match a player by element id while failing to attribute a club
            # (unknown/renamed team); losing the club is far better than
            # dropping the projection, so write NULL rather than crashing.
            None if record.get("team_id") is None else int(record["team_id"]),
            str(record["Pos"]).upper(),
            *(_clean(record.get(csv_col)) for csv_col in WEEKLY_COLUMN_MAP),
        ))
    return rows


def _join_mc(mc, staged):
    """Attach resolved ids to the MC frame via the (GW, player_key) grain."""
    keys = staged[["GW", "player_key", "player_id"]].drop_duplicates()
    merged = mc.merge(keys, on=["GW", "player_key"], how="inner")
    return merged


def _mc_tuples(frame, run_id: str, season: str) -> list[tuple]:
    rows = []
    for record in frame.to_dict("records"):
        rows.append((
            run_id, season, int(record["GW"]), int(record["player_id"]),
            *(_clean(record.get(csv_col)) for csv_col in MC_COLUMN_MAP),
        ))
    return rows


# ---------------------------------------------------------- fixture writes
_FIXTURE_DIMENSION_COLUMNS = [
    "season", "id", "gameweek_id", "home_team_id", "away_team_id", "kickoff_time",
    *FIXTURE_DIMENSION_MAP.values(),
]
_FIXTURE_FORECAST_COLUMNS = [
    "run_id", "season", "fixture_id", "gameweek_id", *FIXTURE_FORECAST_MAP.values(),
]


def _fixture_upsert_sql() -> str:
    cols = ", ".join(_FIXTURE_DIMENSION_COLUMNS)
    ph = ", ".join(["%s"] * len(_FIXTURE_DIMENSION_COLUMNS))
    # Idempotent by (season, id): re-ingesting a later run refreshes kickoff
    # times and gameweek moves without duplicating the dimension.
    return (
        f"INSERT INTO fixtures ({cols}) VALUES ({ph}) "
        "ON CONFLICT (season, id) DO UPDATE SET "
        "gameweek_id = EXCLUDED.gameweek_id, "
        "home_team_id = EXCLUDED.home_team_id, "
        "away_team_id = EXCLUDED.away_team_id, "
        "kickoff_time = EXCLUDED.kickoff_time, "
        "finished = EXCLUDED.finished, "
        "team_h_fdr_fpl = EXCLUDED.team_h_fdr_fpl, "
        "team_a_fdr_fpl = EXCLUDED.team_a_fdr_fpl"
        # team_h_fdr_override / team_a_fdr_override are intentionally absent:
        # those are admin edits made through PATCH /admin/fixtures/fdr, and a
        # re-ingest must not wipe them. The public endpoint COALESCEs the
        # override over the FPL value, so an override keeps winning.
    )


def _fixture_forecast_insert_sql() -> str:
    cols = ", ".join(_FIXTURE_FORECAST_COLUMNS)
    ph = ", ".join(["%s"] * len(_FIXTURE_FORECAST_COLUMNS))
    return f"INSERT INTO fixture_forecasts ({cols}) VALUES ({ph})"


def _fixture_dimension_tuples(frame, season: str) -> list[tuple]:
    rows = []
    for record in frame.to_dict("records"):
        kickoff = _clean(record.get("kickoff_time"))
        rows.append((
            season,
            int(record["id"]),
            int(record["event"]),
            int(record["team_h"]),
            int(record["team_a"]),
            # Empty strings must become NULL, not '' -- timestamptz would reject.
            str(kickoff) if kickoff not in (None, "") else None,
            # Driven off FIXTURE_DIMENSION_MAP so the value order can never
            # drift from the column order in the generated INSERT.
            *(
                _FIXTURE_COERCE[csv_col](_clean(record.get(csv_col)))
                for csv_col in FIXTURE_DIMENSION_MAP
            ),
        ))
    return rows


#: Per-column coercion for the fixtures dimension. `finished` is NOT NULL
#: DEFAULT false so it must never be None; the FDR columns are nullable ints
#: with a CHECK of 1..5.
_FIXTURE_COERCE = {
    "team_h_difficulty": lambda v: _int_or_none(v),
    "team_a_difficulty": lambda v: _int_or_none(v),
    "finished": lambda v: bool(v) if v is not None else False,
}


def _int_or_none(value):
    """FDR columns are integer + CHECK 1..5; a blank export cell means NULL."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _fixture_forecast_tuples(frame, run_id: str, season: str) -> list[tuple]:
    rows = []
    for record in frame.to_dict("records"):
        rows.append((
            run_id, season, int(record["id"]), int(record["event"]),
            *(_clean(record.get(csv_col)) for csv_col in FIXTURE_FORECAST_MAP),
        ))
    return rows


# -------------------------------------------------------- dimension upserts
# SQL mirrors PostgresProjectionRepository._upsert_teams/_upsert_players so a
# run seeded through this endpoint is byte-identical to one seeded by
# save_run(). Kept as literals rather than imported: the repository methods are
# bound to a repository instance and live in the read-only modelling package.
def _teams_upsert_sql() -> str:
    return (
        "INSERT INTO teams (season, id, name, short_name) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (season, id) DO UPDATE "
        "SET name = EXCLUDED.name, short_name = EXCLUDED.short_name"
    )


def _players_upsert_sql() -> str:
    return (
        "INSERT INTO players "
        "(season, id, first_name, second_name, web_name, now_cost, selected_by_percent) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (season, id) DO UPDATE "
        "SET first_name = EXCLUDED.first_name, "
        "    second_name = EXCLUDED.second_name, "
        "    web_name = EXCLUDED.web_name, "
        # COALESCE, not a bare overwrite: re-seeding from an older export that
        # predates these columns would otherwise wipe live prices to NULL.
        "    now_cost = COALESCE(EXCLUDED.now_cost, players.now_cost), "
        "    selected_by_percent = COALESCE(EXCLUDED.selected_by_percent, "
        "                                   players.selected_by_percent)"
    )
