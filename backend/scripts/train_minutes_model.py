from __future__ import annotations

import argparse
import math
import sys
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fpl_xpts.minutes_model import (  # noqa: E402
    DEFAULT_MINUTES_MODEL_PATH,
    MINUTES_CATEGORICAL_FEATURES,
    MINUTES_FEATURE_COLUMNS,
    MINUTES_NUMERIC_FEATURES,
    build_historical_minutes_features,
    save_minutes_bundle,
    score_minutes,
)
from fpl_xpts.ml_features import MODEL_FILENAMES, build_historical_ml_frame  # noqa: E402
from fpl_xpts.ml_models import (  # noqa: E402
    build_preprocessor,
    load_bundles,
    save_bundle,
    transformed_feature_names,
)
try:  # noqa: E402
    from scripts.train_position_ml_models import _eligible_frame, _fit_model_set
except ModuleNotFoundError:  # Direct script execution puts scripts/ on sys.path.
    from train_position_ml_models import _eligible_frame, _fit_model_set


TRAIN_SEASONS = ["2019-20", "2020-21", "2021-22", "2022-23", "2023-24", "2024-25"]
HOLDOUT_SEASON = "2025-26"


def _dependencies() -> dict[str, Any]:
    from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
    from sklearn.isotonic import IsotonicRegression
    from sklearn.metrics import brier_score_loss, mean_absolute_error
    from sklearn.model_selection import GroupKFold
    from xgboost import XGBClassifier, XGBRegressor

    return {
        "RandomForestClassifier": RandomForestClassifier,
        "RandomForestRegressor": RandomForestRegressor,
        "IsotonicRegression": IsotonicRegression,
        "brier_score_loss": brier_score_loss,
        "mean_absolute_error": mean_absolute_error,
        "GroupKFold": GroupKFold,
        "XGBClassifier": XGBClassifier,
        "XGBRegressor": XGBRegressor,
    }


def _classifier_models(deps: dict[str, Any], seed: int) -> tuple[Any, Any]:
    return (
        deps["XGBClassifier"](
            objective="binary:logistic",
            eval_metric="logloss",
            n_estimators=280,
            max_depth=4,
            learning_rate=0.04,
            subsample=0.85,
            colsample_bytree=0.85,
            reg_lambda=2.0,
            random_state=seed,
            n_jobs=-1,
            tree_method="hist",
        ),
        deps["RandomForestClassifier"](
            n_estimators=240,
            max_depth=12,
            min_samples_leaf=15,
            class_weight="balanced_subsample",
            random_state=seed,
            n_jobs=-1,
        ),
    )


def _regressor_models(deps: dict[str, Any], seed: int) -> tuple[Any, Any]:
    return (
        deps["XGBRegressor"](
            objective="reg:squarederror",
            n_estimators=280,
            max_depth=4,
            learning_rate=0.04,
            subsample=0.85,
            colsample_bytree=0.85,
            reg_lambda=2.0,
            random_state=seed,
            n_jobs=-1,
            tree_method="hist",
        ),
        deps["RandomForestRegressor"](
            n_estimators=240,
            max_depth=12,
            min_samples_leaf=15,
            random_state=seed,
            n_jobs=-1,
        ),
    )


def _folds(frame: pd.DataFrame, deps: dict[str, Any], splits: int = 5) -> list[tuple[np.ndarray, np.ndarray]]:
    groups = frame["team_key"].fillna("unknown").astype(str)
    n_splits = min(int(splits), int(groups.nunique()))
    return list(deps["GroupKFold"](n_splits=n_splits).split(frame, groups=groups))


