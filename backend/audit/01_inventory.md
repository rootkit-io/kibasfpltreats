# KFT Model Audit - Phase 1 Inventory

Audit target: `C:\Users\iamdi\Desktop\modelyansh`.

Scope note: the requested `C:\Users\dvard\OneDrive\Desktop\KFT_2.1` path was not present in this environment. After user clarification, this audit treats `modelyansh` as the target. This folder is the Python/model-generation workspace, not the vanilla HTML/Netlify site: recursive search found no `.html` files and no `netlify/` directory here.

Git note: this target folder has no `.git` directory, and `git.exe` is not available on PATH in this environment. I therefore could not verify checked-in status or create the requested phase commit from this folder.

## 1.1 File-Level Inventory

Recursive file count: 505 files.

By type:

| Type | Count | Notes |
|---|---:|---|
| Python source | 24 | Streamlit app, CLI, model engine, scripts, tests. |
| CSV | 92 | Manual inputs, generated projection outputs, backtests, external comparisons, Vaastav caches. |
| JSON | 350 | 1 Understat league cache plus 349 Understat match-detail caches. |
| Notebook | 2 | Legacy-compatible Colab runners. |
| Markdown | 3 | README, model blueprint, pytest cache README. |
| TOML | 1 | Python project metadata. |
| ZIP | 1 | Packed legacy output CSV bundle. |
| Python bytecode/cache metadata | 32 | `__pycache__` and `.pytest_cache` files. |

### Pages

No HTML pages exist in `modelyansh`. The only user-facing app surface in this folder is Streamlit:

| Path | Type | Purpose |
|---|---|---|
| `app/streamlit_app.py` | Python / Streamlit | Interactive app that calls `run_live_projection(AppConfig(...))`, displays weekly picks, Monte Carlo, fixtures, and audit counts. See `app/streamlit_app.py:5-24` and `app/streamlit_app.py:26-49`. |

### Components / Scripts

| Path | Type | Purpose |
|---|---|---|
| `src/fpl_xpts/__init__.py` | Python | Package marker and short package description. |
| `src/fpl_xpts/config.py` | Python | Defines `AppConfig`, including FPL base URL, simulation count, GW range, odds settings, Elevenify/manual input flags, minutes input path, and Understat cache settings (`src/fpl_xpts/config.py:8-39`). |
| `src/fpl_xpts/data_sources.py` | Python | FPL API client and bootstrap table normalization for `bootstrap-static/`, `fixtures/`, `element-summary/{id}/`, and `event/{event}/live/` (`src/fpl_xpts/data_sources.py:20-80`). |
| `src/fpl_xpts/features.py` | Python | Builds recent player form, team strength, assist factors, and player rates used by the projection engine. |
| `src/fpl_xpts/forecast.py` | Python | Forecasts fixture goal lambdas from FPL team strength fallback. |
| `src/fpl_xpts/market_odds.py` | Python | Optional The Odds API integration; fetches EPL odds if `ODDS_API_KEY` is present and otherwise keeps fallback lambdas (`src/fpl_xpts/market_odds.py:19-20`, `src/fpl_xpts/market_odds.py:146-162`, `src/fpl_xpts/market_odds.py:266-292`). |
| `src/fpl_xpts/minute_overrides.py` | Python | Finds, writes, loads, and applies `player_minutes_inputs.csv` and `minute_overrides.csv` (`src/fpl_xpts/minute_overrides.py:36-55`, `src/fpl_xpts/minute_overrides.py:116-140`, `src/fpl_xpts/minute_overrides.py:217-249`). |
| `src/fpl_xpts/minutes.py` | Python | Expected minutes helper logic. |
| `src/fpl_xpts/monte_carlo.py` | Python | Fixture/player-week Monte Carlo simulation engine. |
| `src/fpl_xpts/pipeline.py` | Python | Main live projection orchestration: FPL fetch, Understat attach, player history, team strength, fixture forecasts, odds/Elevenify/manual overrides, minutes, deterministic xPts, weekly aggregation, and optional Monte Carlo (`src/fpl_xpts/pipeline.py:17-128`). |
| `src/fpl_xpts/projections.py` | Python | Finds and parses Elevenify and old attack/defense projection CSVs; applies them to fixture forecasts (`src/fpl_xpts/projections.py:69-78`, `src/fpl_xpts/projections.py:142-190`, `src/fpl_xpts/projections.py:257-285`). |
| `src/fpl_xpts/scoring.py` | Python | FPL scoring constants/helpers. |
| `src/fpl_xpts/bonus.py` | Python | Bonus-point helper logic. |
| `src/fpl_xpts/shot_profiles.py` | Python | Understat cache/API integration and shot-profile builder (`src/fpl_xpts/shot_profiles.py:19-25`, `src/fpl_xpts/shot_profiles.py:83-96`, `src/fpl_xpts/shot_profiles.py:168-173`). |
| `src/fpl_xpts/xpts.py` | Python | Deterministic player-fixture xPts and gameweek aggregation logic. |
| `src/fpl_xpts/backtest.py` | Python | Vaastav historical loader, holdout backtest models, metrics, calibration tables, and backtest CSV writer (`src/fpl_xpts/backtest.py:15-68`, `src/fpl_xpts/backtest.py:586-720`). |
| `src/fpl_xpts/legacy_export.py` | Python | Legacy output schemas and CSV writers for `fixture_player_week.csv`, `weekly_player_week.csv`, top-50 tables, QC tables, audit tables, and MC bracket files (`src/fpl_xpts/legacy_export.py:16-61`, `src/fpl_xpts/legacy_export.py:309-345`). |
| `src/fpl_xpts/solio_compare.py` | Python | Fetches Solio latest feed and compares it against generated `weekly_player_week.csv` (`src/fpl_xpts/solio_compare.py:15-18`, `src/fpl_xpts/solio_compare.py:107-143`). |
| `src/fpl_xpts/cli.py` | Python | CLI entry point for live CSV generation, raw output generation, Vaastav backtests, and Solio comparison (`src/fpl_xpts/cli.py:13-89`). |
| `scripts/build_notebooks.py` | Python | Generates the two legacy Colab notebook runners and writes notebook files (`scripts/build_notebooks.py:49-145`, `scripts/build_notebooks.py:155-240`, `scripts/build_notebooks.py:251-257`). |
| `tests/test_minutes.py` | Python test | Tests minutes logic. |
| `tests/test_model_contracts.py` | Python test | Tests Monte Carlo bracket probabilities and Elevenify sheet parser contract. |
| `tests/test_scoring.py` | Python test | Tests scoring helpers. |

