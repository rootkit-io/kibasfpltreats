from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from scripts.retrospective_replay import (  # noqa: E402
    _markdown_table,
    build_metrics,
    get_season_scoring_config,
)


SEASONS = ("2024-25", "2025-26")
MODEL_COLUMNS = {
    "KFT rules": "kft_xpts",
    "KFT ML ensemble": "ml_xpts",
    "MC baseline": "mc_baseline_MC_MeanPts",
    "MC ML-weighted": "mc_ml_weighted_MC_MeanPts",
}


def _load_predictions(path: Path, season: str) -> pd.DataFrame:
    frame = pd.read_csv(path)
    scoped = frame.loc[frame["season"].astype(str) == season].copy()
    if scoped.empty:
        raise ValueError(f"{path} contains no {season} rows")
    return scoped


def _side_by_side(rows: list[dict[str, object]], keys: list[str]) -> pd.DataFrame:
    long = pd.DataFrame(rows)
    wide = long.pivot(index=[*keys, "metric"], columns="season", values="value").reset_index()
    for season in SEASONS:
        if season not in wide.columns:
            wide[season] = np.nan
    wide["delta_2025_26_minus_2024_25"] = pd.to_numeric(wide["2025-26"], errors="coerce") - pd.to_numeric(
        wide["2024-25"], errors="coerce"
    )
    return wide[[*keys, "metric", "2024-25", "2025-26", "delta_2025_26_minus_2024_25"]]


def _metric_rows(frames: dict[str, pd.DataFrame], scope: str) -> pd.DataFrame:
    value_columns = [
        "gw_count",
        "rows",
        "gt2_rows",
        "overall_mae_mean",
        "overall_mae_std",
        "gt2_mae_mean",
        "gt2_mae_std",
        "spearman_mean",
        "spearman_std",
    ]
    rows: list[dict[str, object]] = []
    for season, frame in frames.items():
        metrics = build_metrics(frame)
        scoped = metrics.loc[metrics["scope"] == scope].copy()
        for _, item in scoped.iterrows():
            for metric in value_columns:
                rows.append(
                    {
                        "season": season,
                        "position": item["position"],
                        "model": item["model"],
                        "metric": metric,
                        "value": item[metric],
                    }
                )
    keys = ["model"] if scope == "overall" else ["position", "model"]
    return _side_by_side(rows, keys)


def _read_markdown_table(path: Path, heading: str) -> pd.DataFrame:
    lines = path.read_text(encoding="utf-8").splitlines()
    start = lines.index(heading)
    table_lines = []
    for line in lines[start + 1 :]:
        if line.startswith("|"):
            table_lines.append(line)
        elif table_lines:
            break
    if len(table_lines) < 3:
        raise ValueError(f"No markdown table found under {heading!r} in {path}")
    headers = [cell.strip() for cell in table_lines[0].strip("|").split("|")]
    rows = []
    for line in table_lines[2:]:
        values = [cell.strip() for cell in line.strip("|").split("|")]
        rows.append(dict(zip(headers, values)))
    frame = pd.DataFrame(rows)
    for col in frame.columns:
        if col != "mc_version":
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame


