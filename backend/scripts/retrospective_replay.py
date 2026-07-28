from __future__ import annotations

import argparse
import math
import os
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import numpy as np
import pandas as pd

from fpl_xpts.config import AppConfig
from fpl_xpts.minutes_contract import MinutesRunInputs
from fpl_xpts.pipeline import ProjectionInputs, run_projection_stages
from fpl_xpts.rulebook import CURRENT_RULEBOOK, rulebook_for_season
from fpl_xpts.forecast import forecast_fixture_lambdas
from fpl_xpts.historical_validation import (
    ODDS_SEASONS,
    POSITION_TO_ELEMENT_TYPE,
    _first_number,
    _fit_lambdas_vectorized_equivalent,
    _history_for_gw,
    _player_input_frame,
    _read_csv,
    build_fixture_table,
    load_vaastav_seasons,
    season_code_to_label,
)
from fpl_xpts.market_odds import _canon_team, _devig_decimal
from fpl_xpts.minutes_model import (
    DEFAULT_MINUTES_MODEL_PATH,
    build_historical_minutes_features,
    load_minutes_bundle,
)
from fpl_xpts.ml_features import MODEL_FILENAMES, POSITIONS, build_historical_ml_frame
from fpl_xpts.ml_models import load_bundles, predict_with_bundle
from fpl_xpts.monte_carlo import simulate_player_week


