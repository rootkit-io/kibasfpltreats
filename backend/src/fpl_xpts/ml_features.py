from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from .historical_validation import VAASTAV_SEASONS, _read_csv, load_odds_match_features, load_vaastav_seasons
from .market_odds import _canon_team
from .scoring import POSITION_BY_ELEMENT_TYPE


POSITIONS = ["GK", "DEF", "MID", "FWD"]
MODEL_FILENAMES = {
    "GK": "gk_model.pkl",
    "DEF": "def_model.pkl",
    "MID": "mid_model.pkl",
    "FWD": "fwd_model.pkl",
}

OPENFPL_REFERENCE: dict[str, dict[str, float]] = {
    "overall": {
        "openfpl_rmse_zeros": 0.818,
        "openfpl_mae_zeros": 0.427,
        "openfpl_rmse_blanks": 1.291,
        "openfpl_mae_blanks": 0.749,
        "openfpl_rmse_tickers": 1.517,
        "openfpl_mae_tickers": 1.127,
        "openfpl_rmse_haulers": 5.142,
        "openfpl_mae_haulers": 4.317,
    },
    "GK": {
        "openfpl_rmse_zeros": 0.616,
        "openfpl_mae_zeros": 0.208,
        "openfpl_rmse_blanks": 0.888,
        "openfpl_mae_blanks": 0.406,
        "openfpl_rmse_tickers": 1.180,
        "openfpl_mae_tickers": 0.807,
        "openfpl_rmse_haulers": 5.678,
        "openfpl_mae_haulers": 4.960,
    },
    "DEF": {
        "openfpl_rmse_zeros": 0.812,
        "openfpl_mae_zeros": 0.482,
        "openfpl_rmse_blanks": 1.129,
        "openfpl_mae_blanks": 0.723,
        "openfpl_rmse_tickers": 1.448,
        "openfpl_mae_tickers": 1.223,
        "openfpl_rmse_haulers": 5.062,
        "openfpl_mae_haulers": 4.505,
    },
    "MID": {
        "openfpl_rmse_zeros": 0.902,
        "openfpl_mae_zeros": 0.454,
        "openfpl_rmse_blanks": 1.189,
        "openfpl_mae_blanks": 0.744,
        "openfpl_rmse_tickers": 1.375,
        "openfpl_mae_tickers": 1.020,
        "openfpl_rmse_haulers": 5.274,
        "openfpl_mae_haulers": 4.235,
    },
    "FWD": {
        "openfpl_rmse_zeros": 0.719,
        "openfpl_mae_zeros": 0.410,
        "openfpl_rmse_blanks": 1.024,
        "openfpl_mae_blanks": 0.646,
        "openfpl_rmse_tickers": 2.694,
        "openfpl_mae_tickers": 2.266,
        "openfpl_rmse_haulers": 5.235,
        "openfpl_mae_haulers": 4.722,
    },
}

LEAKAGE_COLUMNS = {
    "actual_points",
    "actual_minutes",
    "actual_goals",
    "actual_assists",
    "actual_bonus",
    "expected_goals",
    "expected_assists",
    "expected_goals_conceded",
    "goals_scored",
    "assists",
    "bonus",
    "clean_sheets",
    "goals_conceded",
    "own_goals",
    "penalties_missed",
    "penalties_saved",
    "red_cards",
    "yellow_cards",
    "saves",
    "starts",
    "played",
    "started",
    "xG",
    "xA",
    "npxG",
    "understat_shots",
    "understat_key_passes",
}

IDENTIFIER_COLUMNS = {
    "season",
    "GW",
    "player_id",
    "element",
    "player_name",
    "understat_player_id",
    "understat_player_name",
    "understat_position",
    "match_date",
    "date",
    "feature_failure_reasons",
}

FORCED_CATEGORICAL_COLUMNS = {
    "position",
    "team_key",
    "derived_team_key",
    "opponent_team_key",
    "availability_category",
    "fpl_status",
    "understat_match_method",
}


def _num(series: pd.Series, default: float = np.nan) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(default)


def _availability_category(value: object) -> str:
    if value is None or pd.isna(value):
        return "unknown"
    try:
        chance = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if chance <= 0:
        return "chance_0"
    if chance <= 25:
        return "chance_25"
    if chance <= 50:
        return "chance_50"
    if chance <= 75:
        return "chance_75"
    return "chance_100"