def _top20_rows(summaries: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for season, summary in summaries.items():
        summary = summary.copy()
        summary["top20_p_haul_hit_rate"] = pd.to_numeric(
            summary["top20_p_haul_mean_hits"], errors="coerce"
        ) / 20.0
        for _, item in summary.iterrows():
            for metric in [
                "gws",
                "top20_p_haul_mean_hits",
                "top20_p_haul_hit_rate",
                "top20_mc_mean_mean_hits",
                "random_expected_mean_hits",
                "pct_gws_0_hits",
                "pct_gws_1_2_hits",
                "pct_gws_3_5_hits",
                "pct_gws_5plus_hits",
            ]:
                rows.append(
                    {
                        "season": season,
                        "mc_version": item["mc_version"],
                        "metric": metric,
                        "value": item[metric],
                    }
                )
    return _side_by_side(rows, ["mc_version"])


def _zero_rows(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for season, frame in frames.items():
        zero = frame.loc[
            (pd.to_numeric(frame["actual_minutes"], errors="coerce").fillna(0.0) == 0.0)
            & (pd.to_numeric(frame["actual_points"], errors="coerce").fillna(0.0) == 0.0)
        ].copy()
        for model, pred_col in MODEL_COLUMNS.items():
            predicted = pd.to_numeric(zero[pred_col], errors="coerce").dropna()
            values = {
                "zero_minute_rows": int(len(predicted)),
                "mean_predicted": float(predicted.mean()) if not predicted.empty else np.nan,
                "median_predicted": float(predicted.median()) if not predicted.empty else np.nan,
                "p90_predicted": float(predicted.quantile(0.90)) if not predicted.empty else np.nan,
                "rows_above_2_0": int((predicted > 2.0).sum()),
                "share_above_2_0": float((predicted > 2.0).mean()) if not predicted.empty else np.nan,
            }
            for metric, value in values.items():
                rows.append({"season": season, "model": model, "metric": metric, "value": value})
    return _side_by_side(rows, ["model"])


def _scoring_audit(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows = []
    for season, frame in frames.items():
        config = get_season_scoring_config(season)
        defcon = pd.to_numeric(frame["DefconPts"], errors="coerce").fillna(0.0)
        rows.append(
            {
                "season": season,
                "rows": int(len(frame)),
                "scoring_gk_goal_points": ",".join(sorted(frame["scoring_gk_goal_points"].astype(str).unique())),
                "scoring_defcon_active": ",".join(sorted(frame["scoring_defcon_active"].astype(str).unique())),
                "scoring_assist_rules_version": ",".join(
                    sorted(
                        frame.get(
                            "scoring_assist_rules_version",
                            pd.Series(config["assist_rules_version"], index=frame.index),
                        )
                        .astype(str)
                        .unique()
                    )
                ),
                "scoring_bps_version": ",".join(sorted(frame["scoring_bps_version"].astype(str).unique())),
                "defcon_nonzero_rows": int((defcon > 0).sum()),
                "defcon_nonzero_share": float((defcon > 0).mean()),
                "defcon_points_sum": float(defcon.sum()),
            }
        )
    return pd.DataFrame(rows)


def _value(table: pd.DataFrame, **filters: str) -> float:
    scoped = table.copy()
    for key, value in filters.items():
        scoped = scoped.loc[scoped[key] == value]
    return float(scoped.iloc[0]["2025-26"])


def write_report(
    path: Path,
    frames: dict[str, pd.DataFrame],
    top20_summaries: dict[str, pd.DataFrame],
) -> None:
    headline = _metric_rows(frames, "overall")
    by_position = _metric_rows(frames, "position")
    top20 = _top20_rows(top20_summaries)
    zeros = _zero_rows(frames)
    scoring = _scoring_audit(frames)

    ml_zero = zeros.loc[zeros["model"] == "KFT ML ensemble"].set_index("metric")
    zero_mean_2024 = float(ml_zero.loc["mean_predicted", "2024-25"])
    zero_mean_2025 = float(ml_zero.loc["mean_predicted", "2025-26"])
    zero_share_2024 = float(ml_zero.loc["share_above_2_0", "2024-25"])
    zero_share_2025 = float(ml_zero.loc["share_above_2_0", "2025-26"])
    mean_ratio = zero_mean_2025 / zero_mean_2024 if zero_mean_2024 else np.nan
    if mean_ratio >= 1.10:
        zero_size = "larger"
    elif mean_ratio <= 0.90:
        zero_size = "smaller"
    else:
        zero_size = "the same broad size"

    ml_spearman = headline.loc[
        (headline["model"] == "KFT ML ensemble") & (headline["metric"] == "spearman_mean")
    ].iloc[0]
    spearman_2024 = float(ml_spearman["2024-25"])
    spearman_2025 = float(ml_spearman["2025-26"])
    distance_051 = abs(spearman_2025 - 0.51)
    distance_070 = abs(spearman_2025 - 0.70)
    closer = "0.51 / the 2024-25 level" if distance_051 < distance_070 else "0.70 / the training-era level"
    drift = (
        "This supports a repeatable unseen-season drift problem rather than a failure unique to 2024-25."
        if distance_051 < distance_070
        else "This points more toward a 2024-25-specific failure than persistent unseen-season drift."
    )

    lines = [
        "# 2025-26 Retrospective Replay Findings",
        "",
        "## Scoring Audit",
        _markdown_table(scoring),
        "",
        "The replay uses the existing current-rule assist and BPS proxies without changing live model logic. "
        "The audit labels identify the applicable season rules; official FPL `actual_points` remains the ground truth.",
        "",
        "## Headline Metrics",
        _markdown_table(headline),
        "",
        "## Metrics By Position",
        _markdown_table(by_position),
        "",
        "## P_haul Top-20 Hit Rate",
        _markdown_table(top20),
        "",
        "## Zero-Minute Failure",
        _markdown_table(zeros),
        "",
        "The zeros population is `actual_minutes == 0` and `actual_points == 0`, matching the prior OpenFPL-style diagnosis.",
        "",
        "## Direct Answers",
        "",
        f"1. **Is the zeros failure the same size in 2025-26?** It is **{zero_size}** for the KFT ML ensemble. "
        f"Mean predicted points on zero-minute rows moved from {zero_mean_2024:.3f} to {zero_mean_2025:.3f} "
        f"({mean_ratio:.3f}x), while the share above 2.0 moved from {zero_share_2024:.1%} to {zero_share_2025:.1%}.",
        "",
        f"2. **Is 2025-26 Spearman closer to 0.51 or 0.70?** KFT ML ensemble Spearman is {spearman_2025:.3f}, "
        f"versus {spearman_2024:.3f} in 2024-25. It is closer to **{closer}** "
        f"(distance to 0.51 = {distance_051:.3f}; distance to 0.70 = {distance_070:.3f}).",
        "",
        f"3. **Drift diagnosis.** {drift}",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare 2024-25 and 2025-26 retrospective replay outputs.")
    parser.add_argument(
        "--baseline",
        default="outputs/validation/retrospective_2024_season_rules/retrospective_replay_predictions.csv",
    )
    parser.add_argument(
        "--current",
        default="outputs/validation/retrospective_2025_26/retrospective_replay_predictions.csv",
    )
    parser.add_argument("--out", default="outputs/validation/replay_2025_26_findings.md")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    frames = {
        "2024-25": _load_predictions(ROOT / args.baseline, "2024-25"),
        "2025-26": _load_predictions(ROOT / args.current, "2025-26"),
    }
    top20_summaries = {
        "2024-25": _read_markdown_table(
            (ROOT / args.baseline).parent / "retrospective_replay_findings.md",
            "## P_haul Top-20 Hit Rate",
        ),
        "2025-26": _read_markdown_table(
            (ROOT / args.current).parent / "retrospective_replay_findings.md",
            "## P_haul Top-20 Hit Rate",
        ),
    }
    out = ROOT / args.out
    write_report(out, frames, top20_summaries)
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
