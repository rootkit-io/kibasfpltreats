from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def _detect_backend_root() -> Path:
    """Locate the backend project root (the directory holding pyproject.toml,
    data/, models/, outputs/) regardless of the process working directory.

    Resolution order:

    1. ``FPL_XPTS_ROOT`` environment variable (containers / non-editable
       installs where the source tree lives elsewhere);
    2. the source checkout root, derived from this file's location
       (``backend/src/fpl_xpts/config.py`` -> ``backend/``) -- covers editable
       installs, ``PYTHONPATH=src`` runs, pytest, and uvicorn from any CWD;
    3. the current working directory (last-resort legacy behaviour).
    """
    env_root = os.environ.get("FPL_XPTS_ROOT")
    if env_root:
        return Path(env_root).resolve()
    candidate = Path(__file__).resolve().parents[2]
    if (candidate / "pyproject.toml").exists() or (candidate / "models").exists():
        return candidate
    return Path.cwd()


#: Absolute path of the backend project root. Every default file location in
#: this codebase is anchored here so that reads/writes do not depend on CWD.
BACKEND_ROOT = _detect_backend_root()
DATA_DIR = BACKEND_ROOT / "data"
MODELS_DIR = BACKEND_ROOT / "models"
OUTPUTS_DIR = BACKEND_ROOT / "outputs"


# Provisional normalized academic league ratings, not validated against FPL outcomes.
LEAGUE_DIFFICULTY_FACTORS = {
    "La_liga": 0.9625,
    "Bundesliga": 0.9769,
    "Serie_A": 0.9741,
    "Ligue_1": 0.9352,
}


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path = DATA_DIR
    raw_dir: Path = DATA_DIR / "raw"
    warehouse_path: Path = DATA_DIR / "fpl_xpts.duckdb"
    fpl_base_url: str = "https://fantasy.premierleague.com/api"
    random_seed: int = 42
    n_sim: int = 10_000
    use_market_odds: bool = True
    odds_api_key_env: str = "ODDS_API_KEY"
    odds_api_sport: str = "soccer_epl"
    odds_api_regions: str = "uk,eu,us"
    odds_api_bookmakers: Optional[str] = None
    projection_start_gw: Optional[int] = 37
    projection_end_gw: Optional[int] = 38
    use_elevenify_projection_file: bool = True
    elevenify_projection_path: Optional[Path] = None
    team_assist_factor: float = 0.73
    form_blend_weight: float = 0.0
    set_piece_xa_weight: float = 0.3
    use_fpl_player_history: bool = True
    max_history_players: int = 900
    use_player_minutes_input_file: bool = True
    player_minutes_input_path: Path = BACKEND_ROOT / "player_minutes_inputs.csv"
    write_player_minutes_input_template: bool = True
    overwrite_player_minutes_input_template: bool = False
    use_external_team_projection_files: bool = False
    external_attack_projection_path: Optional[Path] = None
    external_defense_projection_path: Optional[Path] = None
    use_understat_profiles: bool = True
    include_big_chance_profiles: bool = True
    understat_league: str = "EPL"
    understat_season: Optional[int] = None
    understat_cache_dir: Path = DATA_DIR / "understat"
    use_ml_predictions: bool = False
    ml_model_dir: Path = MODELS_DIR / "position_models"
    minutes_model_path: Path = MODELS_DIR / "minutes_model.pkl"