### Data Files

| Path / pattern | Type | Purpose |
|---|---|---|
| `elevenify.com 25_26 Subscriber Season Long Data - Sheet1.csv` | CSV | Manual/third-party season-long projected goals and clean-sheet sheet. Auto-detected by filename when Elevenify projections are enabled (`src/fpl_xpts/projections.py:76-78`, `src/fpl_xpts/projections.py:142-153`). |
| `league-players - attack_projections (2).csv` | CSV | Old manual team xG/xA input file. Only used if external team files are opted in (`src/fpl_xpts/projections.py:69-73`, `src/fpl_xpts/projections.py:257-285`). |
| `league-players - defense-projections (2).csv` | CSV | Old manual clean-sheet probability input file. Only used if external team files are opted in (`src/fpl_xpts/projections.py:69-73`, `src/fpl_xpts/projections.py:257-285`). |
| `league-players - league_teams.csv (2).csv` | CSV | Old/manual team stats file; not directly referenced by current code search. |
| `Untitled spreadsheet - league_players (1).csv` | CSV | Old/manual player rate sheet; not directly referenced by current code search. |
| `Untitled spreadsheet - player_inputs (1).csv` | CSV | Old/manual player input sheet; not directly referenced by current code search. |
| `player_minutes_inputs.csv` | CSV | Editable expected-minutes input/template. Default config points here (`src/fpl_xpts/config.py:28-31`) and the pipeline can write/read it (`src/fpl_xpts/pipeline.py:95-115`). |
| `player_minutes_inputs_gw37_to_38.csv` | CSV | GW37-GW38-specific editable minutes file selected by generated notebooks when present (`scripts/build_notebooks.py:101-117`, `scripts/build_notebooks.py:199-215`). |
| `minute_overrides.csv` | CSV | Sparse exact minute override file, found by `find_minutes_override_file` (`src/fpl_xpts/minute_overrides.py:36-45`). |
| `mc_brackets_fixture_player_week.csv` | CSV | Root copy of MC fixture-player-week legacy output. Generated schema is in `legacy_export.py` (`src/fpl_xpts/legacy_export.py:46-54`, `src/fpl_xpts/legacy_export.py:331-343`). |
| `mc_brackets_full_player_week.csv` | CSV | Root copy of MC player-gameweek legacy output. Generated schema is in `legacy_export.py` (`src/fpl_xpts/legacy_export.py:56-61`, `src/fpl_xpts/legacy_export.py:331-343`). |
| `mc_brackets_top50_by_week.csv` | CSV | Root copy of top-50 MC player-gameweek legacy output. |
| `kiba_outputs.zip` | ZIP | Archive containing legacy point-projection CSVs: `fixture_player_week.csv`, `weekly_player_week.csv`, `six_week_totals.csv`, top-50 tables, QC tables, and `form_weighting_audit.csv`. |
| `data/understat/league_EPL_2025.json` | JSON | Cached Understat EPL 2025 league data. Contains 20 teams, 530 players, and 380 fixture-date rows in the inspected file. |
| `data/understat/matches/28778.json` through `data/understat/matches/29127.json`, except missing `29084.json` | JSON | 349 cached Understat match-detail files. Each inspected match JSON has `rosters`, `shots`, and `tmpl` keys. |
| `data/vaastav/2022-23_merged_gw.csv` | CSV | Cached Vaastav merged gameweek history, used by backtest loader (`src/fpl_xpts/backtest.py:55-68`). |
| `data/vaastav/2023-24_merged_gw.csv` | CSV | Cached Vaastav merged gameweek history, used by backtest loader (`src/fpl_xpts/backtest.py:55-68`). |
| `data/vaastav/2024-25_merged_gw.csv` | CSV | Cached Vaastav merged gameweek history, used by backtest loader (`src/fpl_xpts/backtest.py:55-68`). |
| `outputs/legacy_live/*.csv` | CSV | Main legacy-compatible projection export set written by `write_legacy_outputs` (`src/fpl_xpts/legacy_export.py:309-345`). |
| `outputs/legacy_live_smoke/*.csv` | CSV | Larger smoke-run legacy-compatible export set, including pre-bonus copies. |
| `outputs/live_smoke/*.csv` | CSV | Raw table output from the live pipeline, matching `run_live_projection` return keys (`src/fpl_xpts/pipeline.py:119-128`, `src/fpl_xpts/cli.py:84-88`). |
| `outputs/minutes_template_run/*.csv` | CSV | Raw output from a minutes-template run. |
| `outputs/backtest_latest/*.csv` | CSV | Backtest metrics, predictions, coefficients, and calibration outputs from `write_backtest_outputs` (`src/fpl_xpts/backtest.py:691-720`). |
| `outputs/backtest_vaastav/*.csv` | CSV | Duplicate/named Vaastav backtest output set from the same backtest writer. |
| `outputs/external_comparison/*.csv` | CSV | Saved Solio comparison outputs, generated by `write_solio_comparison` (`src/fpl_xpts/solio_compare.py:140-143`). |

