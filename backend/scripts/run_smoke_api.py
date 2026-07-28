"""Dev/smoke launcher for the Admin API.

Boots fpl_xpts.api with a GW1-scoped, enrichment-light config so an
end-to-end smoke test (Admin Panel -> BFF -> FastAPI -> core -> Postgres)
completes in seconds rather than minutes: live FPL bootstrap + fixtures only,
no Understat, no per-player history fetch, no odds, no ML, small MC.

Usage:
    ADMIN_API_TOKEN=... DATABASE_URL=... PYTHONPATH=src python scripts/run_smoke_api.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import uvicorn

from fpl_xpts.api import create_app
from fpl_xpts.config import AppConfig

SMOKE_CONFIG = AppConfig(
    n_sim=200,
    projection_start_gw=1,
    projection_end_gw=1,
    use_understat_profiles=False,
    use_fpl_player_history=False,
    use_elevenify_projection_file=False,
    use_external_team_projection_files=False,
    use_market_odds=False,
    use_ml_predictions=False,
    write_player_minutes_input_template=False,
)

app = create_app(SMOKE_CONFIG)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
