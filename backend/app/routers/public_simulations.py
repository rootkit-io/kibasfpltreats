"""Public read surface for Monte Carlo simulation data.

``GET /api/v1/public/simulations/latest``

Exposes ``published_player_week_simulations`` -- a view that already existed
but was referenced by no endpoint, so the simulation layer was unreachable
from the dashboard.

Mirrors ``/api/v1/public/projections/latest`` exactly: Clerk-verified, rate
limited per user, and Redis-cached by run id + gameweek. Those dependencies
are imported from ``fpl_xpts.api_public`` rather than reimplemented so the two
public routes cannot drift apart on auth or caching.

Column naming note: the DB columns are the repository's names (``mean_pts``,
``std_pts``, ``floor_p10``, ``upside_p90``, ``bracket_3_6``...), NOT the
model's export names (``MC_MeanPts``, ``MC_Floor``, ``Bracket_3_to_6``...).
The rows are returned verbatim under the DB names; the client types match.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

try:
    from fpl_xpts.api_public import (
        _PUBLIC_RUN_COLUMNS,
        _records,
        _require_connection,
    )
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
    from fpl_xpts.api_public import (
        _PUBLIC_RUN_COLUMNS,
        _records,
        _require_connection,
    )
    from fpl_xpts.auth import verify_clerk_token
    from fpl_xpts.redis_utils import (
        PROJECTIONS_CACHE_TTL,
        cache_get,
        cache_set,
        check_rate_limit,
    )

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/public", tags=["public-simulations"])

#: Selected explicitly rather than `SELECT *` so a schema addition cannot
#: silently widen the public payload.
_SIMULATION_COLUMNS = (
    "player_id",
    "gameweek_id",
    "web_name",
    "team_short",
    "n_sim",
    "mean_pts",
    "std_pts",
    "min_pts",
    "max_pts",
    "floor_p10",
    "p25",
    "p75",
    "upside_p90",
    "p1_return",
    "p2_return",
    "p_return",
    "p_haul",
    "bracket_le_2",
    "bracket_3_6",
    "bracket_7_9",
    "bracket_10_14",
    "bracket_15_plus",
)


@router.get("/simulations/latest")
async def latest_published_simulations(
    request: Request,
    gameweek: int | None = Query(default=None, ge=1, le=38, alias="gw"),
    claims: dict = Depends(verify_clerk_token),
    connection: Any = Depends(_require_connection),
) -> dict:
    """Monte Carlo distributions for the most recently published run.

    Returns an empty ``simulations`` list (not a 404) when the published run
    was executed with ``include_mc = false`` or was ingested from CSVs that
    carried no simulation grain -- the run itself still exists, so the caller
    should render a graceful empty state rather than an error.
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

    cache_key = f"simulations:{run_id}:{gameweek}"
    cached = await cache_get(request.app.state, cache_key)
    if cached is not None:
        return cached

    columns = ", ".join(_SIMULATION_COLUMNS)
    if gameweek is None:
        cursor = connection.execute(
            f"""
            SELECT {columns} FROM published_player_week_simulations
            ORDER BY gameweek_id, mean_pts DESC NULLS LAST, player_id
            """
        )
    else:
        cursor = connection.execute(
            f"""
            SELECT {columns} FROM published_player_week_simulations
            WHERE gameweek_id = %s
            ORDER BY mean_pts DESC NULLS LAST, player_id
            """,
            (gameweek,),
        )
    simulations = _records(cursor)

    result = {
        "run": run,
        "gameweek": gameweek,
        "count": len(simulations),
        "simulations": simulations,
    }
    await cache_set(request.app.state, cache_key, result, ttl=PROJECTIONS_CACHE_TTL)
    return result
