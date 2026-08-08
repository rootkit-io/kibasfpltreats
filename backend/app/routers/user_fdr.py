"""Per-user fixture difficulty overrides.

GET    /api/v1/user/fdr?season=2627
PUT    /api/v1/user/fdr
DELETE /api/v1/user/fdr

Auth: Clerk JWT (verify_clerk_token). Admin rights not required.
"""
from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/user", tags=["user-fdr"])

try:
    from fpl_xpts.auth import verify_clerk_token
except ModuleNotFoundError:  # pragma: no cover
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from fpl_xpts.auth import verify_clerk_token


def _pool(request: Request):
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="persistence not configured")
    return pool


class FdrEntry(BaseModel):
    fixture_id: int
    team_id: int
    fdr: int = Field(..., ge=1, le=5)


class FdrUpsert(BaseModel):
    season: str
    fixture_id: int
    team_id: int
    fdr: int = Field(..., ge=1, le=5)

    @field_validator("season")
    @classmethod
    def season_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("season must not be empty")
        return v.strip()


class FdrDelete(BaseModel):
    season: str
    fixture_id: int
    team_id: int


@router.get("/fdr", response_model=dict)
async def list_overrides(
    request: Request,
    season: Annotated[str, Query()],
    claims: dict[str, Any] = Depends(verify_clerk_token),
) -> dict:
    user_id: str = claims.get("sub", "")
    pool = _pool(request)
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT fixture_id, team_id, fdr FROM user_fixture_overrides "
            "WHERE clerk_user_id = %s AND season = %s",
            (user_id, season),
        ).fetchall()
    return {
        "season": season,
        "entries": [{"fixture_id": r[0], "team_id": r[1], "fdr": r[2]} for r in rows],
    }


@router.put("/fdr", response_model=FdrEntry, status_code=200)
async def upsert_override(
    request: Request,
    body: FdrUpsert,
    claims: dict[str, Any] = Depends(verify_clerk_token),
) -> dict:
    user_id: str = claims.get("sub", "")
    pool = _pool(request)
    with pool.connection() as conn:
        try:
            conn.execute(
                "INSERT INTO user_fixture_overrides"
                " (clerk_user_id, season, fixture_id, team_id, fdr)"
                " VALUES (%s, %s, %s, %s, %s)"
                " ON CONFLICT (clerk_user_id, season, fixture_id, team_id)"
                " DO UPDATE SET fdr = EXCLUDED.fdr",
                (user_id, body.season, body.fixture_id, body.team_id, body.fdr),
            )
        except Exception as exc:
            if "foreign key" in str(exc).lower():
                raise HTTPException(
                    status_code=409,
                    detail=f"fixture {body.fixture_id} / team {body.team_id} "
                           f"not found for season {body.season!r}",
                ) from exc
            raise
    log.info("upsert fdr user=%s season=%s fixture=%d team=%d fdr=%d",
             user_id, body.season, body.fixture_id, body.team_id, body.fdr)
    return {"fixture_id": body.fixture_id, "team_id": body.team_id, "fdr": body.fdr}


@router.delete("/fdr", status_code=204)
async def delete_override(
    request: Request,
    body: FdrDelete,
    claims: dict[str, Any] = Depends(verify_clerk_token),
) -> None:
    user_id: str = claims.get("sub", "")
    pool = _pool(request)
    with pool.connection() as conn:
        conn.execute(
            "DELETE FROM user_fixture_overrides"
            " WHERE clerk_user_id = %s AND season = %s"
            "   AND fixture_id = %s AND team_id = %s",
            (user_id, body.season, body.fixture_id, body.team_id),
        )
