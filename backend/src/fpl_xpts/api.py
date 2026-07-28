"""Admin Panel REST API -- the in-memory adapter from ADR-0001.

Run with::

    uvicorn fpl_xpts.api:app --host 127.0.0.1 --port 8000

Requires the ``api`` extra (``pip install -e ".[api]"``) and the
``ADMIN_API_TOKEN`` environment variable (the endpoint fails closed without
it).

Design rules (see ``docs/adr/0001-isolate-minutes-engine-and-io.md``):

- the minutes model bundle is loaded **exactly once**, at server startup,
  into ``app.state`` -- never per request;
- request payloads are validated against the minutes contracts inside the
  handler, so contract violations map to **400 with field errors**, not 500;
- the run itself is the five-argument in-memory call: contract states in,
  pre-loaded bundle, ``write_snapshot=False`` -- zero disk I/O on the
  request path.
"""

from __future__ import annotations

import json
import os
import secrets
from contextlib import asynccontextmanager
from typing import Any, Iterator

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Header, Query, Request
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, ValidationError

from .api_public import public_router
from .config import AppConfig
from .minutes_contract import ManualMinutesError, resolve_minutes_run_inputs
from .minutes_model import load_minutes_bundle
from .pipeline import run_live_projection
from .projection_repository import (
    ProjectionRepository,
    RunId,
    RunMetadata,
    RunRecord,
    RunSource,
)

ADMIN_TOKEN_ENV = "ADMIN_API_TOKEN"
ADMIN_TOKEN_HEADER = "X-Admin-Token"
DATABASE_URL_ENV = "DATABASE_URL"

router = APIRouter(prefix="/api/v1")


# ------------------------------------------------------------------- auth


def require_admin_token(
    x_admin_token: str | None = Header(default=None, alias=ADMIN_TOKEN_HEADER),
) -> None:
    """Admin auth placeholder: shared-secret header, fail-closed.

    Replace with real admin authentication before multi-user exposure; the
    dependency seam stays the same. With no token configured the endpoint is
    unavailable (503) rather than open.
    """
    expected = os.environ.get(ADMIN_TOKEN_ENV)
    if not expected:
        raise HTTPException(
            status_code=503,
            detail=f"admin API token not configured; set {ADMIN_TOKEN_ENV}",
        )
    if x_admin_token is None or not secrets.compare_digest(x_admin_token, expected):
        raise HTTPException(status_code=401, detail="invalid or missing admin token")


# ----------------------------------------------------- repository injection


def get_optional_repository(request: Request) -> Iterator[ProjectionRepository | None]:
    """Request-scoped repository, or None when persistence is unconfigured.

    A connection is checked out of the pool for the duration of the request
    and returned on completion. Tests override THIS dependency with the
    in-memory fake; ``get_repository`` composes on top of it, so one
    override covers every persistence-touching route.
    """
    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        yield None
        return
    from .postgres_repository import PostgresProjectionRepository

    with pool.connection() as connection:
        yield PostgresProjectionRepository(connection=connection)


def get_repository(
    repository: ProjectionRepository | None = Depends(get_optional_repository),
) -> ProjectionRepository:
    """Strict variant: persistence is required (fail-closed 503 otherwise)."""
    if repository is None:
        raise HTTPException(
            status_code=503,
            detail=f"persistence not configured; set {DATABASE_URL_ENV}",
        )
    return repository


# -------------------------------------------------------------- request


class ProjectionRunRequest(BaseModel):
    """Admin Panel payload.

    ``manual_minutes`` (flat list, or list of layers -- later layers win)
    and ``overrides`` are kept loosely typed here on purpose: they are
    validated against the minutes contracts *inside the handler*, so
    violations surface as 400 with specific field errors instead of the
    framework's default handling.
    """

    model_config = ConfigDict(extra="forbid")

    manual_minutes: list[Any] = []
    overrides: list[Any] = []
    include_mc: bool = False
    save_as_draft: bool = False
    season: str | None = None  # required when save_as_draft (FPL ids recycle)
    notes: str | None = None


