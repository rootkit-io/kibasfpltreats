from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .minutes import minute_outcomes
from .rulebook import CURRENT_RULEBOOK, Rulebook


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not np.isfinite(out):
        return float(default)
    return out


def _numeric_array(frame: pd.DataFrame, column: str, default: float = 0.0) -> np.ndarray:
    if column not in frame.columns:
        return np.full(len(frame), float(default), dtype=float)
    return pd.to_numeric(frame[column], errors="coerce").fillna(default).to_numpy(dtype=float)


def _first_numeric(frame: pd.DataFrame, column: str, default: float = 0.0) -> float:
    if column not in frame.columns:
        return float(default)
    values = pd.to_numeric(frame[column], errors="coerce").dropna()
    if values.empty:
        return float(default)
    return float(values.iloc[0])


def _as_bool(value: object) -> bool | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "y"}:
            return True
        if text in {"false", "0", "no", "n"}:
            return False
        return None
    return bool(value)


def _actual_percentile(points: np.ndarray, q: float) -> float:
    try:
        return float(np.percentile(points, q, method="nearest"))
    except TypeError:
        return float(np.percentile(points, q, interpolation="nearest"))


def _fixture_metrics(points: np.ndarray, returns: np.ndarray) -> dict[str, float]:
    bracket_10_to_14 = float(np.mean((points >= 10) & (points <= 14)))
    bracket_15_plus = float(np.mean(points >= 15))
    return {
        "MC_MeanPts": float(points.mean()),
        "MC_StdPts": float(points.std()),
        "MC_Floor": _actual_percentile(points, 10),
        "MC_P25": _actual_percentile(points, 25),
        "MC_P75": _actual_percentile(points, 75),
        "MC_Upside": _actual_percentile(points, 90),
        "MC_P1_Return": float(np.mean(returns >= 1)),
        "MC_P2_Return": float(np.mean(returns >= 2)),
        "P_return": float(np.mean(points >= 6)),
        "P_haul": bracket_10_to_14 + bracket_15_plus,
        "Bracket_LE_2": float(np.mean(points <= 2)),
        "Bracket_3_to_6": float(np.mean((points >= 3) & (points <= 6))),
        "Bracket_7_to_9": float(np.mean((points >= 7) & (points <= 9))),
        "Bracket_10_to_14": bracket_10_to_14,
        "Bracket_15_plus": bracket_15_plus,
        "MC_MinPts": float(points.min()),
        "MC_MaxPts": float(points.max()),
    }


def _identify_sides(fixture_rows: pd.DataFrame) -> tuple[int, int] | None:
    teams = [int(team) for team in fixture_rows["team"].dropna().drop_duplicates().tolist()]
    if len(teams) != 2:
        return None

    if "was_home" in fixture_rows.columns:
        parsed = fixture_rows["was_home"].map(_as_bool)
        home_candidates = fixture_rows.loc[parsed == True, "team"].dropna().drop_duplicates().tolist()  # noqa: E712
        away_candidates = fixture_rows.loc[parsed == False, "team"].dropna().drop_duplicates().tolist()  # noqa: E712
        if len(home_candidates) == 1 and len(away_candidates) == 1:
            return int(home_candidates[0]), int(away_candidates[0])

    return teams[0], teams[1]


def _weighted_choice(rng: np.random.Generator, indices: np.ndarray, weights: np.ndarray) -> int | None:
    if len(indices) == 0:
        return None
    weights = np.asarray(weights, dtype=float)
    valid = np.isfinite(weights) & (weights > 0)
    if not np.any(valid):
        return int(rng.choice(indices))
    valid_indices = indices[valid]
    valid_weights = weights[valid]
    return int(rng.choice(valid_indices, p=valid_weights / valid_weights.sum()))


def _fallback_rate(frame: pd.DataFrame, exact: list[str], fallback: list[tuple[str, float]]) -> np.ndarray:
    for column in exact:
        if column in frame.columns:
            values = _numeric_array(frame, column, 0.0).clip(min=0.0)
            if np.any(values > 0):
                return values
    for column, multiplier in fallback:
        if column in frame.columns:
            values = (_numeric_array(frame, column, 0.0) * float(multiplier)).clip(min=0.0)
            if np.any(values > 0):
                return values
    return np.zeros(len(frame), dtype=float)