### Generators

| Path | Type | Purpose |
|---|---|---|
| `KFT_xPts_FORM_ZERO_MINS_PATCHED.ipynb` | Notebook | Legacy-compatible xPts/xG/xA runner. README says this notebook writes point/xG/xA CSVs (`README.md:35-40`). |
| `Kiba_Bracket_MonteCarlo_v6_upgraded_(3).ipynb` | Notebook | Legacy-compatible Monte Carlo bracket runner. README says this notebook writes MC bracket CSVs (`README.md:35-40`). |
| `scripts/build_notebooks.py` | Python | Source generator for both notebooks (`scripts/build_notebooks.py:251-257`). |
| `src/fpl_xpts/cli.py` | Python | Primary command-line generator for legacy/raw live outputs, backtests, and Solio comparison (`src/fpl_xpts/cli.py:67-89`). |
| `src/fpl_xpts/legacy_export.py` | Python | Writes the legacy CSV output family (`src/fpl_xpts/legacy_export.py:337-345`). |
| `src/fpl_xpts/backtest.py` | Python | Writes the backtest CSV output family (`src/fpl_xpts/backtest.py:710-720`). |

### Infrastructure

| Path / pattern | Type | Purpose |
|---|---|---|
| `pyproject.toml` | TOML | Python package metadata. Declares package name `fpl-xpts`, dependencies `duckdb`, `numpy`, `pandas`, `pydantic`, `streamlit`, dev dependency `pytest`, and script entry point `fpl-xpts = fpl_xpts.cli:main` (`pyproject.toml:1-21`). |
| `src/fpl_xpts.egg-info/*` | Package metadata | Installed/editable package metadata: dependency links, entry points, PKG-INFO, requirements, sources, top-level package. |
| `.pytest_cache/*` | Test cache | Pytest run cache and metadata. |
| `src/fpl_xpts/__pycache__/*.pyc` | Bytecode cache | Python bytecode cache for package modules. |
| `tests/__pycache__/*.pyc` | Bytecode cache | Python bytecode cache for tests. |

### Content / Copy

| Path | Type | Purpose |
|---|---|---|
| `README.md` | Markdown | Project goal, data source strategy, pipeline shape, and command examples. |
| `docs/model_blueprint.md` | Markdown | Methodology/blueprint document describing intended inputs, feature tables, xPts engine, MC engine, and validation. |

## 1.2 Data Flow Map

### High-Level Flow Actually Present In This Folder

