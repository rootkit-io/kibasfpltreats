"""Phase 1 seam tests: the Manual Minutes Inputs contract and its wiring.

Proves three things without touching the six-layer stack:
1. the contract parses today's weekly CSV shape and rejects bad edits loudly,
2. path resolution reproduces the legacy pipeline behaviour when no explicit
   paths are given, and lets explicit paths (e.g. this week's Admin Panel
   export) win outright,
3. a validated file flows into the unchanged ``apply_player_minutes_inputs``
   layer and lands on the player-fixture frame.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import fpl_xpts.minutes_contract as minutes_contract
from fpl_xpts.config import AppConfig
from fpl_xpts.minute_overrides import apply_minute_overrides, apply_player_minutes_inputs
from fpl_xpts.minutes_contract import (
    LEGACY_EXTRA_MANUAL_MINUTES_PATH,
    LEGACY_MINUTE_OVERRIDE_FILENAMES,
    ManualMinutesError,
    load_manual_minutes_csv,
    load_minute_overrides_csv,
    resolve_manual_minutes_paths,
    resolve_minute_override_paths,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

HEADER = (
    "GW,player_id,player_key,player,team,Pos,start,mins,"
    "api_start,api_mins,appearances,total_minutes,chance_of_playing\n"
)


def _write_csv(path: Path, rows: list[str]) -> Path:
    path.write_text(HEADER + "".join(rows), encoding="utf-8")
    return path


# ---------------------------------------------------------------- contract


def test_contract_parses_valid_weekly_csv(tmp_path):
    csv_path = _write_csv(
        tmp_path / "this_weeks_manual.csv",
        [
            "1,101,erling haaland|manchester city,Erling Haaland,Manchester City,FWD,0.8,75,0.9,80,10,900,90\n",
            ",,bukayo saka|arsenal,Bukayo Saka,Arsenal,MID,55,60,,,,,\n",
        ],
    )
    manual = load_manual_minutes_csv(csv_path)

    assert manual.path == csv_path
    assert len(manual.states) == 2

    haaland = manual.states[0]
    assert haaland.gameweek == 1
    assert haaland.player_id == 101
    assert haaland.likely_minutes == 75.0
    assert haaland.start_probability == 0.8
    assert haaland.chance_of_playing == 0.9  # 90 given as percent, normalised

    saka = manual.states[1]
    assert saka.gameweek is None
    assert saka.player_id is None
    assert saka.player_key == "bukayo saka|arsenal"
    assert saka.start_probability == 0.55  # 55 given as percent, normalised
    assert saka.chance_of_playing is None


def test_contract_rejects_missing_required_column(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("GW,player_id,start\n1,101,0.5\n", encoding="utf-8")
    with pytest.raises(ManualMinutesError, match="mins"):
        load_manual_minutes_csv(bad)


def test_contract_rejects_missing_identity_columns(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("GW,start,mins\n1,0.5,60\n", encoding="utf-8")
    with pytest.raises(ManualMinutesError, match="player_id"):
        load_manual_minutes_csv(bad)


def test_contract_rejects_bad_rows_with_csv_line_numbers(tmp_path):
    csv_path = _write_csv(
        tmp_path / "bad_rows.csv",
        [
            "1,101,x|y,X,Y,MID,0.5,120,,,,,\n",  # mins > 90 -> line 2
            "1,,,,,MID,-5,45,,,,,\n",  # negative start prob -> line 3
        ],
    )
    with pytest.raises(ManualMinutesError) as excinfo:
        load_manual_minutes_csv(csv_path)
    message = str(excinfo.value)
    assert "line 2" in message
    assert "line 3" in message


def test_contract_rejects_missing_file(tmp_path):
    with pytest.raises(ManualMinutesError, match="not found"):
        load_manual_minutes_csv(tmp_path / "nope.csv")


# ------------------------------------------------------- path resolution


def test_resolve_explicit_paths_win_and_must_exist(tmp_path):
    config = AppConfig(player_minutes_input_path=tmp_path / "config_file.csv")
    weekly = _write_csv(
        tmp_path / "this_weeks_manual.csv",
        ["1,101,x|y,X,Y,MID,0.5,60,,,,,\n"],
    )

    assert resolve_manual_minutes_paths(config, [weekly]) == [weekly]
    assert resolve_manual_minutes_paths(config, []) == []  # explicit "none"

    with pytest.raises(ManualMinutesError, match="not found"):
        resolve_manual_minutes_paths(config, [tmp_path / "missing.csv"])


def test_resolve_defaults_reproduce_legacy_order_and_dedupe(tmp_path, monkeypatch):
    # Defaults are anchored to the backend root, not the CWD; the test anchors
    # them to tmp_path instead so it is hermetic on any machine.
    monkeypatch.chdir(tmp_path)  # CWD must not matter
    row = "1,101,x|y,X,Y,MID,0.5,60,,,,,\n"
    config_file = _write_csv(tmp_path / "player_minutes_inputs.csv", [row])
    legacy_extra = _write_csv(tmp_path / LEGACY_EXTRA_MANUAL_MINUTES_PATH.name, [row])
    monkeypatch.setattr(minutes_contract, "LEGACY_EXTRA_MANUAL_MINUTES_PATH", legacy_extra)
    config = AppConfig(player_minutes_input_path=config_file)

    resolved = resolve_manual_minutes_paths(config, None)
    assert [p.name for p in resolved] == [
        "player_minutes_inputs.csv",
        "player_minutes_inputs_gw37_to_38.csv",
    ]

    # the same file passed twice is applied once (legacy dedupe preserved)
    assert resolve_manual_minutes_paths(config, [config_file, config_file]) == [config_file]


def test_resolve_defaults_skip_absent_files(tmp_path, monkeypatch):
    # Anchor the defaults at (empty) tmp_path: absent files resolve to [].
    monkeypatch.chdir(tmp_path)  # CWD must not matter
    monkeypatch.setattr(
        minutes_contract,
        "LEGACY_EXTRA_MANUAL_MINUTES_PATH",
        tmp_path / LEGACY_EXTRA_MANUAL_MINUTES_PATH.name,
    )
    config = AppConfig(player_minutes_input_path=tmp_path / "player_minutes_inputs.csv")
    assert resolve_manual_minutes_paths(config, None) == []


def test_no_minutes_inputs_flag_skips_defaults_even_when_files_exist(tmp_path, monkeypatch):
    """use_player_minutes_input_file=False (--no-minutes-inputs) bypasses the
    legacy defaults entirely: no discovery, empty resolution."""
    monkeypatch.chdir(tmp_path)
    row = "1,101,x|y,X,Y,MID,0.5,60,,,,,\n"
    _write_csv(tmp_path / "player_minutes_inputs.csv", [row])
    _write_csv(tmp_path / LEGACY_EXTRA_MANUAL_MINUTES_PATH.name, [row])
    config = AppConfig(use_player_minutes_input_file=False)

    assert resolve_manual_minutes_paths(config, None) == []


def test_explicit_paths_win_over_no_minutes_inputs_flag(tmp_path):
    """Explicit intent (--manual-minutes x.csv) beats the config flag."""
    config = AppConfig(use_player_minutes_input_file=False)
    explicit = _write_csv(
        tmp_path / "this_weeks_manual.csv", ["1,101,x|y,X,Y,MID,0.5,60,,,,,\n"]
    )
    assert resolve_manual_minutes_paths(config, [explicit]) == [explicit]


# ----------------------------------------------------------------- wiring


def test_contract_flows_into_existing_apply_layer(tmp_path):
    """A validated file drives the unchanged layer 3/4 logic end to end."""
    csv_path = _write_csv(
        tmp_path / "this_weeks_manual.csv",
        [
            "1,101,erling haaland|manchester city,Erling Haaland,Manchester City,FWD,0.8,75,,,,,90\n",
        ],
    )
    manual = load_manual_minutes_csv(csv_path)  # the contract gate

    players = pd.DataFrame(
        {
            "id": [101, 202],
            "first_name": ["Erling", "Bukayo"],
            "second_name": ["Haaland", "Saka"],
            "web_name": ["Haaland", "Saka"],
            "team": [1, 2],
        }
    )
    teams = pd.DataFrame({"id": [1, 2], "name": ["Manchester City", "Arsenal"]})
    player_fixture = pd.DataFrame(
        {
            "player_id": [101, 202],
            "team": [1, 2],
            "event": [1, 1],
            "fixture": [10, 11],
            "likely_minutes": [45.0, 45.0],
            "start_probability": [0.5, 0.5],
            "play_probability": [0.6, 0.6],
            "expected_minutes": [45.0, 45.0],
            "minutes_model_source": ["model", "model"],
        }
    )

    out = apply_player_minutes_inputs(player_fixture, players, teams, path=manual.path)

    haaland = out.loc[out["player_id"] == 101].iloc[0]
    assert haaland["minutes_model_source"] == "manual_player_minutes_input"
    assert haaland["likely_minutes"] == 75.0
    assert haaland["start_probability"] == 0.8
    assert haaland["play_probability"] == 0.9
    # minute_outcomes(75, start=0.8, play=0.9) -> E[minutes] = 62.6
    assert np.isclose(haaland["expected_minutes"], 62.6)

    saka = out.loc[out["player_id"] == 202].iloc[0]
    assert saka["minutes_model_source"] == "model"  # untouched
    assert saka["expected_minutes"] == 45.0


# ------------------------------------------------ override contract (Phase 2)


def test_override_contract_parses_valid_csv(tmp_path):
    csv_path = tmp_path / "overrides.csv"
    csv_path.write_text(
        "GW,fixture_in_week,player_key,mins\n"
        "36,2,erling haaland|manchester city,90\n"
        "36,,x|y,45\n",
        encoding="utf-8",
    )
    overrides = load_minute_overrides_csv(csv_path)
    assert len(overrides.states) == 2
    first = overrides.states[0]
    assert first.gameweek == 36
    assert first.fixture_in_week == 2
    assert first.minutes == 90.0
    assert overrides.states[1].fixture_in_week == 1  # defaulted


def test_override_contract_requires_gameweek(tmp_path):
    csv_path = tmp_path / "overrides.csv"
    csv_path.write_text("GW,player_id,mins\n,101,45\n", encoding="utf-8")
    with pytest.raises(ManualMinutesError, match="line 2"):
        load_minute_overrides_csv(csv_path)


def test_override_contract_rejects_missing_columns(tmp_path):
    csv_path = tmp_path / "overrides.csv"
    csv_path.write_text("player_id,mins\n101,45\n", encoding="utf-8")
    with pytest.raises(ManualMinutesError, match="GW"):
        load_minute_overrides_csv(csv_path)


def test_resolve_override_paths_semantics(tmp_path, monkeypatch):
    # Discovery is anchored to the backend root, not the CWD; re-anchor it to
    # tmp_path so the test is hermetic regardless of real files on disk.
    monkeypatch.chdir(tmp_path)  # CWD must not matter
    monkeypatch.setattr(
        minutes_contract,
        "LEGACY_MINUTE_OVERRIDE_FILENAMES",
        tuple(tmp_path / p.name for p in LEGACY_MINUTE_OVERRIDE_FILENAMES),
    )
    assert resolve_minute_override_paths(None) == []  # nothing to discover
    assert resolve_minute_override_paths([]) == []  # explicitly disabled

    discovered = tmp_path / LEGACY_MINUTE_OVERRIDE_FILENAMES[0].name
    discovered.write_text("GW,player_id,mins\n1,101,45\n", encoding="utf-8")
    assert [p.name for p in resolve_minute_override_paths(None)] == [discovered.name]

    explicit = tmp_path / "special_overrides.csv"
    explicit.write_text("GW,player_id,mins\n1,101,45\n", encoding="utf-8")
    assert resolve_minute_override_paths([explicit]) == [explicit]

    with pytest.raises(ManualMinutesError, match="not found"):
        resolve_minute_override_paths([tmp_path / "missing.csv"])


def test_override_adapter_wires_contract_into_engine(tmp_path):
    """apply_minute_overrides (compat adapter) validates then delegates."""
    csv_path = tmp_path / "overrides.csv"
    csv_path.write_text("GW,player_id,mins\n1,101,15\n", encoding="utf-8")

    players = pd.DataFrame(
        {
            "id": [101],
            "first_name": ["Erling"],
            "second_name": ["Haaland"],
            "web_name": ["Haaland"],
            "team": [1],
        }
    )
    teams = pd.DataFrame({"id": [1], "name": ["Manchester City"]})
    player_fixture = pd.DataFrame(
        {
            "player_id": [101],
            "team": [1],
            "event": [1],
            "fixture": [10],
            "kickoff_time": ["2025-08-16T14:00:00Z"],
            "expected_minutes": [45.0],
        }
    )

    out = apply_minute_overrides(player_fixture, players, teams, path=csv_path)
    assert out["expected_minutes"].iloc[0] == 15.0


# ------------------------------------------- current weekly files conform


@pytest.mark.parametrize(
    "name",
    ["player_minutes_inputs.csv", "player_minutes_inputs_gw37_to_38.csv"],
)
def test_current_weekly_files_conform_to_contract(name):
    """Regression guard: the files the weekly workflow uses today must pass."""
    path = REPO_ROOT / name
    if not path.exists():
        pytest.skip(f"{name} not present in repo root")
    manual = load_manual_minutes_csv(path)
    assert len(manual.states) > 0


def test_current_override_file_conforms_to_contract():
    path = REPO_ROOT / "minute_overrides.csv"
    if not path.exists():
        pytest.skip("minute_overrides.csv not present in repo root")
    overrides = load_minute_overrides_csv(path)
    assert len(overrides.states) > 0