def _frame_records(frame: pd.DataFrame) -> list[dict]:
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def _contract_errors(exc: ValidationError) -> list[dict]:
    """Flatten pydantic errors to a JSON-safe shape (loc/msg/type only).

    ``exc.errors()`` can embed raw exception objects under ``ctx``, which are
    not JSON-serializable and would turn a client error into a 500.
    """
    return [
        {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
        for error in exc.errors(include_url=False, include_input=False)
    ]


# ------------------------------------------------------------------ routes


@router.get("/health")
def health(request: Request) -> dict:
    """Open liveness probe: no auth, no compute."""
    return {
        "status": "ok",
        "minutes_model_loaded": request.app.state.minutes_bundle is not None,
    }


@router.post("/admin/projections/run", dependencies=[Depends(require_admin_token)])
def run_projection(
    request: Request,
    payload: ProjectionRunRequest,
    repository: ProjectionRepository | None = Depends(get_optional_repository),
) -> dict:
    config: AppConfig = request.app.state.config

    # Fail fast on persistence preconditions BEFORE burning compute.
    if payload.save_as_draft:
        if repository is None:
            raise HTTPException(
                status_code=503,
                detail=f"persistence not configured; set {DATABASE_URL_ENV}",
            )
        if not payload.season:
            raise HTTPException(
                status_code=400,
                detail={"message": "season is required when save_as_draft is true"},
            )

    # Validate against the minutes contracts BEFORE any work: a payload that
    # violates the contract is the client's error (400), never a 500.
    try:
        inputs = resolve_minutes_run_inputs(
            config,
            manual_states=payload.manual_minutes,
            override_states=payload.overrides,
        )
    except ValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "minutes payload violates the contract",
                "errors": _contract_errors(exc),
            },
        ) from exc
    except ManualMinutesError as exc:
        raise HTTPException(status_code=400, detail={"message": str(exc)}) from exc

    # The Phase 3 in-memory call: validated states, the bundle cached at
    # startup, and no snapshot write. Zero disk I/O on the request path.
    results = run_live_projection(
        config=config,
        include_mc=payload.include_mc,
        manual_minutes_states=inputs.manual_inputs,
        minute_override_states=inputs.overrides,
        minutes_model_bundle=request.app.state.minutes_bundle,
        write_snapshot=False,
    )

    run_id: str | None = None
    if payload.save_as_draft:
        metadata = RunMetadata(
            season=payload.season,
            source=RunSource.ADMIN_API,
            gw_start=config.projection_start_gw,
            gw_end=config.projection_end_gw,
            n_sim=config.n_sim,
            include_mc=payload.include_mc,
            minutes_model_loaded=request.app.state.minutes_bundle is not None,
            manual_minutes_layers=len(inputs.manual_inputs),
            override_count=len(inputs.overrides),
            inputs={
                "manual_minutes_layers": len(inputs.manual_inputs),
                "override_count": len(inputs.overrides),
            },
            notes=payload.notes,
        )
        run_id = str(repository.save_run(results, metadata))

    return {
        "run_id": run_id,
        "minutes_model_loaded": request.app.state.minutes_bundle is not None,
        "include_mc": payload.include_mc,
        "manual_minutes_layers": len(inputs.manual_inputs),
        "override_count": len(inputs.overrides),
        "tables": {
            name: _frame_records(frame)
            for name, frame in results.items()
            if hasattr(frame, "to_json")
        },
    }


@router.post(
    "/admin/projections/runs/{run_id}/publish",
    dependencies=[Depends(require_admin_token)],
)
def publish_projection_run(
    run_id: str,
    repository: ProjectionRepository = Depends(get_repository),
) -> dict:
    """Finalize a saved draft: the dashboard's published_* views flip to it.

    404 for unknown runs; 409 for archived runs (state conflict, per the
    repository contract).
    """
    try:
        repository.publish_run(RunId(run_id))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    record = repository.get_run(RunId(run_id))
    return {
        "run_id": run_id,
        "status": record.status.value,
        "published_at": record.published_at.isoformat(),
    }


# ------------------------------------------------------- run history (read)

#: The result tables the preview grids re-hydrate from. Fixed set so the
#: fake (which stores every saved frame) and Postgres (which reloads exactly
#: the persisted facts) serve identical response shapes.
_PREVIEW_TABLE_KEYS = ("weekly", "player_fixture", "monte_carlo", "fixtures_forecast")