1. `src/fpl_xpts.cli` builds an `AppConfig` from CLI args, then either writes backtest outputs, Solio comparison, legacy CSVs, or raw pipeline tables (`src/fpl_xpts/cli.py:13-89`).
2. `run_live_projection` fetches FPL bootstrap and fixtures, optionally builds/attaches Understat profiles, fetches FPL element-summary history, builds team strength and fixture forecasts, optionally applies market odds, optionally applies Elevenify projections, optionally applies old external attack/defense projection files, writes/reads minutes input files, recomputes components, aggregates weekly projections, and optionally runs Monte Carlo (`src/fpl_xpts/pipeline.py:17-128`).
3. `write_legacy_outputs` converts the live in-memory tables into legacy CSV names including `fixture_player_week.csv`, `weekly_player_week.csv`, top-50 tables, QC tables, audit tables, and MC bracket tables (`src/fpl_xpts/legacy_export.py:309-345`).
4. The generated notebooks are wrappers around the same package functions. The xPts notebook writes point projection CSVs (`scripts/build_notebooks.py:121-142`); the MC notebook writes MC bracket CSVs (`scripts/build_notebooks.py:219-239`).
5. The Streamlit app does not load saved CSVs. It calls `run_live_projection` directly and displays in-memory tables (`app/streamlit_app.py:19-49`).

### Projection-Carrying CSVs

These are the CSVs most directly carrying model inputs or outputs.

| File | Last modified | Rows | Columns | Loaded by / generated by | Git status |
|---|---:|---:|---:|---|---|
| `outputs/legacy_live/weekly_player_week.csv` | 2026-05-13 08:19:14 | 1,676 | 23 | Generated by `legacy_export.py` and notebook wrapper (`src/fpl_xpts/legacy_export.py:321-343`, `scripts/build_notebooks.py:128-140`). | Cannot verify: no `.git`. |
| `outputs/legacy_live/fixture_player_week.csv` | 2026-05-13 08:19:14 | 1,676 | 25 | Generated by `legacy_export.py` and notebook wrapper (`src/fpl_xpts/legacy_export.py:321-343`, `scripts/build_notebooks.py:128-140`). | Cannot verify. |
| `outputs/legacy_live/mc_brackets_full_player_week.csv` | 2026-05-13 08:19:14 | 1,676 | 28 | Generated by `legacy_export.py` and MC notebook wrapper (`src/fpl_xpts/legacy_export.py:331-343`, `scripts/build_notebooks.py:231-239`). | Cannot verify. |
| `outputs/legacy_live/mc_brackets_fixture_player_week.csv` | 2026-05-13 08:19:14 | 1,676 | 47 | Generated by `legacy_export.py` and MC notebook wrapper (`src/fpl_xpts/legacy_export.py:331-343`, `scripts/build_notebooks.py:231-239`). | Cannot verify. |
| `outputs/legacy_live/mc_brackets_top50_by_week.csv` | 2026-05-13 08:19:14 | 100 | 28 | Generated top-50 slice of MC weekly output. | Cannot verify. |
| `outputs/legacy_live/six_week_totals.csv` | 2026-05-13 08:19:14 | 838 | 10 | Generated by `legacy_export.py` (`src/fpl_xpts/legacy_export.py:321-343`). | Cannot verify. |
| `outputs/legacy_live/top50_p1_ga_by_week.csv` | 2026-05-13 08:19:14 | 100 | 23 | Generated top-50 slice of weekly output sorted by `P1_GA` (`src/fpl_xpts/legacy_export.py:325`). | Cannot verify. |
| `outputs/legacy_live/top50_xga_by_week.csv` | 2026-05-13 08:19:14 | 100 | 23 | Generated top-50 slice of weekly output sorted by `xGA_exp` (`src/fpl_xpts/legacy_export.py:326`). | Cannot verify. |
| `outputs/legacy_live/qc_team_week.csv` | 2026-05-13 08:19:14 | 40 | 9 | Generated QC table (`src/fpl_xpts/legacy_export.py:327`). | Cannot verify. |
| `outputs/legacy_live/qc_team_week_fixture.csv` | 2026-05-13 08:19:14 | 40 | 10 | Generated fixture-level QC table (`src/fpl_xpts/legacy_export.py:328`). | Cannot verify. |
| `outputs/legacy_live/form_weighting_audit.csv` | 2026-05-13 08:19:14 | 838 | 8 | Generated form audit table (`src/fpl_xpts/legacy_export.py:329`). | Cannot verify. |
| `outputs/legacy_live/shot_profile_audit.csv` | 2026-05-13 08:19:14 | 530 | 21 | Generated Understat audit table (`src/fpl_xpts/legacy_export.py:330`). | Cannot verify. |
| `mc_brackets_full_player_week.csv` | 2026-05-13 08:19:13 | 3,126 | 28 | Root copy of MC full output. Exact generating/copy step not found. | Cannot verify. |
| `mc_brackets_fixture_player_week.csv` | 2026-05-13 08:19:13 | 3,126 | 47 | Root copy of MC fixture output. Exact generating/copy step not found. | Cannot verify. |
| `mc_brackets_top50_by_week.csv` | 2026-05-13 08:19:13 | 300 | 28 | Root copy of MC top-50 output. Exact generating/copy step not found. | Cannot verify. |
| `outputs/live_smoke/weekly.csv` | 2026-05-13 08:19:15 | 3,328 | 11 | Raw `weekly` table from pipeline return (`src/fpl_xpts/pipeline.py:119-128`, `src/fpl_xpts/cli.py:84-88`). | Cannot verify. |
| `outputs/live_smoke/player_fixture.csv` | 2026-05-13 08:19:15 | 3,410 | 16 | Raw `player_fixture` table from pipeline return. | Cannot verify. |
| `outputs/live_smoke/monte_carlo.csv` | 2026-05-13 08:19:15 | 3,328 | 18 | Raw `monte_carlo` table from pipeline return. | Cannot verify. |
| `player_minutes_inputs.csv` | 2026-05-13 08:19:13 | 1,676 | 13 | Editable minutes file read by `apply_player_minutes_inputs` (`src/fpl_xpts/pipeline.py:106-115`). | Cannot verify. |
| `player_minutes_inputs_gw37_to_38.csv` | 2026-05-13 08:19:13 | 1,676 | 13 | Notebook-preferred minutes input when present (`scripts/build_notebooks.py:101-117`, `scripts/build_notebooks.py:199-215`). | Cannot verify. |
| `minute_overrides.csv` | 2026-05-13 08:19:12 | 1 | 4 | Sparse minutes override file (`src/fpl_xpts/minute_overrides.py:36-45`, `src/fpl_xpts/minute_overrides.py:217-249`). | Cannot verify. |
| `elevenify.com 25_26 Subscriber Season Long Data - Sheet1.csv` | 2026-05-13 08:19:13 | 52 raw rows | 41 raw columns | Auto-detected by Elevenify parser (`src/fpl_xpts/projections.py:76-78`, `src/fpl_xpts/projections.py:142-153`). | Cannot verify. |
| `league-players - attack_projections (2).csv` | 2026-05-13 08:19:13 | 20 | 14 | Old opt-in external attack projection file (`src/fpl_xpts/projections.py:257-285`). | Cannot verify. |
| `league-players - defense-projections (2).csv` | 2026-05-13 08:19:13 | 20 | 7 | Old opt-in external clean-sheet projection file (`src/fpl_xpts/projections.py:257-285`). | Cannot verify. |

