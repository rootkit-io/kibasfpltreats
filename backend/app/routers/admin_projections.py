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

from .ingest_csv import (
    MC_COLUMN_MAP,
    MC_REQUIRED,
    UNMAPPED_MC,
    UNMAPPED_WEEKLY,
    WEEKLY_COLUMN_MAP,
    WEEKLY_REQUIRED,
    IngestValidationError,
    build_identity_index,
    check_positions,
    check_symmetry,
    check_value_ranges,
    read_projection_csv,
    require_columns,
    resolve_identities,
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

#: Refuse to stage a run when identity resolution is this bad -- a wholesale
#: failure means the wrong season or an unpopulated dimension table, and
#: staging a mostly-empty run would be worse than refusing.
MAX_UNMATCHED_FRACTION = 0.02


def _fail(message: str, errors: list[dict] | None = None, status: int = 400):
    raise HTTPException(status_code=status, detail={"message": message, "errors": errors or []})


@router.post("/admin/projections/ingest-csvs", dependencies=[Depends(require_admin_token)])
async def ingest_precomputed_run(
    request: Request,
    season: str = Form(..., description="e.g. '2627' -- absent from both CSVs"),
    weekly_file: UploadFile = File(..., description=WEEKLY_FILENAME),
    mc_file: UploadFile = File(..., description=MC_FILENAME),
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
    except IngestValidationError as exc:
        logger.info("ingest preflight rejected: %s", exc.message)
        raise HTTPException(status_code=400, detail=exc.as_detail()) from exc

    with pool.connection() as conn:  # one transaction: commit on exit, rollback on raise
        cur = conn.cursor()

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

    logger.info(
        "ingested draft run %s season=%s gws=%s weekly=%d sims=%d unmatched=%d",
        run_id, season, report.gameweeks, len(weekly_rows), len(mc_rows),
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
        "unmatched_players": resolution.unmatched,
        "dropped_columns": {"weekly": list(UNMAPPED_WEEKLY), "monte_carlo": list(UNMAPPED_MC)},
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
            int(record["team_id"]), str(record["Pos"]).upper(),
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
