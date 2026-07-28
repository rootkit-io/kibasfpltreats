from __future__ import annotations

import argparse
import hashlib
import shutil
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

from fpl_xpts.config import AppConfig  # noqa: E402
from fpl_xpts.ml_features import MODEL_FILENAMES, POSITIONS, build_historical_ml_frame  # noqa: E402
from fpl_xpts.ml_models import (  # noqa: E402
    feature_importance,
    fit_position_ensemble,
    load_bundles,
    metric_block,
    predict_with_bundle,
    save_bundle,
)


ROLLING_TRAIN_SEASONS = ["2019-20", "2020-21", "2021-22", "2022-23", "2023-24", "2024-25"]
HOLDOUT_SEASON = "2025-26"
FINAL_TRAIN_SEASONS = [*ROLLING_TRAIN_SEASONS, HOLDOUT_SEASON]
OLDER_VALIDATION_SEASONS = ["2022-23", "2023-24"]
KFT_COLUMNS = [
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


def _is_complete(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().isin({"true", "1", "1.0"})


def _write_dependency_failure(exc: Exception) -> None:
    out = ROOT / "outputs" / "validation" / "ml_training_dependency_failure.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "ML training could not run because one or more optional ML dependencies were unavailable.\n"
        f"{type(exc).__name__}: {exc}\n",
        encoding="utf-8",
    )
    print(f"Dependency failure logged to {out}")


def _attach_2025_replay_kft(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    target = out["season"].astype(str) == HOLDOUT_SEASON
    complete_target = target & _is_complete(out["complete_features"])
    existing = int(out.loc[complete_target, "kft_xpts"].notna().sum())
    required = int(complete_target.sum())
    if existing == required:
        return out

    replay_path = ROOT / "outputs" / "validation" / "retrospective_2025_26" / "retrospective_replay_predictions.csv"
    if not replay_path.exists():
        raise FileNotFoundError(
            f"2025-26 KFT prediction layer is incomplete ({existing}/{required}) and replay output is missing: {replay_path}"
        )
    replay = pd.read_csv(
        replay_path,
        usecols=lambda col: col in {"season", "GW", "player_id", *KFT_COLUMNS},
    )
    replay = replay.loc[replay["season"].astype(str) == HOLDOUT_SEASON].copy()
    if replay.duplicated(["season", "GW", "player_id"]).any():
        raise RuntimeError("2025-26 replay KFT layer has duplicate season/GW/player_id keys")

    out = out.merge(replay, on=["season", "GW", "player_id"], how="left", suffixes=("", "_replay"))
    for col in KFT_COLUMNS:
        replay_col = f"{col}_replay"
        if replay_col in out.columns:
            out[col] = out[col].where(out[col].notna(), out[replay_col])
            out = out.drop(columns=replay_col)

    attached = int(out.loc[complete_target, "kft_xpts"].notna().sum())
    if attached != required:
        raise RuntimeError(f"2025-26 KFT prediction layer remains incomplete after replay merge: {attached}/{required}")
    print(f"Attached 2025-26 replay KFT layer: rows={attached}")
    return out


def _eligible_frame(frame: pd.DataFrame) -> pd.DataFrame:
    scoped = frame.loc[frame["position"].isin(POSITIONS)].copy()
    scoped = scoped.loc[_is_complete(scoped["complete_features"])].copy()
    return scoped.dropna(subset=["actual_points", "kft_xpts", "team_key"])


def _season_position_counts(frame: pd.DataFrame) -> pd.DataFrame:
    counts = (
        frame.loc[frame["season"].isin(FINAL_TRAIN_SEASONS)]
        .groupby(["season", "position"], as_index=False)
        .size()
        .rename(columns={"size": "rows"})
    )
    return counts


def _locked_position_frame(frame: pd.DataFrame, position: str, feature_columns: list[str]) -> pd.DataFrame:
    required = [
        "season",
        "GW",
        "player_id",
        "position",
        "team_key",
        "actual_points",
        *feature_columns,
    ]
    missing = sorted(set(required) - set(frame.columns))
    if missing:
        raise ValueError(f"Missing locked features for {position}: {', '.join(missing)}")
    keep = list(dict.fromkeys(required))
    return frame.loc[frame["position"] == position, keep].copy()


def _fit_model_set(
    train: pd.DataFrame,
    old_bundles: dict[str, dict[str, Any]],
    training_seasons: list[str],
    holdout_season: str,
    label: str,
    extra_feature_columns: list[str] | None = None,
) -> tuple[dict[str, dict[str, Any]], pd.DataFrame]:
    bundles: dict[str, dict[str, Any]] = {}
    cv_frames = []
    for position in POSITIONS:
        locked_features = list(old_bundles[position]["feature_columns"])
        for feature in extra_feature_columns or []:
            if feature not in train.columns:
                raise ValueError(f"Missing requested extra feature for {position}: {feature}")
            if feature not in locked_features:
                locked_features.append(feature)
        locked_features = sorted(dict.fromkeys(locked_features))
        pos_train = _locked_position_frame(train, position, locked_features)
        print(f"{label} {position}: train_rows={len(pos_train)} features={len(locked_features)}")
        bundle, cv_metrics = fit_position_ensemble(pos_train, position, random_seed=42)
        if list(bundle["feature_columns"]) != locked_features:
            raise RuntimeError(f"{position} feature set changed during data-window-only retrain")
        if not cv_metrics.empty and int(pd.to_numeric(cv_metrics["team_overlap"], errors="coerce").fillna(0).max()) != 0:
            raise RuntimeError(f"{position} GroupKFold leaked a team between train and validation")
        bundle["training_seasons"] = list(training_seasons)
        bundle["training_rows"] = int(len(pos_train))
        bundle["holdout_season"] = str(holdout_season)
        bundles[position] = bundle
        cv_frames.append(cv_metrics.assign(training_window=label))
    return bundles, pd.concat(cv_frames, ignore_index=True)


def _predict_model_set(
    bundles: dict[str, dict[str, Any]],
    frame: pd.DataFrame,
    prediction_name: str,
) -> pd.DataFrame:
    predictions = []
    for position in POSITIONS:
        pos = _locked_position_frame(frame, position, list(bundles[position]["feature_columns"]))
        if pos.empty:
            continue
        scored = predict_with_bundle(bundles[position], pos)
        predictions.append(
            scored[
                ["season", "GW", "player_id", "position", "actual_points", "ml_xpts", "ml_xpts_xgb", "ml_xpts_rf"]
            ].rename(
                columns={
                    "ml_xpts": prediction_name,
                    "ml_xpts_xgb": f"{prediction_name}_xgb",
                    "ml_xpts_rf": f"{prediction_name}_rf",
                }
            )
        )
    if not predictions:
        raise RuntimeError(f"No predictions generated for {prediction_name}")
    return pd.concat(predictions, ignore_index=True)


def _mean_std(values: list[float]) -> tuple[float, float]:
    clean = pd.Series(values, dtype=float).dropna()
    if clean.empty:
        return np.nan, np.nan
    return float(clean.mean()), float(clean.std(ddof=1) if len(clean) > 1 else 0.0)


def _per_gw_metric_block(frame: pd.DataFrame, pred_col: str) -> dict[str, float | int]:
    per_gw = []
    for _, group in frame.groupby("GW", dropna=False):
        metrics = metric_block(group, pred_col)
        if metrics["rows"]:
            per_gw.append(metrics)
    mae_mean, mae_std = _mean_std([float(row["mae"]) for row in per_gw])
    spearman_mean, spearman_std = _mean_std([float(row["spearman"]) for row in per_gw])
    return {
        "rows": int(sum(int(row["rows"]) for row in per_gw)),
        "gw_count": int(len(per_gw)),
        "mae": mae_mean,
        "mae_std": mae_std,
        "spearman": spearman_mean,
        "spearman_std": spearman_std,
    }


def _comparison_rows(
    scored: pd.DataFrame,
    old_col: str,
    new_col: str,
    include_season: bool = False,
) -> pd.DataFrame:
    scopes: list[tuple[str, str, str, pd.DataFrame]] = []
    if include_season:
        for season, season_frame in scored.groupby("season", dropna=False):
            scopes.append(("season", str(season), "all", season_frame))
            scopes.extend(
                ("season_position", str(season), str(position), group)
                for position, group in season_frame.groupby("position", dropna=False)
            )
    else:
        scopes.append(("overall", HOLDOUT_SEASON, "all", scored))
        scopes.extend(
            ("position", HOLDOUT_SEASON, str(position), group)
            for position, group in scored.groupby("position", dropna=False)
        )

    rows = []
    for scope, season, position, group in scopes:
        old = _per_gw_metric_block(group, old_col)
        new = _per_gw_metric_block(group, new_col)
        rows.append(
            {
                "scope": scope,
                "season": season,
                "position": position,
                "rows": new["rows"],
                "gw_count": new["gw_count"],
                "old_mae": old["mae"],
                "new_mae": new["mae"],
                "delta_mae": float(new["mae"] - old["mae"]),
                "old_spearman": old["spearman"],
                "new_spearman": new["spearman"],
                "delta_spearman": float(new["spearman"] - old["spearman"]),
            }
        )
    return pd.DataFrame(rows)


def _merge_prediction_sets(old: pd.DataFrame, new: pd.DataFrame, old_name: str, new_name: str) -> pd.DataFrame:
    keys = ["season", "GW", "player_id", "position"]
    merged = old[keys + ["actual_points", old_name]].merge(
        new[keys + [new_name]],
        on=keys,
        how="inner",
    )
    if len(merged) != len(old) or len(merged) != len(new):
        raise RuntimeError(
            f"Prediction row mismatch for {old_name}/{new_name}: old={len(old)} new={len(new)} merged={len(merged)}"
        )
    return merged


def _classify_improvement(overall: pd.Series) -> str:
    spearman_delta = float(overall["delta_spearman"])
    mae_delta = float(overall["delta_mae"])
    if spearman_delta >= 0.02 and mae_delta <= -0.02:
        return "clear improvement"
    if spearman_delta > 0.0 and mae_delta < 0.0:
        return "marginal improvement"
    return "made things worse"


def _archive_old_bundles(model_dir: Path) -> Path:
    archive_dir = model_dir / "archive_pre_retrain"
    archive_dir.mkdir(parents=True, exist_ok=True)
    for filename in MODEL_FILENAMES.values():
        source = model_dir / filename
        target = archive_dir / filename
        if target.exists():
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            target_hash = hashlib.sha256(target.read_bytes()).hexdigest()
            if source_hash != target_hash:
                raise RuntimeError(f"Refusing to overwrite different archived bundle: {target}")
        else:
            shutil.copy2(source, target)
    return archive_dir


def _markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "_No rows._"
    text = frame.copy()
    for col in text.columns:
        text[col] = text[col].map(
            lambda value: ""
            if pd.isna(value)
            else f"{float(value):.6f}" if isinstance(value, (float, np.floating)) else str(value).replace("|", "\\|")
        )
    lines = [
        "| " + " | ".join(text.columns.astype(str)) + " |",
        "| " + " | ".join(["---"] * len(text.columns)) + " |",
    ]
    lines.extend("| " + " | ".join(str(row[col]) for col in text.columns) + " |" for _, row in text.iterrows())
    return "\n".join(lines)


def _write_findings(
    path: Path,
    counts: pd.DataFrame,
    holdout: pd.DataFrame,
    older: pd.DataFrame,
    classification: str,
    ship: bool,
    archive_dir: Path | None,
    cv_metrics: pd.DataFrame,
) -> None:
    overall = holdout.loc[holdout["scope"] == "overall"].iloc[0]
    older_overall = older.loc[older["scope"] == "season"].copy()
    range_warnings = older_overall.loc[
        (pd.to_numeric(older_overall["new_spearman"], errors="coerce") < 0.64)
        | (pd.to_numeric(older_overall["new_spearman"], errors="coerce") > 0.70)
    ]
    overlap_max = int(pd.to_numeric(cv_metrics.get("team_overlap", pd.Series([0])), errors="coerce").fillna(0).max())
    recommendation = "Ship the retrained production bundles." if ship else "Do not ship; keep the current production bundles."
    lines = [
        "# Position ML Retrain Findings",
        "",
        "## Training Rows",
        _markdown_table(counts),
        "",
        "## 2025-26 Rolling-Origin Holdout",
        _markdown_table(holdout),
        "",
        "Metrics are means of per-gameweek MAE and Spearman, matching the retrospective replay definition.",
        "",
        "## Older-Season Check",
        _markdown_table(older),
        "",
        f"GroupKFold maximum train/validation team overlap: `{overlap_max}`.",
        "",
        "## Assessment",
        f"The rolling-origin retrain is a **{classification}**.",
        (
            f"Overall 2025-26 Spearman changed from {float(overall['old_spearman']):.3f} "
            f"to {float(overall['new_spearman']):.3f}; MAE changed from {float(overall['old_mae']):.3f} "
            f"to {float(overall['new_mae']):.3f}."
        ),
    ]
    if range_warnings.empty:
        lines.append("Both 2022-23 and 2023-24 overall Spearman values remain in the requested 0.64-0.70 band.")
    else:
        affected = ", ".join(
            f"{row['season']}={float(row['new_spearman']):.3f}" for _, row in range_warnings.iterrows()
        )
        lines.append(f"Warning: older-season overall Spearman is outside the requested 0.64-0.70 band: {affected}.")
    lines.extend(
        [
            "",
            "## Recommendation",
            recommendation,
            f"Old bundle archive: `{archive_dir}`." if archive_dir is not None else "Old bundles were not replaced.",
            "",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _holdout_metric_metadata(holdout: pd.DataFrame, position: str) -> dict[str, Any]:
    scope = "overall" if position == "all" else "position"
    row = holdout.loc[(holdout["scope"] == scope) & (holdout["position"] == position)].iloc[0]
    return {
        "metric_basis": "mean_of_per_gameweek_metrics",
        "old_bundle": {
            "rows": int(row["rows"]),
            "gw_count": int(row["gw_count"]),
            "mae": float(row["old_mae"]),
            "spearman": float(row["old_spearman"]),
        },
        "rolling_origin_ml_ensemble": {
            "rows": int(row["rows"]),
            "gw_count": int(row["gw_count"]),
            "mae": float(row["new_mae"]),
            "spearman": float(row["new_spearman"]),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Retrain position ML ensembles with rolling-origin validation.")
    parser.add_argument("--model-dir", default=str(AppConfig().ml_model_dir), help="Directory for position model bundles")
    args = parser.parse_args()

    model_dir = ROOT / args.model_dir
    validation_dir = ROOT / "outputs" / "validation"
    validation_dir.mkdir(parents=True, exist_ok=True)
    model_dir.mkdir(parents=True, exist_ok=True)

    print("Step 1: load current bundles and historical feature frame")
    try:
        old_bundles = load_bundles(model_dir)
        frame = _attach_2025_replay_kft(build_historical_ml_frame(ROOT))
        eligible = _eligible_frame(frame)
        counts = _season_position_counts(eligible)
        print(counts.pivot(index="season", columns="position", values="rows").fillna(0).astype(int).to_string())

        for season in ["2024-25", "2025-26"]:
            season_rows = eligible.loc[eligible["season"] == season]
            if season_rows.empty:
                raise RuntimeError(f"No complete-feature training rows for {season}")
            missing_positions = sorted(set(POSITIONS) - set(season_rows["position"].unique()))
            if missing_positions:
                raise RuntimeError(f"{season} is missing complete-feature rows for: {', '.join(missing_positions)}")

        rolling_train = eligible.loc[eligible["season"].isin(ROLLING_TRAIN_SEASONS)].copy()
        holdout_frame = eligible.loc[eligible["season"] == HOLDOUT_SEASON].copy()
        print(f"Step 1 complete: rolling_train={len(rolling_train)} holdout={len(holdout_frame)}")

        print("Step 2: fit rolling-origin models with exact old feature sets")
        rolling_bundles, rolling_cv = _fit_model_set(
            rolling_train,
            old_bundles,
            ROLLING_TRAIN_SEASONS,
            HOLDOUT_SEASON,
            "rolling_origin",
        )

        new_holdout = _predict_model_set(rolling_bundles, holdout_frame, "new_ml_xpts")
        old_replay = pd.read_csv(
            ROOT / "outputs" / "validation" / "retrospective_2025_26" / "retrospective_replay_predictions.csv",
            usecols=["season", "GW", "player_id", "position", "actual_points", "ml_xpts"],
        ).rename(columns={"ml_xpts": "old_ml_xpts"})
        holdout_scored = _merge_prediction_sets(old_replay, new_holdout, "old_ml_xpts", "new_ml_xpts")
        holdout_comparison = _comparison_rows(holdout_scored, "old_ml_xpts", "new_ml_xpts")
        print("\n2025-26 old vs new")
        print(holdout_comparison.to_string(index=False))

        print("Step 3: check 2022-23 and 2023-24")
        older_frame = eligible.loc[eligible["season"].isin(OLDER_VALIDATION_SEASONS)].copy()
        old_older = _predict_model_set(old_bundles, older_frame, "old_ml_xpts")
        new_older = _predict_model_set(rolling_bundles, older_frame, "new_ml_xpts")
        older_scored = _merge_prediction_sets(old_older, new_older, "old_ml_xpts", "new_ml_xpts")
        older_comparison = _comparison_rows(
            older_scored,
            "old_ml_xpts",
            "new_ml_xpts",
            include_season=True,
        )
        print(older_comparison.to_string(index=False))

        overall = holdout_comparison.loc[holdout_comparison["scope"] == "overall"].iloc[0]
        classification = _classify_improvement(overall)
        older_overall = older_comparison.loc[older_comparison["scope"] == "season"]
        older_floor_ok = bool((pd.to_numeric(older_overall["new_spearman"], errors="coerce") >= 0.64).all())
        holdout_improved = bool(float(overall["delta_spearman"]) > 0 and float(overall["delta_mae"]) < 0)
        ship = holdout_improved and older_floor_ok

        archive_dir: Path | None = None
        final_cv = pd.DataFrame()
        importance = pd.DataFrame()
        if ship:
            print("Step 4: validation passed; fit final production models on all available seasons")
            final_train = eligible.loc[eligible["season"].isin(FINAL_TRAIN_SEASONS)].copy()
            final_bundles, final_cv = _fit_model_set(
                final_train,
                old_bundles,
                FINAL_TRAIN_SEASONS,
                HOLDOUT_SEASON,
                "final_production",
            )
            retrain_date = date.today().isoformat()
            importance_frames = []
            for position, bundle in final_bundles.items():
                bundle["holdout_metrics"] = _holdout_metric_metadata(holdout_comparison, position)
                bundle["retrain_date"] = retrain_date
                bundle["rolling_origin_training_seasons"] = list(ROLLING_TRAIN_SEASONS)
                importance_frames.append(feature_importance(bundle, top_n=10))

            archive_dir = _archive_old_bundles(model_dir)
            for position, bundle in final_bundles.items():
                save_bundle(bundle, model_dir / MODEL_FILENAMES[position])
            importance = pd.concat(importance_frames, ignore_index=True)
            print(f"Archived old bundles to {archive_dir}")
            print("Saved final production bundles.")
        else:
            print("Step 4: validation gate failed; production bundles were not replaced.")

        counts.to_csv(validation_dir / "retrain_row_counts.csv", index=False)
        holdout_comparison.to_csv(validation_dir / "retrain_holdout_comparison.csv", index=False, float_format="%.6f")
        older_comparison.to_csv(validation_dir / "retrain_older_season_comparison.csv", index=False, float_format="%.6f")
        holdout_scored.to_csv(validation_dir / "retrain_holdout_predictions.csv", index=False, float_format="%.6f")
        rolling_cv.to_csv(validation_dir / "retrain_rolling_cv_metrics.csv", index=False, float_format="%.6f")
        if not final_cv.empty:
            final_cv.to_csv(validation_dir / "ml_cv_metrics.csv", index=False, float_format="%.6f")
        if not importance.empty:
            importance.to_csv(validation_dir / "feature_importance_by_position.csv", index=False, float_format="%.6f")

        report_path = validation_dir / "retrain_findings.md"
        _write_findings(
            report_path,
            counts,
            holdout_comparison,
            older_comparison,
            classification,
            ship,
            archive_dir,
            pd.concat([rolling_cv, final_cv], ignore_index=True),
        )
        print(f"Wrote {report_path}")
        print(
            "HEADLINE 2025-26 SPEARMAN: "
            f"old={float(overall['old_spearman']):.6f} "
            f"new={float(overall['new_spearman']):.6f} "
            f"delta={float(overall['delta_spearman']):+.6f}"
        )
    except RuntimeError as exc:
        if "ML predictions require" in str(exc):
            _write_dependency_failure(exc)
            return
        raise
    except ImportError as exc:
        _write_dependency_failure(exc)
        return


if __name__ == "__main__":
    main()
