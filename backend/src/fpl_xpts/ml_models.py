from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .config import AppConfig
from .ml_features import (
    MODEL_FILENAMES,
    OPENFPL_REFERENCE,
    POSITIONS,
    build_live_ml_frame,
    choose_feature_columns,
    split_feature_types,
)


def _require_ml_dependencies() -> dict[str, Any]:
    try:
        import joblib
        from sklearn.compose import ColumnTransformer
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.impute import SimpleImputer
        from sklearn.model_selection import GroupKFold
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import OneHotEncoder
        from xgboost import XGBRegressor
    except ImportError as exc:  # pragma: no cover - exercised when optional deps are absent.
        raise RuntimeError(
            "ML predictions require xgboost, scikit-learn, and joblib. "
            "Install project dependencies before training or enabling use_ml_predictions."
        ) from exc
    return {
        "joblib": joblib,
        "ColumnTransformer": ColumnTransformer,
        "GroupKFold": GroupKFold,
        "OneHotEncoder": OneHotEncoder,
        "Pipeline": Pipeline,
        "RandomForestRegressor": RandomForestRegressor,
        "SimpleImputer": SimpleImputer,
        "XGBRegressor": XGBRegressor,
    }


def spearman_corr(actual: pd.Series, predicted: pd.Series) -> float:
    usable = pd.DataFrame({"actual": actual, "predicted": predicted}).dropna()
    if len(usable) < 2:
        return float("nan")
    return float(usable["actual"].rank().corr(usable["predicted"].rank(), method="pearson"))


def metric_block(frame: pd.DataFrame, pred_col: str) -> dict[str, float | int]:
    usable = frame.dropna(subset=["actual_points", pred_col]).copy()
    if usable.empty:
        return {"rows": 0, "mae": np.nan, "rmse": np.nan, "spearman": np.nan}
    error = pd.to_numeric(usable[pred_col], errors="coerce") - pd.to_numeric(usable["actual_points"], errors="coerce")
    return {
        "rows": int(len(usable)),
        "mae": float(error.abs().mean()),
        "rmse": float(math.sqrt(float(np.mean(error.to_numpy() ** 2)))),
        "spearman": spearman_corr(usable["actual_points"], usable[pred_col]),
    }


def _one_hot_encoder(encoder_cls: Any) -> Any:
    try:
        return encoder_cls(handle_unknown="ignore", sparse_output=False)
    except TypeError:  # pragma: no cover - older sklearn compatibility.
        return encoder_cls(handle_unknown="ignore", sparse=False)


def build_preprocessor(frame: pd.DataFrame, feature_columns: list[str]) -> tuple[Any, list[str], list[str]]:
    deps = _require_ml_dependencies()
    numeric_cols, categorical_cols = split_feature_types(frame, feature_columns)
    def imputer(strategy: str, fill_value: object | None = None) -> Any:
        kwargs: dict[str, Any] = {"strategy": strategy}
        if fill_value is not None:
            kwargs["fill_value"] = fill_value
        try:
            return deps["SimpleImputer"](**kwargs, keep_empty_features=True)
        except TypeError:  # pragma: no cover - older sklearn compatibility.
            return deps["SimpleImputer"](**kwargs)

    transformers = []
    if numeric_cols:
        transformers.append(
            (
                "num",
                deps["Pipeline"]([("imputer", imputer("constant", -1.0))]),
                numeric_cols,
            )
        )
    if categorical_cols:
        transformers.append(
            (
                "cat",
                deps["Pipeline"](
                    [
                        ("imputer", imputer("constant", "missing")),
                        ("onehot", _one_hot_encoder(deps["OneHotEncoder"])),
                    ]
                ),
                categorical_cols,
            )
        )
    if not transformers:
        raise ValueError("No ML feature columns were selected.")
    return deps["ColumnTransformer"](transformers=transformers, remainder="drop"), numeric_cols, categorical_cols