def _minute_draws(
    fixture_rows: pd.DataFrame,
    rng: np.random.Generator,
    n_sim: int,
) -> tuple[np.ndarray, np.ndarray]:
    draws = []
    positive_fallbacks = []
    for _, row in fixture_rows.iterrows():
        expected_minutes = _safe_float(row.get("expected_minutes", 0.0), 0.0)
        likely_minutes = _safe_float(row.get("likely_minutes", expected_minutes), expected_minutes)
        vals, probs = minute_outcomes(
            likely_minutes,
            start_probability=_safe_float(row.get("start_probability", np.nan), np.nan),
            play_probability=_safe_float(row.get("play_probability", np.nan), np.nan),
        )
        vals = vals.astype(int)
        probs = probs.astype(float) / probs.astype(float).sum()
        draws.append(rng.choice(vals, size=n_sim, p=probs).astype(int))
        positives = vals[vals > 0]
        positive_fallbacks.append(int(positives.min()) if len(positives) else 1)
    return np.vstack(draws), np.asarray(positive_fallbacks, dtype=int)


def _ensure_active_scorer(
    minutes: np.ndarray,
    team_indices: np.ndarray,
    expected_minutes: np.ndarray,
    positive_fallbacks: np.ndarray,
) -> None:
    if len(team_indices) == 0 or np.any(minutes[team_indices] > 0):
        return
    candidate = int(team_indices[np.argmax(expected_minutes[team_indices])])
    minutes[candidate] = max(1, int(positive_fallbacks[candidate]))


def _winning_goal_scorer(
    goal_events: list[dict[str, int]],
    home_team: int,
    away_team: int,
    home_goals: int,
    away_goals: int,
) -> int | None:
    if home_goals == away_goals:
        return None
    winning_team = home_team if home_goals > away_goals else away_team
    home_score = 0
    away_score = 0
    states_after = []
    for event in goal_events:
        if event["team"] == home_team:
            home_score += 1
        elif event["team"] == away_team:
            away_score += 1
        states_after.append((home_score, away_score))

    for idx, event in enumerate(goal_events):
        if event["team"] != winning_team:
            continue
        later_states = states_after[idx:]
        if winning_team == home_team:
            if states_after[idx][0] > states_after[idx][1] and all(h > a for h, a in later_states):
                return int(event["scorer_idx"])
        elif states_after[idx][1] > states_after[idx][0] and all(a > h for h, a in later_states):
            return int(event["scorer_idx"])
    return None


def _award_bonus(bps: np.ndarray) -> np.ndarray:
    bonus = np.zeros(len(bps), dtype=int)
    positive_scores = sorted({int(score) for score in bps if int(score) > 0}, reverse=True)
    if not positive_scores:
        return bonus

    first = positive_scores[0]
    first_idx = np.flatnonzero(bps == first)
    bonus[first_idx] = 3
    if len(positive_scores) == 1:
        return bonus

    second = positive_scores[1]
    second_idx = np.flatnonzero(bps == second)
    if len(first_idx) > 1:
        bonus[second_idx] = 1
        return bonus

    bonus[second_idx] = 2
    if len(second_idx) > 1 or len(positive_scores) == 2:
        return bonus

    third = positive_scores[2]
    third_idx = np.flatnonzero(bps == third)
    bonus[third_idx] = 1
    return bonus


def _draw_penalty_misses(
    rng: np.random.Generator,
    active_indices: np.ndarray,
    penalty_share: np.ndarray,
    pen_xg_total: float,
    rulebook: Rulebook = CURRENT_RULEBOOK,
) -> dict[int, int]:
    if pen_xg_total <= 0 or len(active_indices) == 0:
        return {}
    penalty_attempts_mu = pen_xg_total / rulebook.penalty_xg_per_attempt
    miss_count = int(rng.poisson(max(penalty_attempts_mu * (1.0 - rulebook.penalty_xg_per_attempt), 0.0)))
    if miss_count <= 0:
        return {}
    weights = penalty_share[active_indices]
    if float(weights.sum()) <= 0:
        weights = np.ones(len(active_indices), dtype=float)
    misses = rng.multinomial(miss_count, weights / weights.sum())
    return {
        int(player_idx): int(count)
        for player_idx, count in zip(active_indices, misses)
        if int(count) > 0
    }


