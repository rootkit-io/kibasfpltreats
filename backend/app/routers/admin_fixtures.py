"""Admin-only fixture difficulty overrides."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator

try:
    from fpl_xpts.api import require_admin_token
    from fpl_xpts.api_public import _require_connection
except ModuleNotFoundError:  # pragma: no cover - source checkout without install
    import sys
    from pathlib import Path

    _SRC = Path(__file__).resolve().parents[2] / "src"
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    from fpl_xpts.api import require_admin_token
    from fpl_xpts.api_public import _require_connection


router = APIRouter(prefix="/api/v1/admin", tags=["admin-fixtures"])


class FdrOverrideRequest(BaseModel):
    """Apply one FDR value to a team for one fixture or every pairing fixture."""

    model_config = ConfigDict(extra="forbid", strict=True)

    target_team_id: int = Field(..., ge=1)
    fdr_override: int | None = Field(..., ge=1, le=5)
    opponent_team_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def opponent_must_differ_from_target(self) -> "FdrOverrideRequest":
        if self.opponent_team_id == self.target_team_id:
            raise ValueError("opponent_team_id must differ from target_team_id")
        return self


@router.patch("/fixtures/fdr", dependencies=[Depends(require_admin_token)])
async def set_fixture_fdr_override(
    payload: FdrOverrideRequest,
    season: str = Query(..., pattern=r"^\d{4}$", min_length=4, max_length=4),
    fixture_id: int | None = Query(default=None, ge=1),
    connection: Any = Depends(_require_connection),
) -> dict[str, Any]:
    """Set or clear a target team's FDR override.

    Supplying ``opponent_team_id`` applies the override to every fixture for
    the pair in the selected season. Otherwise, ``fixture_id`` is required to
    identify exactly one FPL fixture. ``CASE`` selects the target side's
    override column inside the same SQL update.
    """
    if payload.opponent_team_id is None and fixture_id is None:
        raise HTTPException(
            status_code=422,
            detail="fixture_id is required when opponent_team_id is omitted",
        )
    if payload.opponent_team_id is not None and fixture_id is not None:
        raise HTTPException(
            status_code=422,
            detail="provide fixture_id or opponent_team_id, not both",
        )

    if payload.opponent_team_id is not None:
        cursor = connection.execute(
            """
            UPDATE fixtures AS f
            SET team_h_fdr_override = CASE
                    WHEN f.home_team_id = %s THEN %s
                    ELSE f.team_h_fdr_override
                END,
                team_a_fdr_override = CASE
                    WHEN f.away_team_id = %s THEN %s
                    ELSE f.team_a_fdr_override
                END
            WHERE f.season = %s
              AND (
                  (f.home_team_id = %s AND f.away_team_id = %s)
                  OR (f.home_team_id = %s AND f.away_team_id = %s)
              )
            RETURNING f.id
            """,
            (
                payload.target_team_id,
                payload.fdr_override,
                payload.target_team_id,
                payload.fdr_override,
                season,
                payload.target_team_id,
                payload.opponent_team_id,
                payload.opponent_team_id,
                payload.target_team_id,
            ),
        )
    else:
        cursor = connection.execute(
            """
            UPDATE fixtures AS f
            SET team_h_fdr_override = CASE
                    WHEN f.home_team_id = %s THEN %s
                    ELSE f.team_h_fdr_override
                END,
                team_a_fdr_override = CASE
                    WHEN f.away_team_id = %s THEN %s
                    ELSE f.team_a_fdr_override
                END
            WHERE f.season = %s
              AND f.id = %s
              AND (f.home_team_id = %s OR f.away_team_id = %s)
            RETURNING f.id
            """,
            (
                payload.target_team_id,
                payload.fdr_override,
                payload.target_team_id,
                payload.fdr_override,
                season,
                fixture_id,
                payload.target_team_id,
                payload.target_team_id,
            ),
        )

    updated_fixture_ids = [int(row[0]) for row in cursor.fetchall()]
    if not updated_fixture_ids:
        raise HTTPException(
            status_code=404,
            detail="no fixture matched the supplied season, team, and scope",
        )

    return {
        "season": season,
        "target_team_id": payload.target_team_id,
        "opponent_team_id": payload.opponent_team_id,
        "fixture_id": fixture_id,
        "fdr_override": payload.fdr_override,
        "updated_fixture_ids": updated_fixture_ids,
        "updated": len(updated_fixture_ids),
    }