def add_availability_features(frame: pd.DataFrame, historical: bool) -> pd.DataFrame:
    out = frame.copy()
    if "chance_of_playing_this_round" not in out.columns:
        out["chance_of_playing_this_round"] = np.nan
    if "chance_of_playing_next_round" not in out.columns:
        out["chance_of_playing_next_round"] = np.nan
    if "status" in out.columns and "fpl_status" not in out.columns:
        out["fpl_status"] = out["status"].astype(str)
    elif "fpl_status" not in out.columns:
        out["fpl_status"] = "unknown"

    if historical and out["chance_of_playing_this_round"].isna().all():
        out["availability_category"] = "historical_unknown"
        out["fpl_status"] = out["fpl_status"].replace({"nan": "historical_unknown", "None": "historical_unknown"})
        out.loc[out["fpl_status"].isin(["", "unknown"]), "fpl_status"] = "historical_unknown"
    else:
        out["availability_category"] = out["chance_of_playing_this_round"].apply(_availability_category)
        out["fpl_status"] = out["fpl_status"].replace({"nan": "unknown", "None": "unknown", "": "unknown"})
    return out


def add_shifted_rolling_features(frame: pd.DataFrame, windows: Iterable[int] = (10,)) -> pd.DataFrame:
    out = frame.copy()
    out["_sort_date"] = pd.to_datetime(out.get("date", out.get("match_date")), errors="coerce")
    out["_xg_for_roll"] = pd.to_numeric(out.get("expected_goals", np.nan), errors="coerce")
    out["_xa_for_roll"] = pd.to_numeric(out.get("expected_assists", np.nan), errors="coerce")
    out["_minutes_for_roll"] = _num(out.get("actual_minutes", pd.Series(np.nan, index=out.index)))

    group_keys = ["season", "player_id"] if "player_id" in out.columns else ["season", "element"]
    out = out.sort_values([*group_keys, "GW", "_sort_date"], kind="mergesort").copy()
    grouped = out.groupby(group_keys, dropna=False, sort=False)

    shifted_points = grouped["actual_points"].shift(1) if "actual_points" in out.columns else pd.Series(np.nan, index=out.index)
    shifted_minutes = grouped["_minutes_for_roll"].shift(1)
    shifted_xg = grouped["_xg_for_roll"].shift(1)
    shifted_xa = grouped["_xa_for_roll"].shift(1)
    shifted_played = (shifted_minutes > 0).astype(float)
    shifted_started = grouped.get_group if False else grouped["starts"].shift(1) if "starts" in out.columns else pd.Series(np.nan, index=out.index)

    for window in windows:
        out[f"rolling_points_{window}gw"] = (
            shifted_points.groupby([out[key] for key in group_keys], dropna=False, sort=False)
            .rolling(window, min_periods=1)
            .mean()
            .reset_index(level=list(range(len(group_keys))), drop=True)
        )
        rolling_minutes_sum = (
            shifted_minutes.groupby([out[key] for key in group_keys], dropna=False, sort=False)
            .rolling(window, min_periods=1)
            .sum()
            .reset_index(level=list(range(len(group_keys))), drop=True)
        )
        out[f"rolling_minutes_{window}gw"] = rolling_minutes_sum
        xg_sum = (
            shifted_xg.groupby([out[key] for key in group_keys], dropna=False, sort=False)
            .rolling(window, min_periods=1)
            .sum()
            .reset_index(level=list(range(len(group_keys))), drop=True)
        )
        xa_sum = (
            shifted_xa.groupby([out[key] for key in group_keys], dropna=False, sort=False)
            .rolling(window, min_periods=1)
            .sum()
            .reset_index(level=list(range(len(group_keys))), drop=True)
        )
        out[f"rolling_xg90_{window}gw"] = np.where(rolling_minutes_sum > 0, xg_sum / rolling_minutes_sum * 90.0, np.nan)
        out[f"rolling_xa90_{window}gw"] = np.where(rolling_minutes_sum > 0, xa_sum / rolling_minutes_sum * 90.0, np.nan)
        out[f"rolling_played_{window}gw"] = (
            shifted_played.groupby([out[key] for key in group_keys], dropna=False, sort=False)
            .rolling(window, min_periods=1)
            .mean()
            .reset_index(level=list(range(len(group_keys))), drop=True)
        )
        out[f"rolling_started_{window}gw"] = (
            pd.to_numeric(shifted_started, errors="coerce")
            .groupby([out[key] for key in group_keys], dropna=False, sort=False)
            .rolling(window, min_periods=1)
            .mean()
            .reset_index(level=list(range(len(group_keys))), drop=True)
        )

    return out.drop(columns=["_sort_date", "_xg_for_roll", "_xa_for_roll", "_minutes_for_roll"], errors="ignore")


