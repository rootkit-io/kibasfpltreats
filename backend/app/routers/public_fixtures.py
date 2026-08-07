"""Public read surface for fixture difficulty.

``GET /api/v1/public/fixtures/latest``

Backs the Fixture Difficulty Ticker. Exposes ``published_fixture_forecasts``,
another view that existed with no endpoint in front of it.

The endpoint returns the model's raw per-fixture quantities -- expected goals
for each side and each side's clean-sheet probability -- rather than a
precomputed 1-5 FDR. Difficulty is a *view* concern here because the ticker
offers General / Attack / Defense modes and lets the user override a cell, so
banding client-side keeps all three modes consistent from one payload.

Auth, rate limiting and caching mirror the sibling public routes exactly.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

try:
    from fpl_xpts.api_public import _PUBLIC_RUN_COLUMNS, _records, _require_connection
    from fpl_xpts.auth import verify_clerk_token
    from fpl_xpts.redis_utils import (
        PROJECTIONS_CACHE_TTL,
        cache_get,
        cache_set,
        check_rate_limit,
    )
except ModuleNotFoundError:  # pragma: no cover - source checkout without install
    import sys
    from pathlib import Path

    _SRC = Path(__file__).resolve().parents[2] / "src"
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    from fpl_xpts.api_public import _PUBLIC_RUN_COLUMNS, _records, _require_connection
    from fpl_xpts.auth import verify_clerk_token
    from fpl_xpts.redis_utils import (
        PROJECTIONS_CACHE_TTL,
        cache_get,
        cache_set,
        check_rate_limit,
    )

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/public", tags=["public-fixtures"])

_FIXTURE_COLUMNS = (
    "fixture_id",
    "gameweek_id",
    "kickoff_time",
    "home_team",
    "away_team",
    "home_goals_lambda",
    "away_goals_lambda",
    "home_cs_prob",
    "away_cs_prob",
    "projection_source",
)


@router.get("/fixtures")
async def list_fixture_difficulties(
    request: Request,
    season: str | None = Query(default=None, pattern=r"^\d{4}$", min_length=4, max_length=4),
    gameweek: int | None = Query(default=None, ge=1, le=38),
    claims: dict = Depends(verify_clerk_token),
    connection: Any = Depends(_require_connection),
) -> dict:
    """Return effective home/away FDRs, resolving overrides inside Postgres."""
    user_id: str = claims.get("sub", "anonymous")
    if not await check_rate_limit(request.app.state, user_id):
        raise HTTPException(
            status_code=429,
            detail="rate limit exceeded: 30 requests per minute per user",
            headers={"Retry-After": "60"},
        )

    if season is None:
        season_row = connection.execute("SELECT MAX(season) FROM fixtures").fetchone()
        season = season_row[0] if season_row is not None else None
    if season is None:
        return {"season": None, "gameweek": gameweek, "count": 0, "fixtures": []}

    sql = """
        SELECT f.id AS fixture_id,
               f.gameweek_id AS gameweek,
               f.kickoff_time,
               f.finished,
               f.home_team_id AS team_h_id,
               home.short_name AS team_h_short_name,
               home.name AS team_h_name,
               f.away_team_id AS team_a_id,
               away.short_name AS team_a_short_name,
               away.name AS team_a_name,
               COALESCE(f.team_h_fdr_override, f.team_h_fdr_fpl) AS team_h_fdr,
               COALESCE(f.team_a_fdr_override, f.team_a_fdr_fpl) AS team_a_fdr
        FROM fixtures AS f
        JOIN teams AS home
          ON home.season = f.season AND home.id = f.home_team_id
        JOIN teams AS away
          ON away.season = f.season AND away.id = f.away_team_id
        WHERE f.season = %s
    """
    parameters: tuple[Any, ...] = (season,)
    if gameweek is not None:
        sql += " AND f.gameweek_id = %s"
        parameters += (gameweek,)
    sql += " ORDER BY f.gameweek_id, f.kickoff_time NULLS LAST, f.id"

    fixtures = _records(connection.execute(sql, parameters))
    return {
        "season": season,
        "gameweek": gameweek,
        "count": len(fixtures),
        "fixtures": fixtures,
    }


@router.get("/fixtures/latest")
async def latest_published_fixtures(
    request: Request,
    gameweek: int | None = Query(default=None, ge=1, le=38, alias="gw"),
    claims: dict = Depends(verify_clerk_token),
    connection: Any = Depends(_require_connection),
) -> dict:
    """Fixture-level forecasts for the most recently published run.

    Returns an empty ``fixtures`` list (not a 404) when the run carries no
    fixture grain -- runs ingested from the weekly/MC CSV pair have no
    fixture-level rows, so the ticker must degrade rather than error.
    """
    user_id: str = claims.get("sub", "anonymous")
    if not await check_rate_limit(request.app.state, user_id):
        raise HTTPException(
            status_code=429,
            detail="rate limit exceeded: 30 requests per minute per user",
            headers={"Retry-After": "60"},
        )

    run_cursor = connection.execute(
        f"SELECT {', '.join(_PUBLIC_RUN_COLUMNS)} FROM current_published_run"
    )
    run_row = run_cursor.fetchone()
    if run_row is None:
        raise HTTPException(status_code=404, detail="no published run")
    run = dict(zip(_PUBLIC_RUN_COLUMNS, run_row))
    run_id = str(run.pop("id"))
    run["run_id"] = run_id

    cache_key = f"fixtures:{run_id}:{gameweek}"
    cached = await cache_get(request.app.state, cache_key)
    if cached is not None:
        return cached

    columns = ", ".join(_FIXTURE_COLUMNS)
    if gameweek is None:
        cursor = connection.execute(
            f"""
            SELECT {columns} FROM published_fixture_forecasts
            ORDER BY gameweek_id, kickoff_time, fixture_id
            """
        )
    else:
        cursor = connection.execute(
            f"""
            SELECT {columns} FROM published_fixture_forecasts
            WHERE gameweek_id = %s
            ORDER BY kickoff_time, fixture_id
            """,
            (gameweek,),
        )
    fixtures = _records(cursor)

    result = {
        "run": run,
        "gameweek": gameweek,
        "count": len(fixtures),
        "fixtures": fixtures,
    }
    await cache_set(request.app.state, cache_key, result, ttl=PROJECTIONS_CACHE_TTL)
    return result
