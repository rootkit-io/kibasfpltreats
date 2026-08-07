"""Season dimension seeding, independent of any projection run.

``POST /api/v1/admin/dimensions/seed``

Loads ``gameweeks`` / ``teams`` / ``players`` / ``fixtures`` for a season
without requiring a weekly or Monte Carlo export.

WHY THIS IS SEPARATE FROM ingest-csvs
-------------------------------------
``ingest-csvs`` seeds dimensions as a side effect of staging a run, so it
demands a weekly+MC pair and refuses when identity resolution fails. That is
correct for a projection run, but it makes the two operations inseparable:
loading a new season's schedule before any projections exist is impossible,
because the breaker rejects the payload and rolls the dimensions back with it.

Fixtures are the reason this matters. FPL publishes all 380 with official
difficulty before a ball is kicked; the ticker only needs those, and waiting
for a model run to load them is backwards.

SEASON SAFETY
-------------
Team ids are recycled between seasons -- id 3 is Bournemouth in 2026/27 and
was Burnley in 2025/26 -- so seeding fixtures against another season's teams
renders confidently wrong opponents. Fixtures are therefore validated against
the teams present AFTER the upsert in this same transaction, and the request
is refused if any referenced club is missing.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel

from .ingest_csv import (
    FIXTURE_REQUIRED,
    PLAYERS_REQUIRED,
    TEAMS_REQUIRED,
    IngestValidationError,
    check_fixture_frame,
    player_dimension_tuples,
    read_projection_csv,
    require_columns,
    team_dimension_tuples,
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

router = APIRouter(prefix="/api/v1/admin", tags=["admin-dimensions"])

GAMEWEEKS_REQUIRED: tuple[str, ...] = ("id",)


class SeedResult(BaseModel):
    season: str
    gameweeks_upserted: int = 0
    teams_upserted: int = 0
    players_upserted: int = 0
    fixtures_upserted: int = 0
    fixture_gameweeks: list[int] = []


def _fail(message: str, errors: list[dict] | None = None, status: int = 400):
    raise HTTPException(status_code=status, detail={"message": message, "errors": errors or []})


def _gameweek_tuples(frame, season: str) -> list[tuple]:
    from .ingest_csv import _dimension_id, _dimension_text

    rows = []
    for position, record in enumerate(
        frame.drop_duplicates(subset=["id"], keep="last").to_dict("records"), start=1
    ):
        deadline = _dimension_text(record.get("deadline_time"), label="gameweeks",
                                   column="deadline_time")
        finished = record.get("finished")
        rows.append((
            season,
            _dimension_id(record.get("id"), label="gameweeks", row=position),
            deadline,
            str(finished).strip().lower() in {"true", "1", "yes"},
        ))
    return rows


_GAMEWEEK_SQL = (
    "INSERT INTO gameweeks (season, id, deadline_time, finished) VALUES (%s, %s, %s, %s) "
    "ON CONFLICT (season, id) DO UPDATE "
    "SET deadline_time = EXCLUDED.deadline_time, finished = EXCLUDED.finished"
)


@router.post("/dimensions/seed", dependencies=[Depends(require_admin_token)],
             response_model=SeedResult)
async def seed_season_dimensions(
    request: Request,
    season: str = Form(..., description="e.g. '2627'"),
    gameweeks_csv: UploadFile | None = File(default=None),
    teams_csv: UploadFile | None = File(default=None),
    players_csv: UploadFile | None = File(default=None),
    fixtures_csv: UploadFile | None = File(default=None),
) -> dict[str, Any]:
    """Upsert one season's dimensions. Every file is optional; order is fixed.

    Writes run in FK order (gameweeks and teams before fixtures) inside a
    single transaction, so a failure anywhere leaves the season untouched.
    """
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        _fail("persistence not configured; set DATABASE_URL", status=503)

    season = season.strip()
    if not season:
        _fail("season is required")

    try:
        gameweeks = teams = players = fixtures = None
        if gameweeks_csv is not None and gameweeks_csv.filename:
            gameweeks = read_projection_csv(await gameweeks_csv.read(), label="gameweeks.csv")
            require_columns(gameweeks, GAMEWEEKS_REQUIRED, label="gameweeks.csv")
        if teams_csv is not None and teams_csv.filename:
            teams = read_projection_csv(await teams_csv.read(), label="teams.csv")
            require_columns(teams, TEAMS_REQUIRED, label="teams.csv")
        if players_csv is not None and players_csv.filename:
            players = read_projection_csv(await players_csv.read(), label="players.csv")
            require_columns(players, PLAYERS_REQUIRED, label="players.csv")
        if fixtures_csv is not None and fixtures_csv.filename:
            fixtures = read_projection_csv(await fixtures_csv.read(), label="fixtures_forecast.csv")
            require_columns(fixtures, FIXTURE_REQUIRED, label="fixtures_forecast.csv")
    except IngestValidationError as exc:
        logger.info("dimension seed rejected: %s", exc.message)
        raise HTTPException(status_code=400, detail=exc.as_detail()) from exc

    if all(frame is None for frame in (gameweeks, teams, players, fixtures)):
        _fail("supply at least one of gameweeks_csv, teams_csv, players_csv, fixtures_csv")

    counts = {"gameweeks": 0, "teams": 0, "players": 0, "fixtures": 0}
    fixture_gameweeks: list[int] = []

    with pool.connection() as conn:
        cur = conn.cursor()
        try:
            if gameweeks is not None:
                rows = _gameweek_tuples(gameweeks, season)
                cur.executemany(_GAMEWEEK_SQL, rows)
                counts["gameweeks"] = len(rows)
            if teams is not None:
                rows = team_dimension_tuples(teams, season)
                cur.executemany(_teams_sql(), rows)
                counts["teams"] = len(rows)
            if players is not None:
                rows = player_dimension_tuples(players, season)
                cur.executemany(_players_sql(), rows)
                counts["players"] = len(rows)
        except IngestValidationError as exc:
            raise HTTPException(status_code=400, detail=exc.as_detail()) from exc

        if fixtures is not None:
            # Known gameweeks are read back AFTER the upsert above, so a
            # fixtures file may legitimately introduce gameweeks in the same
            # request as long as gameweeks_csv carried them.
            cur.execute("SELECT id FROM gameweeks WHERE season = %s", (season,))
            known_gws = [int(r[0]) for r in cur.fetchall()]
            try:
                report = check_fixture_frame(fixtures, known_gws)
            except IngestValidationError as exc:
                raise HTTPException(status_code=400, detail=exc.as_detail()) from exc

            cur.execute("SELECT id FROM teams WHERE season = %s", (season,))
            missing = unknown_fixture_teams(report, (int(r[0]) for r in cur.fetchall()))
            if missing:
                _fail(
                    f"fixtures reference team id(s) {missing} not loaded for season "
                    f"{season!r}; supply teams_csv in the same request",
                    [{"missing_team_ids": missing}],
                    status=409,
                )

            from .admin_projections import _fixture_dimension_tuples, _fixture_upsert_sql

            rows = _fixture_dimension_tuples(fixtures, season)
            cur.executemany(_fixture_upsert_sql(), rows)
            counts["fixtures"] = len(rows)
            fixture_gameweeks = report.gameweeks

    logger.info(
        "seeded season %s: gameweeks=%d teams=%d players=%d fixtures=%d",
        season, counts["gameweeks"], counts["teams"], counts["players"], counts["fixtures"],
    )
    return {
        "season": season,
        "gameweeks_upserted": counts["gameweeks"],
        "teams_upserted": counts["teams"],
        "players_upserted": counts["players"],
        "fixtures_upserted": counts["fixtures"],
        "fixture_gameweeks": fixture_gameweeks,
    }


def _teams_sql() -> str:
    from .admin_projections import _teams_upsert_sql
    return _teams_upsert_sql()


def _players_sql() -> str:
    from .admin_projections import _players_upsert_sql
    return _players_upsert_sql()