def _vaastav_player_context(root: Path) -> pd.DataFrame:
    rows = []
    for season in VAASTAV_SEASONS:
        path = root / "data" / "vaastav" / f"{season}_merged_gw.csv"
        if not path.exists():
            continue
        df = _read_csv(path)
        df["season"] = season
        keep = [col for col in ["season", "GW", "element", "fixture", "was_home", "opponent_team", "team"] if col in df.columns]
        if {"season", "GW", "element"}.issubset(keep):
            rows.append(df[keep].copy())
    if not rows:
        return pd.DataFrame()
    out = pd.concat(rows, ignore_index=True)
    out["GW"] = pd.to_numeric(out["GW"], errors="coerce")
    out["element"] = pd.to_numeric(out["element"], errors="coerce")
    if "team" in out.columns:
        out["vaastav_team_key"] = out["team"].apply(_canon_team)
    return out.drop_duplicates(["season", "GW", "element"], keep="first")


def _fixture_context(root: Path) -> pd.DataFrame:
    odds_rows = []
    odds = load_odds_match_features(root)
    if not odds.empty:
        odds["match_date"] = pd.to_datetime(odds["match_date"], errors="coerce").dt.date
        for side in ["home", "away"]:
            is_home = side == "home"
            odds_rows.append(
                pd.DataFrame(
                    {
                        "season": odds["season"],
                        "match_date": odds["match_date"],
                        "team_key": odds["home_team_key"] if is_home else odds["away_team_key"],
                        "opponent_team_key": odds["away_team_key"] if is_home else odds["home_team_key"],
                        "is_home": float(is_home),
                    }
                )
            )
    context = pd.concat(odds_rows, ignore_index=True) if odds_rows else pd.DataFrame()
    try:
        raw = load_vaastav_seasons(root)
        from .historical_validation import build_fixture_table

        fixtures = build_fixture_table(raw, odds)
        vaastav = _vaastav_player_context(root)
        if not fixtures.empty and not vaastav.empty:
            player_context = vaastav.merge(
                fixtures[
                    [
                        "season",
                        "GW",
                        "fixture",
                        "home_team_key",
                        "away_team_key",
                        "match_date",
                    ]
                ],
                on=["season", "GW", "fixture"],
                how="left",
            )
            player_context["team_key_from_fixture"] = np.where(
                player_context["was_home"] == True,
                player_context["home_team_key"],
                player_context["away_team_key"],
            )
            player_context["opponent_team_key"] = np.where(
                player_context["was_home"] == True,
                player_context["away_team_key"],
                player_context["home_team_key"],
            )
            player_context["is_home"] = np.where(player_context["was_home"] == True, 1.0, 0.0)
            player_context["match_date"] = pd.to_datetime(player_context["match_date"], errors="coerce").dt.date
            return player_context[
                [
                    "season",
                    "GW",
                    "element",
                    "fixture",
                    "match_date",
                    "team_key_from_fixture",
                    "opponent_team_key",
                    "is_home",
                ]
            ].drop_duplicates(["season", "GW", "element"], keep="first")
    except Exception:
        pass
    return context


def add_fixture_context(frame: pd.DataFrame, root: Path) -> pd.DataFrame:
    out = frame.copy()
    out["match_date"] = pd.to_datetime(out.get("match_date", out.get("date")), errors="coerce")
    out["_match_date_key"] = out["match_date"].dt.date

    context = _fixture_context(root)
    if context.empty:
        out["is_home"] = np.nan
        out["opponent_team_key"] = "unknown"
        return out.drop(columns=["_match_date_key"], errors="ignore")

    if "element" in out.columns and {"season", "GW", "element"}.issubset(context.columns):
        context_by_player = context.copy()
        context_by_player["GW"] = pd.to_numeric(context_by_player["GW"], errors="coerce")
        context_by_player["element"] = pd.to_numeric(context_by_player["element"], errors="coerce")
        out["GW"] = pd.to_numeric(out["GW"], errors="coerce")
        out["element"] = pd.to_numeric(out["element"], errors="coerce")
        out = out.merge(
            context_by_player[["season", "GW", "element", "opponent_team_key", "is_home"]],
            on=["season", "GW", "element"],
            how="left",
        )
    else:
        out["opponent_team_key"] = np.nan
        out["is_home"] = np.nan

    missing_context = out["opponent_team_key"].isna() | out["is_home"].isna()
    if missing_context.any() and {"team_key", "_match_date_key"}.issubset(out.columns):
        odds_context = _fixture_context(root)
        if not odds_context.empty and "team_key" in odds_context.columns:
            odds_context = odds_context.rename(columns={"match_date": "_match_date_key"})
            fallback = out.loc[missing_context].drop(columns=["opponent_team_key", "is_home"], errors="ignore").merge(
                odds_context[["season", "_match_date_key", "team_key", "opponent_team_key", "is_home"]],
                on=["season", "_match_date_key", "team_key"],
                how="left",
            )
            out.loc[missing_context, "opponent_team_key"] = fallback["opponent_team_key"].to_numpy()
            out.loc[missing_context, "is_home"] = fallback["is_home"].to_numpy()

    out["opponent_team_key"] = out["opponent_team_key"].fillna("unknown").astype(str)
    return out.drop(columns=["_match_date_key"], errors="ignore")