### Other CSV Data Inventory

| Family | Files | Rows / columns |
|---|---|---|
| Vaastav caches | `data/vaastav/2022-23_merged_gw.csv`, `2023-24_merged_gw.csv`, `2024-25_merged_gw.csv` | 26,505 x 41; 29,725 x 41; 27,605 x 49. |
| Backtest outputs | 15 files each under `outputs/backtest_latest/` and `outputs/backtest_vaastav/` | Includes summary, by-season, by-GW, coefficients, player-GW predictions, calibration, minutes metrics, and MC bracket calibration. Generated by `backtest.py` (`src/fpl_xpts/backtest.py:691-720`). |
| External comparisons | 8 files under `outputs/external_comparison/` | Each has 30 rows x 20 columns. Generated Solio comparison output (`src/fpl_xpts/solio_compare.py:140-143`). |
| Legacy smoke outputs | 13 files under `outputs/legacy_live_smoke/` | Same legacy families as `outputs/legacy_live/`, with 3,328 to 3,410 player rows for player-level files. |
| Minutes template outputs | 8 files under `outputs/minutes_template_run/` | Raw pipeline outputs from a minutes-template run. |

### Column Lists For Main Projection Outputs

`outputs/legacy_live/weekly_player_week.csv` columns:

`GW`, `week`, `player_key`, `player`, `team`, `Pos`, `mins`, `xG_scaled`, `xA_scaled`, `xGA_exp`, `cs_prob`, `xPts`, `P1_GA`, `AppPts`, `GoalPts`, `AssistPts`, `CSPts`, `SavePts`, `DefconPts`, `CardPts`, `PenMissPts`, `ConcedePts`, `fixtures_in_week`.

`outputs/legacy_live/fixture_player_week.csv` columns:

`GW`, `week`, `fixture_in_week`, `player_key`, `player`, `team`, `Pos`, `mins`, `team_xG`, `team_xA`, `xG_scaled`, `xA_scaled`, `xGA_exp`, `cs_prob`, `xPts`, `P1_GA`, `AppPts`, `GoalPts`, `AssistPts`, `CSPts`, `SavePts`, `DefconPts`, `CardPts`, `PenMissPts`, `ConcedePts`.

`outputs/legacy_live/mc_brackets_full_player_week.csv` columns:

`GW`, `week`, `player_key`, `player`, `team`, `Pos`, `mins`, `xG_scaled`, `xA_scaled`, `xPts`, `fixtures_in_week`, `MC_MeanPts`, `MC_StdPts`, `MC_Floor`, `MC_P25`, `MC_P75`, `MC_Upside`, `MC_CaptainMean`, `MC_CaptainUpside`, `MC_P1_Return`, `MC_P2_Return`, `Bracket_LE_2`, `Bracket_3_to_6`, `Bracket_7_to_9`, `Bracket_10_to_14`, `Bracket_15_plus`, `MC_MinPts`, `MC_MaxPts`.

`outputs/legacy_live/mc_brackets_fixture_player_week.csv` columns:

`GW`, `week`, `player_key`, `player`, `team`, `Pos`, `mins`, `xG_scaled`, `xA_scaled`, `xGA_exp`, `cs_prob`, `xPts`, `P1_GA`, `AppPts`, `GoalPts`, `AssistPts`, `CSPts`, `SavePts`, `DefconPts`, `CardPts`, `PenMissPts`, `ConcedePts`, `fixtures_in_week`, `fixture_in_week`, `defcon90`, `saves90`, `rc_rate`, `yc_rate`, `yc_prob_90`, `rc_prob_90`, `gc_lambda`, `skip_sim`, `MC_MeanPts`, `MC_StdPts`, `MC_Floor`, `MC_P25`, `MC_P75`, `MC_Upside`, `MC_P1_Return`, `MC_P2_Return`, `Bracket_LE_2`, `Bracket_3_to_6`, `Bracket_7_to_9`, `Bracket_10_to_14`, `Bracket_15_plus`, `MC_MinPts`, `MC_MaxPts`.

`player_minutes_inputs_gw37_to_38.csv` columns:

`GW`, `player_id`, `player_key`, `player`, `team`, `Pos`, `start`, `mins`, `api_start`, `api_mins`, `appearances`, `total_minutes`, `chance_of_playing`.

### Sample First 3 Rows

For `outputs/legacy_live/weekly_player_week.csv`, the first three data rows are Bukayo Saka, Gabriel dos Santos Magalhaes, and Erling Haaland for GW37. The key numeric fields are:

| Player | GW | mins | xG_scaled | xA_scaled | cs_prob | xPts | fixtures_in_week |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bukayo Saka | 37 | 74.02 | 0.538897 | 0.443876 | 0.640000 | 7.185614 | 1 |
| Gabriel dos Santos Magalhaes | 37 | 90.00 | 0.169635 | 0.128158 | 0.640000 | 6.925726 | 1 |
| Erling Haaland | 37 | 88.00 | 0.881636 | 0.095912 | 0.250000 | 6.475465 | 1 |

For `outputs/legacy_live/fixture_player_week.csv`, the first three data rows are zero-minute Arsenal player-fixture rows for GW37 fixture slot 1:

| Player | Team | Pos | mins | team_xG | team_xA | xPts |
|---|---|---|---:|---:|---:|---:|
| Albert Sambi Lokonga | Arsenal | MID | 0.000000 | 2.680000 | 1.956400 | 0.000000 |
| Andre Harriman-Annous | Arsenal | MID | 0.000000 | 2.680000 | 1.956400 | 0.000000 |
| Benjamin White | Arsenal | DEF | 0.000000 | 2.680000 | 1.956400 | 0.000000 |

For `outputs/legacy_live/mc_brackets_full_player_week.csv`, the first three data rows are:

| Player | GW | xPts | MC_MeanPts | MC_StdPts | MC_P1_Return | Bracket_LE_2 | Bracket_15_plus |
|---|---:|---:|---:|---:|---:|---:|---:|
| Bukayo Saka | 37 | 7.185614 | 7.294600 | 5.277936 | 0.598100 | 0.152300 | 0.117600 |
| Gabriel dos Santos Magalhaes | 37 | 6.925726 | 7.096500 | 4.278854 | 0.262800 | 0.166600 | 0.073700 |
| Erling Haaland | 37 | 6.475465 | 7.045200 | 5.027301 | 0.611100 | 0.388900 | 0.070900 |

For `player_minutes_inputs_gw37_to_38.csv`, the first three data rows are:

| Player | GW | Pos | start | mins | api_start | api_mins | chance_of_playing |
|---|---:|---|---:|---:|---:|---:|---:|
| Benjamin White | 37 | DEF | 0.000000 | 0.000000 | 0.375000 | 28.000000 | 0.000000 |
| Brayden Clarke | 37 | DEF | 0.000000 | 0.000000 | 0.000000 | 0.000000 | blank |
| Cristhian Mosquera | 37 | DEF | 0.800000 | 75.000000 | 0.389000 | 40.700000 | 100.000000 |

For `minute_overrides.csv`, the only data row is `GW=36`, `fixture_in_week=2`, `player_key=erling haaland|manchester city`, `mins=90`.

For `league-players - attack_projections (2).csv`, the first three teams are Arsenal, Aston Villa, and Bournemouth. Their `projected_team_xG_W1` values are 2.01, 1.61, and 1.89; their `xG_TOTAL` values are 8.15, 5.67, and 6.11.

For `league-players - defense-projections (2).csv`, the first three teams are Arsenal, Aston Villa, and Bournemouth. Their `CS_ODDS_W1` values are 0.44, 0.27, and 0.35.

For `elevenify.com 25_26 Subscriber Season Long Data - Sheet1.csv`, the raw file begins with blank spacer rows and sheet copy, then a `Projected Goals` section. The first three team rows in that section are Arsenal, Aston Villa, and Bournemouth, with Arsenal GW37/GW38 projected goals of 2.68 and 1.63.

## 1.3 Generation Pipeline

### Pipeline Found In This Folder

The generation pipeline does live in this target folder. The README says the repo is being rebuilt from notebook/manual-input workflows into a live FPL projection system that still exports old CSV filenames (`README.md:3`), and it describes the intended flow as "fetch live data -> normalize IDs -> build feature tables -> forecast fixtures -> calculate xPts -> simulate Monte Carlo -> backtest/audit -> CSVs" (`README.md:28-33`).

The current code path is:

1. `fpl_xpts.cli` parses CLI flags for output directory, simulations, legacy/raw format, backtest mode, Solio compare mode, market odds, GW range, Elevenify, Understat, player history, minutes inputs, and external team projection files (`src/fpl_xpts/cli.py:13-40`).
2. CLI args are converted into `AppConfig` (`src/fpl_xpts/cli.py:43-66`).
3. In normal legacy mode, the CLI calls `write_legacy_outputs(out, config)` (`src/fpl_xpts/cli.py:79-82`).
4. `write_legacy_outputs` calls `build_legacy_outputs`, which calls `run_live_projection(config, include_mc=False)`, then creates fixture, weekly, totals, QC, form audit, shot audit, and MC bracket tables (`src/fpl_xpts/legacy_export.py:309-334`).
5. `write_legacy_outputs` writes each table with `df.to_csv(path, index=False, float_format="%.6f")` (`src/fpl_xpts/legacy_export.py:337-345`).
6. In raw mode, the CLI writes each dataframe returned by `run_live_projection` as `{name}.csv` (`src/fpl_xpts/cli.py:84-88`).
7. In backtest mode, the CLI calls `write_backtest_outputs`, which writes 15 backtest/calibration CSVs (`src/fpl_xpts/cli.py:67-72`, `src/fpl_xpts/backtest.py:691-720`).

### Notebook Pipeline

The two `.ipynb` files are not independent model implementations. `scripts/build_notebooks.py` is the source that writes both notebook files (`scripts/build_notebooks.py:251-257`). The generated xPts notebook configures GW37-GW38, market odds, Elevenify, FPL player history, player minutes inputs, Understat profiles, and no old external team files (`scripts/build_notebooks.py:99-117`), then writes `fixture_player_week.csv`, `weekly_player_week.csv`, `six_week_totals.csv`, top-50 tables, QC tables, form audit, and shot profile audit (`scripts/build_notebooks.py:121-142`). The generated MC notebook uses the same config pattern and writes `mc_brackets_fixture_player_week.csv`, `mc_brackets_full_player_week.csv`, and `mc_brackets_top50_by_week.csv` (`scripts/build_notebooks.py:197-239`).

### Pipeline Pieces Not Found Or Unclear

No deployment/copy script was found that moves `outputs/legacy_live/*.csv` into the vanilla HTML/Netlify site. Root-level MC CSVs exist, and `kiba_outputs.zip` contains legacy point-projection CSVs, but the exact step that copies model outputs from this workspace to the live site repo is not present in `modelyansh`.

No `methodology.html`, HTML page, `netlify.toml`, or Netlify function exists inside this target folder. If the audit must trace which site page loads which CSV, the owner needs to point this audit at the deployed site source as well. In the accessible machine I did see a sibling `C:\Users\iamdi\Desktop\KFT_2.0`, but after user clarification I did not audit it as Phase 1 source.

## 1.4 Methodology Page Claims

