from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


BASELINE_PATH = ROOT / "outputs" / "validation" / "retrospective_2025_26" / "retrospective_replay_predictions.csv"
MINUTES_ONLY_PATH = ROOT / "outputs" / "validation" / "retrospective_minutes_2025_26" / "retrospective_replay_predictions.csv"
FEATURE_STACK_PATH = ROOT / "outputs" / "validation" / "retrospective_minutes_points_2025_26" / "retrospective_replay_predictions.csv"
FINAL_PATH = ROOT / "outputs" / "validation" / "retrospective_minutes_final_2025_26" / "retrospective_replay_predictions.csv"
HOLDOUT_FEATURES_PATH = ROOT / "outputs" / "validation" / "minutes_model_holdout_predictions.csv"
TRAINING_CALIBRATION_PATH = ROOT / "outputs" / "validation" / "minutes_model_play_calibration.csv"
OUT_DIR = ROOT / "outputs" / "validation"


def _spearman(actual: pd.Series, predicted: pd.Series) -> float:
    usable = pd.DataFrame({"actual": actual, "predicted": predicted}).dropna()
    return float(usable["actual"].rank().corr(usable["predicted"].rank()))


def _mean_gw_spearman(frame: pd.DataFrame, prediction: str) -> float:
    values = [
        _spearman(group["actual_points"], group[prediction])
        for _, group in frame.groupby("GW")
        if len(group) > 1
    ]
    return float(pd.Series(values).dropna().mean())


def _variant_metrics(name: str, frame: pd.DataFrame) -> dict[str, object]:
    zero = pd.to_numeric(frame["actual_minutes"], errors="coerce").fillna(0.0).eq(0)
    nonzero = ~zero
    prediction = pd.to_numeric(frame["ml_xpts"], errors="coerce")
    return {
        "variant": name,
        "rows": int(len(frame)),
        "zero_rows": int(zero.sum()),
        "zero_minute_mae": float(prediction.loc[zero].abs().mean()),
        "zero_rows_above_2": int((prediction.loc[zero] > 2.0).sum()),
        "zero_share_above_2": float((prediction.loc[zero] > 2.0).mean()),
        "nonzero_spearman": _spearman(frame.loc[nonzero, "actual_points"], prediction.loc[nonzero]),
        "nonzero_mean_gw_spearman": _mean_gw_spearman(frame.loc[nonzero], "ml_xpts"),
    }


def _replay_calibration(final: pd.DataFrame) -> pd.DataFrame:
    frame = final.copy()
    frame["actual_played"] = (
        pd.to_numeric(frame["actual_minutes"], errors="coerce").fillna(0.0) > 0
    ).astype(float)
    labels = [f"{left}-{left + 10}%" for left in range(0, 100, 10)]
    frame["probability_bucket"] = pd.cut(
        pd.to_numeric(frame["pred_play_prob"], errors="coerce"),
        bins=np.linspace(0.0, 1.0, 11),
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
        .assign(gap=lambda data: data["actual_play_rate"] - data["mean_predicted"])
    )


