from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from .bonus import expected_capped_poisson
from .config import DATA_DIR, OUTPUTS_DIR
from .minutes import minute_outcomes

# Candidate #2, Phase 4: backtests are era-aware. Season-looping functions
# default to rulebook=None, meaning "resolve rulebook_for_season(season) per
# season group"; passing an explicit Rulebook forces it for every season
# (useful for shadow comparisons -- see scripts/quantify_gk_bug.py).
from .rulebook import CURRENT_RULEBOOK, Rulebook, rulebook_for_season


VAASTAV_BASE = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"
XG_SEASONS = ["2022-23", "2023-24", "2024-25"]
POINT_SEASONS = ["2019-20", "2020-21", "2021-22", *XG_SEASONS]


@dataclass(frozen=True)
class RidgeModel:
    features: list[str]
    means: np.ndarray
    scales: np.ndarray
    coef: np.ndarray

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        x = frame[self.features].to_numpy(float)
        x = (x - self.means) / self.scales
        x = np.c_[np.ones(len(x)), x]
        return x @ self.coef


@dataclass(frozen=True)
class LogisticModel:
    features: list[str]
    means: np.ndarray
    scales: np.ndarray
    coef: np.ndarray

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        x = frame[self.features].to_numpy(float)
        x = (x - self.means) / self.scales
        x = np.c_[np.ones(len(x)), x]
        z = np.clip(x @ self.coef, -30, 30)
        return 1.0 / (1.0 + np.exp(-z))


def _read_url(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "fpl-xpts-backtest/0.1"})
    with urlopen(request, timeout=60) as response:
        return response.read()


def load_vaastav_merged_gw(season: str, cache_dir: Path = DATA_DIR / "vaastav") -> pd.DataFrame:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{season}_merged_gw.csv"
    if not path.exists():
        url = f"{VAASTAV_BASE}/{season}/gws/merged_gw.csv"
        path.write_bytes(_read_url(url))
    df = pd.read_csv(path)
    df["season"] = season
    return df


def load_vaastav_seasons(seasons: list[str] | None = None, cache_dir: Path = DATA_DIR / "vaastav") -> pd.DataFrame:
    seasons = seasons or XG_SEASONS
    return pd.concat([load_vaastav_merged_gw(season, cache_dir) for season in seasons], ignore_index=True)


def _numeric(df: pd.DataFrame, column: str, default: float = 0.0) -> pd.Series:
    if column not in df.columns:
        return pd.Series(default, index=df.index)
    return pd.to_numeric(df[column], errors="coerce").fillna(default)