def _fit_classifier(
    frame: pd.DataFrame,
    target: str,
    seed: int,
) -> tuple[dict[str, Any], pd.DataFrame, pd.Series]:
    deps = _dependencies()
    train = frame.dropna(subset=[target, "team_key"]).copy()
    features = list(MINUTES_FEATURE_COLUMNS)
    oof = np.full(len(train), np.nan, dtype=float)
    fold_rows = []
    for fold_idx, (train_idx, valid_idx) in enumerate(_folds(train, deps), start=1):
        fold_train = train.iloc[train_idx]
        fold_valid = train.iloc[valid_idx]
        preprocessor, _, _ = build_preprocessor(fold_train, features)
        x_train = preprocessor.fit_transform(fold_train[features])
        x_valid = preprocessor.transform(fold_valid[features])
        y_train = pd.to_numeric(fold_train[target], errors="coerce").astype(int).to_numpy()
        y_valid = pd.to_numeric(fold_valid[target], errors="coerce").astype(int).to_numpy()
        xgb, rf = _classifier_models(deps, seed + fold_idx)
        xgb.fit(x_train, y_train)
        rf.fit(x_train, y_train)
        raw = (xgb.predict_proba(x_valid)[:, 1] + rf.predict_proba(x_valid)[:, 1]) / 2.0
        oof[valid_idx] = raw
        train_teams = set(fold_train["team_key"].astype(str))
        valid_teams = set(fold_valid["team_key"].astype(str))
        fold_rows.append(
            {
                "output": target,
                "fold": fold_idx,
                "rows": int(len(valid_idx)),
                "team_overlap": int(len(train_teams & valid_teams)),
                "raw_brier": float(deps["brier_score_loss"](y_valid, raw)),
            }
        )
    usable = np.isfinite(oof)
    calibrator = deps["IsotonicRegression"](out_of_bounds="clip", y_min=0.0, y_max=1.0)
    calibrator.fit(oof[usable], pd.to_numeric(train.loc[usable, target], errors="coerce").astype(int))

    preprocessor, numeric, categorical = build_preprocessor(train, features)
    x_all = preprocessor.fit_transform(train[features])
    y_all = pd.to_numeric(train[target], errors="coerce").astype(int).to_numpy()
    xgb, rf = _classifier_models(deps, seed)
    xgb.fit(x_all, y_all)
    rf.fit(x_all, y_all)
    component = {
        "feature_columns": features,
        "numeric_columns": numeric,
        "categorical_columns": categorical,
        "transformed_feature_names": transformed_feature_names(preprocessor, numeric, categorical),
        "preprocessor": preprocessor,
        "xgb_model": xgb,
        "rf_model": rf,
        "calibrator": calibrator,
        "training_rows": int(len(train)),
        "target": target,
        "calibration": "out_of_fold_isotonic",
    }
    calibrated_oof = pd.Series(np.nan, index=train.index, dtype=float)
    calibrated_oof.loc[train.index[usable]] = calibrator.predict(oof[usable])
    return component, pd.DataFrame(fold_rows), calibrated_oof


def _fit_regressor(frame: pd.DataFrame, target: str, seed: int) -> dict[str, Any]:
    deps = _dependencies()
    train = frame.dropna(subset=[target, "team_key"]).copy()
    features = list(MINUTES_FEATURE_COLUMNS)
    preprocessor, numeric, categorical = build_preprocessor(train, features)
    matrix = preprocessor.fit_transform(train[features])
    y = pd.to_numeric(train[target], errors="coerce").astype(float).to_numpy()
    xgb, rf = _regressor_models(deps, seed)
    xgb.fit(matrix, y)
    rf.fit(matrix, y)
    return {
        "feature_columns": features,
        "numeric_columns": numeric,
        "categorical_columns": categorical,
        "transformed_feature_names": transformed_feature_names(preprocessor, numeric, categorical),
        "preprocessor": preprocessor,
        "xgb_model": xgb,
        "rf_model": rf,
        "training_rows": int(len(train)),
        "target": target,
    }