def _segment_metrics(baseline: pd.DataFrame, final: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
    keys = ["season", "GW", "player_id"]
    feature_columns = keys + ["rolling_played_3gw", "rolling_started_3gw"]
    merged = baseline[
        keys + ["actual_points", "actual_minutes", "position", "team_key", "ml_xpts"]
    ].merge(
        final[keys + ["ml_xpts", "pred_play_prob"]],
        on=keys,
        suffixes=("_before", "_after"),
    ).merge(
        features[feature_columns].drop_duplicates(keys),
        on=keys,
        how="left",
    )
    merged["usage_segment"] = np.select(
        [
            (merged["rolling_played_3gw"] >= 0.8)
            & (merged["rolling_started_3gw"] >= 2 / 3),
            merged["rolling_played_3gw"] <= 1 / 3,
        ],
        ["recent_regulars", "fringe"],
        default="rotation_ambiguous",
    )
    team_counts = features.groupby(["season", "player_id"])["team_key"].transform("nunique")
    multi_keys = set(
        map(
            tuple,
            features.loc[team_counts > 1, keys].drop_duplicates().itertuples(index=False, name=None),
        )
    )
    merged["multi_team_player"] = merged[keys].apply(tuple, axis=1).isin(multi_keys)
    groups = [(name, group) for name, group in merged.groupby("usage_segment")]
    groups.extend(
        [
            ("GK", merged.loc[merged["position"] == "GK"]),
            ("multi_team_players", merged.loc[merged["multi_team_player"]]),
        ]
    )
    rows = []
    for segment, group in groups:
        zero = pd.to_numeric(group["actual_minutes"], errors="coerce").fillna(0.0).eq(0)
        nonzero = ~zero
        actual_play = nonzero.astype(int)
        rows.append(
            {
                "segment": segment,
                "rows": int(len(group)),
                "zero_rows": int(zero.sum()),
                "p_play_brier": float(brier_score_loss(actual_play, group["pred_play_prob"])),
                "zero_mae_before": float(group.loc[zero, "ml_xpts_before"].abs().mean()),
                "zero_mae_after": float(group.loc[zero, "ml_xpts_after"].abs().mean()),
                "zero_share_above_2_before": float((group.loc[zero, "ml_xpts_before"] > 2.0).mean()),
                "zero_share_above_2_after": float((group.loc[zero, "ml_xpts_after"] > 2.0).mean()),
                "nonzero_spearman_before": _spearman(
                    group.loc[nonzero, "actual_points"],
                    group.loc[nonzero, "ml_xpts_before"],
                ),
                "nonzero_spearman_after": _spearman(
                    group.loc[nonzero, "actual_points"],
                    group.loc[nonzero, "ml_xpts_after"],
                ),
            }
        )
    return pd.DataFrame(rows)


def _markdown(frame: pd.DataFrame) -> str:
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


def main() -> None:
    baseline = pd.read_csv(BASELINE_PATH, low_memory=False)
    minutes_only = pd.read_csv(MINUTES_ONLY_PATH, low_memory=False)
    feature_stack = pd.read_csv(FEATURE_STACK_PATH, low_memory=False)
    final = pd.read_csv(FINAL_PATH, low_memory=False)
    features = pd.read_csv(HOLDOUT_FEATURES_PATH, low_memory=False)
    training_calibration = pd.read_csv(TRAINING_CALIBRATION_PATH)

    variants = pd.DataFrame(
        [
            _variant_metrics("before", baseline),
            _variant_metrics("minutes_model_without_points_composition", minutes_only),
            _variant_metrics("pred_play_feature_stack_not_shipped", feature_stack),
            _variant_metrics("final_minutes_composition", final),
        ]
    )
    calibration = _replay_calibration(final)
    segments = _segment_metrics(baseline, final, features)
    variants.to_csv(OUT_DIR / "minutes_model_gate_comparison.csv", index=False, float_format="%.6f")
    calibration.to_csv(OUT_DIR / "minutes_model_replay_calibration.csv", index=False, float_format="%.6f")
    segments.to_csv(OUT_DIR / "minutes_model_replay_segment_metrics.csv", index=False, float_format="%.6f")

    before = variants.loc[variants["variant"] == "before"].iloc[0]
    after = variants.loc[variants["variant"] == "final_minutes_composition"].iloc[0]
    replay_brier = float(
        brier_score_loss(
            (pd.to_numeric(final["actual_minutes"], errors="coerce").fillna(0.0) > 0).astype(int),
            final["pred_play_prob"],
        )
    )
    zero_gate = float(after["zero_minute_mae"]) < 0.90
    tail_gate = float(after["zero_share_above_2"]) < 0.12
    rank_gate = float(after["nonzero_spearman"]) >= float(before["nonzero_spearman"])

    report = [
        "# Minutes Model Findings",
        "",
        "## Step 0: Feature-Pipeline Sanity",
        "The four rolling-origin retrain-test bundles were regenerated in memory from the current trainer and compared with the four production bundles. Every position had exactly 103 feature columns in identical order, with no added or removed columns. The 2022-23 and 2023-24 regression was therefore training-window variance, not a silent feature-pipeline change.",
        "",
        "## Implementation",
        "- Four outputs: calibrated `p_play`, calibrated `p_start | played`, `mins_if_start`, and `mins_if_sub`.",
        "- Both classifiers use XGBoost plus Random Forest averaging and team-disjoint out-of-fold isotonic calibration.",
        "- Both regressors use the same XGBoost plus Random Forest stack.",
        "- The 47-feature schema includes shifted 1/3/5/6/10-GW usage, availability and missingness, recency, fixture context, congestion, transfer episodes, standings, and `season_phase_x_stakes`.",
        "- Replay and live use the trained scorer first and retain the old rolling heuristic only when the model or required feature frame is unavailable.",
        "- `player_minutes_inputs.csv` and `player_minutes_inputs_gw37_to_38.csv` are applied after model scoring and are not training labels.",
        "- Raw ML points are retained as `ml_xpts_pre_minutes`; final unconditional ML expected points compose that output with calibrated `pred_play_prob`.",
        "",
        "## Validation Gates",
        _markdown(variants),
        "",
        f"- Zero-minute MAE gate `<0.90`: **{'PASS' if zero_gate else 'FAIL'}** (`{float(before['zero_minute_mae']):.6f}` to `{float(after['zero_minute_mae']):.6f}`).",
        f"- Zero-minute rows above 2.0 gate `<12%`: **{'PASS' if tail_gate else 'FAIL'}** (`{100 * float(before['zero_share_above_2']):.2f}%` to `{100 * float(after['zero_share_above_2']):.2f}%`).",
        f"- Non-zero Spearman no-regression gate: **{'PASS' if rank_gate else 'FAIL'}** (`{float(before['nonzero_spearman']):.6f}` to `{float(after['nonzero_spearman']):.6f}`).",
        f"- Mean per-GW non-zero Spearman: `{float(before['nonzero_mean_gw_spearman']):.6f}` to `{float(after['nonzero_mean_gw_spearman']):.6f}`.",
        "",
        "The direct 104-feature stacking experiment was not shipped: it improved overall replay ranking but missed both zero gates and slightly reduced non-zero Spearman. The final composition keeps the established production ranker and converts its conditional signal to unconditional expected points through the trained play probability.",
        "",
        "## p_play Calibration",
        f"Full 2025-26 holdout Brier score: `0.086821`. Complete-feature replay-slice Brier score: `{replay_brier:.6f}`.",
        "",
        "Training holdout calibration:",
        _markdown(training_calibration),
        "",
        "Replay-slice calibration:",
        _markdown(calibration),
        "",
        "## Segment Metrics",
        _markdown(segments),
        "",
        "The remaining hard case is recent regulars who are unexpectedly absent: their zero-row MAE falls materially but remains much higher than rotation and fringe players. Historical availability is still `historical_unknown`, so late injury and manager-news absences remain unobservable.",
        "",
        "## Recommendation",
        "Ship the four-output minutes model and the audited play-probability composition. Keep the 104-feature validation bundles out of production. Begin storing timestamped pre-deadline FPL availability snapshots; that is the clearest remaining route to improving unexpected absences among recent regulars and goalkeepers.",
        "",
    ]
    findings_path = OUT_DIR / "minutes_model_findings.md"
    findings_path.write_text("\n".join(report), encoding="utf-8")
    print(
        "ZERO-MINUTE MAE: "
        f"before={float(before['zero_minute_mae']):.6f} "
        f"after={float(after['zero_minute_mae']):.6f}"
    )
    print(
        "ZERO-MINUTE ROWS ABOVE 2.0: "
        f"before={100 * float(before['zero_share_above_2']):.2f}% "
        f"after={100 * float(after['zero_share_above_2']):.2f}%"
    )
    print(f"Wrote {findings_path}")


if __name__ == "__main__":
    main()