No `methodology.html` or other HTML methodology page exists inside `modelyansh`. The closest methodology documents are `README.md` and `docs/model_blueprint.md`.

### README Claims

`README.md` states that this repo is a rebuild "from notebook/manual-input workflows into a live FPL projection system that still exports the old CSV filenames" (`README.md:1-3`). It claims the workflow should automatically pull current FPL data, ignore old manual attack/defense spreadsheets unless opted in, blend FPL expected stats with Understat shot/chance profiles, estimate minutes/team/returns/defensive/cards/saves/penalties/bonus, run fixture-level Monte Carlo, and write weekly CSV outputs (`README.md:7-15`). It names FPL as canonical live source and lists Understat, Vaastav, odds sources, and manual overrides as optional enrichments (`README.md:17-26`). It also says the two legacy notebook filenames remain as runners and write outputs under `outputs/legacy_live/` (`README.md:35-40`).

### Full Model Blueprint Text

Source: `docs/model_blueprint.md:1-85`.

```markdown
# Model Blueprint

## Principles

The model should be automatic by default and editable by exception. FPL player ID is the primary key. Any Understat, odds, or historical-row match must resolve back to an FPL `element` ID before it enters the modelling tables.

The app should show both expected value and uncertainty. A 4.8 xPts player with a tight 3-7 range is different from a 4.8 xPts player with a 0-14 range.

## Live FPL Inputs

Use the official Fantasy Premier League endpoints:

- `bootstrap-static/`: players, teams, events, game settings, current expected stats, prices, status, news, ownership, set-piece order fields.
- `fixtures/`: current fixture list, kickoff times, home/away teams, difficulty, results once played.
- `element-summary/{element_id}/`: each player history, upcoming fixtures, previous seasons.
- `event/{event_id}/live/`: actual points and event stats for completed/current gameweeks.

## Historical Inputs

Use historical data for validation, not only for modelling:

- vaastav/Fantasy-Premier-League: historical FPL gameweek files, player season files, fixtures, and data dictionary.
- Understat: player/team xG/xA/xGA history when available.
- Football-Data.co.uk or Odds API: historical and current betting odds for market-implied team goal expectations.

## Feature Tables

Core tables:

- `players_live`: one row per FPL player from `bootstrap-static`.
- `fixtures_live`: one row per fixture from FPL.
- `player_fixture_history`: one row per player-match from `element-summary`.
- `player_gw_actuals`: one row per player-gameweek from `event/{gw}/live`.
- `team_strength`: attack, defence, home advantage, clean-sheet strength, save environment.
- `player_rates`: shrunken attacking, assist, defcon, save, card, start, and minute rates.
- `fixture_forecasts`: expected home/away goals and clean-sheet probabilities.
- `player_fixture_forecasts`: player-level xG, xA, xPts components per fixture.
- `mc_player_week`: risk brackets and percentiles.

## xPts Engine

1. Estimate fixture goal lambdas for each team.
2. Estimate player minutes with start/sub/no-show probabilities.
3. Shrink low-minute player rates toward position/team priors.
4. Allocate team xG and xA to players at fixture level, conserving team totals.
5. Apply FPL scoring rules:
   - appearance;
   - goals by position;
   - assists;
   - clean sheets;
   - goalkeeper saves and penalty saves;
   - defensive contribution thresholds;
   - cards, own goals, penalty misses;
   - goals conceded;
   - expected bonus.

## Monte Carlo Engine

The simulation should be fixture-level, not player-week-only.

For each simulated fixture:

1. Draw shared home and away goals from forecast lambdas.
2. Draw player minutes from a distribution that preserves expected minutes.
3. Allocate team goals to scorers using xG shares.
4. Allocate assists conditionally using xA shares and an assist probability.
5. Apply shared clean-sheet and goals-conceded outcomes.
6. Draw saves, cards, penalties, defcon counts, and own goals.
7. Compute BPS-like scores and award bonus by ranking players within the same simulated match.
8. Aggregate fixture simulations to player-gameweek and squad outputs.

This avoids impossible outcomes like defenders from the same team receiving different goals-conceded results.

## Validation

Every model run should produce an audit:

- missing players or unmatched IDs;
- blank fixtures;
- low-minute rate inflation;
- team xG/xA conservation errors;
- penalty share assigned to zero-minute players;
- Monte Carlo mean drifting too far from deterministic xPts;
- calibration curves for P1 return, clean sheet probability, and bracket probabilities.
```

## Phase 1 Status

Phase 1 inventory is complete for `modelyansh`. The model-generation pipeline is present locally. The deployed vanilla HTML/Netlify site and the copy/deploy step from model outputs to site CSVs are not present in this folder, so those remain unclear without the site source path.