def build_player_gw_frame(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    required = ["season", "name", "element", "GW", "position", "team", "minutes", "total_points"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"Vaastav frame missing required columns: {missing}")

    for col in [
        "minutes", "total_points", "expected_goals", "expected_assists", "expected_goals_conceded",
        "goals_scored", "assists", "clean_sheets", "goals_conceded", "saves", "bonus", "bps",
        "yellow_cards", "red_cards", "starts", "value", "selected", "xP", "creativity",
    ]:
        df[col] = _numeric(df, col)
    df["position"] = df["position"].replace({"AM": "MID"})

    grouped = (
        df.groupby(["season", "element", "GW"], as_index=False)
        .agg(
            name=("name", "first"),
            position=("position", "first"),
            team=("team", "first"),
            minutes=("minutes", "sum"),
            total_points=("total_points", "sum"),
            expected_goals=("expected_goals", "sum"),
            expected_assists=("expected_assists", "sum"),
            expected_goals_conceded=("expected_goals_conceded", "sum"),
            goals_scored=("goals_scored", "sum"),
            assists=("assists", "sum"),
            clean_sheets=("clean_sheets", "sum"),
            goals_conceded=("goals_conceded", "sum"),
            saves=("saves", "sum"),
            bonus=("bonus", "sum"),
            bps=("bps", "sum"),
            creativity=("creativity", "sum"),
            yellow_cards=("yellow_cards", "sum"),
            red_cards=("red_cards", "sum"),
            starts=("starts", "sum"),
            value=("value", "mean"),
            selected=("selected", "mean"),
            xP=("xP", "max"),
        )
    )
    grouped["played"] = (grouped["minutes"] > 0).astype(float)
    grouped["started"] = (grouped["starts"] > 0).astype(float)
    grouped["player_season_key"] = grouped["season"].astype(str) + "|" + grouped["element"].astype(str)
    grouped = grouped.sort_values(["season", "element", "GW"]).reset_index(drop=True)
    return grouped


def add_rolling_features(player_gw: pd.DataFrame) -> pd.DataFrame:
    df = player_gw.sort_values(["season", "element", "GW"]).copy()
    group = df.groupby(["season", "element"], group_keys=False)

    for col in [
        "minutes", "total_points", "expected_goals", "expected_assists", "expected_goals_conceded",
        "goals_scored", "assists", "clean_sheets", "saves", "bonus", "creativity", "started", "played",
    ]:
        shifted = group[col].shift(1)
        df[f"{col}_l4"] = shifted.groupby([df["season"], df["element"]]).rolling(4, min_periods=1).mean().reset_index(level=[0, 1], drop=True)
        df[f"{col}_l8"] = shifted.groupby([df["season"], df["element"]]).rolling(8, min_periods=1).mean().reset_index(level=[0, 1], drop=True)

    prev_mins = group["minutes"].shift(1)
    cum_mins = prev_mins.groupby([df["season"], df["element"]]).cumsum().fillna(0.0)
    for col in ["expected_goals", "expected_assists", "expected_goals_conceded", "total_points", "saves"]:
        cum_col = group[col].shift(1).groupby([df["season"], df["element"]]).cumsum().fillna(0.0)
        df[f"{col}_season90"] = np.where(cum_mins > 0, cum_col / cum_mins * 90.0, 0.0)

    played_team = df.loc[df["minutes"] > 0].copy()
    team_group = played_team.groupby(["season", "team", "GW"], as_index=False).agg(
        team_xg=("expected_goals", "sum"),
        team_xa=("expected_assists", "sum"),
        team_xgc=("expected_goals_conceded", "mean"),
        team_points=("total_points", "sum"),
    )
    team_group = team_group.sort_values(["season", "team", "GW"])
    for col in ["team_xg", "team_xa", "team_xgc", "team_points"]:
        shifted = team_group.groupby(["season", "team"])[col].shift(1)
        team_group[f"{col}_l4"] = shifted.groupby([team_group["season"], team_group["team"]]).rolling(4, min_periods=1).mean().reset_index(level=[0, 1], drop=True)

    df = df.merge(
        team_group[["season", "team", "GW", "team_xg_l4", "team_xa_l4", "team_xgc_l4", "team_points_l4"]],
        on=["season", "team", "GW"],
        how="left",
    )

    for pos in ["GK", "DEF", "MID", "FWD"]:
        df[f"pos_{pos}"] = (df["position"] == pos).astype(float)

    df["log_selected"] = np.log1p(df["selected"].clip(lower=0))
    feature_cols = list(dict.fromkeys([*backtest_feature_columns(), *minutes_feature_columns()]))
    df[feature_cols] = df[feature_cols].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return df


def backtest_feature_columns() -> list[str]:
    return [
        "minutes_l4", "minutes_l8", "started_l4", "played_l4", "total_points_l4", "total_points_l8",
        "expected_goals_l4", "expected_assists_l4", "expected_goals_conceded_l4",
        "expected_goals_season90", "expected_assists_season90", "expected_goals_conceded_season90",
        "total_points_season90", "saves_season90", "team_xg_l4", "team_xa_l4", "team_xgc_l4",
        "value", "log_selected", "pos_GK", "pos_DEF", "pos_MID", "pos_FWD",
    ]


def minutes_feature_columns() -> list[str]:
    return [
        "minutes_l4", "minutes_l8", "started_l4", "started_l8", "played_l4", "played_l8",
        "total_points_l4", "total_points_l8", "value", "log_selected",
        "pos_GK", "pos_DEF", "pos_MID", "pos_FWD",
    ]


def fit_ridge(train: pd.DataFrame, target: str = "total_points", alpha: float = 15.0) -> RidgeModel:
    features = backtest_feature_columns()
    return fit_ridge_with_features(train, features, target, alpha)


def fit_ridge_with_features(train: pd.DataFrame, features: list[str], target: str, alpha: float = 15.0) -> RidgeModel:
    x = train[features].to_numpy(float)
    y = train[target].to_numpy(float)
    means = x.mean(axis=0)
    scales = x.std(axis=0)
    scales[scales == 0] = 1.0
    xs = (x - means) / scales
    design = np.c_[np.ones(len(xs)), xs]
    penalty = np.eye(design.shape[1]) * alpha
    penalty[0, 0] = 0.0
    coef = np.linalg.solve(design.T @ design + penalty, design.T @ y)
    return RidgeModel(features=features, means=means, scales=scales, coef=coef)


def fit_logistic(
    train: pd.DataFrame,
    features: list[str],
    target: str,
    alpha: float = 1.0,
    lr: float = 0.12,
    epochs: int = 900,
) -> LogisticModel:
    x = train[features].to_numpy(float)
    y = train[target].to_numpy(float)
    means = x.mean(axis=0)
    scales = x.std(axis=0)
    scales[scales == 0] = 1.0
    xs = (x - means) / scales
    design = np.c_[np.ones(len(xs)), xs]
    coef = np.zeros(design.shape[1], dtype=float)
    for _ in range(epochs):
        z = np.clip(design @ coef, -30, 30)
        pred = 1.0 / (1.0 + np.exp(-z))
        grad = design.T @ (pred - y) / len(y)
        grad[1:] += alpha * coef[1:] / len(y)
        coef -= lr * grad
    return LogisticModel(features=features, means=means, scales=scales, coef=coef)


def fit_minutes_models(train: pd.DataFrame) -> dict[str, RidgeModel | LogisticModel]:
    features = minutes_feature_columns()
    return {
        "start": fit_logistic(train, features, "started", alpha=5.0, lr=0.18, epochs=1000),
        "play": fit_logistic(train, features, "played", alpha=5.0, lr=0.18, epochs=1000),
        "minutes": fit_ridge_with_features(train.loc[train["minutes"] > 0], features, "minutes", alpha=25.0),
    }


def predict_expected_minutes(models: dict[str, RidgeModel | LogisticModel], frame: pd.DataFrame) -> pd.Series:
    p_play = models["play"].predict_proba(frame)  # type: ignore[union-attr]
    mins_if_play = np.clip(models["minutes"].predict(frame), 1.0, 90.0)  # type: ignore[union-attr]
    return pd.Series(np.clip(p_play * mins_if_play, 0.0, 90.0), index=frame.index)


def _metrics(frame: pd.DataFrame, pred_col: str) -> dict[str, float]:
    actual = frame["total_points"].to_numpy(float)
    pred = frame[pred_col].to_numpy(float)
    err = pred - actual
    pred_s = pd.Series(pred)
    actual_s = pd.Series(actual)
    out = {
        "rows": float(len(frame)),
        "mae": float(np.mean(np.abs(err))),
        "rmse": float(np.sqrt(np.mean(err ** 2))),
        "bias": float(np.mean(err)),
        "actual_mean": float(np.mean(actual)),
        "pred_mean": float(np.mean(pred)),
        "pearson": float(pred_s.corr(actual_s, method="pearson")),
        "spearman": float(pred_s.rank().corr(actual_s.rank(), method="pearson")),
    }
    return out


def _regression_metrics(frame: pd.DataFrame, actual_col: str, pred_col: str) -> dict[str, float]:
    actual = frame[actual_col].to_numpy(float)
    pred = frame[pred_col].to_numpy(float)
    err = pred - actual
    return {
        "rows": float(len(frame)),
        "mae": float(np.mean(np.abs(err))),
        "rmse": float(np.sqrt(np.mean(err ** 2))),
        "bias": float(np.mean(err)),
        "actual_mean": float(np.mean(actual)),
        "pred_mean": float(np.mean(pred)),
        "pearson": float(pd.Series(pred).corr(pd.Series(actual), method="pearson")),
        "spearman": float(pd.Series(pred).rank().corr(pd.Series(actual).rank(), method="pearson")),
    }


def _binary_metrics(frame: pd.DataFrame, actual_col: str, pred_col: str) -> dict[str, float]:
    actual = frame[actual_col].to_numpy(float)
    pred = np.clip(frame[pred_col].to_numpy(float), 1e-6, 1 - 1e-6)
    return {
        "rows": float(len(frame)),
        "brier": float(np.mean((pred - actual) ** 2)),
        "logloss": float(-np.mean(actual * np.log(pred) + (1 - actual) * np.log(1 - pred))),
        "actual_rate": float(np.mean(actual)),
        "pred_rate": float(np.mean(pred)),
        "pearson": float(pd.Series(pred).corr(pd.Series(actual), method="pearson")),
    }


def _top_overlap(frame: pd.DataFrame, pred_col: str, k: int = 10) -> float:
    overlaps = []
    for _, gw in frame.groupby(["season", "GW"]):
        if len(gw) < k:
            continue
        pred_top = set(gw.nlargest(k, pred_col).index)
        actual_top = set(gw.nlargest(k, "total_points").index)
        overlaps.append(len(pred_top & actual_top) / k)
    return float(np.mean(overlaps)) if overlaps else np.nan


BRACKET_COLS = ["Bracket_LE_2", "Bracket_3_to_6", "Bracket_7_to_9", "Bracket_10_to_14", "Bracket_15_plus"]


def _actual_bracket(points: pd.Series) -> pd.Series:
    p = pd.to_numeric(points, errors="coerce").fillna(0)
    return pd.Series(
        np.select(
            [p <= 2, (p >= 3) & (p <= 6), (p >= 7) & (p <= 9), (p >= 10) & (p <= 14), p >= 15],
            BRACKET_COLS,
            default="Bracket_LE_2",
        ),
        index=points.index,
    )


def _add_bracket_dummies(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["actual_bracket"] = _actual_bracket(out["total_points"])
    for col in BRACKET_COLS:
        out[f"actual_{col}"] = (out["actual_bracket"] == col).astype(float)
    return out


def empirical_bracket_model(train: pd.DataFrame, pred_col: str) -> pd.DataFrame:
    bins = [-0.01, 1, 2, 3, 4, 5, 6, 8, 10, 15, 100]
    labels = ["0-1", "1-2", "2-3", "3-4", "4-5", "5-6", "6-8", "8-10", "10-15", "15+"]
    framed = _add_bracket_dummies(train)
    framed["pred_bucket"] = pd.cut(framed[pred_col], bins=bins, labels=labels, include_lowest=True)
    rates = (
        framed.groupby("pred_bucket", observed=False)
        .agg(rows=("total_points", "size"), **{col: (f"actual_{col}", "mean") for col in BRACKET_COLS})
        .reset_index()
    )
    fallback = {col: framed[f"actual_{col}"].mean() for col in BRACKET_COLS}
    for col in BRACKET_COLS:
        rates[col] = rates[col].fillna(fallback[col])
    return rates


def apply_empirical_brackets(test: pd.DataFrame, rates: pd.DataFrame, pred_col: str) -> pd.DataFrame:
    bins = [-0.01, 1, 2, 3, 4, 5, 6, 8, 10, 15, 100]
    labels = ["0-1", "1-2", "2-3", "3-4", "4-5", "5-6", "6-8", "8-10", "10-15", "15+"]
    out = _add_bracket_dummies(test)
    out["pred_bucket"] = pd.cut(out[pred_col], bins=bins, labels=labels, include_lowest=True)
    out = out.merge(rates[["pred_bucket", *BRACKET_COLS]], on="pred_bucket", how="left", suffixes=("", "_pred"))
    return out


def bracket_calibration_summary(predictions: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for bracket in BRACKET_COLS:
        pred = predictions[bracket].to_numpy(float)
        actual = predictions[f"actual_{bracket}"].to_numpy(float)
        rows.append(
            {
                "bracket": bracket,
                "rows": len(predictions),
                "predicted_rate": float(np.mean(pred)),
                "actual_rate": float(np.mean(actual)),
                "brier": float(np.mean((pred - actual) ** 2)),
            }
        )
    return pd.DataFrame(rows)


def _poisson_prob_ge(threshold: int, mu: float) -> float:
    if threshold <= 0:
        return 1.0
    mu = max(float(mu), 0.0)
    if mu <= 0:
        return 0.0
    term = np.exp(-mu)
    cdf = term
    for k in range(1, threshold):
        term *= mu / k
        cdf += term
    return float(np.clip(1.0 - cdf, 0.0, 1.0))


def _expected_gc_deduction(mu: float) -> float:
    return float(sum(_poisson_prob_ge(threshold, mu) for threshold in range(2, 31, 2)))


def _expected_save_points(mu_saves: float) -> float:
    mu = max(float(mu_saves), 0.0)
    if mu <= 0:
        return 0.0
    total = 0.0
    pmf = np.exp(-mu)
    cdf = pmf
    k = 0
    for threshold in range(3, 31, 3):
        while k < threshold - 1:
            k += 1
            pmf *= mu / k
            cdf += pmf
        total += max(0.0, 1.0 - cdf)
    return float(total)


def _rate_blend(frame: pd.DataFrame, stat: str, form_blend_weight: float = 0.3) -> pd.Series:
    season = _numeric(frame, f"{stat}_season90")
    l4 = _numeric(frame, f"{stat}_l4")
    mins_l4 = _numeric(frame, "minutes_l4")
    l4_rate = np.where(mins_l4 > 0, l4 / (mins_l4 / 90.0), 0.0)
    position_prior = frame.groupby("position")[f"{stat}_season90"].transform("median").fillna(season.median())
    blend = float(np.clip(form_blend_weight, 0.0, 1.0))
    raw = np.where(season > 0, season * (1.0 - blend) + l4_rate * blend, l4_rate)
    exposure = (_numeric(frame, "minutes_l8") / 450.0).clip(0.0, 1.0)
    return pd.Series(raw * exposure + position_prior * (1.0 - exposure) * 0.55, index=frame.index).clip(lower=0.0)


def add_production_formula_predictions(
    frame: pd.DataFrame,
    form_blend_weight: float = 0.3,
    set_piece_xa_weight: float = 0.3,
    rulebook: Rulebook = CURRENT_RULEBOOK,
) -> pd.DataFrame:
    """Backtest approximation of the live component engine using only pre-GW data."""
    out = frame.copy()
    out["prod_xg_rate"] = _rate_blend(out, "expected_goals", form_blend_weight=form_blend_weight)
    out["prod_xa_rate"] = _rate_blend(out, "expected_assists", form_blend_weight=form_blend_weight)
    out["prod_mins_if_play"] = _numeric(out, "pred_mins_if_play", default=0.0).clip(0.0, 90.0)
    out["prod_play_prob"] = _numeric(out, "pred_play_prob", default=0.0).clip(0.0, 1.0)
    out["prod_start_prob"] = np.minimum(_numeric(out, "pred_start_prob", default=0.0).clip(0.0, 1.0), out["prod_play_prob"])
    out["prod_expected_minutes"] = 0.0

    state_rows = []
    for idx, row in out.iterrows():
        vals, probs = minute_outcomes(
            float(row["prod_mins_if_play"]),
            start_probability=float(row["prod_start_prob"]),
            play_probability=float(row["prod_play_prob"]),
        )
        out.at[idx, "prod_expected_minutes"] = float(np.dot(vals, probs))
        state_rows.append((vals, probs))

    out["prod_xg_weight"] = out["prod_xg_rate"] * out["prod_expected_minutes"] / 90.0
    creativity = _numeric(out, "creativity_l4", default=0.0).clip(lower=0.0)
    team_creativity = creativity.groupby([out["season"], out["GW"], out["team"]]).transform("sum").to_numpy(dtype=float)
    out["prod_set_piece_share"] = np.divide(
        creativity.to_numpy(dtype=float),
        team_creativity,
        out=np.zeros(len(out), dtype=float),
        where=team_creativity > 0,
    )
    out["prod_set_piece_share"] = out["prod_set_piece_share"].clip(0.0, 1.25)
    out["prod_xa_weight"] = (
        out["prod_xa_rate"]
        * (1.0 + float(set_piece_xa_weight) * out["prod_set_piece_share"])
        * out["prod_expected_minutes"]
        / 90.0
    )
    out["prod_team_xg"] = _numeric(out, "team_xg_l4", default=1.2).clip(0.25, 3.5)
    assist_factor = np.divide(
        _numeric(out, "team_xa_l4", default=0.85),
        out["prod_team_xg"].clip(lower=0.25),
    )
    out["prod_team_xa"] = (out["prod_team_xg"] * pd.Series(assist_factor, index=out.index).clip(0.58, 0.88)).clip(0.1, 3.0)

    keys = ["season", "GW", "team"]
    xg_sum = out.groupby(keys)["prod_xg_weight"].transform("sum")
    xa_sum = out.groupby(keys)["prod_xa_weight"].transform("sum")
    out["prod_xG"] = np.where(xg_sum > 0, out["prod_team_xg"] * out["prod_xg_weight"] / xg_sum, out["prod_xg_weight"])
    out["prod_xA"] = np.where(xa_sum > 0, out["prod_team_xa"] * out["prod_xa_weight"] / xa_sum, out["prod_xa_weight"])
    out["prod_return_prob"] = 1.0 - np.exp(-(out["prod_xG"] + out["prod_xA"]))

    components = []
    for (_, row), (vals, probs) in zip(out.iterrows(), state_rows):
        pos = str(row["position"])
        xgc = float(np.clip(row.get("team_xgc_l4", 1.2), 0.05, 4.0))
        cs_prob = float(np.exp(-xgc))
        app = cs = concede = saves = p_cs_eligible = 0.0
        for m, p in zip(vals, probs):
            minute_value = float(m)
            prob = float(p)
            app += prob * rulebook.appearance_points_for(minute_value)
            if minute_value >= 60:
                p_cs_eligible += prob
                cs += prob * rulebook.clean_sheet_points_for(pos) * cs_prob
            if pos in {"GK", "DEF"} and minute_value > 0:
                concede -= prob * _expected_gc_deduction(xgc * minute_value / 90.0)
            if pos == "GK":
                saves += prob * _expected_save_points(float(row.get("saves_season90", 0.0)) * minute_value / 90.0)
        goals = float(row["prod_xG"]) * rulebook.goal_points_for(pos)
        assists = float(row["prod_xA"]) * 3.0
        card_rate = 0.12 if pos in {"DEF", "MID"} else 0.09
        cards = -card_rate * float(row["prod_expected_minutes"]) / 90.0
        bonus_lam = (
            float(row["prod_xG"]) * 0.9
            + float(row["prod_xA"]) * 0.42
            + (cs_prob * p_cs_eligible * 0.28 if pos in {"GK", "DEF"} else 0.0)
            + saves * 0.20
        )
        bonus = expected_capped_poisson(bonus_lam, cap=3)
        xpts = app + goals + assists + cs + concede + saves + cards + bonus
        components.append(
            {
                "prod_AppPts": app,
                "prod_GoalPts": goals,
                "prod_AssistPts": assists,
                "prod_CSPts": cs,
                "prod_ConcedePts": concede,
                "prod_SavePts": saves,
                "prod_CardPts": cards,
                "prod_BonusPts": bonus,
                "production_xPts": max(0.0, xpts),
                "prod_cs_prob": cs_prob,
            }
        )
    return pd.concat([out, pd.DataFrame(components, index=out.index)], axis=1)


def apply_production_formula_by_season(
    frame: pd.DataFrame,
    form_blend_weight: float = 0.3,
    set_piece_xa_weight: float = 0.3,
    rulebook: Rulebook | None = None,
) -> pd.DataFrame:
    """Era-aware application of the production formula (the Phase 4 fix).

    With ``rulebook=None`` (the default), each season group is scored with
    the Rulebook that was in force that season -- GK goals are worth 6 points
    in pre-2024 seasons, not today's 10. An explicit rulebook forces one rule
    set for every season (shadow-comparison use only).
    """
    if rulebook is not None or "season" not in frame.columns:
        return add_production_formula_predictions(
            frame,
            form_blend_weight=form_blend_weight,
            set_piece_xa_weight=set_piece_xa_weight,
            rulebook=rulebook if rulebook is not None else CURRENT_RULEBOOK,
        )
    parts = [
        add_production_formula_predictions(
            season_frame,
            form_blend_weight=form_blend_weight,
            set_piece_xa_weight=set_piece_xa_weight,
            rulebook=rulebook_for_season(season),
        )
        for season, season_frame in frame.groupby("season", sort=False)
    ]
    # Frames carry a unique RangeIndex (ignore_index concat upstream), so
    # sort_index restores the original row order after the per-season split.
    return pd.concat(parts).sort_index()


def sweep_form_weight(
    out_dir: Path = OUTPUTS_DIR / "backtest_latest",
    train_seasons: list[str] | None = None,
    test_seasons: list[str] | None = None,
    cache_dir: Path = DATA_DIR / "vaastav",
    rulebook: Rulebook | None = None,
) -> pd.DataFrame:
    train_seasons = train_seasons or ["2022-23", "2023-24"]
    test_seasons = test_seasons or ["2024-25"]
    all_seasons = list(dict.fromkeys([*train_seasons, *test_seasons]))
    missing = [season for season in all_seasons if not (cache_dir / f"{season}_merged_gw.csv").exists()]
    if missing:
        raise FileNotFoundError(
            "Missing cached Vaastav files for parameter sweep: " + ", ".join(missing)
        )

    raw = load_vaastav_seasons(all_seasons, cache_dir)
    frame = add_rolling_features(build_player_gw_frame(raw))
    usable = frame.loc[frame["GW"] >= 5].copy()
    train = usable.loc[usable["season"].isin(train_seasons)].copy()
    test = usable.loc[usable["season"].isin(test_seasons)].copy()
    if train.empty or test.empty:
        raise ValueError("Parameter sweep requires non-empty train and test frames.")

    minute_models = fit_minutes_models(train)
    test["pred_start_prob"] = minute_models["start"].predict_proba(test)  # type: ignore[union-attr]
    test["pred_play_prob"] = minute_models["play"].predict_proba(test)  # type: ignore[union-attr]
    test["pred_mins_if_play"] = np.clip(minute_models["minutes"].predict(test), 1.0, 90.0)  # type: ignore[union-attr]
    test["pred_minutes"] = np.clip(test["pred_play_prob"] * test["pred_mins_if_play"], 0.0, 90.0)

    records = []
    for form_weight in [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]:
        for set_piece_weight in [0.0, 0.2, 0.4]:
            scored = apply_production_formula_by_season(
                test,
                form_blend_weight=form_weight,
                set_piece_xa_weight=set_piece_weight,
                rulebook=rulebook,
            )
            metrics = _metrics(scored, "production_xPts")
            records.append(
                {
                    "form_blend_weight": form_weight,
                    "set_piece_xa_weight": set_piece_weight,
                    "rows": metrics["rows"],
                    "spearman": metrics["spearman"],
                    "pearson": metrics["pearson"],
                    "mae": metrics["mae"],
                    "rmse": metrics["rmse"],
                    "bias": metrics["bias"],
                    "actual_mean": metrics["actual_mean"],
                    "pred_mean": metrics["pred_mean"],
                }
            )

    sweep = pd.DataFrame(records).sort_values(["spearman", "mae"], ascending=[False, True])
    out_dir.mkdir(parents=True, exist_ok=True)
    sweep.to_csv(out_dir / "param_sweep.csv", index=False, float_format="%.6f")
    best = sweep.iloc[0]
    print(
        "Best parameter sweep: "
        f"form_blend_weight={best['form_blend_weight']:.1f}, "
        f"set_piece_xa_weight={best['set_piece_xa_weight']:.1f}, "
        f"spearman={best['spearman']:.6f}"
    )
    return sweep


def _projection_tiers(frame: pd.DataFrame, pred_col: str) -> pd.Series:
    pct = frame.groupby(["season", "GW"])[pred_col].rank(pct=True, method="first")
    return pd.cut(
        pct,
        bins=[0.0, 0.50, 0.75, 0.90, 0.98, 1.0],
        labels=["bottom50", "mid50_75", "good75_90", "top10", "elite2"],
        include_lowest=True,
    ).astype(str)


def calibration_tables(frame: pd.DataFrame, pred_col: str) -> dict[str, pd.DataFrame]:
    out = frame.copy()
    out["projection_tier"] = _projection_tiers(out, pred_col)
    out["actual_return"] = ((out["goals_scored"] + out["assists"]) >= 1).astype(float)
    out["actual_cs"] = ((out["clean_sheets"] >= 1) & (out["minutes"] >= 60)).astype(float)
    tables = {}
    for group_cols, name in [
        (["position"], "calibration_by_position.csv"),
        (["projection_tier"], "calibration_by_tier.csv"),
        (["position", "projection_tier"], "calibration_by_position_tier.csv"),
    ]:
        grouped = out.groupby(group_cols, dropna=False).apply(
            lambda g: pd.Series(
                {
                    "rows": len(g),
                    "actual_pts": g["total_points"].mean(),
                    "pred_xpts": g[pred_col].mean(),
                    "bias": (g[pred_col] - g["total_points"]).mean(),
                    "mae": (g[pred_col] - g["total_points"]).abs().mean(),
                    "actual_return_rate": g["actual_return"].mean(),
                    "pred_return_rate": g["prod_return_prob"].mean() if "prod_return_prob" in g else np.nan,
                    "return_brier": ((g.get("prod_return_prob", 0.0) - g["actual_return"]) ** 2).mean()
                    if "prod_return_prob" in g
                    else np.nan,
                    "actual_cs_rate": g["actual_cs"].mean(),
                    "pred_cs_rate": g["prod_cs_prob"].mean() if "prod_cs_prob" in g else np.nan,
                    "minutes_actual": g["minutes"].mean(),
                    "minutes_pred": g["pred_minutes"].mean() if "pred_minutes" in g else np.nan,
                }
            ),
            include_groups=False,
        ).reset_index()
        tables[name] = grouped
    return tables


def production_calibration_features() -> list[str]:
    return [
        "production_xPts", "prod_xG", "prod_xA", "prod_return_prob", "prod_cs_prob",
        "prod_expected_minutes", "prod_start_prob", "prod_play_prob",
        "value", "log_selected", "pos_GK", "pos_DEF", "pos_MID", "pos_FWD",
    ]


def weekly_model_scorecard(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (season, gw), gw_df in frame.groupby(["season", "GW"]):
        for pred_col, label in [
            ("production_xPts", "production_formula_engine"),
            ("production_xPts_calibrated", "production_formula_calibrated"),
            ("model_xPts", "ridge_history_model"),
            ("fpl_xP", "fpl_api_xP_column_baseline"),
        ]:
            row = {"season": season, "GW": gw, "model": label, **_metrics(gw_df, pred_col)}
            row["top10_overlap"] = _top_overlap(gw_df, pred_col, 10)
            row["top25_overlap"] = _top_overlap(gw_df, pred_col, 25)
            rows.append(row)
    return pd.DataFrame(rows).sort_values(["season", "GW", "model"]).reset_index(drop=True)


def weekly_bracket_scorecard(predictions: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (season, gw), gw_df in predictions.groupby(["season", "GW"]):
        row = {"season": season, "GW": gw, "rows": len(gw_df)}
        for bracket in BRACKET_COLS:
            pred = gw_df[bracket].to_numpy(float)
            actual = gw_df[f"actual_{bracket}"].to_numpy(float)
            row[f"{bracket}_predicted_rate"] = float(np.mean(pred))
            row[f"{bracket}_actual_rate"] = float(np.mean(actual))
            row[f"{bracket}_brier"] = float(np.mean((pred - actual) ** 2))
        rows.append(row)
    return pd.DataFrame(rows).sort_values(["season", "GW"]).reset_index(drop=True)


def run_holdout_backtest(
    train_seasons: list[str] | None = None,
    test_seasons: list[str] | None = None,
    cache_dir: Path = DATA_DIR / "vaastav",
    rulebook: Rulebook | None = None,
) -> dict[str, pd.DataFrame]:
    train_seasons = train_seasons or ["2022-23", "2023-24"]
    test_seasons = test_seasons or ["2024-25"]
    all_seasons = list(dict.fromkeys([*train_seasons, *test_seasons]))
    raw = load_vaastav_seasons(all_seasons, cache_dir)
    frame = add_rolling_features(build_player_gw_frame(raw))
    usable = frame.loc[frame["GW"] >= 5].copy()
    train = usable.loc[usable["season"].isin(train_seasons)].copy()
    test = usable.loc[usable["season"].isin(test_seasons)].copy()

    model = fit_ridge(train)
    minute_models = fit_minutes_models(train)
    train = train.copy()
    train["model_xPts"] = np.clip(model.predict(train), 0.0, None)
    train["pred_start_prob"] = minute_models["start"].predict_proba(train)  # type: ignore[union-attr]
    train["pred_play_prob"] = minute_models["play"].predict_proba(train)  # type: ignore[union-attr]
    train["pred_mins_if_play"] = np.clip(minute_models["minutes"].predict(train), 1.0, 90.0)  # type: ignore[union-attr]
    train["pred_minutes"] = np.clip(train["pred_play_prob"] * train["pred_mins_if_play"], 0.0, 90.0)
    test["model_xPts"] = np.clip(model.predict(test), 0.0, None)
    test["pred_start_prob"] = minute_models["start"].predict_proba(test)  # type: ignore[union-attr]
    test["pred_play_prob"] = minute_models["play"].predict_proba(test)  # type: ignore[union-attr]
    test["pred_mins_if_play"] = np.clip(minute_models["minutes"].predict(test), 1.0, 90.0)  # type: ignore[union-attr]
    test["pred_minutes"] = np.clip(test["pred_play_prob"] * test["pred_mins_if_play"], 0.0, 90.0)
    test["fpl_xP"] = pd.to_numeric(test["xP"], errors="coerce").fillna(0.0).clip(lower=0.0)
    # Era-aware scoring: each season's rows are scored with that season's
    # Rulebook (train and test can span different rule eras).
    train = apply_production_formula_by_season(train, rulebook=rulebook)
    test = apply_production_formula_by_season(test, rulebook=rulebook)
    calibration_model = fit_ridge_with_features(
        train,
        production_calibration_features(),
        target="total_points",
        alpha=12.0,
    )
    train["production_xPts_calibrated"] = np.clip(calibration_model.predict(train), 0.0, None)
    test["production_xPts_calibrated"] = np.clip(calibration_model.predict(test), 0.0, None)

    summary_rows = []
    for pred_col, label in [
        ("production_xPts", "production_formula_engine"),
        ("production_xPts_calibrated", "production_formula_calibrated"),
        ("model_xPts", "ridge_history_model"),
        ("fpl_xP", "fpl_api_xP_column_baseline"),
    ]:
        row = {"model": label, **_metrics(test, pred_col)}
        row["top10_overlap"] = _top_overlap(test, pred_col, 10)
        row["top25_overlap"] = _top_overlap(test, pred_col, 25)
        summary_rows.append(row)

    by_season_rows = []
    for season, season_df in test.groupby("season"):
        for pred_col, label in [
            ("production_xPts", "production_formula_engine"),
            ("production_xPts_calibrated", "production_formula_calibrated"),
            ("model_xPts", "ridge_history_model"),
            ("fpl_xP", "fpl_api_xP_column_baseline"),
        ]:
            row = {"season": season, "model": label, **_metrics(season_df, pred_col)}
            row["top10_overlap"] = _top_overlap(season_df, pred_col, 10)
            by_season_rows.append(row)

    predictions = test[
        [
            "season", "GW", "element", "name", "position", "team", "minutes", "total_points",
            "production_xPts", "production_xPts_calibrated", "model_xPts", "fpl_xP", "pred_minutes", "pred_mins_if_play",
            "pred_start_prob", "pred_play_prob", "prod_xG", "prod_xA", "prod_return_prob", "prod_cs_prob",
            "expected_goals", "expected_assists", "expected_goals_conceded",
            "minutes_l4", "expected_goals_l4", "expected_assists_l4", "value", "selected",
        ]
    ].sort_values(["season", "GW", "production_xPts"], ascending=[True, True, False])

    coef = pd.DataFrame(
        {
            "feature": ["intercept", *model.features],
            "coefficient": model.coef,
        }
    ).sort_values("coefficient", ascending=False)

    minutes_metrics = pd.DataFrame(
        [
            {"model": "expected_minutes", **_regression_metrics(test, "minutes", "pred_minutes")},
            {"model": "start_probability", **_binary_metrics(test, "started", "pred_start_prob")},
            {"model": "play_probability", **_binary_metrics(test, "played", "pred_play_prob")},
        ]
    )
    minute_coef_rows = []
    for name, fitted in minute_models.items():
        minute_coef_rows.extend(
            {"model": name, "feature": feature, "coefficient": coef_value}
            for feature, coef_value in zip(["intercept", *fitted.features], fitted.coef)
        )
    minute_coef = pd.DataFrame(minute_coef_rows)

    bracket_rates = empirical_bracket_model(train, "production_xPts_calibrated")
    bracket_predictions = apply_empirical_brackets(test, bracket_rates, "production_xPts_calibrated")
    bracket_summary = bracket_calibration_summary(bracket_predictions)
    weekly_scorecard = weekly_model_scorecard(test)
    weekly_brackets = weekly_bracket_scorecard(bracket_predictions)
    calibration = calibration_tables(test, "production_xPts_calibrated")
    bracket_predictions = bracket_predictions[
        [
            "season", "GW", "element", "name", "position", "team", "total_points", "production_xPts_calibrated",
            "actual_bracket", "pred_bucket", *BRACKET_COLS,
        ]
    ].sort_values(["season", "GW", "production_xPts_calibrated"], ascending=[True, True, False])

    outputs = {
        "backtest_summary.csv": pd.DataFrame(summary_rows),
        "backtest_by_season.csv": pd.DataFrame(by_season_rows),
        "backtest_by_gw.csv": weekly_scorecard,
        "minutes_model_metrics.csv": minutes_metrics,
        "minutes_model_coefficients.csv": minute_coef,
        "mc_bracket_calibration.csv": bracket_summary,
        "mc_bracket_calibration_by_gw.csv": weekly_brackets,
        "mc_bracket_backtest_predictions.csv": bracket_predictions,
        "backtest_player_gw_predictions.csv": predictions,
        "backtest_model_coefficients.csv": coef,
        "production_calibration_coefficients.csv": pd.DataFrame(
            {"feature": ["intercept", *calibration_model.features], "coefficient": calibration_model.coef}
        ),
    }
    outputs.update(calibration)
    return outputs


def write_backtest_outputs(
    out_dir: Path,
    train_seasons: list[str] | None = None,
    test_seasons: list[str] | None = None,
    rulebook: Rulebook | None = None,
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = run_holdout_backtest(
        train_seasons=train_seasons, test_seasons=test_seasons, rulebook=rulebook
    )
    paths = {}
    for filename, df in outputs.items():
        path = out_dir / filename
        df.to_csv(path, index=False, float_format="%.6f")
        paths[filename] = path
    sweep_form_weight(
        out_dir=out_dir,
        train_seasons=train_seasons,
        test_seasons=test_seasons,
        rulebook=rulebook,
    )
    paths["param_sweep.csv"] = out_dir / "param_sweep.csv"
    return paths
