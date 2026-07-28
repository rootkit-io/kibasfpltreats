from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd

from .projections import (
    _canon_team,
    _lookup_indexed_value,
    add_team_scoring_probabilities,
    find_elevenify_projection_file,
    load_elevenify_projection_tables,
)


LOGGER = logging.getLogger(__name__)


def _load_elevenify_tables() -> tuple[pd.DataFrame, pd.DataFrame]:
    path = find_elevenify_projection_file()
    if path is None or not path.exists():
        return pd.DataFrame(), pd.DataFrame()
    try:
        return load_elevenify_projection_tables(path)
    except Exception as exc:
        LOGGER.warning("Could not load Elevenify projection file %s: %s", path, exc)
        return pd.DataFrame(), pd.DataFrame()


def _check_cs_consistency(team_key: str, gw: int, direct: float | None, derived: float, side: str) -> None:
    if direct is None:
        return
    if abs(float(direct) - float(derived)) > 0.05:
        LOGGER.warning(
            "Elevenify CS differs from Poisson-derived CS for %s GW%s %s: direct=%.3f derived=%.3f",
            team_key,
            gw,
            side,
            float(direct),
            float(derived),
        )


def forecast_fixture_lambdas(fixtures: pd.DataFrame, team_strength: pd.DataFrame) -> pd.DataFrame:
    """Create simple home/away goal lambdas from FPL team strength fields.

    This baseline is intentionally transparent. It can be blended with Understat
    or odds-derived lambdas when those adapters are configured.
    """
    if fixtures.empty:
        return fixtures.copy()

    strength = team_strength.set_index("id")
    # Calibrated from Vaastav/FPL expected-goals history for 2023-24 and 2024-25.
    # 2022-23 expected-goal rows are noticeably lower in the public archive, so the
    # recent two-season baseline is a better default until odds are wired in.
    league_goal_avg = 1.51
    home_adv = 1.10
    elevenify_goals, elevenify_cs = _load_elevenify_tables()
    team_keys = team_strength[["id", "name"]].copy()
    team_keys["team_key"] = team_keys["name"].apply(_canon_team)
    key_by_id = dict(zip(team_keys["id"], team_keys["team_key"]))

    rows = []
    for _, fx in fixtures.iterrows():
        home_id = int(fx["team_h"])
        away_id = int(fx["team_a"])
        home = strength.loc[home_id]
        away = strength.loc[away_id]

        h_att = float(home.get("strength_attack_home", 1000)) / 1000.0
        a_att = float(away.get("strength_attack_away", 1000)) / 1000.0
        h_def_weak = 1000.0 / max(float(home.get("strength_defence_home", 1000)), 1.0)
        a_def_weak = 1000.0 / max(float(away.get("strength_defence_away", 1000)), 1.0)

        lam_h = league_goal_avg * home_adv * h_att * a_def_weak
        lam_a = league_goal_avg / home_adv * a_att * h_def_weak
        gw = int(fx["event"]) if not pd.isna(fx.get("event")) else None
        home_key = key_by_id.get(home_id)
        away_key = key_by_id.get(away_id)
        source = "fpl_strength_fallback"

        if gw is not None:
            home_elevenify_goal = _lookup_indexed_value(elevenify_goals, home_key, gw, "projected_goals")
            away_elevenify_goal = _lookup_indexed_value(elevenify_goals, away_key, gw, "projected_goals")
            if home_elevenify_goal is not None:
                lam_h = home_elevenify_goal
                source = "elevenify_team_projection"
            if away_elevenify_goal is not None:
                lam_a = away_elevenify_goal
                source = "elevenify_team_projection"

        home_xg = float(np.clip(lam_h, 0.15, 4.0))
        away_xg = float(np.clip(lam_a, 0.15, 4.0))
        home_cs_derived = math.exp(-away_xg)
        away_cs_derived = math.exp(-home_xg)
        home_cs = home_cs_derived
        away_cs = away_cs_derived

        if gw is not None:
            home_elevenify_cs = _lookup_indexed_value(elevenify_cs, home_key, gw, "cs_prob")
            away_elevenify_cs = _lookup_indexed_value(elevenify_cs, away_key, gw, "cs_prob")
            _check_cs_consistency(str(home_key), gw, home_elevenify_cs, home_cs_derived, "home")
            _check_cs_consistency(str(away_key), gw, away_elevenify_cs, away_cs_derived, "away")
            if home_elevenify_cs is not None:
                home_cs = float(np.clip(home_elevenify_cs, 0.01, 0.90))
                source = "elevenify_team_projection"
            if away_elevenify_cs is not None:
                away_cs = float(np.clip(away_elevenify_cs, 0.01, 0.90))
                source = "elevenify_team_projection"

        rows.append(
            {
                **fx.to_dict(),
                "home_xg": home_xg,
                "away_xg": away_xg,
                "home_xa": float(np.clip(home_xg * 0.72, 0.0, 4.0)),
                "away_xa": float(np.clip(away_xg * 0.72, 0.0, 4.0)),
                "home_cs_prob": home_cs,
                "away_cs_prob": away_cs,
                "home_cs_prob_poisson": home_cs_derived,
                "away_cs_prob_poisson": away_cs_derived,
                "projection_source": source,
            }
        )
    return add_team_scoring_probabilities(pd.DataFrame(rows))