def transformed_feature_names(preprocessor: Any, numeric_cols: list[str], categorical_cols: list[str]) -> list[str]:
    try:
        return [str(name) for name in preprocessor.get_feature_names_out()]
    except Exception:
        names = [f"num__{col}" for col in numeric_cols]
        if categorical_cols:
            names.extend(f"cat__{col}" for col in categorical_cols)
        return names


def team_group_folds(frame: pd.DataFrame, n_splits: int = 5) -> list[tuple[np.ndarray, np.ndarray]]:
    deps = _require_ml_dependencies()
    if "team_key" not in frame.columns:
        raise ValueError("team_key is required for team-grouped cross-validation.")
    groups = frame["team_key"].fillna("unknown").astype(str)
    unique_groups = groups.nunique()
    if unique_groups < 2:
        return []
    folds = deps["GroupKFold"](n_splits=min(int(n_splits), int(unique_groups)))
    return list(folds.split(frame, frame["actual_points"], groups=groups))


def fit_position_ensemble(frame: pd.DataFrame, position: str, random_seed: int = 42) -> tuple[dict[str, Any], pd.DataFrame]:
    deps = _require_ml_dependencies()
    pos_frame = frame.loc[frame["position"] == position].copy()
    pos_frame = pos_frame.dropna(subset=["actual_points", "kft_xpts", "team_key"])
    if pos_frame.empty:
        raise ValueError(f"No rows available for position {position}")
    feature_columns = choose_feature_columns(pos_frame)
    if "kft_xpts" not in feature_columns:
        feature_columns.append("kft_xpts")
    feature_columns = sorted(dict.fromkeys(feature_columns))
    preprocessor, numeric_cols, categorical_cols = build_preprocessor(pos_frame, feature_columns)
    x_train = preprocessor.fit_transform(pos_frame[feature_columns])
    y_train = pd.to_numeric(pos_frame["actual_points"], errors="coerce").astype(float).to_numpy()

    xgb = deps["XGBRegressor"](
        objective="reg:squarederror",
        n_estimators=350,
        max_depth=4,
        learning_rate=0.035,
        subsample=0.85,
        colsample_bytree=0.85,
        reg_lambda=2.0,
        random_state=random_seed,
        n_jobs=-1,
        tree_method="hist",
    )
    rf = deps["RandomForestRegressor"](
        n_estimators=250,
        max_depth=12,
        min_samples_leaf=15,
        random_state=random_seed,
        n_jobs=-1,
    )
    xgb.fit(x_train, y_train)
    rf.fit(x_train, y_train)
    cv_rows = []
    folds = team_group_folds(pos_frame, n_splits=5)
    for fold_idx, (train_idx, valid_idx) in enumerate(folds, start=1):
        train_fold = pos_frame.iloc[train_idx].copy()
        valid_fold = pos_frame.iloc[valid_idx].copy()
        fold_preprocessor, _, _ = build_preprocessor(train_fold, feature_columns)
        x_fold_train = fold_preprocessor.fit_transform(train_fold[feature_columns])
        x_fold_valid = fold_preprocessor.transform(valid_fold[feature_columns])
        y_fold_train = pd.to_numeric(train_fold["actual_points"], errors="coerce").astype(float).to_numpy()
        fold_xgb = deps["XGBRegressor"](
            objective="reg:squarederror",
            n_estimators=250,
            max_depth=4,
            learning_rate=0.04,
            subsample=0.85,
            colsample_bytree=0.85,
            reg_lambda=2.0,
            random_state=random_seed + fold_idx,
            n_jobs=-1,
            tree_method="hist",
        )
        fold_rf = deps["RandomForestRegressor"](
            n_estimators=160,
            max_depth=12,
            min_samples_leaf=15,
            random_state=random_seed + fold_idx,
            n_jobs=-1,
        )
        fold_xgb.fit(x_fold_train, y_fold_train)
        fold_rf.fit(x_fold_train, y_fold_train)
        valid_fold["cv_xgb"] = np.clip(fold_xgb.predict(x_fold_valid), 0.0, None)
        valid_fold["cv_rf"] = np.clip(fold_rf.predict(x_fold_valid), 0.0, None)
        valid_fold["cv_ml_xpts"] = (valid_fold["cv_xgb"] + valid_fold["cv_rf"]) / 2.0
        teams_train = set(train_fold["team_key"].dropna().astype(str))
        teams_valid = set(valid_fold["team_key"].dropna().astype(str))
        cv_rows.append(
            {
                "position": position,
                "fold": fold_idx,
                "rows": int(len(valid_fold)),
                "team_overlap": int(len(teams_train & teams_valid)),
                **{f"cv_{key}": value for key, value in metric_block(valid_fold, "cv_ml_xpts").items()},
            }
        )

    bundle = {
        "position": position,
        "feature_columns": feature_columns,
        "numeric_columns": numeric_cols,
        "categorical_columns": categorical_cols,
        "transformed_feature_names": transformed_feature_names(preprocessor, numeric_cols, categorical_cols),
        "preprocessor": preprocessor,
        "xgb_model": xgb,
        "rf_model": rf,
        "training_rows": int(len(pos_frame)),
        "training_seasons": ["2019-20", "2020-21", "2021-22", "2022-23", "2023-24"],
        "holdout_season": "2024-25",
        "random_seed": int(random_seed),
    }
    return bundle, pd.DataFrame(cv_rows)


