from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fpl_xpts.historical_validation import (  # noqa: E402
    build_inventory,
    build_training_dataset,
    compare_validation_tables,
    download_odds_files,
    download_understat_stats,
    download_vaastav_files,
    gk_component_breakdown,
    markdown_table,
    score_training_dataset,
    sweep_form_weight,
    update_config_form_weight,
    validation_tables,
    write_findings_report,
)


def _print_dataframe(title: str, frame: pd.DataFrame) -> None:
    print(f"\n{title}")
    print(markdown_table(frame))


def main() -> None:
    parser = argparse.ArgumentParser(description="Build full historical KFT validation data and reports.")
    parser.add_argument("--refresh", action="store_true", help="Refetch files even if cached")
    parser.add_argument("--update-config", action="store_true", help="Update AppConfig.form_blend_weight from the sweep")
    args = parser.parse_args()

    print("Step 1: Download historical football-data.co.uk odds")
    odds_rows = download_odds_files(ROOT, refresh=args.refresh)
    for row in odds_rows:
        print(f"- {row['season']}: rows={row['rows']} columns={', '.join(row['columns'])}")

    print("\nStep 2: Download Understat team data 2014-15 through 2025-26")
    team_rows, player_rows = download_understat_stats(ROOT, refresh=args.refresh)
    for row in team_rows:
        print(f"- {row['year']}: teams={row['teams']} fields={', '.join(row['fields'])}")

    print("\nStep 3: Download Understat player data 2014-15 through 2025-26")
    for row in player_rows:
        print(f"- {row['year']}: players={row['players']} fields={', '.join(row['fields'])}")

    print("\nStep 4: Download Vaastav historical GW data")
    vaastav_rows = download_vaastav_files(ROOT, refresh=args.refresh)
    for row in vaastav_rows:
        print(f"- {row['season']}: rows={row['rows']}")

    print("\nStep 5: Inspect everything and print a data inventory")
    inventory = build_inventory(ROOT)
    _print_dataframe("Odds inventory", inventory["odds"])
    _print_dataframe("Understat team inventory", inventory["understat_team"])
    _print_dataframe("Understat player inventory", inventory["understat_player"])
    _print_dataframe("Vaastav inventory", inventory["vaastav"])

    print("\nStep 6: Build the joined training dataset")
    result = build_training_dataset(ROOT)
    dataset = result.dataset
    fixtures = result.fixtures
    date_series = pd.to_datetime(dataset["date"], errors="coerce")
    dataset_status = {
        "rows": int(len(dataset)),
        "date_min": str(date_series.min().date()) if date_series.notna().any() else "",
        "date_max": str(date_series.max().date()) if date_series.notna().any() else "",
        "complete_rows": int(dataset["complete_features"].sum()),
        "incomplete_rows": int((~dataset["complete_features"]).sum()),
    }
    dataset_status["complete_share"] = dataset_status["complete_rows"] / max(dataset_status["rows"], 1)
    print(
        "full_training_dataset.csv "
        f"rows={dataset_status['rows']} "
        f"date_range={dataset_status['date_min']}..{dataset_status['date_max']} "
        f"complete={dataset_status['complete_rows']} incomplete={dataset_status['incomplete_rows']} "
        f"complete_share={dataset_status['complete_share']:.2%}"
    )
    failure_path = ROOT / "outputs" / "validation" / "feature_failure_reasons.csv"
    failure_summary = pd.read_csv(failure_path) if failure_path.exists() else pd.DataFrame()
    if dataset_status["complete_share"] < 0.60:
        _print_dataframe("Top 20 feature failure reasons", failure_summary.head(20))

    print("\nStep 7: Run the production formula against the full training dataset")
    predictions = score_training_dataset(dataset, fixtures)
    if predictions.empty:
        raise RuntimeError("No historical KFT predictions were generated.")
    predictions_path = ROOT / "outputs" / "validation" / "full_historical_predictions.csv"
    predictions_path.parent.mkdir(parents=True, exist_ok=True)
    predictions.to_csv(predictions_path, index=False, float_format="%.6f")
    validation = validation_tables(predictions)
    validation_path = ROOT / "outputs" / "validation" / "full_historical_validation.csv"
    previous_validation = pd.read_csv(validation_path) if validation_path.exists() else pd.DataFrame()
    validation.to_csv(validation_path, index=False, float_format="%.6f")
    comparison = compare_validation_tables(previous_validation, validation)
    comparison_path = ROOT / "outputs" / "validation" / "validation_comparison.csv"
    comparison.to_csv(comparison_path, index=False, float_format="%.6f")
    gk_breakdown = gk_component_breakdown(predictions)
    gk_path = ROOT / "outputs" / "validation" / "gk_component_breakdown.csv"
    gk_breakdown.to_csv(gk_path, index=False, float_format="%.6f")
    _print_dataframe("Headline validation metrics", validation.loc[validation["scope"] == "overall"])
    _print_dataframe("Validation by season", validation.loc[validation["scope"] == "season"])
    _print_dataframe("Validation by position", validation.loc[validation["scope"] == "position"])
    _print_dataframe("Validation by points bracket", validation.loc[validation["scope"] == "points_bracket"])
    _print_dataframe("GK average predicted vs actual and components", gk_breakdown)
    _print_dataframe("Comparison to previous run", comparison.loc[comparison["scope"].isin(["overall", "position"])] if not comparison.empty else comparison)

    print("\nStep 8: Find the best form weights from data")
    sweep = sweep_form_weight(dataset, fixtures)
    sweep_path = ROOT / "outputs" / "validation" / "form_weight_sweep.csv"
    sweep.to_csv(sweep_path, index=False, float_format="%.6f")
    best = sweep.iloc[0]
    changed = update_config_form_weight(ROOT / "src" / "fpl_xpts" / "config.py", float(best["form_blend_weight"])) if args.update_config else False
    print(
        f"best form_blend_weight={float(best['form_blend_weight']):.1f} "
        f"spearman={float(best['spearman']):.6f} "
        f"mae={float(best['mae']):.6f} "
        f"config_updated={changed}"
    )

    print("\nStep 9: Write a findings report")
    report_path = ROOT / "outputs" / "validation" / "historical_findings.md"
    write_findings_report(
        report_path,
        result.inventory,
        validation,
        sweep,
        dataset_status,
        gk_breakdown=gk_breakdown,
        failure_summary=failure_summary,
        comparison=comparison,
    )
    print(f"Wrote {report_path}")

    print("\nDone")
    print(f"- {ROOT / 'data' / 'modelling' / 'full_training_dataset.csv'}")
    print(f"- {validation_path}")
    print(f"- {sweep_path}")
    print(f"- {predictions_path}")
    print(f"- {comparison_path}")
    print(f"- {gk_path}")
    print(f"- {failure_path}")
    print(f"- {ROOT / 'outputs' / 'validation' / 'unmatched_players.csv'}")
    print(f"- {report_path}")


if __name__ == "__main__":
    main()
