from __future__ import annotations

import json
from pathlib import Path


def code_cell(source: str) -> dict:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": source.splitlines(keepends=True),
    }


def markdown_cell(source: str) -> dict:
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": source.splitlines(keepends=True),
    }


def notebook(cells: list[dict]) -> dict:
    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {
                "codemirror_mode": {"name": "ipython", "version": 3},
                "file_extension": ".py",
                "mimetype": "text/x-python",
                "name": "python",
                "nbconvert_exporter": "python",
                "pygments_lexer": "ipython3",
                "version": "3.12",
            },
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


POINTS_NOTEBOOK = notebook(
    [
        markdown_cell(
            "# FPL xPts, xG and xA projection runner\n\n"
            "Run this notebook from the repository root in Google Colab. It fetches live FPL and Understat data, "
            "uses the uploaded Elevenify team goals/clean-sheet CSV when present, and writes the legacy point-projection CSVs with unchanged filenames and column order."
        ),
        code_cell(
            "from pathlib import Path\n"
            "import os\n"
            "import shutil\n"
            "import sys\n"
            "import pandas as pd\n\n"
            "try:\n"
            "    import google.colab  # type: ignore\n"
            "    IN_COLAB = True\n"
            "except Exception:\n"
            "    IN_COLAB = False\n"
            "if IN_COLAB and not (Path.cwd() / 'src' / 'fpl_xpts').exists():\n"
            "    from google.colab import drive\n"
            "    drive.mount('/content/drive', force_remount=False)\n\n"
            "search_roots = [Path.cwd(), Path('/content'), Path('/content/drive/MyDrive')]\n"
            "candidates = []\n"
            "for root in search_roots:\n"
            "    if root.exists():\n"
            "        candidates.extend([root, *[p for p in root.iterdir() if p.is_dir()]])\n"
            "        for child in [p for p in root.iterdir() if p.is_dir()]:\n"
            "            try:\n"
            "                candidates.extend([p for p in child.iterdir() if p.is_dir()])\n"
            "            except Exception:\n"
            "                pass\n"
            "ROOT = next((p for p in candidates if (p / 'src' / 'fpl_xpts').exists()), None)\n"
            "if ROOT is None:\n"
            "    raise FileNotFoundError('Upload/open the repo folder so src/fpl_xpts exists, then run again.')\n"
            "os.chdir(ROOT)\n"
            "SRC = ROOT / 'src'\n"
            "sys.path.insert(0, str(SRC.resolve()))\n\n"
            "# Keep the uploaded Elevenify season-long CSV in the repo root. It overrides the fallback team goal/CS model for GW37-GW38.\n"
            "# Optional fallback if you do not have the Elevenify file:\n"
            "# os.environ['ODDS_API_KEY'] = 'paste-your-the-odds-api-key-here'\n\n"
            "from fpl_xpts.config import AppConfig\n"
            "from fpl_xpts.legacy_export import (\n"
            "    fixture_player_week,\n"
            "    form_weighting_audit,\n"
            "    qc_tables,\n"
            "    shot_profile_audit,\n"
            "    six_week_totals,\n"
            "    weekly_player_week,\n"
            ")\n"
            "from fpl_xpts.pipeline import run_live_projection\n\n"
            "OUT_DIR = Path('outputs/legacy_live')\n"
            "OUT_DIR.mkdir(parents=True, exist_ok=True)\n"
            "MINUTES_INPUT = Path('player_minutes_inputs_gw37_to_38.csv') if Path('player_minutes_inputs_gw37_to_38.csv').exists() else Path('player_minutes_inputs.csv')\n"
            "CONFIG = AppConfig(\n"
            "    n_sim=10_000,\n"
            "    projection_start_gw=37,\n"
            "    projection_end_gw=38,\n"
            "    use_market_odds=True,\n"
            "    use_elevenify_projection_file=True,\n"
            "    use_fpl_player_history=True,\n"
            "    use_player_minutes_input_file=True,\n"
            "    player_minutes_input_path=MINUTES_INPUT,\n"
            "    write_player_minutes_input_template=True,\n"
            "    overwrite_player_minutes_input_template=False,\n"
            "    use_external_team_projection_files=False,\n"
            "    use_understat_profiles=True,\n"
            "    include_big_chance_profiles=True,\n"
            "    understat_season=None,\n"
            ")\n"
            "CONFIG"
        ),
        code_cell(
            "# Minutes inputs stated explicitly at the boundary: None keeps the\n"
            "# legacy weekly defaults (player_minutes_inputs*.csv + minute_overrides.csv).\n"
            "live = run_live_projection(\n"
            "    CONFIG,\n"
            "    include_mc=False,\n"
            "    manual_minutes_paths=None,\n"
            "    minute_override_paths=None,\n"
            ")\n"
            "fixture_df = fixture_player_week(live['player_fixture'], live['players'], live['teams'])\n"
            "weekly_df = weekly_player_week(fixture_df)\n"
            "totals_df = six_week_totals(weekly_df)\n"
            "qc_week, qc_fixture = qc_tables(fixture_df)\n"
            "form_audit = form_weighting_audit(live['players'], live['teams'])\n"
            "shot_audit = shot_profile_audit(live.get('shot_profiles', pd.DataFrame()))\n\n"
            "outputs = {\n"
            "    'fixture_player_week.csv': fixture_df,\n"
            "    'weekly_player_week.csv': weekly_df,\n"
            "    'six_week_totals.csv': totals_df,\n"
            "    'top50_p1_ga_by_week.csv': weekly_df.sort_values(['week', 'P1_GA'], ascending=[True, False]).groupby('week', group_keys=False).head(50).reset_index(drop=True),\n"
            "    'top50_xga_by_week.csv': weekly_df.sort_values(['week', 'xGA_exp'], ascending=[True, False]).groupby('week', group_keys=False).head(50).reset_index(drop=True),\n"
            "    'qc_team_week.csv': qc_week,\n"
            "    'qc_team_week_fixture.csv': qc_fixture,\n"
            "    'form_weighting_audit.csv': form_audit,\n"
            "    'shot_profile_audit.csv': shot_audit,\n"
            "}\n"
            "for name, df in outputs.items():\n"
            "    df.to_csv(OUT_DIR / name, index=False, float_format='%.6f')\n"
            "archive = shutil.make_archive(str(OUT_DIR), 'zip', OUT_DIR)\n"
            "sorted(outputs), archive"
        ),
        code_cell(
            "weekly_df.loc[weekly_df['GW'].eq(37), ['player', 'team', 'Pos', 'mins', 'xG_scaled', 'xA_scaled', 'xPts', 'fixtures_in_week']].head(25)"
        ),
        code_cell(
            "shot_audit[['player', 'team', 'understat_shots90', 'understat_xG_per_shot', 'understat_chances_created90', 'understat_xA_per_chance', 'understat_big_chance_received90', 'understat_big_chance_created90']].head(25)"
        ),
    ]
)


