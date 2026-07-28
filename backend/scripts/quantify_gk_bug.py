"""Phase 4 shadow test: quantify the historical GK scoring bug in backtest.py.

Temporary Candidate #2 artifact. Runs the backtest's production-formula engine
over the 2022-23 season twice:

- BUGGY:  rulebook=CURRENT_RULEBOOK          (GK goals worth 10 -- the 2024-25+
          value applied to a 2022-23 season; exactly what backtest.py did
          before the Phase 4 fix)
- FIXED:  rulebook=rulebook_for_season("2022-23")  (GK goals worth 6, as they
          were that season)

and reports the season-total production xPts delta for the top-5 goalkeepers.
It also reports which behaviour the *installed* backtest module exhibits by
default, so running this script before and after the fix shows the flip.

Usage: PYTHONPATH=src python scripts/quantify_gk_bug.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import numpy as np
import pandas as pd

import fpl_xpts.backtest as backtest
from fpl_xpts.backtest import (
    add_production_formula_predictions,
    add_rolling_features,
    build_player_gw_frame,
    fit_minutes_models,
    load_vaastav_seasons,
)
from fpl_xpts.rulebook import CURRENT_RULEBOOK, rulebook_for_season

SEASON = "2022-23"
TOP_N = 5


def _prepared_frame() -> pd.DataFrame:
    """Mirror run_holdout_backtest's frame prep for one season."""
    raw = load_vaastav_seasons([SEASON])
    frame = add_rolling_features(build_player_gw_frame(raw))
    minute_models = fit_minutes_models(frame)
    frame["pred_start_prob"] = minute_models["start"].predict_proba(frame)  # type: ignore[union-attr]
    frame["pred_play_prob"] = minute_models["play"].predict_proba(frame)  # type: ignore[union-attr]
    frame["pred_mins_if_play"] = np.clip(minute_models["minutes"].predict(frame), 1.0, 90.0)  # type: ignore[union-attr]
    frame["pred_minutes"] = np.clip(frame["pred_play_prob"] * frame["pred_mins_if_play"], 0.0, 90.0)
    return frame


def _gk_season_totals(scored: pd.DataFrame) -> pd.Series:
    goalkeepers = scored.loc[scored["position"] == "GK"]
    return goalkeepers.groupby("name")["production_xPts"].sum()


def main() -> None:
    frame = _prepared_frame()

    buggy = _gk_season_totals(
        add_production_formula_predictions(frame, rulebook=CURRENT_RULEBOOK)
    )
    fixed = _gk_season_totals(
        add_production_formula_predictions(frame, rulebook=rulebook_for_season(SEASON))
    )

    top = buggy.sort_values(ascending=False).head(TOP_N)
    print(f"\n## GK scoring bug impact -- {SEASON} season backtest (top {TOP_N} GKs)\n")
    print("| Player | Buggy xPts | Fixed xPts | Delta |")
    print("|--------|-----------:|-----------:|------:|")
    for name, buggy_total in top.items():
        fixed_total = float(fixed.get(name, float("nan")))
        delta = fixed_total - float(buggy_total)
        print(f"| {name} | {buggy_total:.3f} | {fixed_total:.3f} | {delta:+.3f} |")

    total_delta = float(fixed.sum() - buggy.sum())
    gk_xg = float(
        add_production_formula_predictions(frame, rulebook=CURRENT_RULEBOOK)
        .loc[lambda f: f["position"] == "GK", "prod_xG"]
        .sum()
    )
    print(f"\nAll-GK season delta: {total_delta:+.3f} xPts "
          f"(= all-GK season xG {gk_xg:.3f} x (6 - 10) points/goal)")

    # Which behaviour does the installed backtest module exhibit by default?
    if hasattr(backtest, "apply_production_formula_by_season"):
        installed = _gk_season_totals(
            backtest.apply_production_formula_by_season(frame)
        )
        matches = "FIXED" if np.allclose(installed.reindex(top.index), fixed.reindex(top.index)) else "BUGGY"
        print(f"\nInstalled backtest default path (era-aware wrapper present): matches {matches}.")
    else:
        print("\nInstalled backtest default path: no era-aware wrapper -- "
              "defaults to CURRENT_RULEBOOK for all seasons (BUGGY).")


if __name__ == "__main__":
    main()