REPLAY_SEASONS = ["2019-20", "2020-21", "2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]
MC_COLS = [
    "MC_MeanPts",
    "MC_StdPts",
    "MC_Floor",
    "MC_P25",
    "MC_P75",
    "MC_Upside",
    "MC_P1_Return",
    "MC_P2_Return",
    "P_return",
    "P_haul",
    "Bracket_LE_2",
    "Bracket_3_to_6",
    "Bracket_7_to_9",
    "Bracket_10_to_14",
    "Bracket_15_plus",
    "MC_MinPts",
    "MC_MaxPts",
]
KFT_COLS = [
    "kft_xpts",
    "kft_xg",
    "kft_xa",
    "kft_expected_minutes",
    "AppPts",
    "GoalPts",
    "AssistPts",
    "CSPts",
    "SavePts",
    "DefconPts",
    "CardPts",
    "PenMissPts",
    "ConcedePts",
]
MINUTES_ML_COLS = ["pred_play_prob"]

_WORK: dict[str, Any] = {}
# Era scoring knowledge now lives in fpl_xpts.rulebook (rulebook_for_season);
# the hand-maintained mirrors and the season_scoring_context monkeypatch were
# deleted in Candidate #2 Phase 3 -- rulebooks are passed to the engines as
# explicit parameters instead of mutating module globals.


@dataclass(frozen=True)
class ReplayConfig:
    n_sim: int
    random_seed: int
    form_blend_weight: float
    set_piece_xa_weight: float


def print_scoring_rule_report() -> None:
    current_goal_points = {pos: CURRENT_RULEBOOK.goal_points_for(pos) for pos in ["GK", "DEF", "MID", "FWD"]}
    current_defcon_thresholds = {pos: CURRENT_RULEBOOK.defcon_threshold_for(pos) for pos in ["GK", "DEF", "MID", "FWD"]}
    bonus_proxy_weights = {
        "BONUS_PER_GOAL": CURRENT_RULEBOOK.bonus_per_goal,
        "BONUS_PER_ASSIST": CURRENT_RULEBOOK.bonus_per_assist,
        "BONUS_CS_GK_DEF": CURRENT_RULEBOOK.bonus_cs_gk_def,
        "BONUS_PER_SAVE3": CURRENT_RULEBOOK.bonus_per_save3,
        "BONUS_PER_DEFCON": CURRENT_RULEBOOK.bonus_per_defcon,
    }
    print("Current Rulebook (fpl_xpts.rulebook.CURRENT_RULEBOOK) rule values:")
    print(f"  goal_points={current_goal_points}")
    print(f"  defcon_threshold={current_defcon_thresholds}")
    print("  Corresponds to current/2025-26-aware live scoring for GK goals and DEFCON, not pre-2024 history.")
    print("Current expected-bonus proxy weights (Rulebook):")
    print(f"  {bonus_proxy_weights}")
    print("  These are not official FPL BPS weights; BONUS_PER_DEFCON is a current-rule proxy and is zeroed in replay before 2025-26 through DefconPts=0.")
    print("Current monte_carlo.py BPS proxy weights:")
    print(
        "  appearance=6 if >=60 mins else 3; "
        f"goal_bps={dict(CURRENT_RULEBOOK.mc_goal_bps)}; assist_bps=9; "
        "clean_sheet_bps=12 (GK/DEF, inline sim heuristic); save_bps=2 per save; "
        "yellow=-3; red=-9; penalty_conceded=-3"
    )
    print("  Goal BPS are positional and do not model the 2025-26 penalty-goal equalization branch.")


def print_replay_season_rules(seasons: list[str]) -> None:
    print("Selected replay season scoring metadata:")
    for season in seasons:
        book = rulebook_for_season(season)
        print(
            f"  {season}: gk_goal_points={book.goal_points_for('GK')} "
            f"defcon_active={book.defcon_active} "
            f"assist_rules={book.assist_rules_version} "
            f"bps_version={book.bps_version}"
        )


def _full_read_pass(root: Path) -> tuple[int, int]:
    files = [line.strip() for line in os.popen("rg --files").read().splitlines() if line.strip()]
    failures: list[str] = []
    total_bytes = 0
    for name in files:
        path = root / name
        try:
            data = path.read_bytes()
            total_bytes += len(data)
        except Exception as exc:  # pragma: no cover - defensive startup guard.
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    if failures:
        raise RuntimeError("Full repo read pass failed:\n" + "\n".join(failures[:50]))
    return len(files), total_bytes


def _safe_float(value: object, default: float = np.nan) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _load_replay_odds_features(root: Path, seasons: list[str] | None = None) -> pd.DataFrame:
    season_filter = set(seasons or [])
    rows: list[dict[str, Any]] = []
    for season in ODDS_SEASONS:
        season_label = season_code_to_label(season)
        if season_filter and season_label not in season_filter:
            continue
        path = root / "data" / "odds_historical" / f"E0_{season}.csv"
        if not path.exists():
            legacy = root / "data" / "odds_historical" / f"{season}_E0.csv"
            path = legacy if legacy.exists() else path
        if not path.exists():
            continue
        frame = _read_csv(path)
        if not {"HomeTeam", "AwayTeam", "Date"}.issubset(frame.columns):
            continue
        for _, row in frame.iterrows():
            match_date = pd.to_datetime(row.get("Date"), dayfirst=True, errors="coerce")
            if pd.isna(match_date):
                continue
            closing_h = _first_number(row, ["AvgCH", "B365CH", "PSCH", "MaxCH", "BWCH", "WHCH", "BFCH", "BFECH"])
            closing_d = _first_number(row, ["AvgCD", "B365CD", "PSCD", "MaxCD", "BWCD", "WHCD", "BFCD", "BFECD"])
            closing_a = _first_number(row, ["AvgCA", "B365CA", "PSCA", "MaxCA", "BWCA", "WHCA", "BFCA", "BFECA"])
            home_odds = closing_h or _first_number(row, ["AvgH", "B365H", "PSH", "MaxH", "BWH", "WHH", "BFH", "BFEH"])
            draw_odds = closing_d or _first_number(row, ["AvgD", "B365D", "PSD", "MaxD", "BWD", "WHD", "BFD", "BFED"])
            away_odds = closing_a or _first_number(row, ["AvgA", "B365A", "PSA", "MaxA", "BWA", "WHA", "BFA", "BFEA"])
            h2h = _devig_decimal({"home": home_odds, "draw": draw_odds, "away": away_odds})
            if not {"home", "draw", "away"}.issubset(h2h):
                continue
            over_odds = _first_number(row, ["AvgC>2.5", "B365C>2.5", "PC>2.5", "MaxC>2.5", "BFEC>2.5"]) or _first_number(
                row, ["Avg>2.5", "B365>2.5", "P>2.5", "Max>2.5", "BFE>2.5"]
            )
            under_odds = _first_number(row, ["AvgC<2.5", "B365C<2.5", "PC<2.5", "MaxC<2.5", "BFEC<2.5"]) or _first_number(
                row, ["Avg<2.5", "B365<2.5", "P<2.5", "Max<2.5", "BFE<2.5"]
            )
            total_probs = _devig_decimal({"over": over_odds, "under": under_odds})
            home_lambda, away_lambda, fit_error = _fit_lambdas_vectorized_equivalent(h2h, total_probs.get("over"))
            rows.append(
                {
                    "season": season_label,
                    "match_date": match_date.date(),
                    "home_team": row.get("HomeTeam"),
                    "away_team": row.get("AwayTeam"),
                    "home_team_key": _canon_team(row.get("HomeTeam")),
                    "away_team_key": _canon_team(row.get("AwayTeam")),
                    "home_goals": _safe_float(row.get("FTHG")),
                    "away_goals": _safe_float(row.get("FTAG")),
                    "home_lambda_odds": home_lambda,
                    "away_lambda_odds": away_lambda,
                    "home_cs_prob_odds": math.exp(-away_lambda),
                    "away_cs_prob_odds": math.exp(-home_lambda),
                    "odds_fit_error": fit_error,
                    "home_win_prob": h2h.get("home"),
                    "draw_prob": h2h.get("draw"),
                    "away_win_prob": h2h.get("away"),
                    "closing_odds_used": bool(closing_h and closing_d and closing_a),
                }
            )
    return pd.DataFrame(rows)


def _fallback_team_strength(fixtures: pd.DataFrame) -> pd.DataFrame:
    team_ids = sorted(
        set(pd.to_numeric(fixtures["team_h"], errors="coerce").dropna().astype(int))
        | set(pd.to_numeric(fixtures["team_a"], errors="coerce").dropna().astype(int))
    )
    return pd.DataFrame(
        {
            "id": team_ids,
            "name": [f"Replay Team {team_id}" for team_id in team_ids],
            "strength_attack_home": 1000,
            "strength_attack_away": 1000,
            "strength_defence_home": 1000,
            "strength_defence_away": 1000,
        }
    )


def _apply_fpl_strength_fallback(fixtures: pd.DataFrame) -> pd.DataFrame:
    if fixtures.empty:
        return fixtures.copy()
    out = fixtures.copy()
    required = ["home_xg", "away_xg", "home_cs_prob", "away_cs_prob"]
    for col in required:
        if col not in out.columns:
            out[col] = np.nan
    missing = out[required].isna().any(axis=1)
    out["fixture_lambda_source"] = np.where(missing, "fpl_strength_fallback", "football_data_closing_odds")
    if not missing.any():
        out["home_xa"] = pd.to_numeric(out["home_xg"], errors="coerce") * 0.73
        out["away_xa"] = pd.to_numeric(out["away_xg"], errors="coerce") * 0.73
        return out
    fallback = forecast_fixture_lambdas(out.loc[missing].copy(), _fallback_team_strength(out))
    for col in ["home_xg", "away_xg", "home_xa", "away_xa", "home_cs_prob", "away_cs_prob"]:
        if col in fallback.columns:
            out.loc[missing, col] = fallback[col].to_numpy()
    out["home_xa"] = pd.to_numeric(out.get("home_xa"), errors="coerce").fillna(pd.to_numeric(out["home_xg"], errors="coerce") * 0.73)
    out["away_xa"] = pd.to_numeric(out.get("away_xa"), errors="coerce").fillna(pd.to_numeric(out["away_xg"], errors="coerce") * 0.73)
    return out


def build_replay_fixtures(root: Path, seasons: list[str] | None = None) -> pd.DataFrame:
    raw = load_vaastav_seasons(root)
    if seasons:
        raw = raw.loc[raw["season"].isin(seasons)].copy()
    odds = _load_replay_odds_features(root, seasons=seasons)
    fixtures = build_fixture_table(raw, odds)
    return _apply_fpl_strength_fallback(fixtures)


def _minutes_report_from_player_fixture(player_fixture: pd.DataFrame) -> pd.DataFrame:
    """Per-player minutes reporting columns, read off the core's resolved frame.

    Replaces the deleted ``derive_replay_minutes``: the Minutes Engine already
    resolved these values (scored from the adapter-supplied historical
    features), so reporting reads the outcome instead of re-deriving it.
    """
    report = player_fixture.drop_duplicates(["event", "player_id"])[
        [
            "event",
            "player_id",
            "play_probability",
            "start_probability",
            "likely_minutes",
            "expected_minutes",
            "minutes_model_source",
        ]
    ].rename(
        columns={
            "event": "GW",
            "play_probability": "pred_play_prob",
            "start_probability": "pred_start_prob",
            "likely_minutes": "pred_mins_if_play",
            "expected_minutes": "replay_expected_minutes",
        }
    )
    return report.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Candidate #3 Phase 2: the historical Data Adapter + core shadow test.
# The legacy orchestration above/below is UNTOUCHED; these functions run the
# shared projection core side-by-side to quantify drift before any migration.
# ---------------------------------------------------------------------------


def _historical_teams_frame(season_frame: pd.DataFrame) -> pd.DataFrame:
    """Adapt season rows to the teams-dimension shape the core expects."""
    teams = (
        season_frame[["team_id", "team_key"]]
        .dropna(subset=["team_id"])
        .drop_duplicates("team_id")
    )
    frame = pd.DataFrame(
        {
            "id": pd.to_numeric(teams["team_id"], errors="coerce").astype(int),
            "name": teams["team_key"].astype(str),
        }
    )
    # League-table context is unknown historically at this grain; the minutes
    # features tolerate NaN, but the columns must exist.
    for column in ("position", "points", "played"):
        frame[column] = np.nan
    return frame


def _element_summary_history(season_frame: pd.DataFrame, gw: int) -> dict[int, pd.DataFrame]:
    """Adapt pre-GW season rows to element-summary-shaped history frames.

    Unlike the legacy ``_history_for_gw`` (minutes/starts only), the core's
    minutes features need ``round`` and ``kickoff_time`` to build rolling
    windows -- providing them is the adapter's job.
    """
    prior = season_frame.loc[pd.to_numeric(season_frame["GW"], errors="coerce") < int(gw)].copy()
    if prior.empty:
        return {}
    prior = prior[["player_id", "GW", "actual_minutes", "starts", "match_date"]].rename(
        columns={"GW": "round", "actual_minutes": "minutes", "match_date": "kickoff_time"}
    )
    return {
        int(player_id): group[["round", "minutes", "starts", "kickoff_time"]].copy()
        for player_id, group in prior.groupby("player_id")
    }


def _prepared_minutes_features(minutes_gw: pd.DataFrame | None) -> pd.DataFrame | None:
    """Adapt the historical minutes-features slice to the engine's L2 seam.

    The features come from ``build_historical_minutes_features`` (availability,
    standings, fixture congestion -- fidelity the live builder cannot recover
    from element-summary history). The engine maps scores by
    (``event``, ``player_id``), so the adapter supplies an integer ``event``
    key and one row per player (keep-first, matching the legacy
    ``derive_replay_minutes`` dedupe).
    """
    if minutes_gw is None or minutes_gw.empty:
        return None
    features = minutes_gw.copy()
    if "event" not in features.columns:
        features["event"] = pd.to_numeric(features["GW"], errors="coerce")
    features = features.dropna(subset=["event"])
    features["event"] = features["event"].astype(int)
    return features.drop_duplicates(["event", "player_id"], keep="first")


def build_historical_inputs(
    players: pd.DataFrame,
    fixture_frame: pd.DataFrame,
    season_frame: pd.DataFrame,
    season: str,
    gw: int,
    minutes_bundle: dict[str, Any] | None,
    minutes_features: pd.DataFrame | None = None,
) -> ProjectionInputs:
    """The historical Data Adapter: vaastav/odds frames -> ProjectionInputs.

    Manual minutes inputs are empty by construction (none existed
    historically); the rulebook is the season's own era rules; and
    ``minutes_features`` carries the point-in-time historical model features
    (``build_historical_minutes_features``) through the L2 feature seam so
    the engine scores the same high-fidelity inputs the legacy path scored.
    """
    return ProjectionInputs(
        players=players,
        teams=_historical_teams_frame(season_frame),
        fixtures_forecast=fixture_frame,
        history_by_player=_element_summary_history(season_frame, gw),
        rulebook=rulebook_for_season(season),
        minutes_inputs=MinutesRunInputs(),
        minutes_model_bundle=minutes_bundle,
        minutes_features=_prepared_minutes_features(minutes_features),
    )


def _fixture_input_frame(fixtures: pd.DataFrame, season: str, gw: int) -> pd.DataFrame:
    frame = fixtures.loc[(fixtures["season"] == season) & (pd.to_numeric(fixtures["GW"], errors="coerce") == int(gw))].copy()
    if frame.empty:
        return frame
    frame["home_xa"] = pd.to_numeric(frame.get("home_xa"), errors="coerce").fillna(pd.to_numeric(frame["home_xg"], errors="coerce") * 0.73)
    frame["away_xa"] = pd.to_numeric(frame.get("away_xa"), errors="coerce").fillna(pd.to_numeric(frame["away_xg"], errors="coerce") * 0.73)
    keep = [
        "id",
        "event",
        "kickoff_time",
        "team_h",
        "team_a",
        "home_xg",
        "away_xg",
        "home_xa",
        "away_xa",
        "home_cs_prob",
        "away_cs_prob",
        "fixture_lambda_source",
    ]
    return frame[[col for col in keep if col in frame.columns]]


def _rename_kft_weekly(weekly: pd.DataFrame) -> pd.DataFrame:
    renamed = weekly.rename(
        columns={
            "event": "GW",
            "xPts": "kft_xpts",
            "xG": "kft_xg",
            "xA": "kft_xa",
            "expected_minutes": "kft_expected_minutes",
        }
    )
    return renamed


def _prepare_ml_features(
    ml_frame: pd.DataFrame,
    replay_weekly: pd.DataFrame,
    season: str,
    gw: int,
) -> pd.DataFrame:
    scoped = ml_frame.loc[
        (ml_frame["season"] == season)
        & (pd.to_numeric(ml_frame["GW"], errors="coerce") == int(gw))
    ].copy()
    if scoped.empty:
        return scoped
    replay_columns = [
        col
        for col in ["season", "GW", "player_id", "position", *KFT_COLS, *MINUTES_ML_COLS]
        if col in replay_weekly.columns
    ]
    replay = replay_weekly[replay_columns].drop_duplicates(["season", "GW", "player_id"])
    replay = replay.rename(columns={"position": "replay_position"})
    scoped = scoped.drop(columns=[col for col in [*KFT_COLS, *MINUTES_ML_COLS] if col in scoped.columns], errors="ignore")
    scoped = scoped.merge(replay, on=["season", "GW", "player_id"], how="inner")
    if "position" not in scoped.columns:
        scoped["position"] = scoped["replay_position"]
    else:
        scoped["position"] = scoped["position"].fillna(scoped["replay_position"])
    return scoped.drop(columns=["replay_position"], errors="ignore")


def _predict_ml(features: pd.DataFrame, bundles: dict[str, dict[str, Any]]) -> pd.DataFrame:
    frames = []
    for position, bundle in bundles.items():
        pos = features.loc[features["position"] == position].copy()
        if pos.empty:
            continue
        frames.append(predict_with_bundle(bundle, pos))
    if not frames:
        return pd.DataFrame(columns=["season", "GW", "player_id", "ml_xpts", "ml_xpts_xgb", "ml_xpts_rf"])
    pred = pd.concat(frames, ignore_index=True)
    keep = [
        col
        for col in [
            "season",
            "GW",
            "player_id",
            "ml_xpts",
            "ml_xpts_xgb",
            "ml_xpts_rf",
            "ml_xpts_pre_minutes",
        ]
        if col in pred.columns
    ]
    return pred[keep]


def apply_ml_weighting(player_fixture: pd.DataFrame, ml_predictions: pd.DataFrame, cap: float = 2.5) -> pd.DataFrame:
    if player_fixture.empty or ml_predictions.empty:
        return player_fixture.copy()
    pred = ml_predictions.rename(columns={"GW": "event"})[["event", "player_id", "ml_xpts"]].drop_duplicates(["event", "player_id"])
    out = player_fixture.merge(pred, on=["event", "player_id"], how="left")
    active = pd.to_numeric(out["expected_minutes"], errors="coerce").fillna(0.0) > 0
    team_mean = (
        out.loc[active]
        .groupby(["fixture", "team"])["ml_xpts"]
        .transform("mean")
        .reindex(out.index)
    )
    multiplier = pd.to_numeric(out["ml_xpts"], errors="coerce") / team_mean
    multiplier = multiplier.replace([np.inf, -np.inf], np.nan).fillna(1.0).clip(lower=0.0, upper=float(cap))
    open_xg = (pd.to_numeric(out["xG"], errors="coerce").fillna(0.0) - pd.to_numeric(out.get("pen_xG", 0.0), errors="coerce").fillna(0.0)).clip(lower=0.0)
    out["ml_weight_multiplier"] = multiplier
    out["xG"] = open_xg * multiplier + pd.to_numeric(out.get("pen_xG", 0.0), errors="coerce").fillna(0.0)
    out["xA"] = pd.to_numeric(out["xA"], errors="coerce").fillna(0.0) * multiplier
    out["xGA_exp"] = out["xG"] + out["xA"]
    return out


def pad_missing_mc_sides(player_fixture: pd.DataFrame, fixture_frame: pd.DataFrame) -> pd.DataFrame:
    if player_fixture.empty or fixture_frame.empty:
        return player_fixture.copy()
    out = player_fixture.copy()
    rows: list[dict[str, Any]] = []
    existing = {
        str(fixture): set(pd.to_numeric(group["team"], errors="coerce").dropna().astype(int).tolist())
        for fixture, group in out.groupby("fixture", dropna=False)
    }
    for _, fixture in fixture_frame.iterrows():
        fixture_id = fixture.get("id")
        if fixture_id is None or pd.isna(fixture_id):
            continue
        fixture_key = str(fixture_id)
        present = existing.get(fixture_key, set())
        if not present:
            continue
        home_team = int(fixture["team_h"])
        away_team = int(fixture["team_a"])
        for team_id, opponent_id, side in [(home_team, away_team, "home"), (away_team, home_team, "away")]:
            if team_id in present:
                continue
            team_xg = float(fixture["home_xg"] if side == "home" else fixture["away_xg"])
            opponent_xg = float(fixture["away_xg"] if side == "home" else fixture["home_xg"])
            team_xa = float(fixture.get("home_xa" if side == "home" else "away_xa", team_xg * 0.73))
            cs_prob = float(fixture["home_cs_prob"] if side == "home" else fixture["away_cs_prob"])
            dummy_id = -1_000_000_000 - len(rows)
            rows.append(
                {
                    "fixture": fixture_id,
                    "event": fixture.get("event"),
                    "kickoff_time": fixture.get("kickoff_time"),
                    "team": team_id,
                    "opponent": opponent_id,
                    "was_home": side == "home",
                    "player_id": dummy_id,
                    "web_name": "__dummy_missing_side__",
                    "position": "MID",
                    "expected_minutes": 0.0,
                    "likely_minutes": 0.0,
                    "start_probability": 0.0,
                    "play_probability": 0.0,
                    "team_xg": team_xg,
                    "team_xa": team_xa,
                    "opponent_xg": opponent_xg,
                    "cs_prob": cs_prob,
                    "xG": 0.0,
                    "xA": 0.0,
                    "xGA_exp": 0.0,
                    "xPts": 0.0,
                    "P1_GA": 0.0,
                    "pen_xG": 0.0,
                    "penalty_share": 0.0,
                    "set_piece_share": 0.0,
                    "xg90_shrunk": 0.0,
                    "xa90_shrunk": 0.0,
                    "defcon90": 0.0,
                    "saves90": 0.0,
                    "yc_rate": 0.0,
                    "rc_rate": 0.0,
                }
            )
    if rows:
        out = pd.concat([out, pd.DataFrame(rows)], ignore_index=True, sort=False)
    return out


def _prefix_mc(mc: pd.DataFrame, prefix: str) -> pd.DataFrame:
    if mc.empty:
        return pd.DataFrame(columns=["GW", "player_id"])
    keep = ["event", "player_id"] + [col for col in MC_COLS if col in mc.columns]
    out = mc[keep].rename(columns={"event": "GW"}).copy()
    rename = {col: f"{prefix}_{col}" for col in MC_COLS if col in out.columns}
    return out.rename(columns=rename)


def _init_worker(
    dataset: pd.DataFrame,
    fixtures: pd.DataFrame,
    ml_frame: pd.DataFrame,
    bundles: dict[str, dict[str, Any]],
    minutes_frame: pd.DataFrame,
    minutes_bundle: dict[str, Any] | None,
    config: ReplayConfig,
) -> None:
    _WORK["dataset"] = dataset
    _WORK["fixtures"] = fixtures
    _WORK["ml_frame"] = ml_frame
    _WORK["bundles"] = bundles
    _WORK["minutes_frame"] = minutes_frame
    _WORK["minutes_bundle"] = minutes_bundle
    _WORK["config"] = config


def _run_gw(task: tuple[str, int, int]) -> dict[str, Any]:
    season, gw, ordinal = task
    try:
        dataset: pd.DataFrame = _WORK["dataset"]
        fixtures: pd.DataFrame = _WORK["fixtures"]
        ml_frame: pd.DataFrame = _WORK["ml_frame"]
        bundles: dict[str, dict[str, Any]] = _WORK["bundles"]
        minutes_frame: pd.DataFrame = _WORK["minutes_frame"]
        minutes_bundle: dict[str, Any] | None = _WORK["minutes_bundle"]
        config: ReplayConfig = _WORK["config"]

        season_frame = dataset.loc[dataset["season"] == season].sort_values(["GW", "player_id"]).copy()
        gw_frame = season_frame.loc[
            (pd.to_numeric(season_frame["GW"], errors="coerce") == int(gw))
            & (season_frame["complete_features"].astype(bool))
        ].copy()
        if gw_frame.empty:
            return {"season": season, "GW": gw, "predictions": pd.DataFrame(), "skipped": "no_complete_feature_rows"}
        minutes_gw = minutes_frame.loc[
            (minutes_frame["season"] == season)
            & (pd.to_numeric(minutes_frame["GW"], errors="coerce") == int(gw))
        ].copy()
        if minutes_gw.empty:
            minutes_gw = gw_frame

        fixture_frame = _fixture_input_frame(fixtures, season, gw)
        if fixture_frame.empty:
            return {"season": season, "GW": gw, "predictions": pd.DataFrame(), "skipped": "no_fixture_inputs"}

        players = _player_input_frame(gw_frame)
        players = players.loc[pd.to_numeric(players["team"], errors="coerce").fillna(0).astype(int) > 0].copy()
        if players.empty:
            return {"season": season, "GW": gw, "predictions": pd.DataFrame(), "skipped": "no_player_inputs"}

        # Candidate #3 Phase 4: the replay drives the unified projection core
        # through the historical Data Adapter. Era rules, empty manual
        # minutes, and the point-in-time minutes features all travel inside
        # ProjectionInputs; the stage sequence lives once, in the core.
        # (Zero-drift equivalence with the deleted legacy orchestration was
        # proven by the Phase 2/3 shadow tests.)
        season_rulebook = rulebook_for_season(season)
        inputs = build_historical_inputs(
            players,
            fixture_frame,
            season_frame,
            season,
            int(gw),
            minutes_bundle,
            minutes_features=minutes_gw,
        )
        stages = run_projection_stages(
            inputs,
            config=AppConfig(
                n_sim=config.n_sim,
                random_seed=config.random_seed,
                form_blend_weight=config.form_blend_weight,
                set_piece_xa_weight=config.set_piece_xa_weight,
            ),
            include_mc=False,  # the replay runs its own padded + ML-weighted MC below
        )
        player_fixture = stages["player_fixture"]
        if player_fixture.empty:
            return {"season": season, "GW": gw, "predictions": pd.DataFrame(), "skipped": "empty_player_fixture"}

        weekly = _rename_kft_weekly(stages["weekly"])
        weekly["season"] = season
        minutes_report = _minutes_report_from_player_fixture(player_fixture)
        weekly = weekly.merge(
            minutes_report[["GW", "player_id", *MINUTES_ML_COLS]],
            on=["GW", "player_id"],
            how="left",
        )
        ml_features = _prepare_ml_features(ml_frame, weekly, season, int(gw))
        ml_predictions = _predict_ml(ml_features, bundles)

        seed = int(config.random_seed + ordinal * 1009 + int(gw))
        mc_fixture = pad_missing_mc_sides(player_fixture, fixture_frame)
        baseline_mc = _prefix_mc(
            simulate_player_week(mc_fixture, config.n_sim, seed, rulebook=season_rulebook),
            "mc_baseline",
        )
        weighted_fixture = apply_ml_weighting(player_fixture, ml_predictions)
        weighted_fixture = pad_missing_mc_sides(weighted_fixture, fixture_frame)
        weighted_mc = _prefix_mc(
            simulate_player_week(weighted_fixture, config.n_sim, seed + 17, rulebook=season_rulebook),
            "mc_ml_weighted",
        )

        base_cols = [
            "season",
            "GW",
            "player_id",
            "element",
            "player_name",
            "team",
            "team_key",
            "position",
            "actual_points",
            "actual_minutes",
            "actual_goals",
            "actual_assists",
            "actual_bonus",
            "match_date",
            "complete_features",
            "fixture_count",
            "odds_fixture_count",
            "team_lambda_odds",
            "opponent_lambda_odds",
            "cs_prob_odds",
        ]
        out = gw_frame[[col for col in base_cols if col in gw_frame.columns]].copy()
        out = out.merge(minutes_report.drop(columns=["GW"], errors="ignore"), on="player_id", how="left")
        weekly_for_output = weekly[["season", "GW", "player_id", "position", *KFT_COLS]].rename(columns={"position": "replay_position"})
        out = out.merge(weekly_for_output, on=["season", "GW", "player_id"], how="left")
        if "position" not in out.columns:
            out["position"] = out["replay_position"]
        else:
            out["position"] = out["position"].fillna(out["replay_position"])
        out = out.drop(columns=["replay_position"], errors="ignore")
        out = out.merge(ml_predictions, on=["season", "GW", "player_id"], how="left")
        out = out.merge(baseline_mc, on=["GW", "player_id"], how="left")
        out = out.merge(weighted_mc, on=["GW", "player_id"], how="left")
        out["scoring_gk_goal_points"] = int(season_rulebook.goal_points_for("GK"))
        out["scoring_defcon_active"] = bool(season_rulebook.defcon_active)
        out["scoring_assist_rules_version"] = str(season_rulebook.assist_rules_version)
        out["scoring_bps_version"] = str(season_rulebook.bps_version)

        sources = (
            fixtures.loc[(fixtures["season"] == season) & (pd.to_numeric(fixtures["GW"], errors="coerce") == int(gw))]
            .get("fixture_lambda_source", pd.Series(dtype=object))
            .dropna()
            .astype(str)
            .value_counts()
            .to_dict()
        )
        out["fixture_lambda_sources"] = ";".join(f"{key}:{value}" for key, value in sorted(sources.items()))
        return {"season": season, "GW": gw, "predictions": out, "skipped": ""}
    except Exception as exc:  # pragma: no cover - exercised by real replay failures.
        return {
            "season": season,
            "GW": gw,
            "predictions": pd.DataFrame(),
            "skipped": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }


def spearman_corr(actual: pd.Series, predicted: pd.Series) -> float:
    usable = pd.DataFrame({"actual": actual, "predicted": predicted}).dropna()
    if len(usable) < 2:
        return float("nan")
    return float(usable["actual"].rank().corr(usable["predicted"].rank(), method="pearson"))


def _per_gw_metrics(frame: pd.DataFrame, pred_col: str) -> pd.DataFrame:
    rows = []
    for (season, gw), group in frame.groupby(["season", "GW"], dropna=False):
        usable = group.dropna(subset=["actual_points", pred_col]).copy()
        if usable.empty:
            continue
        error = pd.to_numeric(usable[pred_col], errors="coerce") - pd.to_numeric(usable["actual_points"], errors="coerce")
        high = usable.loc[pd.to_numeric(usable["actual_points"], errors="coerce") > 2].copy()
        high_error = pd.to_numeric(high[pred_col], errors="coerce") - pd.to_numeric(high["actual_points"], errors="coerce")
        rows.append(
            {
                "season": season,
                "GW": int(gw),
                "rows": int(len(usable)),
                "gt2_rows": int(len(high)),
                "mae": float(error.abs().mean()),
                "gt2_mae": float(high_error.abs().mean()) if not high.empty else np.nan,
                "spearman": spearman_corr(usable["actual_points"], usable[pred_col]),
            }
        )
    return pd.DataFrame(rows)


def _mean_std(values: pd.Series) -> tuple[float, float]:
    clean = pd.to_numeric(values, errors="coerce").dropna()
    if clean.empty:
        return np.nan, np.nan
    return float(clean.mean()), float(clean.std(ddof=1) if len(clean) > 1 else 0.0)


def build_metrics(predictions: pd.DataFrame) -> pd.DataFrame:
    variants = {
        "KFT rules": "kft_xpts",
        "KFT ML ensemble": "ml_xpts",
        "MC baseline": "mc_baseline_MC_MeanPts",
        "MC ML-weighted": "mc_ml_weighted_MC_MeanPts",
    }
    scopes: list[tuple[str, str, str, pd.DataFrame]] = [("overall", "all", "all", predictions)]
    scopes.extend(("season", str(season), "all", group) for season, group in predictions.groupby("season", dropna=False))
    scopes.extend(("position", "all", str(pos), group) for pos, group in predictions.groupby("position", dropna=False))
    scopes.extend(
        ("season_position", str(season), str(pos), group)
        for (season, pos), group in predictions.groupby(["season", "position"], dropna=False)
    )
    rows = []
    for scope, season, position, group in scopes:
        for model, pred_col in variants.items():
            if pred_col not in group.columns:
                continue
            per_gw = _per_gw_metrics(group, pred_col)
            mae_mean, mae_std = _mean_std(per_gw.get("mae", pd.Series(dtype=float)))
            gt2_mean, gt2_std = _mean_std(per_gw.get("gt2_mae", pd.Series(dtype=float)))
            sp_mean, sp_std = _mean_std(per_gw.get("spearman", pd.Series(dtype=float)))
            rows.append(
                {
                    "scope": scope,
                    "season": season,
                    "position": position,
                    "model": model,
                    "gw_count": int(len(per_gw)),
                    "rows": int(per_gw["rows"].sum()) if not per_gw.empty else 0,
                    "gt2_rows": int(per_gw["gt2_rows"].sum()) if not per_gw.empty else 0,
                    "overall_mae_mean": mae_mean,
                    "overall_mae_std": mae_std,
                    "gt2_mae_mean": gt2_mean,
                    "gt2_mae_std": gt2_std,
                    "spearman_mean": sp_mean,
                    "spearman_std": sp_std,
                }
            )
    return pd.DataFrame(rows)


def _format_mean_std(mean: object, std: object) -> str:
    if pd.isna(mean):
        return "nan (\u00b1nan)"
    return f"{float(mean):.3f} (\u00b1{float(std) if pd.notna(std) else float('nan'):.3f})"


def _prob_calibration(frame: pd.DataFrame, version: str, pred_col: str, actual_col: str, stat: str) -> pd.DataFrame:
    bins = [-np.inf, 0.05, 0.10, 0.15, 0.20, 0.25, np.inf]
    labels = ["0-5%", "5-10%", "10-15%", "15-20%", "20-25%", "25%+"]
    scoped = frame.dropna(subset=[pred_col, "actual_points"]).copy()
    scoped["_bucket"] = pd.cut(pd.to_numeric(scoped[pred_col], errors="coerce"), bins=bins, labels=labels, right=False)
    scoped["_actual"] = actual_col
    rows = []
    for bucket, group in scoped.groupby("_bucket", observed=False):
        if group.empty:
            rows.append({"mc_version": version, "stat": stat, "bucket": str(bucket), "mean_predicted": np.nan, "actual_value": np.nan, "rows": 0, "gap": np.nan})
            continue
        mean_pred = float(pd.to_numeric(group[pred_col], errors="coerce").mean())
        actual = float(pd.to_numeric(group["_actual"], errors="coerce").mean())
        rows.append({"mc_version": version, "stat": stat, "bucket": str(bucket), "mean_predicted": mean_pred, "actual_value": actual, "rows": int(len(group)), "gap": actual - mean_pred})
    return pd.DataFrame(rows)


def _percentile_calibration(frame: pd.DataFrame, version: str, pred_col: str, q: float, stat: str) -> pd.DataFrame:
    scoped = frame.dropna(subset=[pred_col, "actual_points"]).copy()
    if scoped.empty:
        return pd.DataFrame(columns=["mc_version", "stat", "bucket", "mean_predicted", "actual_value", "rows", "gap"])
    values = pd.to_numeric(scoped[pred_col], errors="coerce")
    lower = int(math.floor(float(values.min())))
    upper = int(math.ceil(float(values.max())))
    bins = list(range(lower, upper + 2))
    labels = [f"{left} to {left + 1}" for left in bins[:-1]]
    scoped["_bucket"] = pd.cut(values, bins=bins, labels=labels, right=False, include_lowest=True)
    rows = []
    for bucket, group in scoped.groupby("_bucket", observed=False):
        if group.empty:
            rows.append({"mc_version": version, "stat": stat, "bucket": str(bucket), "mean_predicted": np.nan, "actual_value": np.nan, "rows": 0, "gap": np.nan})
            continue
        mean_pred = float(pd.to_numeric(group[pred_col], errors="coerce").mean())
        actual = float(np.percentile(pd.to_numeric(group["actual_points"], errors="coerce").dropna(), q))
        rows.append({"mc_version": version, "stat": stat, "bucket": str(bucket), "mean_predicted": mean_pred, "actual_value": actual, "rows": int(len(group)), "gap": actual - mean_pred})
    return pd.DataFrame(rows)


def build_calibration(predictions: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for version, prefix in [("mc_baseline", "mc_baseline"), ("mc_ml_weighted", "mc_ml_weighted")]:
        rows.append(_prob_calibration(predictions, version, f"{prefix}_P_haul", (pd.to_numeric(predictions["actual_points"], errors="coerce") >= 10).astype(float), "P_haul"))
        rows.append(_prob_calibration(predictions, version, f"{prefix}_P_return", (pd.to_numeric(predictions["actual_points"], errors="coerce") >= 6).astype(float), "P_return"))
        rows.append(_percentile_calibration(predictions, version, f"{prefix}_MC_Floor", 10, "MC_Floor"))
        rows.append(_percentile_calibration(predictions, version, f"{prefix}_MC_Upside", 90, "MC_Upside"))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def build_top20_hits(predictions: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for version, p_col, mean_col in [
        ("mc_baseline", "mc_baseline_P_haul", "mc_baseline_MC_MeanPts"),
        ("mc_ml_weighted", "mc_ml_weighted_P_haul", "mc_ml_weighted_MC_MeanPts"),
    ]:
        for (season, gw), group in predictions.groupby(["season", "GW"], dropna=False):
            usable = group.dropna(subset=[p_col, mean_col, "actual_points"]).copy()
            if usable.empty:
                continue
            actual_haul = pd.to_numeric(usable["actual_points"], errors="coerce") >= 10
            p_top = usable.nlargest(min(20, len(usable)), p_col)
            mean_top = usable.nlargest(min(20, len(usable)), mean_col)
            rows.append(
                {
                    "season": season,
                    "GW": int(gw),
                    "mc_version": version,
                    "top20_p_haul_hits": int((pd.to_numeric(p_top["actual_points"], errors="coerce") >= 10).sum()),
                    "top20_mean_hits": int((pd.to_numeric(mean_top["actual_points"], errors="coerce") >= 10).sum()),
                    "random_expected_hits": float(min(20, len(usable)) * actual_haul.mean()),
                    "rows": int(len(usable)),
                }
            )
    return pd.DataFrame(rows)


def _markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "_No rows._"
    text = frame.copy()
    for col in text.columns:
        text[col] = text[col].map(lambda value: "" if pd.isna(value) else str(value).replace("|", "\\|"))
    headers = [str(col).replace("|", "\\|") for col in text.columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for _, row in text.iterrows():
        lines.append("| " + " | ".join(str(row[col]) for col in text.columns) + " |")
    return "\n".join(lines)


def _top20_summary(top20: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for version, group in top20.groupby("mc_version", dropna=False):
        hits = pd.to_numeric(group["top20_p_haul_hits"], errors="coerce")
        mean_hits = pd.to_numeric(group["top20_mean_hits"], errors="coerce")
        rows.append(
            {
                "mc_version": version,
                "gws": int(len(group)),
                "top20_p_haul_mean_hits": float(hits.mean()),
                "top20_mc_mean_mean_hits": float(mean_hits.mean()),
                "random_expected_mean_hits": float(pd.to_numeric(group["random_expected_hits"], errors="coerce").mean()),
                "pct_gws_0_hits": float((hits == 0).mean()),
                "pct_gws_1_2_hits": float(((hits >= 1) & (hits <= 2)).mean()),
                "pct_gws_3_5_hits": float(((hits >= 3) & (hits <= 5)).mean()),
                "pct_gws_5plus_hits": float((hits >= 5).mean()),
            }
        )
    return pd.DataFrame(rows)


def write_findings(
    path: Path,
    predictions: pd.DataFrame,
    metrics: pd.DataFrame,
    calibration: pd.DataFrame,
    skipped: list[dict[str, Any]],
) -> None:
    overall = metrics.loc[(metrics["scope"] == "overall") & (metrics["season"] == "all") & (metrics["position"] == "all")].copy()
    comp_rows = [
        {"Model": "FPL Review*", "Overall MAE": "0.918 (\u00b10.372)", ">2pts MAE": "1.260 (\u00b10.666)", "Spearman": "not reported"},
        {"Model": "OpenFPL*", "Overall MAE": "0.966 (\u00b10.506)", ">2pts MAE": "1.306 (\u00b10.775)", "Spearman": "not reported"},
    ]
    for model in ["KFT rules", "KFT ML ensemble", "MC baseline", "MC ML-weighted"]:
        row = overall.loc[overall["model"] == model]
        if row.empty:
            continue
        r = row.iloc[0]
        comp_rows.append(
            {
                "Model": model,
                "Overall MAE": _format_mean_std(r["overall_mae_mean"], r["overall_mae_std"]),
                ">2pts MAE": _format_mean_std(r["gt2_mae_mean"], r["gt2_mae_std"]),
                "Spearman": _format_mean_std(r["spearman_mean"], r["spearman_std"]),
            }
        )
    comp_rows.append({"Model": "* GWs 32-38 of 2024-25 only per OpenFPL paper", "Overall MAE": "", ">2pts MAE": "", "Spearman": ""})
    comparison = pd.DataFrame(comp_rows)

    position = metrics.loc[(metrics["scope"] == "position")].copy()
    position_view = position[
        ["position", "model", "gw_count", "rows", "overall_mae_mean", "overall_mae_std", "gt2_mae_mean", "gt2_mae_std", "spearman_mean", "spearman_std"]
    ].sort_values(["position", "model"])
    season = metrics.loc[(metrics["scope"] == "season")].copy()
    season_view = season[
        ["season", "model", "gw_count", "rows", "overall_mae_mean", "overall_mae_std", "gt2_mae_mean", "gt2_mae_std", "spearman_mean", "spearman_std"]
    ].sort_values(["season", "model"])
    top20 = _top20_summary(build_top20_hits(predictions))

    cal_summary = (
        calibration.groupby(["mc_version", "stat"], as_index=False)
        .agg(rows=("rows", "sum"), mean_abs_gap=("gap", lambda s: float(pd.to_numeric(s, errors="coerce").abs().mean())))
        .sort_values(["stat", "mc_version"])
    )
    next_fixes = []
    if not cal_summary.empty:
        worst = cal_summary.sort_values("mean_abs_gap", ascending=False).head(1).iloc[0]
        next_fixes.append(f"- Fix `{worst['stat']}` calibration first: `{worst['mc_version']}` has the largest mean absolute calibration gap in this replay output.")
    if not position.empty:
        worst_pos = position.loc[position["model"] == "KFT rules"].sort_values("spearman_mean").head(1)
        if not worst_pos.empty:
            r = worst_pos.iloc[0]
            next_fixes.append(f"- Investigate `{r['position']}` KFT ranking: it has the lowest replay Spearman mean for KFT rules.")
    if not top20.empty:
        delta = top20.set_index("mc_version")
        if {"mc_baseline", "mc_ml_weighted"}.issubset(delta.index):
            diff = float(delta.loc["mc_ml_weighted", "top20_p_haul_mean_hits"] - delta.loc["mc_baseline", "top20_p_haul_mean_hits"])
            next_fixes.append(f"- Revisit ML-weighted MC allocation if needed: top-20 P_haul hit delta versus baseline is {diff:.3f} hits per GW.")
    while len(next_fixes) < 3:
        next_fixes.append("- No additional evidence-backed fix was generated because the replay output did not expose another clear worst bucket.")

    lines = [
        "# Retrospective Replay Findings",
        "",
        "## Full Comparison Table",
        _markdown_table(comparison),
        "",
        "## Position Breakdown",
        _markdown_table(position_view),
        "",
        "## P_haul Top-20 Hit Rate",
        _markdown_table(top20),
        "",
        "## Calibration Summary",
        _markdown_table(cal_summary),
        "",
        "## Season Breakdown",
        _markdown_table(season_view),
        "",
        "## Calibration Interpretation",
    ]
    if not cal_summary.empty and {"mc_baseline", "mc_ml_weighted"}.issubset(set(cal_summary["mc_version"])):
        pivot = cal_summary.pivot(index="stat", columns="mc_version", values="mean_abs_gap")
        better = []
        for stat, row in pivot.iterrows():
            base = row.get("mc_baseline")
            weighted = row.get("mc_ml_weighted")
            if pd.notna(base) and pd.notna(weighted):
                direction = "improved" if weighted < base else "hurt"
                better.append(f"- `{stat}` calibration {direction} under ML weighting: baseline gap {base:.6f}, ML-weighted gap {weighted:.6f}.")
        lines.extend(better or ["No paired calibration comparison could be computed."])
    else:
        lines.append("No paired calibration comparison could be computed.")
    lines.extend(
        [
            "",
            "## Comparison To FPL Review And OpenFPL",
            "The FPL Review and OpenFPL rows are included exactly as requested, but they are GWs 32-38 of 2024-25 only per the OpenFPL paper. KFT replay rows cover the selected replay seasons and should not be treated as the same evaluation slice.",
            "",
            "## Skipped GWs",
            _markdown_table(pd.DataFrame(skipped)) if skipped else "No GWs were skipped.",
            "",
            "## Three Evidence-Based Fixes To Try Next",
            "\n".join(next_fixes[:3]),
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay historical KFT/ML/MC predictions GW by GW.")
    parser.add_argument("--seasons", default=",".join(REPLAY_SEASONS), help="Comma-separated seasons to replay")
    parser.add_argument("--n-sim", type=int, default=1000, help="Monte Carlo simulations per GW")
    parser.add_argument("--workers", type=int, default=max((os.cpu_count() or 2) - 1, 1), help="multiprocessing workers")
    parser.add_argument("--model-dir", default=str(AppConfig().ml_model_dir), help="Directory containing position model bundles")
    parser.add_argument("--minutes-model", default=str(DEFAULT_MINUTES_MODEL_PATH), help="Trained minutes model bundle")
    parser.add_argument("--out-dir", default="outputs/validation", help="Output directory")
    parser.add_argument("--skip-read-pass", action="store_true", help="Skip full repo read pass for local debugging only")
    return parser.parse_args()


def _validate_inputs(root: Path, model_dir: Path) -> None:
    required = [
        root / "data" / "modelling" / "full_training_dataset.csv",
        root / "outputs" / "validation" / "full_historical_predictions.csv",
    ]
    for path in required:
        if not path.exists():
            raise FileNotFoundError(path)
    for season in REPLAY_SEASONS:
        path = root / "data" / "vaastav" / f"{season}_merged_gw.csv"
        if not path.exists():
            raise FileNotFoundError(path)
    for season in ODDS_SEASONS:
        canonical = root / "data" / "odds_historical" / f"E0_{season}.csv"
        legacy = root / "data" / "odds_historical" / f"{season}_E0.csv"
        if not canonical.exists() and not legacy.exists():
            raise FileNotFoundError(canonical)
    for filename in MODEL_FILENAMES.values():
        path = model_dir / filename
        if not path.exists():
            raise FileNotFoundError(path)


def main() -> None:
    args = parse_args()
    seasons = [season.strip() for season in str(args.seasons).split(",") if season.strip()]
    bad = [season for season in seasons if season not in REPLAY_SEASONS]
    if bad:
        raise ValueError(f"Unsupported seasons: {', '.join(bad)}")
    app_config = AppConfig()
    model_dir = (ROOT / args.model_dir).resolve() if not Path(args.model_dir).is_absolute() else Path(args.model_dir)
    out_dir = (ROOT / args.out_dir).resolve() if not Path(args.out_dir).is_absolute() else Path(args.out_dir)

    if not args.skip_read_pass:
        count, total_bytes = _full_read_pass(ROOT)
        print(f"Full repo read pass complete: files={count} bytes={total_bytes}")

    _validate_inputs(ROOT, model_dir)
    print("Inputs validated.")
    print_scoring_rule_report()
    print_replay_season_rules(seasons)

    dataset = pd.read_csv(ROOT / "data" / "modelling" / "full_training_dataset.csv")
    dataset = dataset.loc[dataset["season"].isin(seasons)].copy()
    dataset["GW"] = pd.to_numeric(dataset["GW"], errors="coerce").astype("Int64")
    dataset["complete_features"] = dataset["complete_features"].astype(bool)
    print(f"Loaded training dataset: rows={len(dataset)} complete={int(dataset['complete_features'].sum())}")

    fixtures = build_replay_fixtures(ROOT, seasons=seasons)
    fixtures = fixtures.loc[fixtures["season"].isin(seasons)].copy()
    print(f"Built replay fixtures: rows={len(fixtures)} fallback_rows={(fixtures.get('fixture_lambda_source') == 'fpl_strength_fallback').sum() if 'fixture_lambda_source' in fixtures else 0}")

    ml_frame = build_historical_ml_frame(ROOT)
    ml_frame = ml_frame.loc[ml_frame["season"].isin(seasons)].copy()
    bundles = load_bundles(model_dir)
    print(f"Loaded ML frame rows={len(ml_frame)} and bundles={','.join(sorted(bundles))}")
    minutes_model_path = (ROOT / args.minutes_model).resolve() if not Path(args.minutes_model).is_absolute() else Path(args.minutes_model)
    if minutes_model_path.exists():
        minutes_bundle = load_minutes_bundle(minutes_model_path)
        minutes_frame = build_historical_minutes_features(ROOT, dataset=pd.read_csv(
            ROOT / "data" / "modelling" / "full_training_dataset.csv",
            low_memory=False,
        ))
        minutes_frame = minutes_frame.loc[minutes_frame["season"].isin(seasons)].copy()
        print(f"Loaded minutes model and replay-safe feature frame rows={len(minutes_frame)}")
    else:
        minutes_bundle = None
        minutes_frame = dataset.copy()
        print(f"Minutes model missing at {minutes_model_path}; using rolling heuristic fallback.")

    tasks = []
    ordinal = 0
    for season in seasons:
        gws = sorted(pd.to_numeric(dataset.loc[dataset["season"] == season, "GW"], errors="coerce").dropna().astype(int).unique().tolist())
        for gw in gws:
            ordinal += 1
            tasks.append((season, int(gw), ordinal))

    config = ReplayConfig(
        n_sim=int(args.n_sim),
        random_seed=int(app_config.random_seed),
        form_blend_weight=float(app_config.form_blend_weight),
        set_piece_xa_weight=float(app_config.set_piece_xa_weight),
    )

    predictions = []
    skipped = []
    completed = 0
    if int(args.workers) <= 1:
        _init_worker(dataset, fixtures, ml_frame, bundles, minutes_frame, minutes_bundle, config)
        iterator = map(_run_gw, tasks)
    else:
        import multiprocessing as mp

        pool = mp.Pool(
            processes=int(args.workers),
            initializer=_init_worker,
            initargs=(dataset, fixtures, ml_frame, bundles, minutes_frame, minutes_bundle, config),
        )
        iterator = pool.imap_unordered(_run_gw, tasks)
    try:
        for result in iterator:
            completed += 1
            if result.get("skipped"):
                skipped.append({k: result.get(k) for k in ["season", "GW", "skipped", "traceback"] if result.get(k)})
            elif isinstance(result.get("predictions"), pd.DataFrame) and not result["predictions"].empty:
                predictions.append(result["predictions"])
            if completed % 5 == 0 or completed == len(tasks):
                print(f"Progress: {completed}/{len(tasks)} GWs complete; skipped={len(skipped)}")
    finally:
        if "pool" in locals():
            pool.close()
            pool.join()

    replay = pd.concat(predictions, ignore_index=True) if predictions else pd.DataFrame()
    if replay.empty:
        raise RuntimeError("No retrospective replay predictions were generated.")

    out_dir.mkdir(parents=True, exist_ok=True)
    predictions_path = out_dir / "retrospective_replay_predictions.csv"
    replay.to_csv(predictions_path, index=False, float_format="%.6f")
    print(f"Wrote row-level predictions: {predictions_path} rows={len(replay)}")

    metrics = build_metrics(replay)
    metrics_path = out_dir / "metrics_by_position.csv"
    metrics.to_csv(metrics_path, index=False, float_format="%.6f")
    print(f"Wrote metrics: {metrics_path} rows={len(metrics)}")

    calibration = build_calibration(replay)
    calibration_path = out_dir / "mc_calibration.csv"
    calibration.to_csv(calibration_path, index=False, float_format="%.6f")
    print(f"Wrote calibration: {calibration_path} rows={len(calibration)}")

    findings_path = out_dir / "retrospective_replay_findings.md"
    write_findings(findings_path, replay, metrics, calibration, skipped)
    print(f"Wrote findings: {findings_path}")
    if skipped:
        print("Skipped GW summary:")
        print(pd.DataFrame(skipped)[["season", "GW", "skipped"]].to_string(index=False))


if __name__ == "__main__":
    main()
