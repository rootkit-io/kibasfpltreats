# FPL xPts Rebuild

This repo is being rebuilt from notebook/manual-input workflows into a live FPL projection system that still exports the old CSV filenames.

## Goal

Create a local-first model workflow that:

- pulls current FPL player, team, fixture, injury, price, ownership, expected-stat, and gameweek data automatically;
- stores historical snapshots and actual results for backtesting;
- fetches current FPL data automatically and ignores the old manual attack/defense spreadsheets unless explicitly opted in;
- blends FPL current expected stats with Understat player shot/chance profiles;
- estimates player minutes, team goal expectations, clean-sheet probabilities, attacking returns, defensive contribution points, cards, saves, penalties, and bonus;
- runs a fixture-level Monte Carlo simulation with shot-volume, xG-per-shot, chance-volume, xA-per-chance, clean sheets, saves, cards, defcon, and bonus;
- writes simple weekly CSV outputs for picks, captaincy, risk brackets, fixture audits, and model validation.

## Data Source Strategy

FPL is the canonical live source because it has stable player IDs and official scoring fields.

Optional enrichments sit around it:

- Understat for player xG/xA, shots, key passes, xG/shot, xA/chance, and a high-xG-shot big-chance proxy.
- vaastav/Fantasy-Premier-League for historical FPL CSVs and backtests.
- Odds APIs or Football-Data.co.uk for market-implied team strength.
- Manual overrides only for things that cannot be inferred reliably, such as late injury interpretation or expected minutes edge cases.

## New Pipeline Shape

```text
fetch live data -> normalize IDs -> build feature tables -> forecast fixtures
              -> calculate xPts -> simulate Monte Carlo -> backtest/audit -> CSVs
```

The rebuild starts in `src/fpl_xpts/`. The two notebook filenames remain as runners:

- `KFT_xPts_FORM_ZERO_MINS_PATCHED.ipynb` writes the point/xG/xA CSVs.
- `Kiba_Bracket_MonteCarlo_v6_upgraded_(3).ipynb` writes the MC bracket CSVs.

For Google Colab, upload/open the repo folder, then run those two notebooks from the repo root. They fetch live FPL data, recent player history, Understat profiles, and market odds when `ODDS_API_KEY` is set, then write the same legacy CSV filenames and column order under `outputs/legacy_live/`.

Market odds are optional but recommended. In Colab, set `ODDS_API_KEY` in the first notebook cell if you want market-implied team goals from The Odds API. Without a key, the model still runs using the live FPL-strength fallback.

## Commands

Write the legacy-compatible CSV set:

```powershell
& 'C:\Users\dvard\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m fpl_xpts.cli --out outputs\legacy_live --n-sim 10000
```

The old manual team projection CSVs are now opt-in:

```powershell
& 'C:\Users\dvard\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m fpl_xpts.cli --out outputs\legacy_live --n-sim 10000 --use-external-team-files
```

Run a Vaastav holdout backtest:

```powershell
& 'C:\Users\dvard\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m fpl_xpts.cli --backtest --out outputs\backtest_vaastav --train-seasons 2022-23,2023-24 --test-seasons 2024-25
```

Compare current live projections against Solio's latest public projection feed:

```powershell
& 'C:\Users\dvard\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -B -m fpl_xpts.cli --compare-solio --out outputs\external_comparison --n-sim 100
```