def _simulate_fixture(
    fixture_rows: pd.DataFrame,
    rng: np.random.Generator,
    n_sim: int,
    capture_debug: bool = False,
    rulebook: Rulebook = CURRENT_RULEBOOK,
) -> tuple[dict[int, np.ndarray], dict[int, np.ndarray], dict[str, Any] | None]:
    fixture_rows = fixture_rows.reset_index(drop=True).copy()
    sides = _identify_sides(fixture_rows)
    if sides is None:
        return {}, {}, None
    home_team, away_team = sides

    player_ids = fixture_rows["player_id"].astype(int).to_numpy()
    teams = fixture_rows["team"].astype(int).to_numpy()
    positions = fixture_rows["position"].astype(str).to_numpy()
    web_names = fixture_rows.get("web_name", pd.Series(player_ids, index=fixture_rows.index)).astype(str).to_numpy()
    expected_minutes = _numeric_array(fixture_rows, "expected_minutes", 0.0).clip(min=0.0)
    team_xg = _numeric_array(fixture_rows, "team_xg", 0.0).clip(min=0.0)
    team_xa = _numeric_array(fixture_rows, "team_xa", 0.0).clip(min=0.0)
    xg = _numeric_array(fixture_rows, "xG", 0.0).clip(min=0.0)
    xa = _numeric_array(fixture_rows, "xA", 0.0).clip(min=0.0)
    pen_xg = _numeric_array(fixture_rows, "pen_xG", 0.0).clip(min=0.0)
    penalty_share = _numeric_array(fixture_rows, "penalty_share", 0.0).clip(min=0.0)
    open_xg = np.maximum(xg - pen_xg, 0.0)
    open_rate = np.divide(open_xg, np.maximum(expected_minutes, 1.0), out=np.zeros_like(open_xg), where=expected_minutes > 0)
    assist_rate = np.divide(xa, np.maximum(expected_minutes, 1.0), out=np.zeros_like(xa), where=expected_minutes > 0)

    saves90 = _numeric_array(fixture_rows, "saves90", 0.0).clip(min=0.0)
    defcon90 = _numeric_array(fixture_rows, "defcon90", 0.0).clip(min=0.0)
    yc_rate = _numeric_array(fixture_rows, "yc_rate", 0.0).clip(min=0.0)
    rc_rate = _numeric_array(fixture_rows, "rc_rate", 0.0).clip(min=0.0)
    creativity_rate = _fallback_rate(
        fixture_rows,
        ["creativity_rate"],
        [("understat_big_chance_created90", 1.0), ("understat_chances_created90", 0.25)],
    )
    key_pass_rate = _fallback_rate(
        fixture_rows,
        ["key_pass_rate"],
        [("understat_key_passes90", 1.0), ("understat_chances_created90", 1.0)],
    )
    defensive_action_rate = _fallback_rate(
        fixture_rows,
        ["defensive_action_rate", "cbi_rate", "clearances_blocks_interceptions_rate", "clearances_blocks_interceptions_per_90"],
        [("defcon90", 0.5)],
    )
    recovery_rate = _fallback_rate(
        fixture_rows,
        ["recovery_rate", "recoveries_rate", "recoveries_per_90"],
        [("defcon90", 0.5)],
    )
    penalty_conceded_rate = _fallback_rate(
        fixture_rows,
        ["penalty_conceded_rate", "penalties_conceded_per_90"],
        [],
    )

    home_idx = np.flatnonzero(teams == home_team)
    away_idx = np.flatnonzero(teams == away_team)
    home_lambda = max(_first_numeric(fixture_rows.loc[teams == home_team], "team_xg", 0.0), 0.0)
    away_lambda = max(_first_numeric(fixture_rows.loc[teams == away_team], "team_xg", 0.0), 0.0)
    home_xa = max(_first_numeric(fixture_rows.loc[teams == home_team], "team_xa", home_lambda * 0.72), 0.0)
    away_xa = max(_first_numeric(fixture_rows.loc[teams == away_team], "team_xa", away_lambda * 0.72), 0.0)
    team_assist_prob = {
        home_team: float(np.clip(home_xa / max(home_lambda, 1e-6), 0.0, 0.95)),
        away_team: float(np.clip(away_xa / max(away_lambda, 1e-6), 0.0, 0.95)),
    }
    team_penalty_prob = {}
    for team, lam in [(home_team, home_lambda), (away_team, away_lambda)]:
        idx = np.flatnonzero(teams == team)
        pen_total = float(pen_xg[idx].sum())
        team_penalty_prob[team] = float(np.clip(pen_total / max(lam, 1e-6), 0.0, 0.35))

    minute_matrix, positive_fallbacks = _minute_draws(fixture_rows, rng, n_sim)
    home_goal_draws = rng.poisson(home_lambda, size=n_sim).astype(int)
    away_goal_draws = rng.poisson(away_lambda, size=n_sim).astype(int)

    fixture_points = {int(pid): np.zeros(n_sim, dtype=int) for pid in player_ids}
    fixture_returns = {int(pid): np.zeros(n_sim, dtype=int) for pid in player_ids}
    scorelines = np.zeros((n_sim, 2), dtype=int)
    debug_match: dict[str, Any] | None = None

    for sim_idx in range(n_sim):
        home_goals = int(home_goal_draws[sim_idx])
        away_goals = int(away_goal_draws[sim_idx])
        scorelines[sim_idx] = [home_goals, away_goals]

        minutes = minute_matrix[:, sim_idx].copy()
        if home_goals > 0:
            _ensure_active_scorer(minutes, home_idx, expected_minutes, positive_fallbacks)
        if away_goals > 0:
            _ensure_active_scorer(minutes, away_idx, expected_minutes, positive_fallbacks)

        goals = np.zeros(len(player_ids), dtype=int)
        assists = np.zeros(len(player_ids), dtype=int)
        clean_sheet = np.zeros(len(player_ids), dtype=int)
        saves = np.zeros(len(player_ids), dtype=int)
        defcon_count = np.zeros(len(player_ids), dtype=int)
        yellow = np.zeros(len(player_ids), dtype=int)
        red = np.zeros(len(player_ids), dtype=int)
        penalty_misses = np.zeros(len(player_ids), dtype=int)
        penalty_conceded = np.zeros(len(player_ids), dtype=int)
        bps = np.zeros(len(player_ids), dtype=int)
        goal_events: list[dict[str, int]] = []

        event_teams = [home_team] * home_goals + [away_team] * away_goals
        rng.shuffle(event_teams)
        for scoring_team in event_teams:
            team_indices = home_idx if scoring_team == home_team else away_idx
            active = minutes[team_indices] > 0
            active_indices = team_indices[active]
            use_penalty = float(penalty_share[team_indices].sum()) > 0 and rng.random() < team_penalty_prob[scoring_team]
            if use_penalty:
                scorer_weights = penalty_share[active_indices]
            else:
                scorer_weights = open_rate[active_indices] * minutes[active_indices]
            scorer_idx = _weighted_choice(rng, active_indices, scorer_weights)
            if scorer_idx is None:
                continue

            goals[scorer_idx] += 1
            goal_events.append({"team": int(scoring_team), "scorer_idx": int(scorer_idx)})

            assister_weights = assist_rate[active_indices] * minutes[active_indices]
            assister_weights = assister_weights.copy()
            assister_weights[active_indices == scorer_idx] = 0.0
            if rng.random() < team_assist_prob[scoring_team] and float(assister_weights.sum()) > 0:
                assister_idx = _weighted_choice(rng, active_indices, assister_weights)
                if assister_idx is not None and assister_idx != scorer_idx:
                    assists[assister_idx] += 1

        for team, team_indices in [(home_team, home_idx), (away_team, away_idx)]:
            active_indices = team_indices[minutes[team_indices] > 0]
            misses = _draw_penalty_misses(
                rng,
                active_indices,
                penalty_share,
                float(pen_xg[team_indices].sum()),
                rulebook=rulebook,
            )
            for player_idx, count in misses.items():
                penalty_misses[player_idx] += count

        for idx, pos in enumerate(positions):
            mins = int(minutes[idx])
            if mins <= 0:
                continue

            opponent_goals = away_goals if teams[idx] == home_team else home_goals
            if pos in {"GK", "DEF"} and opponent_goals == 0 and mins >= 60:
                clean_sheet[idx] = 1

            if pos == "GK":
                saves[idx] = int(rng.poisson(max(saves90[idx] * mins / 90.0, 0.0)))

            threshold = rulebook.defcon_threshold_for(pos)
            if threshold is not None:
                defcon_count[idx] = int(rng.poisson(max(defcon90[idx] * mins / 90.0, 0.0)))

            y_prob = float(np.clip(yc_rate[idx] * mins / 90.0, 0.0, 1.0))
            yellow[idx] = int(rng.random() < y_prob)
            if yellow[idx] == 0:
                r_prob = float(np.clip(rc_rate[idx] * mins / 90.0, 0.0, 1.0))
                red[idx] = int(rng.random() < r_prob)

            if penalty_conceded_rate[idx] > 0:
                penalty_conceded[idx] = int(rng.poisson(max(penalty_conceded_rate[idx] * mins / 90.0, 0.0)))

        winning_idx = _winning_goal_scorer(goal_events, home_team, away_team, home_goals, away_goals)
        for idx, pos in enumerate(positions):
            mins = int(minutes[idx])
            if mins <= 0:
                continue

            bps[idx] += 6 if mins >= 60 else 3
            bps[idx] += goals[idx] * rulebook.mc_goal_bps.get(pos, 0)
            bps[idx] += assists[idx] * 9
            if pos in {"GK", "DEF"}:
                bps[idx] += clean_sheet[idx] * 12
                cbi_count = int(rng.poisson(max(defensive_action_rate[idx] * mins / 90.0, 0.0)))
                bps[idx] += cbi_count // 2
            if pos == "GK":
                bps[idx] += saves[idx] * 2
            if pos in {"DEF", "MID"}:
                recovery_count = int(rng.poisson(max(recovery_rate[idx] * mins / 90.0, 0.0)))
                bps[idx] += recovery_count // 3
            if idx == winning_idx:
                bps[idx] += 3
            bps[idx] += int(rng.poisson(max(creativity_rate[idx] * mins / 90.0, 0.0))) * 3
            bps[idx] += int(rng.poisson(max(key_pass_rate[idx] * mins / 90.0, 0.0)))
            bps[idx] += {"GK": 4, "DEF": 4, "MID": 2, "FWD": 0}.get(pos, 0)
            bps[idx] -= yellow[idx] * 3
            bps[idx] -= red[idx] * 9
            bps[idx] -= penalty_conceded[idx] * 3

        bonus = _award_bonus(bps)

        total_points = np.zeros(len(player_ids), dtype=int)
        for idx, pos in enumerate(positions):
            mins = int(minutes[idx])
            opponent_goals = away_goals if teams[idx] == home_team else home_goals
            app = int(rulebook.appearance_points_for(mins))
            goal_pts = int(goals[idx] * rulebook.goal_points_for(pos))
            assist_pts = int(assists[idx] * 3)
            cs_pts = int(clean_sheet[idx] * rulebook.clean_sheet_points_for(pos))
            save_pts = int(saves[idx] // 3) if pos == "GK" else 0
            threshold = rulebook.defcon_threshold_for(pos)
            defcon_pts = int(2 if threshold is not None and defcon_count[idx] >= threshold else 0)
            card_pts = int(-yellow[idx] - 3 * red[idx])
            pen_miss_pts = int(rulebook.penalty_miss_points * penalty_misses[idx])
            concede_pts = int(-(opponent_goals // 2)) if pos in {"GK", "DEF"} and mins > 0 else 0
            total_points[idx] = (
                app
                + goal_pts
                + assist_pts
                + cs_pts
                + save_pts
                + defcon_pts
                + card_pts
                + pen_miss_pts
                + concede_pts
                + int(bonus[idx])
            )

        for idx, pid in enumerate(player_ids):
            fixture_points[int(pid)][sim_idx] = int(total_points[idx])
            fixture_returns[int(pid)][sim_idx] = int(goals[idx] + assists[idx])

        if capture_debug and debug_match is None:
            player_rows = []
            for idx, pid in enumerate(player_ids):
                player_rows.append(
                    {
                        "player_id": int(pid),
                        "web_name": str(web_names[idx]),
                        "team": int(teams[idx]),
                        "position": str(positions[idx]),
                        "minutes": int(minutes[idx]),
                        "goals": int(goals[idx]),
                        "assists": int(assists[idx]),
                        "clean_sheet": int(clean_sheet[idx]),
                        "saves": int(saves[idx]),
                        "defcon_count": int(defcon_count[idx]),
                        "yellow": int(yellow[idx]),
                        "red": int(red[idx]),
                        "bonus": int(bonus[idx]),
                        "bps": int(bps[idx]),
                        "total_points": int(total_points[idx]),
                    }
                )
            debug_match = {
                "fixture": fixture_rows["fixture"].iloc[0] if "fixture" in fixture_rows.columns else None,
                "home_team": int(home_team),
                "away_team": int(away_team),
                "home_goals": int(home_goals),
                "away_goals": int(away_goals),
                "home_player_goals": int(goals[home_idx].sum()),
                "away_player_goals": int(goals[away_idx].sum()),
                "bonus_players": int(np.sum(bonus > 0)),
                "players": player_rows,
            }

    debug: dict[str, Any] | None = None
    if capture_debug:
        debug = {
            "home_team": int(home_team),
            "away_team": int(away_team),
            "home_goal_mean": float(scorelines[:, 0].mean()),
            "away_goal_mean": float(scorelines[:, 1].mean()),
            "scorelines": scorelines,
            "sample_match": debug_match,
            "player_points": {int(pid): fixture_points[int(pid)].copy() for pid in player_ids},
            "player_names": {int(pid): str(name) for pid, name in zip(player_ids, web_names)},
            "player_positions": {int(pid): str(pos) for pid, pos in zip(player_ids, positions)},
        }

    return fixture_points, fixture_returns, debug


def _simulate_fixture_debug(
    player_fixture: pd.DataFrame,
    n_sim: int = 1_000,
    seed: int = 42,
    rulebook: Rulebook = CURRENT_RULEBOOK,
) -> dict[str, Any]:
    """Private debugging hook for MC sanity checks."""
    if player_fixture.empty:
        return {}
    fixture_key = player_fixture["fixture"].dropna().iloc[0] if "fixture" in player_fixture.columns and player_fixture["fixture"].notna().any() else None
    if fixture_key is not None:
        fixture_rows = player_fixture.loc[player_fixture["fixture"] == fixture_key].copy()
    else:
        fixture_rows = player_fixture.copy()
    _, _, debug = _simulate_fixture(fixture_rows, np.random.default_rng(seed), max(1, int(n_sim)), capture_debug=True, rulebook=rulebook)
    return debug or {}


def simulate_player_week(
    player_fixture: pd.DataFrame,
    n_sim: int = 10_000,
    seed: int = 42,
    return_fixture: bool = False,
    rulebook: Rulebook = CURRENT_RULEBOOK,
) -> pd.DataFrame:
    """Simulate real discrete fixture outcomes first, then aggregate player points."""
    rng = np.random.default_rng(seed)
    n_sim = max(1, int(n_sim))
    if player_fixture.empty:
        return pd.DataFrame()

    player_totals: dict[tuple[int, int], np.ndarray] = {}
    return_totals: dict[tuple[int, int], np.ndarray] = {}
    fixture_results = []

    for _, fixture_rows in player_fixture.groupby("fixture", sort=False):
        fixture_points, fixture_returns, _ = _simulate_fixture(fixture_rows, rng, n_sim, capture_debug=False, rulebook=rulebook)
        if not fixture_points:
            continue

        for _, row in fixture_rows.iterrows():
            if pd.isna(row.get("event")):
                continue
            player_id = int(row["player_id"])
            if player_id not in fixture_points:
                continue
            key = (int(row["event"]), player_id)
            player_totals.setdefault(key, np.zeros(n_sim, dtype=int))
            return_totals.setdefault(key, np.zeros(n_sim, dtype=int))
            player_totals[key] += fixture_points[player_id]
            return_totals[key] += fixture_returns[player_id]

            if return_fixture:
                fixture_results.append(
                    {
                        **row.to_dict(),
                        **_fixture_metrics(fixture_points[player_id], fixture_returns[player_id]),
                    }
                )

    rows = []
    if not player_totals:
        empty = pd.DataFrame()
        return (empty, pd.DataFrame(fixture_results)) if return_fixture else empty

    meta = player_fixture.drop_duplicates(["event", "player_id"]).set_index(["event", "player_id"])
    for key, pts in player_totals.items():
        rets = return_totals[key]
        row = meta.loc[key]
        rows.append(
            {
                "event": key[0],
                "player_id": key[1],
                "web_name": row["web_name"],
                "position": row["position"],
                "team": row["team"],
                **_fixture_metrics(pts, rets),
            }
        )
    weekly = pd.DataFrame(rows).sort_values(["event", "MC_MeanPts"], ascending=[True, False])
    if return_fixture:
        fixture = pd.DataFrame(fixture_results)
        return weekly, fixture
    return weekly
