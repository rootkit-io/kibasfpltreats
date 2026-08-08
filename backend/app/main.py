"""Uvicorn entrypoint for the Admin API.

Run from inside ``backend/`` (or in the container)::

    uvicorn app.main:app --host 0.0.0.0 --port 8000

This is a thin alias around :func:`fpl_xpts.api.create_app`; all routes,
startup wiring (minutes-model bundle, DB pool) and configuration live in
``fpl_xpts.api``. Requires ``ADMIN_API_TOKEN`` (admin routes fail closed
without it) and optionally ``DATABASE_URL`` for persistence.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from fpl_xpts.api import create_app
except ModuleNotFoundError:  # pragma: no cover - source checkout without install
    _SRC = Path(__file__).resolve().parents[1] / "src"
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    from fpl_xpts.api import create_app

app = create_app()

# Precomputed-CSV ingestion lives outside ``fpl_xpts`` (that package stays a
# pure modelling library), so it is mounted here rather than inside
# ``create_app``.
try:
    from app.routers.admin_fixtures import router as admin_fixtures_router
    from app.routers.admin_dimensions import router as dimensions_router
    from app.routers.admin_projections import router as ingest_router
    from app.routers.public_fixtures import router as fixtures_router
    from app.routers.public_simulations import router as simulations_router
    from app.routers.user_fdr import router as user_fdr_router
except ImportError:  # pragma: no cover - executed as a top-level module
    from .routers.admin_fixtures import router as admin_fixtures_router
    from .routers.admin_dimensions import router as dimensions_router
    from .routers.admin_projections import router as ingest_router
    from .routers.public_fixtures import router as fixtures_router
    from .routers.public_simulations import router as simulations_router
    from .routers.user_fdr import router as user_fdr_router

app.include_router(ingest_router)
# Season dimension seeding, independent of any projection run.
app.include_router(dimensions_router)
app.include_router(admin_fixtures_router)
# Public Monte Carlo read surface (Clerk-gated + rate limited, same as the
# projections route it sits beside).
app.include_router(simulations_router)
app.include_router(fixtures_router)
# Per-user FDR overrides -- Clerk-authed, no admin rights required.
app.include_router(user_fdr_router)