MC_NOTEBOOK = notebook(
    [
        markdown_cell(
            "# FPL Monte Carlo bracket runner\n\n"
            "Run this notebook from the repository root in Google Colab after, or independently from, the xPts notebook. "
            "It fetches live FPL and Understat data, uses the uploaded Elevenify team goals/clean-sheet CSV when present, and writes the legacy MC bracket CSVs with unchanged filenames and column order."
        ),
        code_cell(
            "from pathlib import Path\n"
            "import os\n"
            "import shutil\n"
            "import sys\n"
            "import pandas as pd\n\n"
            "try:\n"
            "    import google.colab  # type: ignore\n"
            "    IN_COLAB = True\n"
            "except Exception:\n"
            "    IN_COLAB = False\n"
            "if IN_COLAB and not (Path.cwd() / 'src' / 'fpl_xpts').exists():\n"
            "    from google.colab import drive\n"
            "    drive.mount('/content/drive', force_remount=False)\n\n"
            "search_roots = [Path.cwd(), Path('/content'), Path('/content/drive/MyDrive')]\n"
            "candidates = []\n"
            "for root in search_roots:\n"
            "    if root.exists():\n"
            "        candidates.extend([root, *[p for p in root.iterdir() if p.is_dir()]])\n"
            "        for child in [p for p in root.iterdir() if p.is_dir()]:\n"
            "            try:\n"
            "                candidates.extend([p for p in child.iterdir() if p.is_dir()])\n"
            "            except Exception:\n"
            "                pass\n"
            "ROOT = next((p for p in candidates if (p / 'src' / 'fpl_xpts').exists()), None)\n"
            "if ROOT is None:\n"
            "    raise FileNotFoundError('Upload/open the repo folder so src/fpl_xpts exists, then run again.')\n"
            "os.chdir(ROOT)\n"
            "SRC = ROOT / 'src'\n"
            "sys.path.insert(0, str(SRC.resolve()))\n\n"
            "# Keep the uploaded Elevenify season-long CSV in the repo root. It overrides the fallback team goal/CS model for GW37-GW38.\n"
            "# Optional fallback if you do not have the Elevenify file:\n"
            "# os.environ['ODDS_API_KEY'] = 'paste-your-the-odds-api-key-here'\n\n"
            "from fpl_xpts.config import AppConfig\n"
            "from fpl_xpts.legacy_export import fixture_player_week, mc_legacy_tables, weekly_player_week\n"
            "from fpl_xpts.pipeline import run_live_projection\n\n"
            "OUT_DIR = Path('outputs/legacy_live')\n"
            "OUT_DIR.mkdir(parents=True, exist_ok=True)\n"
            "MINUTES_INPUT = Path('player_minutes_inputs_gw37_to_38.csv') if Path('player_minutes_inputs_gw37_to_38.csv').exists() else Path('player_minutes_inputs.csv')\n"
            "CONFIG = AppConfig(\n"
            "    n_sim=10_000,\n"
            "    projection_start_gw=37,\n"
            "    projection_end_gw=38,\n"
            "    use_market_odds=True,\n"
            "    use_elevenify_projection_file=True,\n"
            "    use_fpl_player_history=True,\n"
            "    use_player_minutes_input_file=True,\n"
            "    player_minutes_input_path=MINUTES_INPUT,\n"
            "    write_player_minutes_input_template=True,\n"
            "    overwrite_player_minutes_input_template=False,\n"
            "    use_external_team_projection_files=False,\n"
            "    use_understat_profiles=True,\n"
            "    include_big_chance_profiles=True,\n"
            "    understat_season=None,\n"
            ")\n"
            "CONFIG"
        ),
        code_cell(
            "# Minutes inputs stated explicitly at the boundary: None keeps the\n"
            "# legacy weekly defaults (player_minutes_inputs*.csv + minute_overrides.csv).\n"
            "live = run_live_projection(\n"
            "    CONFIG,\n"
            "    include_mc=False,\n"
            "    manual_minutes_paths=None,\n"
            "    minute_override_paths=None,\n"
            ")\n"
            "fixture_df = fixture_player_week(live['player_fixture'], live['players'], live['teams'])\n"
            "weekly_df = weekly_player_week(fixture_df)\n"
            "mc_fixture, mc_full, mc_top50 = mc_legacy_tables(\n"
            "    live['player_fixture'],\n"
            "    fixture_df,\n"
            "    weekly_df,\n"
            "    live['players'],\n"
            "    live['teams'],\n"
            "    CONFIG.n_sim,\n"
            "    CONFIG.random_seed,\n"
            ")\n"
            "outputs = {\n"
            "    'mc_brackets_fixture_player_week.csv': mc_fixture,\n"
            "    'mc_brackets_full_player_week.csv': mc_full,\n"
            "    'mc_brackets_top50_by_week.csv': mc_top50,\n"
            "}\n"
            "for name, df in outputs.items():\n"
            "    df.to_csv(OUT_DIR / name, index=False, float_format='%.6f')\n"
            "archive = shutil.make_archive(str(OUT_DIR), 'zip', OUT_DIR)\n"
            "sorted(outputs), archive"
        ),
        code_cell(
            "mc_full.loc[mc_full['GW'].eq(37), ['player', 'team', 'Pos', 'MC_MeanPts', 'MC_Floor', 'MC_P75', 'MC_Upside', 'Bracket_10_to_14', 'Bracket_15_plus']].head(25)"
        ),
        code_cell(
            "mc_fixture.loc[mc_fixture['GW'].eq(37), ['player', 'team', 'fixture_in_week', 'MC_MeanPts', 'MC_Upside', 'Bracket_LE_2', 'Bracket_15_plus']].head(25)"
        ),
    ]
)


def main() -> None:
    targets = {
        "KFT_xPts_FORM_ZERO_MINS_PATCHED.ipynb": POINTS_NOTEBOOK,
        "Kiba_Bracket_MonteCarlo_v6_upgraded_(3).ipynb": MC_NOTEBOOK,
    }
    for filename, nb in targets.items():
        Path(filename).write_text(json.dumps(nb, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