def predict_with_bundle(bundle: dict[str, Any], frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    feature_columns = list(bundle["feature_columns"])
    data = frame.copy()
    for col in feature_columns:
        if col not in data.columns:
            data[col] = np.nan
    x_mat = bundle["preprocessor"].transform(data[feature_columns])
    out = data.copy()
    out["ml_xpts_xgb"] = np.clip(bundle["xgb_model"].predict(x_mat), 0.0, None)
    out["ml_xpts_rf"] = np.clip(bundle["rf_model"].predict(x_mat), 0.0, None)
    out["ml_xpts"] = (out["ml_xpts_xgb"] + out["ml_xpts_rf"]) / 2.0
    if "pred_play_prob" in out.columns:
        play_probability = pd.to_numeric(out["pred_play_prob"], errors="coerce")
        available = play_probability.notna()
        if available.any():
            out["ml_xpts_xgb_pre_minutes"] = out["ml_xpts_xgb"]
            out["ml_xpts_rf_pre_minutes"] = out["ml_xpts_rf"]
            out["ml_xpts_pre_minutes"] = out["ml_xpts"]
            multiplier = play_probability.clip(0.0, 1.0).fillna(1.0)
            out["ml_xpts_xgb"] = out["ml_xpts_xgb"] * multiplier
            out["ml_xpts_rf"] = out["ml_xpts_rf"] * multiplier
            out["ml_xpts"] = out["ml_xpts"] * multiplier
    return out


def feature_importance(bundle: dict[str, Any], top_n: int = 10) -> pd.DataFrame:
    names = list(bundle.get("transformed_feature_names", []))
    rows = []
    for model_name, key in [("xgboost", "xgb_model"), ("random_forest", "rf_model")]:
        model = bundle[key]
        importances = getattr(model, "feature_importances_", None)
        if importances is None:
            continue
        if not names or len(names) != len(importances):
            names = [f"feature_{idx}" for idx in range(len(importances))]
        order = np.argsort(importances)[::-1][:top_n]
        total = float(np.sum(importances)) or 1.0
        for rank, idx in enumerate(order, start=1):
            rows.append(
                {
                    "position": bundle["position"],
                    "model": model_name,
                    "rank": rank,
                    "feature": names[int(idx)],
                    "importance": float(importances[int(idx)]),
                    "importance_share": float(importances[int(idx)] / total),
                }
            )
    return pd.DataFrame(rows)


def comparison_rows(scored: pd.DataFrame) -> pd.DataFrame:
    rows = []
    scopes = [("overall", "overall", scored)]
    scopes.extend((("position", pos, group) for pos, group in scored.groupby("position", dropna=False)))
    for scope, group_name, group in scopes:
        if str(group_name) not in {"overall", *POSITIONS}:
            continue
        kft = metric_block(group, "kft_xpts")
        ml = metric_block(group, "ml_xpts")
        high = group.loc[pd.to_numeric(group["actual_points"], errors="coerce") > 2].copy()
        kft_high = metric_block(high, "kft_xpts")
        ml_high = metric_block(high, "ml_xpts")
        row: dict[str, Any] = {
            "scope": scope,
            "group": group_name,
            "rows": ml["rows"],
            "kft_mae": kft["mae"],
            "kft_rmse": kft["rmse"],
            "kft_spearman": kft["spearman"],
            "ml_ensemble_mae": ml["mae"],
            "ml_ensemble_rmse": ml["rmse"],
            "ml_ensemble_spearman": ml["spearman"],
            "delta_spearman": float(ml["spearman"] - kft["spearman"]) if pd.notna(ml["spearman"]) and pd.notna(kft["spearman"]) else np.nan,
            "gt2_rows": ml_high["rows"],
            "gt2_kft_mae": kft_high["mae"],
            "gt2_kft_rmse": kft_high["rmse"],
            "gt2_kft_spearman": kft_high["spearman"],
            "gt2_ml_ensemble_mae": ml_high["mae"],
            "gt2_ml_ensemble_rmse": ml_high["rmse"],
            "gt2_ml_ensemble_spearman": ml_high["spearman"],
            "gt2_delta_spearman": float(ml_high["spearman"] - kft_high["spearman"]) if pd.notna(ml_high["spearman"]) and pd.notna(kft_high["spearman"]) else np.nan,
            "openfpl_reported_spearman": "not_reported",
        }
        row.update(OPENFPL_REFERENCE.get(str(group_name), {}))
        rows.append(row)
    return pd.DataFrame(rows)


def save_bundle(bundle: dict[str, Any], path: Path) -> None:
    deps = _require_ml_dependencies()
    path.parent.mkdir(parents=True, exist_ok=True)
    deps["joblib"].dump(bundle, path)


def load_bundles(model_dir: Path) -> dict[str, dict[str, Any]]:
    deps = _require_ml_dependencies()
    bundles = {}
    for position, filename in MODEL_FILENAMES.items():
        path = model_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Missing ML model bundle for {position}: {path}")
        bundles[position] = deps["joblib"].load(path)
    return bundles


def attach_live_ml_predictions(
    weekly: pd.DataFrame,
    players: pd.DataFrame,
    player_fixture: pd.DataFrame,
    teams: pd.DataFrame,
    config: AppConfig,
) -> pd.DataFrame:
    bundles = load_bundles(config.ml_model_dir)
    live_features = build_live_ml_frame(weekly, players, player_fixture, teams)
    predictions = []
    for position, bundle in bundles.items():
        pos_frame = live_features.loc[live_features["position"] == position].copy()
        if pos_frame.empty:
            continue
        predicted = predict_with_bundle(bundle, pos_frame)
        keep = [
            col
            for col in [
                "event",
                "player_id",
                "ml_xpts",
                "ml_xpts_xgb",
                "ml_xpts_rf",
                "ml_xpts_pre_minutes",
            ]
            if col in predicted.columns
        ]
        predictions.append(predicted[keep])
    if not predictions:
        out = weekly.copy()
        out["ml_xpts"] = np.nan
        return out
    pred = pd.concat(predictions, ignore_index=True)
    return weekly.drop(
        columns=["ml_xpts", "ml_xpts_xgb", "ml_xpts_rf", "ml_xpts_pre_minutes"],
        errors="ignore",
    ).merge(
        pred,
        on=["event", "player_id"],
        how="left",
    )
