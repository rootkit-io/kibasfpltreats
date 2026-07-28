"""Public (dashboard-facing) REST API -- the read side of the persistence seam.

Phase 14 additions:
- Redis caching (60s TTL, keyed by run_id + gameweek) protects Postgres from
  repeated identical queries. Cache miss -> DB query -> cache set. Cache hit
  -> return immediately, no DB touch.
- Sliding-window rate limiting (30 req/min) keyed by Clerk ``sub`` (user ID),
  NOT client IP. This ensures fair use per authenticated user even behind the
  Next.js BFF proxy. Falls back to allow when Redis is unavailable.
- Both degrade gracefully: Redis down -> cache miss + rate-limit skip.
"""

from __future__ import annotations

from typing import Any, Iterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .auth import verify_clerk_token
from .redis_utils import (
    PROJECTIONS_CACHE_TTL,
    cache_get,
    cache_set,
    check_rate_limit,
)

public_router = APIRouter(prefix="/api/v1/public")

#: Run-header columns the public surface exposes.
_PUBLIC_RUN_COLUMNS = (
    "id",
    "season",
    "gw_start",
    "gw_end",
    "n_sim",
    "include_mc",
    "published_at",
)


# -------------------------------------------------------- connection seam


def get_public_connection(request: Request) -> Iterator[Any | None]:
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        yield None
        return
    with pool.connection() as connection:
        yield connection


def _require_connection(
    connection: Any | None = Depends(get_public_connection),
) -> Any:
    if connection is None:
        raise HTTPException(
            status_code=503,
            detail="public projections unavailable: persistence not configured",
        )
    return connection


def _records(cursor) -> list[dict[str, Any]]:
    columns = [column.name for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


# ------------------------------------------------------------------ routes


@public_router.get("/projections/latest")
async def latest_published_projections(
    request: Request,
    gameweek: int | None = Query(default=None, ge=1, le=38),
    claims: dict = Depends(verify_clerk_token),
    connection: Any = Depends(_require_connection),
) -> dict:
    """The most recently published run's weekly projections.

    Rate-limited per Clerk user (sub claim). Cached in Redis by run_id +
    gameweek so repeated requests within the TTL window skip the DB entirely.
    """
    # ---- rate limit (keyed by Clerk sub, not IP) -------------------------
    user_id: str = claims.get("sub", "anonymous")
    allowed = await check_rate_limit(request.app.state, user_id)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="rate limit exceeded: 30 requests per minute per user",
            headers={"Retry-After": "60"},
        )

    # ---- fetch run header (cheap: single-row view) -----------------------
    run_cursor = connection.execute(
        f"SELECT {', '.join(_PUBLIC_RUN_COLUMNS)} FROM current_published_run"
    )
    run_row = run_cursor.fetchone()
    if run_row is None:
        raise HTTPException(status_code=404, detail="no published run")
    run = dict(zip(_PUBLIC_RUN_COLUMNS, run_row))
    run_id = str(run.pop("id"))
    run["run_id"] = run_id

    # ---- cache lookup (keyed by run_id + gameweek) -----------------------
    cache_key = f"projections:{run_id}:{gameweek}"
    cached = await cache_get(request.app.state, cache_key)
    if cached is not None:
        return cached

    # ---- DB query (only on cache miss) -----------------------------------
    if gameweek is None:
        cursor = connection.execute(
            """
            SELECT * FROM published_player_week
            ORDER BY gameweek_id, xpts DESC NULLS LAST, player_id
            """
        )
    else:
        cursor = connection.execute(
            """
            SELECT * FROM published_player_week
            WHERE gameweek_id = %s
            ORDER BY xpts DESC NULLS LAST, player_id
            """,
            (gameweek,),
        )
    player_week = _records(cursor)

    result = {
        "run": run,
        "gameweek": gameweek,
        "count": len(player_week),
        "player_week": player_week,
    }

    # ---- populate cache --------------------------------------------------
    await cache_set(request.app.state, cache_key, result, ttl=PROJECTIONS_CACHE_TTL)

    return result
