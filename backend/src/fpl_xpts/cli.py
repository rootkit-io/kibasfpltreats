from __future__ import annotations

import argparse
from pathlib import Path

from .config import AppConfig
from .backtest import write_backtest_outputs
from .legacy_export import write_legacy_outputs
from .pipeline import run_live_projection
from .solio_compare import write_solio_comparison


def main() -> None:
    parser = argparse.ArgumentParser(description="Run live FPL xPts projections.")
    parser.add_argument("--out", default="outputs/live", help="Output directory")
    parser.add_argument("--n-sim", type=int, default=10_000, help="Monte Carlo simulations")
    parser.add_argument("--no-mc", action="store_true", help="Skip Monte Carlo")
    parser.add_argument("--format", choices=["legacy", "raw"], default="legacy", help="CSV output format")
    parser.add_argument("--backtest", action="store_true", help="Write Vaastav holdout backtest CSVs and exit")
    parser.add_argument("--compare-solio", action="store_true", help="Write current Solio comparison CSV and exit")
    parser.add_argument("--no-market-odds", action="store_true", help="Disable market-odds team-goal override")
    parser.add_argument("--odds-api-key-env", default="ODDS_API_KEY", help="Environment variable containing The Odds API key")
    parser.add_argument("--odds-regions", default="uk,eu,us", help="Comma-separated odds regions for The Odds API")
    parser.add_argument("--odds-bookmakers", default=None, help="Optional comma-separated bookmaker keys for The Odds API")
    parser.add_argument("--start-gw", type=int, default=37, help="First gameweek to project")
    parser.add_argument("--end-gw", type=int, default=38, help="Last gameweek to project")
    parser.add_argument("--no-elevenify", action="store_true", help="Do not use the local Elevenify season-long CSV")
    parser.add_argument("--elevenify-projections", default=None, help="Path to Elevenify season-long projected goals/CS CSV")
    parser.add_argument("--train-seasons", default="2022-23,2023-24", help="Comma-separated Vaastav training seasons")
    parser.add_argument("--test-seasons", default="2024-25", help="Comma-separated Vaastav test seasons")
    parser.add_argument("--no-understat", action="store_true", help="Do not blend Understat shot/chance profiles")
    parser.add_argument("--no-player-history", action="store_true", help="Skip FPL element-summary history for recent minutes")
    parser.add_argument("--no-minutes-inputs", action="store_true", help="Do not read/write player_minutes_inputs.csv")
    parser.add_argument("--minutes-input", default="player_minutes_inputs.csv", help="Editable player start/minutes CSV")
    parser.add_argument(
        "--manual-minutes",
        action="append",
        default=None,
        metavar="CSV",
        help="Manual minutes CSV for this run (repeatable; later files win). "
        "Omit to fall back to the legacy defaults.",
    )
    parser.add_argument(
        "--minute-overrides",
        action="append",
        default=None,
        metavar="CSV",
        help="Hard minute-override CSV for this run (repeatable). "
        "Omit to fall back to legacy minute_overrides.csv auto-discovery.",
    )
    parser.add_argument("--overwrite-minutes-inputs", action="store_true", help="Overwrite the editable minutes CSV with API defaults")
    parser.add_argument("--no-big-chances", action="store_true", help="Skip Understat match-detail big chance proxy fetch")
    parser.add_argument("--understat-season", type=int, default=None, help="Understat season start year, e.g. 2025")
    parser.add_argument("--use-external-team-files", action="store_true", help="Use local manual team xG/xA/CS projection CSVs if present")
    parser.add_argument("--attack-projections", default=None, help="Path to manual attack projection CSV")
    parser.add_argument("--defense-projections", default=None, help="Path to manual clean-sheet projection CSV")
    parser.add_argument("--use-ml-predictions", action="store_true", help="Attach optional trained ML xPts predictions")
    parser.add_argument("--ml-model-dir", default="models/position_models", help="Directory containing position ML model bundles")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    config = AppConfig(
        n_sim=args.n_sim,
        use_market_odds=not args.no_market_odds,
        odds_api_key_env=args.odds_api_key_env,
        odds_api_regions=args.odds_regions,
        odds_api_bookmakers=args.odds_bookmakers,
        projection_start_gw=args.start_gw,
        projection_end_gw=args.end_gw,
        use_elevenify_projection_file=not args.no_elevenify,
        elevenify_projection_path=Path(args.elevenify_projections) if args.elevenify_projections else None,
        use_fpl_player_history=not args.no_player_history,
        use_player_minutes_input_file=not args.no_minutes_inputs,
        player_minutes_input_path=Path(args.minutes_input),
        write_player_minutes_input_template=not args.no_minutes_inputs,
        overwrite_player_minutes_input_template=args.overwrite_minutes_inputs,
        use_understat_profiles=not args.no_understat,
        include_big_chance_profiles=not args.no_big_chances,
        understat_season=args.understat_season,
        use_external_team_projection_files=args.use_external_team_files,
        external_attack_projection_path=Path(args.attack_projections) if args.attack_projections else None,
        external_defense_projection_path=Path(args.defense_projections) if args.defense_projections else None,
        use_ml_predictions=args.use_ml_predictions,
        ml_model_dir=Path(args.ml_model_dir),
    )
    manual_minutes_paths = [Path(p) for p in args.manual_minutes] if args.manual_minutes else None
    minute_override_paths = [Path(p) for p in args.minute_overrides] if args.minute_overrides else None

    if args.backtest:
        train = [s.strip() for s in args.train_seasons.split(",") if s.strip()]
        test = [s.strip() for s in args.test_seasons.split(",") if s.strip()]
        paths = write_backtest_outputs(out, train, test)
        print(f"Wrote {len(paths)} backtest CSVs to {out}")
        return

    if args.compare_solio:
        path = write_solio_comparison(
            out,
            config,
            manual_minutes_paths=manual_minutes_paths,
            minute_override_paths=minute_override_paths,
        )
        print(f"Wrote Solio comparison to {path}")
        return

    if args.format == "legacy":
        paths = write_legacy_outputs(
            out,
            config,
            manual_minutes_paths=manual_minutes_paths,
            minute_override_paths=minute_override_paths,
        )
        print(f"Wrote {len(paths)} legacy CSVs to {out}")
        return

    results = run_live_projection(
        config=config,
        include_mc=not args.no_mc,
        manual_minutes_paths=manual_minutes_paths,
        minute_override_paths=minute_override_paths,
    )

    for name, df in results.items():
        if hasattr(df, "to_csv"):
            df.to_csv(out / f"{name}.csv", index=False)
    print(f"Wrote {len(results)} tables to {out}")


if __name__ == "__main__":
    main()
