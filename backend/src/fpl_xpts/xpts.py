from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .bonus import expected_capped_poisson
from .features import build_player_rates, numeric
from .minutes import estimate_start_and_minutes
from .rulebook import CURRENT_RULEBOOK, Rulebook


def _allocate_minutes_by_priority(minutes: pd.Series, priority: pd.Series, target: float) -> pd.Series:
    out = pd.Series(0.0, index=minutes.index)
    remaining = float(target)
    ordered = priority.sort_values(ascending=False).index
    for idx in ordered:
        if remaining <= 0:
            break
        value = min(float(minutes.loc[idx]), remaining)
        out.loc[idx] = value
        remaining -= value
    return out


def _expected_concede_penalty(lambda_gc: float) -> float:
    lam = max(float(lambda_gc), 0.0)
    return sum(_poisson_prob_ge(threshold, lam) for threshold in range(2, 31, 2))


def _expected_save_points(mu_saves: float) -> float:
    mu = max(float(mu_saves), 0.0)
    if mu <= 0:
        return 0.0
    # FPL awards 1 point per full 3 saves. The tail-sum form converges quickly
    # at realistic goalkeeper save means.
    total = 0.0
    pmf = math.exp(-mu)
    cdf = pmf
    k = 0
    for threshold in range(3, 31, 3):
        while k < threshold - 1:
            k += 1
            pmf *= mu / k
            cdf += pmf
        total += max(0.0, 1.0 - cdf)
    return float(total)


def _poisson_prob_ge(threshold: int, mu: float) -> float:
    if threshold <= 0:
        return 1.0
    mu = max(float(mu), 0.0)
    if mu == 0:
        return 0.0
    term = math.exp(-mu)
    cdf = term
    for k in range(1, threshold):
        term *= mu / k
        cdf += term
    return float(max(0.0, min(1.0, 1.0 - cdf)))


def _minute_ev_components(
    mins: float,
    start_probability: float | None,
    play_probability: float | None,
    likely_minutes: float | None,
    pos: str,
    cs_prob: float,
    opponent_lambda: float,
    saves90: float,
    defcon90: float,
    yc_rate: float,
    rc_rate: float,
    rulebook: Rulebook = CURRENT_RULEBOOK,
) -> dict[str, float]:
    state_minutes = mins if likely_minutes is None or pd.isna(likely_minutes) else likely_minutes
    vals, probs = estimate_minute_states(
        state_minutes,
        start_probability=start_probability,
        play_probability=play_probability,
    )
    app = cs = saves = defcon = cards = concede = p_cs_eligible = 0.0
    thr = rulebook.defcon_threshold_for(pos)
    for minute_value, prob in zip(vals, probs):
        m = float(minute_value)
        p = float(prob)
        app += p * rulebook.appearance_points_for(m)
        if m >= 60:
            p_cs_eligible += p
            cs += p * rulebook.clean_sheet_points_for(pos) * cs_prob
        if pos == "GK":
            saves += p * _expected_save_points(float(saves90) * m / 90.0)
        if thr is not None:
            defcon += p * 2.0 * _poisson_prob_ge(thr, float(defcon90) * m / 90.0)
        cards += p * (-float(yc_rate) * m / 90.0 - 3.0 * float(rc_rate) * m / 90.0)
        if pos in {"GK", "DEF"} and m > 0:
            concede += p * -_expected_concede_penalty(float(opponent_lambda) * m / 90.0)
    return {
        "app": app,
        "cs": cs,
        "saves": saves,
        "defcon": defcon,
        "cards": cards,
        "concede": concede,
        "p_cs_eligible": p_cs_eligible,
    }