def _gameweek_label(metadata: RunMetadata) -> str | None:
    """Human-readable GW scope: '1', '1-38', or None when unscoped."""
    start, end = metadata.gw_start, metadata.gw_end
    if start is None and end is None:
        return None
    if start is not None and end is not None:
        return str(start) if start == end else f"{start}-{end}"
    return str(start if start is not None else end)


def _run_summary(record: RunRecord) -> dict:
    """The history-list projection of a run: metadata only, no fact tables."""
    metadata = record.metadata
    return {
        "run_id": str(record.run_id),
        "season": metadata.season,
        "gameweek": _gameweek_label(metadata),
        "gw_start": metadata.gw_start,
        "gw_end": metadata.gw_end,
        "status": record.status.value,
        "created_at": record.created_at.isoformat(),
        "published_at": (
            record.published_at.isoformat() if record.published_at else None
        ),
        "source": metadata.source.value,
        "n_sim": metadata.n_sim,
        "include_mc": metadata.include_mc,
        "minutes_model_loaded": metadata.minutes_model_loaded,
        "manual_minutes_layers": metadata.manual_minutes_layers,
        "override_count": metadata.override_count,
        "notes": metadata.notes,
    }


@router.get(
    "/admin/projections/runs",
    dependencies=[Depends(require_admin_token)],
)
def list_projection_runs(
    limit: int = Query(default=20, ge=1, le=100),
    repository: ProjectionRepository = Depends(get_repository),
) -> dict:
    """Run history for the Admin Panel: most recent first, metadata only.

    Fact tables are deliberately excluded here (they run to thousands of
    rows per run); the client fetches one run's tables on selection via
    ``GET /admin/projections/runs/{run_id}``.
    """
    return {"runs": [_run_summary(record) for record in repository.list_runs(limit=limit)]}


@router.get(
    "/admin/projections/runs/{run_id}",
    dependencies=[Depends(require_admin_token)],
)
def get_projection_run(
    run_id: str,
    repository: ProjectionRepository = Depends(get_repository),
) -> dict:
    """One run's full state: header metadata plus the persisted result
    tables (same ``tables`` shape as the run endpoint's preview), so the
    Admin Panel can re-hydrate the preview grids for any historical run."""
    record = repository.get_run(RunId(run_id))
    if record is None:
        raise HTTPException(status_code=404, detail=f"run not found: {run_id}")

    tables = repository.load_run_tables(RunId(run_id)) or {}
    return {
        **_run_summary(record),
        "tables": {
            name: _frame_records(frame)
            for name, frame in tables.items()
            if name in _PREVIEW_TABLE_KEYS and hasattr(frame, "to_json")
        },
    }


# --------------------------------------------------------------- app factory


def _load_startup_bundle(config: AppConfig) -> dict | None:
    if config.minutes_model_path.exists():
        return load_minutes_bundle(config.minutes_model_path)
    return None


def _open_db_pool():
    """Open the connection pool from DATABASE_URL, or None when unset.

    Without a database the API still serves previews; persistence endpoints
    fail closed with 503 via ``get_repository``.
    """
    conninfo = os.environ.get(DATABASE_URL_ENV)
    if not conninfo:
        return None
    from psycopg_pool import ConnectionPool

    return ConnectionPool(conninfo, min_size=1, max_size=4, open=True)


def create_app(config: AppConfig | None = None) -> FastAPI:
    """Build the Admin API app. Bundle and DB pool initialize in the lifespan."""
    app_config = config or AppConfig()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # ADR-0001: load the minutes model bundle exactly once, at startup.
        app.state.config = app_config
        app.state.minutes_bundle = _load_startup_bundle(app_config)
        app.state.db_pool = _open_db_pool()
        # Phase 14: Redis connection pool for caching + rate limiting.
        # Opened here so it shuts down cleanly; None when REDIS_URL is unset.
        from .redis_utils import close_redis_pool, open_redis_pool
        app.state.redis_pool = open_redis_pool()
        yield
        if app.state.db_pool is not None:
            app.state.db_pool.close()
        app.state.db_pool = None
        await close_redis_pool(app.state.redis_pool)
        app.state.redis_pool = None
        app.state.minutes_bundle = None

    app = FastAPI(title="fpl-xpts Admin API", lifespan=lifespan)
    app.include_router(router)
    # Public read surface (published_* views; deliberately unauthenticated).
    app.include_router(public_router)
    return app


app = create_app()