def fit_minutes_bundle(
    train: pd.DataFrame,
    seed: int = 42,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    frame = train.copy()
    frame["target_played"] = (pd.to_numeric(frame["actual_minutes"], errors="coerce").fillna(0.0) > 0).astype(int)
    starts = pd.to_numeric(frame.get("starts"), errors="coerce")
    frame["target_started"] = np.where(
        starts.notna(),
        starts > 0,
        pd.to_numeric(frame["actual_minutes"], errors="coerce").fillna(0.0) >= 60,
    ).astype(int)
    frame["target_minutes"] = (
        pd.to_numeric(frame["actual_minutes"], errors="coerce")
        .div(pd.to_numeric(frame["fixture_count"], errors="coerce").clip(lower=1.0))
        .clip(0.0, 90.0)
    )

    play, play_cv, play_oof = _fit_classifier(frame, "target_played", seed)
    start_frame = frame.loc[frame["target_played"] == 1].copy()
    start, start_cv, start_oof = _fit_classifier(start_frame, "target_started", seed + 100)
    mins_start = _fit_regressor(
        frame.loc[frame["target_started"] == 1].assign(target_mins_if_start=lambda df: df["target_minutes"]),
        "target_mins_if_start",
        seed + 200,
    )
    mins_sub = _fit_regressor(
        frame.loc[(frame["target_played"] == 1) & (frame["target_started"] == 0)].assign(
            target_mins_if_sub=lambda df: df["target_minutes"]
        ),
        "target_mins_if_sub",
        seed + 300,
    )
    bundle = {
        "model_type": "four_output_minutes_model",
        "feature_columns": list(MINUTES_FEATURE_COLUMNS),
        "numeric_columns": list(MINUTES_NUMERIC_FEATURES),
        "categorical_columns": list(MINUTES_CATEGORICAL_FEATURES),
        "play_classifier": play,
        "start_classifier": start,
        "mins_if_start_regressor": mins_start,
        "mins_if_sub_regressor": mins_sub,
        "training_seasons": list(TRAIN_SEASONS),
        "holdout_season": HOLDOUT_SEASON,
        "training_rows": int(len(frame)),
        "retrain_date": date.today().isoformat(),
        "random_seed": int(seed),
    }
    oof = frame[["season", "GW", "player_id", "position", "team_key"]].copy()
    oof["pred_play_prob"] = play_oof.reindex(frame.index)
    oof["pred_start_given_play_prob"] = start_oof.reindex(frame.index)
    oof["pred_start_prob"] = oof["pred_play_prob"] * oof["pred_start_given_play_prob"]
    return bundle, pd.concat([play_cv, start_cv], ignore_index=True), oof


def train_minutes_aware_points_bundles(
    minutes_oof: pd.DataFrame,
    output_dir: Path,
) -> pd.DataFrame:
    old_bundles = load_bundles(ROOT / "models" / "position_models")
    ml_frame = _eligible_frame(build_historical_ml_frame(ROOT))
    keys = ["season", "GW", "player_id", "position"]
    play = minutes_oof[keys + ["pred_play_prob"]].drop_duplicates(keys)
    train = ml_frame.loc[ml_frame["season"].isin(TRAIN_SEASONS)].drop(
        columns=["pred_play_prob"],
        errors="ignore",
    ).merge(play, on=keys, how="left")
    if train["pred_play_prob"].isna().any():
        raise RuntimeError(
            f"Missing OOF pred_play_prob for {int(train['pred_play_prob'].isna().sum())} points-model rows"
        )
    bundles, cv = _fit_model_set(
        train,
        old_bundles,
        TRAIN_SEASONS,
        HOLDOUT_SEASON,
        "minutes_aware_points",
        extra_feature_columns=["pred_play_prob"],
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    for position, bundle in bundles.items():
        bundle["minutes_feature"] = "pred_play_prob"
        bundle["minutes_feature_source"] = "team_grouped_out_of_fold_isotonic"
        save_bundle(bundle, output_dir / MODEL_FILENAMES[position])
    return cv


def calibration_table(scored: pd.DataFrame) -> pd.DataFrame:
    bins = np.linspace(0.0, 1.0, 11)
    labels = [f"{int(left * 100)}-{int(right * 100)}%" for left, right in zip(bins[:-1], bins[1:])]
    frame = scored.copy()
    frame["actual_played"] = (pd.to_numeric(frame["actual_minutes"], errors="coerce").fillna(0.0) > 0).astype(float)
    frame["probability_bucket"] = pd.cut(
        pd.to_numeric(frame["pred_play_prob"], errors="coerce"),
        bins=bins,
        labels=labels,
        include_lowest=True,
    )
    return (
        frame.groupby("probability_bucket", observed=False)
        .agg(
            rows=("actual_played", "size"),
            mean_predicted=("pred_play_prob", "mean"),
            actual_play_rate=("actual_played", "mean"),
        )
        .reset_index()
        .assign(gap=lambda df: df["actual_play_rate"] - df["mean_predicted"])
    )


def _segment_labels(frame: pd.DataFrame) -> pd.Series:
    played = pd.to_numeric(frame["rolling_played_3gw"], errors="coerce").fillna(0.0)
    started = pd.to_numeric(frame["rolling_started_3gw"], errors="coerce").fillna(0.0)
    return pd.Series(
        np.select(
            [played.ge(0.8) & started.ge(2 / 3), played.le(1 / 3)],
            ["recent_regulars", "fringe"],
            default="rotation_ambiguous",
        ),
        index=frame.index,
    )


def segment_metrics(scored: pd.DataFrame) -> pd.DataFrame:
    deps = _dependencies()
    frame = scored.copy()
    frame["actual_played"] = (pd.to_numeric(frame["actual_minutes"], errors="coerce").fillna(0.0) > 0).astype(int)
    frame["usage_segment"] = _segment_labels(frame)
    player_team_count = frame.groupby(["season", "player_id"])["team_key"].transform("nunique")
    groups: list[tuple[str, pd.DataFrame]] = [
        (segment, group) for segment, group in frame.groupby("usage_segment")
    ]
    groups.extend(
        [
            ("GK", frame.loc[frame["position"] == "GK"]),
            ("multi_team_players", frame.loc[player_team_count > 1]),
        ]
    )
    rows = []
    for segment, group in groups:
        if group.empty:
            continue
        actual_play = group["actual_played"]
        pred_play = pd.to_numeric(group["pred_play_prob"], errors="coerce")
        rows.append(
            {
                "segment": segment,
                "rows": int(len(group)),
                "play_rate": float(actual_play.mean()),
                "mean_pred_play": float(pred_play.mean()),
                "p_play_brier": float(deps["brier_score_loss"](actual_play, pred_play)),
                "expected_minutes_mae": float(
                    deps["mean_absolute_error"](
                        pd.to_numeric(group["actual_minutes"], errors="coerce").clip(0.0, 90.0),
                        pd.to_numeric(group["expected_minutes"], errors="coerce"),
                    )
                ),
            }
        )
    return pd.DataFrame(rows)


def _markdown(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "_No rows._"
    view = frame.copy()
    for col in view.columns:
        view[col] = view[col].map(
            lambda value: ""
            if pd.isna(value)
            else f"{float(value):.6f}" if isinstance(value, (float, np.floating)) else str(value)
        )
    lines = [
        "| " + " | ".join(view.columns) + " |",
        "| " + " | ".join(["---"] * len(view.columns)) + " |",
    ]
    lines.extend("| " + " | ".join(str(row[col]) for col in view.columns) + " |" for _, row in view.iterrows())
    return "\n".join(lines)


def write_training_findings(
    path: Path,
    holdout: pd.DataFrame,
    calibration: pd.DataFrame,
    segments: pd.DataFrame,
    cv: pd.DataFrame,
) -> None:
    deps = _dependencies()
    actual_play = (pd.to_numeric(holdout["actual_minutes"], errors="coerce").fillna(0.0) > 0).astype(int)
    brier = float(deps["brier_score_loss"](actual_play, holdout["pred_play_prob"]))
    expected_mae = float(
        deps["mean_absolute_error"](
            pd.to_numeric(holdout["actual_minutes"], errors="coerce").clip(0.0, 90.0),
            holdout["expected_minutes"],
        )
    )
    lines = [
        "# Minutes Model Training Findings",
        "",
        f"- Holdout: `{HOLDOUT_SEASON}`",
        f"- Rows: `{len(holdout)}`",
        f"- p_play Brier score: `{brier:.6f}`",
        f"- Expected-minutes MAE: `{expected_mae:.6f}`",
        f"- Maximum GroupKFold team overlap: `{int(cv['team_overlap'].max())}`",
        "",
        "## p_play Calibration",
        _markdown(calibration),
        "",
        "## Segment Metrics",
        _markdown(segments),
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the calibrated four-output minutes model.")
    parser.add_argument("--model-path", default=str(DEFAULT_MINUTES_MODEL_PATH))
    args = parser.parse_args()

    print("Building replay-safe minutes features.")
    features = build_historical_minutes_features(ROOT)
    train = features.loc[features["season"].isin(TRAIN_SEASONS)].copy()
    holdout = features.loc[features["season"] == HOLDOUT_SEASON].copy()
    if train.empty or holdout.empty:
        raise RuntimeError(f"Missing train or holdout rows: train={len(train)} holdout={len(holdout)}")
    print(f"Training rows={len(train)} holdout_rows={len(holdout)}")

    bundle, cv, minutes_oof = fit_minutes_bundle(train)
    if int(cv["team_overlap"].max()) != 0:
        raise RuntimeError("Team leakage detected in minutes-model calibration folds")
    scored = score_minutes(holdout, bundle)
    calibration = calibration_table(scored)
    segments = segment_metrics(scored)
    bundle["holdout_metrics"] = {
        "p_play_brier": float(
            _dependencies()["brier_score_loss"](
                (pd.to_numeric(scored["actual_minutes"], errors="coerce").fillna(0.0) > 0).astype(int),
                scored["pred_play_prob"],
            )
        ),
        "expected_minutes_mae": float(
            np.mean(
                np.abs(
                    pd.to_numeric(scored["actual_minutes"], errors="coerce").clip(0.0, 90.0)
                    - scored["expected_minutes"]
                )
            )
        ),
    }
    model_path = ROOT / args.model_path
    save_minutes_bundle(bundle, model_path)

    output_dir = ROOT / "outputs" / "validation"
    holdout_play = scored[
        ["season", "GW", "player_id", "position", "team_key", "pred_play_prob", "pred_start_prob"]
    ].copy()
    stacked_play = pd.concat([minutes_oof, holdout_play], ignore_index=True, sort=False)
    stacked_play.to_csv(
        output_dir / "minutes_model_stacking_predictions.csv",
        index=False,
        float_format="%.6f",
    )
    points_model_dir = ROOT / "models" / "position_models_minutes_validation"
    points_cv = train_minutes_aware_points_bundles(minutes_oof, points_model_dir)
    points_cv.to_csv(
        output_dir / "minutes_aware_points_cv_metrics.csv",
        index=False,
        float_format="%.6f",
    )
    scored.to_csv(output_dir / "minutes_model_holdout_predictions.csv", index=False, float_format="%.6f")
    calibration.to_csv(output_dir / "minutes_model_play_calibration.csv", index=False, float_format="%.6f")
    segments.to_csv(output_dir / "minutes_model_segment_metrics.csv", index=False, float_format="%.6f")
    cv.to_csv(output_dir / "minutes_model_cv_metrics.csv", index=False, float_format="%.6f")
    write_training_findings(
        output_dir / "minutes_model_training_findings.md",
        scored,
        calibration,
        segments,
        cv,
    )
    print(f"Saved model: {model_path}")
    print(f"p_play Brier={bundle['holdout_metrics']['p_play_brier']:.6f}")
    print(f"Expected-minutes MAE={bundle['holdout_metrics']['expected_minutes_mae']:.6f}")


if __name__ == "__main__":
    main()