def _team_match_history(root: Path) -> pd.DataFrame:
    rows = []
    for path in sorted((root / "data" / "understat" / "team_stats").glob("team_stats_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for team in payload:
            team_key = team.get("team_key") or _canon_team(team.get("team"))
            season = team.get("season")
            for match in team.get("history", []) or []:
                date = pd.to_datetime(match.get("date"), errors="coerce")
                if pd.isna(date):
                    continue
                ppda_def = float(match.get("ppda_def") or 0.0)
                allowed_def = float(match.get("ppda_allowed_def") or 0.0)
                rows.append(
                    {
                        "season": season,
                        "team_key": team_key,
                        "match_date": date.date(),
                        "team_understat_xg": float(match.get("xG") or 0.0),
                        "team_understat_xga": float(match.get("xGA") or 0.0),
                        "team_understat_deep": float(match.get("deep") or 0.0),
                        "team_understat_deep_allowed": float(match.get("deep_allowed") or 0.0),
                        "team_understat_ppda": float(match.get("ppda_att") or 0.0) / ppda_def if ppda_def > 0 else np.nan,
                        "team_understat_ppda_allowed": float(match.get("ppda_allowed_att") or 0.0) / allowed_def if allowed_def > 0 else np.nan,
                    }
                )
    return pd.DataFrame(rows)


def _rolling_team_metrics(root: Path, windows: Iterable[int] = (3, 6, 10)) -> pd.DataFrame:
    history = _team_match_history(root)
    if history.empty:
        return history
    history = history.sort_values(["season", "team_key", "match_date"], kind="mergesort").copy()
    metrics = [
        "team_understat_xg",
        "team_understat_xga",
        "team_understat_deep",
        "team_understat_deep_allowed",
        "team_understat_ppda",
        "team_understat_ppda_allowed",
    ]
    grouped = history.groupby(["season", "team_key"], dropna=False, sort=False)
    for metric in metrics:
        shifted = grouped[metric].shift(1)
        for window in windows:
            history[f"{metric}_{window}"] = (
                shifted.groupby([history["season"], history["team_key"]], dropna=False, sort=False)
                .rolling(window, min_periods=1)
                .mean()
                .reset_index(level=[0, 1], drop=True)
            )
    keep = ["season", "team_key", "match_date"] + [col for col in history.columns if col.endswith(("_3", "_6", "_10"))]
    return history[keep]


def add_understat_team_features(frame: pd.DataFrame, root: Path) -> pd.DataFrame:
    out = frame.copy()
    out["_match_date_key"] = pd.to_datetime(out.get("match_date", out.get("date")), errors="coerce").dt.date
    team_metrics = _rolling_team_metrics(root)
    if team_metrics.empty:
        return out.drop(columns=["_match_date_key"], errors="ignore")
    team_metrics = team_metrics.rename(columns={"match_date": "_match_date_key"})
    out = out.merge(team_metrics, on=["season", "team_key", "_match_date_key"], how="left")

    opponent_metrics = team_metrics.rename(
        columns={
            "team_key": "opponent_team_key",
            **{
                col: f"opponent_{col.removeprefix('team_')}"
                for col in team_metrics.columns
                if col not in {"season", "_match_date_key", "team_key"}
            },
        }
    )
    out = out.merge(opponent_metrics, on=["season", "opponent_team_key", "_match_date_key"], how="left")
    return out.drop(columns=["_match_date_key"], errors="ignore")


def merge_kft_predictions(dataset: pd.DataFrame, predictions_path: Path) -> pd.DataFrame:
    out = dataset.copy()
    if "kft_xpts" in out.columns:
        return out
    if not predictions_path.exists():
        raise FileNotFoundError(f"Missing KFT predictions file: {predictions_path}")
    predictions = pd.read_csv(
        predictions_path,
        usecols=lambda col: col
        in {
            "season",
            "GW",
            "player_id",
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
        },
    )
    keep = [col for col in predictions.columns if col in {"season", "GW", "player_id"} or col not in out.columns]
    predictions = predictions[keep].drop_duplicates(["season", "GW", "player_id"], keep="first")
    return out.merge(predictions, on=["season", "GW", "player_id"], how="left")


def build_historical_ml_frame(root: Path) -> pd.DataFrame:
    dataset_path = root / "data" / "modelling" / "full_training_dataset.csv"
    predictions_path = root / "outputs" / "validation" / "full_historical_predictions.csv"
    dataset = pd.read_csv(dataset_path)
    dataset = merge_kft_predictions(dataset, predictions_path)
    dataset = add_shifted_rolling_features(dataset, windows=(10,))
    dataset = add_fixture_context(dataset, root)
    dataset = add_understat_team_features(dataset, root)
    dataset = add_availability_features(dataset, historical=True)
    return dataset


def build_live_ml_frame(
    weekly: pd.DataFrame,
    players: pd.DataFrame,
    player_fixture: pd.DataFrame,
    teams: pd.DataFrame,
) -> pd.DataFrame:
    out = weekly.copy()
    if out.empty:
        return out
    out = out.rename(columns={"xPts": "kft_xpts", "xG": "kft_xg", "xA": "kft_xa", "expected_minutes": "kft_expected_minutes"})
    team_names = teams[["id", "name"]].copy() if {"id", "name"}.issubset(teams.columns) else pd.DataFrame()
    if not team_names.empty:
        team_names["team_key"] = team_names["name"].apply(_canon_team)
        team_key_by_id = dict(zip(team_names["id"], team_names["team_key"]))
        out["team_key"] = out["team"].map(team_key_by_id)
    if not player_fixture.empty:
        fixture_minutes = player_fixture.copy()
        for col in ["play_probability", "start_probability", "likely_minutes"]:
            if col not in fixture_minutes.columns:
                fixture_minutes[col] = np.nan
        fixture_summary = (
            fixture_minutes.groupby(["event", "player_id"], as_index=False)
            .agg(
                opponent=("opponent", "first"),
                is_home=("was_home", "mean"),
                pred_play_prob=("play_probability", "mean"),
                pred_start_prob=("start_probability", "mean"),
                pred_mins_if_play=("likely_minutes", "mean"),
                team_lambda_odds=("team_xg", "mean"),
                opponent_lambda_odds=("opponent_xg", "mean"),
                cs_prob_odds=("cs_prob", "mean"),
                fixture_count=("fixture", "count"),
            )
        )
        out = out.merge(fixture_summary, on=["event", "player_id"], how="left")
        if not team_names.empty and "opponent" in out.columns:
            out["opponent_team_key"] = out["opponent"].map(team_key_by_id)
    out["opponent_team_key"] = out.get("opponent_team_key", pd.Series("unknown", index=out.index)).fillna("unknown")

    if not players.empty and "id" in players.columns:
        player_cols = [
            col
            for col in [
                "id",
                "element_type",
                "chance_of_playing_this_round",
                "chance_of_playing_next_round",
                "status",
                "selected_by_percent",
                "value",
                "minutes",
                "points_per_game",
                "form_minutes",
                "form_xg90",
                "form_xa90",
                "understat_npxG90",
                "understat_xA90",
                "understat_minutes",
            ]
            if col in players.columns
        ]
        player_features = players[player_cols].rename(columns={"id": "player_id"})
        out = out.merge(player_features, on="player_id", how="left")
    if "position" not in out.columns and "element_type" in out.columns:
        out["position"] = out["element_type"].map(POSITION_BY_ELEMENT_TYPE)
    out = add_availability_features(out, historical=False)
    return out


def choose_feature_columns(frame: pd.DataFrame) -> list[str]:
    excluded = LEAKAGE_COLUMNS | IDENTIFIER_COLUMNS
    features = []
    for col in frame.columns:
        if col in excluded:
            continue
        if col.startswith("_"):
            continue
        if col == "complete_features" or col == "prediction_complete":
            continue
        if pd.api.types.is_numeric_dtype(frame[col]) or col in FORCED_CATEGORICAL_COLUMNS or frame[col].dtype == object:
            features.append(col)
    return sorted(dict.fromkeys(features))


def split_feature_types(frame: pd.DataFrame, feature_columns: list[str]) -> tuple[list[str], list[str]]:
    categorical = []
    numeric = []
    for col in feature_columns:
        if col in FORCED_CATEGORICAL_COLUMNS or frame[col].dtype == object or str(frame[col].dtype).startswith("bool"):
            categorical.append(col)
        else:
            numeric.append(col)
    return numeric, categorical