def estimate_minute_states(
    expected_minutes: float,
    start_probability: float | None = None,
    play_probability: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    from .minutes import minute_outcomes

    return minute_outcomes(expected_minutes, start_probability=start_probability, play_probability=play_probability)


def build_player_fixture_forecast(
    players: pd.DataFrame,
    fixtures_forecast: pd.DataFrame,
    history_by_player: dict[int, pd.DataFrame] | None = None,
    form_blend_weight: float = 0.3,
    set_piece_xa_weight: float = 0.3,
    rulebook: Rulebook = CURRENT_RULEBOOK,
) -> pd.DataFrame:
    rates = build_player_rates(players, form_blend_weight=form_blend_weight)
    history_by_player = history_by_player or {}
    set_piece_boost = float(set_piece_xa_weight)

    rows = []
    for _, fixture in fixtures_forecast.iterrows():
        for side in ["home", "away"]:
            team_id = int(fixture["team_h"] if side == "home" else fixture["team_a"])
            opponent_lambda = float(fixture["away_xg"] if side == "home" else fixture["home_xg"])
            team_lambda = float(fixture["home_xg"] if side == "home" else fixture["away_xg"])
            team_assist_lambda = float(fixture.get("home_xa" if side == "home" else "away_xa", team_lambda * 0.72))
            cs_prob = float(fixture["home_cs_prob"] if side == "home" else fixture["away_cs_prob"])

            team_players = rates.loc[rates["team"] == team_id].copy()
            if team_players.empty:
                continue

            minute_estimates = team_players.apply(
                lambda row: estimate_start_and_minutes(row, history_by_player.get(int(row["id"]))),
                axis=1,
            )
            team_players["start_probability"] = minute_estimates.apply(lambda item: float(item[0]))
            team_players["exp_mins"] = minute_estimates.apply(lambda item: float(item[1]))
            team_players["play_probability"] = np.where(team_players["exp_mins"] > 0, 1.0, 0.0)
            team_players["likely_minutes"] = team_players["exp_mins"]
            selected = pd.to_numeric(team_players.get("selected_by_percent", 0.0), errors="coerce").fillna(0.0)
            ppg = pd.to_numeric(team_players.get("points_per_game", 0.0), errors="coerce").fillna(0.0)
            starts_per_90 = pd.to_numeric(team_players.get("starts_per_90", 0.0), errors="coerce").fillna(0.0)
            priority = team_players["exp_mins"] * (1.0 + selected / 100.0) * (1.0 + ppg / 10.0) * (1.0 + starts_per_90)
            gk_mask = team_players["position"] == "GK"
            if float(team_players.loc[gk_mask, "exp_mins"].sum()) > 90.0:
                team_players.loc[gk_mask, "exp_mins"] = _allocate_minutes_by_priority(
                    team_players.loc[gk_mask, "exp_mins"],
                    priority.loc[gk_mask],
                    90.0,
                )
            if float(team_players.loc[~gk_mask, "exp_mins"].sum()) > 900.0:
                team_players.loc[~gk_mask, "exp_mins"] = _allocate_minutes_by_priority(
                    team_players.loc[~gk_mask, "exp_mins"],
                    priority.loc[~gk_mask],
                    900.0,
                )

            team_players["mins_frac"] = team_players["exp_mins"].clip(0, 90) / 90.0
            team_players["xg_weight"] = team_players["expected_goals_per_90_shrunk"] * team_players["mins_frac"]
            team_players["xa_weight"] = (
                team_players["expected_assists_per_90_shrunk"]
                * (1.0 + set_piece_boost * pd.to_numeric(team_players.get("set_piece_share", 0.0), errors="coerce").fillna(0.0))
                * team_players["mins_frac"]
            )
            xg_sum = float(team_players["xg_weight"].sum())
            xa_sum = float(team_players["xa_weight"].sum())
            pen_share_sum = float(team_players.get("penalty_share", pd.Series(0.0, index=team_players.index)).sum())
            team_penalty_xg = min(rulebook.max_team_penalty_xg, team_lambda * 0.07) if pen_share_sum > 0 else 0.0
            open_play_lambda = max(team_lambda - team_penalty_xg, 0.0)

            for _, player in team_players.iterrows():
                pos = str(player["position"])
                mins = float(player["exp_mins"])
                if mins <= 0:
                    xg = xa = xpts = 0.0
                    app = goals = assists = cs = saves = defcon = cards = concede = bonus = pen_miss = pen_xg = 0.0
                else:
                    pen_xg = team_penalty_xg * float(player.get("penalty_share", 0.0) or 0.0) / pen_share_sum if pen_share_sum > 0 else 0.0
                    xg = open_play_lambda * float(player["xg_weight"]) / xg_sum if xg_sum > 0 else 0.0
                    xg += pen_xg
                    team_xa = team_assist_lambda
                    xa = team_xa * float(player["xa_weight"]) / xa_sum if xa_sum > 0 else 0.0
                    minute_ev = _minute_ev_components(
                        mins=mins,
                        start_probability=float(player.get("start_probability", np.nan)),
                        play_probability=float(player.get("play_probability", np.nan)),
                        likely_minutes=float(player.get("likely_minutes", np.nan)),
                        pos=pos,
                        cs_prob=cs_prob,
                        opponent_lambda=opponent_lambda,
                        saves90=float(player.get("saves_per_90_shrunk", 0.0)),
                        defcon90=float(player.get("defensive_contribution_per_90_shrunk", 0.0)),
                        yc_rate=float(player.get("yc_rate90", 0.0)),
                        rc_rate=float(player.get("rc_rate90", 0.0)),
                        rulebook=rulebook,
                    )
                    app = minute_ev["app"]
                    goals = xg * rulebook.goal_points_for(pos)
                    assists = xa * 3.0
                    cs = minute_ev["cs"]
                    saves = minute_ev["saves"]
                    defcon = minute_ev["defcon"]
                    cards = minute_ev["cards"]
                    concede = minute_ev["concede"]
                    pen_attempts = pen_xg / rulebook.penalty_xg_per_attempt if pen_xg > 0 else 0.0
                    pen_miss = rulebook.penalty_miss_points * pen_attempts * (1.0 - rulebook.penalty_xg_per_attempt)
                    p_defcon_hit = max(0.0, min(1.0, defcon / 2.0))
                    bonus_lambda = (
                        xg * rulebook.bonus_per_goal
                        + xa * rulebook.bonus_per_assist
                        + (cs_prob * minute_ev["p_cs_eligible"] * rulebook.bonus_cs_gk_def if pos in {"GK", "DEF"} else 0.0)
                        + saves * rulebook.bonus_per_save3
                        + p_defcon_hit * rulebook.bonus_per_defcon
                    )
                    bonus = expected_capped_poisson(bonus_lambda, cap=3)
                    xpts = app + goals + assists + cs + saves + defcon + cards + pen_miss + concede + bonus
                team_xa = team_assist_lambda

                rows.append(
                    {
                        "fixture": fixture.get("id"),
                        "event": fixture.get("event"),
                        "kickoff_time": fixture.get("kickoff_time"),
                        "team": team_id,
                        "opponent": int(fixture["team_a"] if side == "home" else fixture["team_h"]),
                        "was_home": side == "home",
                        "player_id": int(player["id"]),
                        "web_name": player.get("web_name"),
                        "position": pos,
                        "expected_minutes": mins,
                        "likely_minutes": float(player.get("likely_minutes", mins) or 0.0),
                        "start_probability": float(player.get("start_probability", 0.0) or 0.0),
                        "play_probability": float(player.get("play_probability", 0.0) or 0.0),
                        "team_xg": team_lambda,
                        "team_xa": team_xa,
                        "opponent_xg": opponent_lambda,
                        "cs_prob": cs_prob,
                        "xG": float(xg),
                        "xA": float(xa),
                        "xGA_exp": float(xg + xa),
                        "xPts": float(xpts),
                        "P1_GA": float(1.0 - math.exp(-(xg + xa))),
                        "AppPts": float(app),
                        "GoalPts": float(goals),
                        "AssistPts": float(assists),
                        "CSPts": float(cs),
                        "SavePts": float(saves),
                        "DefconPts": float(defcon),
                        "CardPts": float(cards),
                        "PenMissPts": float(pen_miss),
                        "ConcedePts": float(concede),
                        "BonusPts": float(bonus),
                        "pen_xG": float(pen_xg),
                        "penalty_share": float(player.get("penalty_share", 0.0) or 0.0),
                        "set_piece_share": float(player.get("set_piece_share", 0.0) or 0.0),
                        "xg90_shrunk": float(player.get("expected_goals_per_90_shrunk", 0.0)),
                        "xa90_shrunk": float(player.get("expected_assists_per_90_shrunk", 0.0)),
                        "prior_based": bool(player.get("prior_based", False)),
                        "prior_source": str(player.get("prior_source", "observed_pl")),
                        "defcon90": float(player.get("defensive_contribution_per_90_shrunk", 0.0)),
                        "saves90": float(player.get("saves_per_90_shrunk", 0.0)),
                        "yc_rate": float(player.get("yc_rate90", 0.0)),
                        "rc_rate": float(player.get("rc_rate90", 0.0)),
                        "understat_profile_matched": bool(player.get("understat_profile_matched", False)),
                        "understat_blend_weight": float(player.get("understat_blend_weight", 0.0) or 0.0),
                        "understat_shots90": float(player.get("understat_shots90", 0.0) or 0.0),
                        "understat_chances_created90": float(player.get("understat_chances_created90", 0.0) or 0.0),
                        "understat_xG_per_shot": float(player.get("understat_xG_per_shot", 0.0) or 0.0),
                        "understat_xA_per_chance": float(player.get("understat_xA_per_chance", 0.0) or 0.0),
                        "understat_big_chance_received90": float(player.get("understat_big_chance_received90", 0.0) or 0.0),
                        "understat_big_chance_created90": float(player.get("understat_big_chance_created90", 0.0) or 0.0),
                    }
                )
    return pd.DataFrame(rows)


def recompute_player_fixture_components(
    player_fixture: pd.DataFrame,
    set_piece_xa_weight: float = 0.3,
    rulebook: Rulebook = CURRENT_RULEBOOK,
) -> pd.DataFrame:
    """Recompute xG/xA and point components after minute overrides."""
    if player_fixture.empty or "xg90_shrunk" not in player_fixture.columns:
        return player_fixture

    out = player_fixture.copy()
    out["expected_minutes"] = pd.to_numeric(out["expected_minutes"], errors="coerce").fillna(0.0).clip(0, 90)
    out["mins_frac"] = out["expected_minutes"] / 90.0
    out["xg_weight"] = pd.to_numeric(out["xg90_shrunk"], errors="coerce").fillna(0.0) * out["mins_frac"]
    if "set_piece_share" not in out.columns:
        out["set_piece_share"] = 0.0
    out["set_piece_share"] = pd.to_numeric(out["set_piece_share"], errors="coerce").fillna(0.0)
    out["xa_weight"] = (
        pd.to_numeric(out["xa90_shrunk"], errors="coerce").fillna(0.0)
        * (1.0 + float(set_piece_xa_weight) * out["set_piece_share"])
        * out["mins_frac"]
    )
    if "penalty_share" not in out.columns:
        out["penalty_share"] = 0.0
    out["penalty_share"] = pd.to_numeric(out["penalty_share"], errors="coerce").fillna(0.0)

    keys = ["fixture", "team"]
    xg_sum = out.groupby(keys)["xg_weight"].transform("sum")
    xa_sum = out.groupby(keys)["xa_weight"].transform("sum")
    pen_share_sum = out.groupby(keys)["penalty_share"].transform("sum")
    team_penalty_xg = np.where(pen_share_sum > 0, np.minimum(rulebook.max_team_penalty_xg, out["team_xg"] * 0.07), 0.0)
    open_play_xg = np.maximum(pd.to_numeric(out["team_xg"], errors="coerce").fillna(0.0) - team_penalty_xg, 0.0)
    out["pen_xG"] = np.where(pen_share_sum > 0, team_penalty_xg * out["penalty_share"] / pen_share_sum, 0.0)
    out["xG"] = np.where(xg_sum > 0, open_play_xg * out["xg_weight"] / xg_sum, 0.0) + out["pen_xG"]
    out["xA"] = np.where(xa_sum > 0, out["team_xa"] * out["xa_weight"] / xa_sum, 0.0)
    out["xGA_exp"] = out["xG"] + out["xA"]
    out["P1_GA"] = 1.0 - np.exp(-out["xGA_exp"])

    def recalc(row: pd.Series) -> pd.Series:
        pos = str(row["position"])
        mins = float(row["expected_minutes"])
        if mins <= 0:
            app = goals = assists = cs = saves = defcon = cards = concede = bonus = pen_miss = xpts = 0.0
        else:
            minute_ev = _minute_ev_components(
                mins=mins,
                start_probability=float(row.get("start_probability", np.nan)),
                play_probability=float(row.get("play_probability", np.nan)),
                likely_minutes=float(row.get("likely_minutes", np.nan)),
                pos=pos,
                cs_prob=float(row["cs_prob"]),
                opponent_lambda=float(row["opponent_xg"]),
                saves90=float(row.get("saves90", 0.0) or 0.0),
                defcon90=float(row.get("defcon90", 0.0) or 0.0),
                yc_rate=float(row.get("yc_rate", 0.0) or 0.0),
                rc_rate=float(row.get("rc_rate", 0.0) or 0.0),
                rulebook=rulebook,
            )
            app = minute_ev["app"]
            goals = float(row["xG"]) * rulebook.goal_points_for(pos)
            assists = float(row["xA"]) * 3.0
            cs = minute_ev["cs"]
            saves = minute_ev["saves"]
            defcon = minute_ev["defcon"]
            cards = minute_ev["cards"]
            concede = minute_ev["concede"]
            pen_xg = float(row.get("pen_xG", 0.0) or 0.0)
            pen_attempts = pen_xg / rulebook.penalty_xg_per_attempt if pen_xg > 0 else 0.0
            pen_miss = rulebook.penalty_miss_points * pen_attempts * (1.0 - rulebook.penalty_xg_per_attempt)
            p_defcon_hit = max(0.0, min(1.0, defcon / 2.0))
            bonus_lambda = (
                float(row["xG"]) * rulebook.bonus_per_goal
                + float(row["xA"]) * rulebook.bonus_per_assist
                + (float(row["cs_prob"]) * minute_ev["p_cs_eligible"] * rulebook.bonus_cs_gk_def if pos in {"GK", "DEF"} else 0.0)
                + saves * rulebook.bonus_per_save3
                + p_defcon_hit * rulebook.bonus_per_defcon
            )
            bonus = expected_capped_poisson(bonus_lambda, cap=3)
            xpts = app + goals + assists + cs + saves + defcon + cards + pen_miss + concede + bonus
        return pd.Series(
            {
                "AppPts": app,
                "GoalPts": goals,
                "AssistPts": assists,
                "CSPts": cs,
                "SavePts": saves,
                "DefconPts": defcon,
                "CardPts": cards,
                "PenMissPts": pen_miss,
                "ConcedePts": concede,
                "BonusPts": bonus,
                "xPts": xpts,
            }
        )

    components = out.apply(recalc, axis=1)
    for col in components.columns:
        out[col] = components[col]
    return out.drop(columns=["mins_frac", "xg_weight", "xa_weight"], errors="ignore")


def aggregate_gameweek(player_fixture: pd.DataFrame) -> pd.DataFrame:
    if player_fixture.empty:
        return player_fixture.copy()
    grouped = (
        player_fixture.groupby(["event", "player_id"], as_index=False)
        .agg(
            web_name=("web_name", "first"),
            position=("position", "first"),
            team=("team", "first"),
            fixtures=("fixture", "count"),
            expected_minutes=("expected_minutes", "sum"),
            xG=("xG", "sum"),
            xA=("xA", "sum"),
            xGA_exp=("xGA_exp", "sum"),
            xPts=("xPts", "sum"),
            P1_GA=("P1_GA", "sum"),
            AppPts=("AppPts", "sum"),
            GoalPts=("GoalPts", "sum"),
            AssistPts=("AssistPts", "sum"),
            CSPts=("CSPts", "sum"),
            SavePts=("SavePts", "sum"),
            DefconPts=("DefconPts", "sum"),
            CardPts=("CardPts", "sum"),
            PenMissPts=("PenMissPts", "sum"),
            ConcedePts=("ConcedePts", "sum"),
        )
        .sort_values(["event", "xPts"], ascending=[True, False])
    )
    grouped["p_return"] = 1.0 - np.exp(-(numeric(grouped["xG"]) + numeric(grouped["xA"])))
    return grouped


def attach_mc_tail_probabilities(weekly: pd.DataFrame, monte_carlo: pd.DataFrame) -> pd.DataFrame:
    """Attach MC-derived return and haul probabilities to the EV weekly table."""
    if weekly.empty or monte_carlo.empty:
        return weekly
    cols = ["event", "player_id", "P_return", "P_haul"]
    if not set(cols).issubset(monte_carlo.columns):
        return weekly
    return weekly.drop(columns=["P_return", "P_haul"], errors="ignore").merge(
        monte_carlo[cols],
        on=["event", "player_id"],
        how="left",
    )
