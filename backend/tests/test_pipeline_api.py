"""Phase 3: the Admin Panel API seam and CLI plumbing.

The centrepiece test simulates an Admin Panel execution: contract states are
constructed in memory and passed straight into the REAL ``run_live_projection``
with a pre-loaded model bundle. Network and xPts stages (out of the Minutes
module's scope) are stubbed in the pipeline namespace; the minutes boundary
and the engine run for real. Tripwires prove strictly zero file I/O:

- ``pd.read_csv`` raises if anything reads a CSV,
- the contract file-loaders and path resolvers raise if the CSV route is hit,
- ``load_minutes_bundle`` raises if the model is loaded from disk,
- the snapshot writer raises if called (``write_snapshot=False``).
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

import fpl_xpts.cli as cli
import fpl_xpts.minutes_contract as minutes_contract
import fpl_xpts.pipeline as pipeline
from fpl_xpts.config import AppConfig
from fpl_xpts.minutes_contract import (
    ManualMinutesError,
    MinuteOverrideState,
    MinutesRunInputs,
    PlayerMinutesState,
    resolve_minutes_run_inputs,
)
from fpl_xpts.minutes_model import MINUTES_FEATURE_COLUMNS

# ------------------------------------------------------------ fake ML bundle
# Same constants as tests/test_minutes_engine.py:
#   expected=52.0, start_prob=0.6, source="trained_four_output_model"


class _IdentityPreprocessor:
    def transform(self, frame):
        return np.zeros((len(frame), 1))


class _ConstantClassifier:
    def __init__(self, probability):
        self.probability = float(probability)

    def predict_proba(self, matrix):
        positive = np.full(len(matrix), self.probability)
        return np.column_stack([1.0 - positive, positive])


class _ConstantRegressor:
    def __init__(self, value):
        self.value = float(value)

    def predict(self, matrix):
        return np.full(len(matrix), self.value)


class _IdentityCalibrator:
    def predict(self, values):
        return np.asarray(values)


def _component(model, classifier=False):
    component = {
        "feature_columns": list(MINUTES_FEATURE_COLUMNS),
        "preprocessor": _IdentityPreprocessor(),
        "xgb_model": model,
        "rf_model": model,
    }
    if classifier:
        component["calibrator"] = _IdentityCalibrator()
    return component


def _fake_bundle():
    return {
        "play_classifier": _component(_ConstantClassifier(0.8), classifier=True),
        "start_classifier": _component(_ConstantClassifier(0.75), classifier=True),
        "mins_if_start_regressor": _component(_ConstantRegressor(80.0)),
        "mins_if_sub_regressor": _component(_ConstantRegressor(20.0)),
    }


# ------------------------------------------------------------ stub universe


def _players():
    return pd.DataFrame(
        {
            "id": [101, 202],
            "first_name": ["Erling", "Bukayo"],
            "second_name": ["Haaland", "Saka"],
            "web_name": ["Haaland", "Saka"],
            "team": [1, 2],
            "status": ["a", "a"],
            "chance_of_playing_this_round": [100, 100],
            "chance_of_playing_next_round": [100, 100],
        }
    )


def _teams():
    return pd.DataFrame(
        {
            "id": [1, 2],
            "name": ["Manchester City", "Arsenal"],
            "position": [1, 2],
            "points": [10, 8],
            "played": [4, 4],
        }
    )


def _base_player_fixture():
    return pd.DataFrame(
        [
            {
                "player_id": 101,
                "team": 1,
                "opponent": 2,
                "position": "FWD",
                "was_home": True,
                "event": 1,
                "fixture": 10,
                "kickoff_time": "2025-08-16T14:00:00Z",
                "likely_minutes": 45.0,
                "start_probability": 0.5,
                "play_probability": 0.6,
                "expected_minutes": 45.0,
                "minutes_model_source": "heuristic_baseline",
            },
            {
                "player_id": 202,
                "team": 2,
                "opponent": 1,
                "position": "MID",
                "was_home": True,
                "event": 1,
                "fixture": 11,
                "kickoff_time": "2025-08-16T16:30:00Z",
                "likely_minutes": 45.0,
                "start_probability": 0.5,
                "play_probability": 0.6,
                "expected_minutes": 45.0,
                "minutes_model_source": "heuristic_baseline",
            },
        ]
    )


class _StubFplClient:
    def __init__(self, config=None):
        self.config = config

    def bootstrap(self):
        return {}

    def fixtures(self):
        return [
            {"event": 1, "id": 10, "team_h": 1, "team_a": 2},
            {"event": 1, "id": 11, "team_h": 2, "team_a": 1},
        ]


def _offline_config(**overrides):
    kwargs = dict(
        use_understat_profiles=False,
        use_fpl_player_history=False,
        use_elevenify_projection_file=False,
        use_external_team_projection_files=False,
        use_market_odds=False,
        use_ml_predictions=False,
        write_player_minutes_input_template=False,
        projection_start_gw=None,
        projection_end_gw=None,
    )
    kwargs.update(overrides)
    return AppConfig(**kwargs)


def _boom(name):
    def _fail(*args, **kwargs):  # pragma: no cover - tripwire
        raise AssertionError(f"file I/O tripwire hit: {name}")

    return _fail


def _stub_pipeline_stages(monkeypatch):
    """Stub network + xPts stages (out of scope); minutes stage stays real."""
    monkeypatch.setattr(pipeline, "FplClient", _StubFplClient)
    monkeypatch.setattr(
        pipeline,
        "bootstrap_tables",
        lambda bootstrap: {
            "players": _players(),
            "teams": _teams(),
            "events": pd.DataFrame({"id": [1], "is_current": [True]}),
        },
    )
    monkeypatch.setattr(pipeline, "current_event", lambda events: 1)
    monkeypatch.setattr(pipeline, "attach_recent_player_form", lambda players, history: players)
    monkeypatch.setattr(pipeline, "build_team_strength", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(pipeline, "build_team_assist_factors", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(pipeline, "forecast_fixture_lambdas", lambda upcoming, ts: upcoming.copy())
    monkeypatch.setattr(pipeline, "apply_market_odds_projections", lambda ff, teams, config: ff)
    monkeypatch.setattr(
        pipeline, "build_player_fixture_forecast", lambda *a, **k: _base_player_fixture()
    )
    monkeypatch.setattr(
        pipeline, "recompute_player_fixture_components", lambda pf, **k: pf
    )
    monkeypatch.setattr(pipeline, "aggregate_gameweek", lambda pf: pf.copy())
    monkeypatch.setattr(pipeline, "attach_mc_tail_probabilities", lambda weekly, mc: weekly)


def _arm_io_tripwires(monkeypatch):
    monkeypatch.setattr(pd, "read_csv", _boom("pd.read_csv"))
    monkeypatch.setattr(pipeline, "load_minutes_bundle", _boom("load_minutes_bundle"))
    monkeypatch.setattr(pipeline, "_write_projection_snapshot", _boom("snapshot writer"))
    # The in-memory route must bypass the CSV adapters and path discovery
    # entirely -- resolving or loading any file is a failure.
    monkeypatch.setattr(
        minutes_contract, "load_manual_minutes_files", _boom("load_manual_minutes_files")
    )
    monkeypatch.setattr(
        minutes_contract, "load_minute_override_files", _boom("load_minute_override_files")
    )
    monkeypatch.setattr(
        minutes_contract, "resolve_manual_minutes_paths", _boom("resolve_manual_minutes_paths")
    )
    monkeypatch.setattr(
        minutes_contract, "resolve_minute_override_paths", _boom("resolve_minute_override_paths")
    )


# ----------------------------------------------- the Admin Panel simulation


def test_admin_panel_in_memory_run_with_zero_file_io(monkeypatch):
    _stub_pipeline_stages(monkeypatch)
    _arm_io_tripwires(monkeypatch)

    manual_states = [
        PlayerMinutesState(
            gameweek=1,
            player_id=101,
            likely_minutes=75,
            start_probability=0.8,
            chance_of_playing=0.9,
        )
    ]
    override_states = [
        MinuteOverrideState(gameweek=1, fixture_in_week=1, player_id=101, minutes=15)
    ]

    results = pipeline.run_live_projection(
        config=_offline_config(),
        include_mc=False,
        manual_minutes_states=manual_states,
        minute_override_states=override_states,
        minutes_model_bundle=_fake_bundle(),
        write_snapshot=False,
    )

    player_fixture = results["player_fixture"]

    # Haaland: model (52.0) -> manual (62.6) -> override pins 15.0;
    # probabilities keep the manual-layer values.
    haaland = player_fixture.loc[player_fixture["player_id"] == 101].iloc[0]
    assert haaland["expected_minutes"] == 15.0
    assert haaland["likely_minutes"] == 75.0
    assert np.isclose(haaland["start_probability"], 0.8)
    assert np.isclose(haaland["play_probability"], 0.9)
    assert haaland["minutes_model_source"] == "manual_player_minutes_input"

    # Saka: untouched by manual/override layers -> keeps the model values.
    saka = player_fixture.loc[player_fixture["player_id"] == 202].iloc[0]
    assert np.isclose(saka["expected_minutes"], 52.0)
    assert np.isclose(saka["start_probability"], 0.6)
    assert saka["minutes_model_source"] == "trained_four_output_model"


def test_admin_panel_states_accept_raw_json_dicts(monkeypatch):
    """The Admin Panel sends JSON: raw dicts coerce through the contract."""
    _stub_pipeline_stages(monkeypatch)
    _arm_io_tripwires(monkeypatch)

    results = pipeline.run_live_projection(
        # No pre-loaded bundle and no model on disk: layer 2 is skipped
        # without any load attempt (the repo's real models/ dir is not used).
        config=_offline_config(minutes_model_path=Path("nonexistent_model.pkl")),
        include_mc=False,
        manual_minutes_states=[
            {
                "gameweek": 1,
                "player_id": 101,
                "likely_minutes": 75,
                "start_probability": 0.8,
                "chance_of_playing": 90,  # percent, normalised by the contract
            }
        ],
        minute_override_states=[],
        minutes_model_bundle=None,
        write_snapshot=False,
    )
    haaland = results["player_fixture"].loc[
        results["player_fixture"]["player_id"] == 101
    ].iloc[0]
    assert np.isclose(haaland["expected_minutes"], 62.6)
    assert np.isclose(haaland["play_probability"], 0.9)


# ------------------------------------------- the projection core seam (C#3)


def test_projection_core_is_callable_without_the_live_adapter(monkeypatch):
    """Candidate #3 Phase 1: run_projection_stages consumes ProjectionInputs
    directly -- no network, no acquisition adapter, no file I/O -- and the
    real Minutes Engine precedence still applies. This is the path the
    historical replay will drive in later phases."""
    from fpl_xpts.minutes_contract import MinutesRunInputs

    _stub_pipeline_stages(monkeypatch)  # layer 1/6 + aggregation stubs
    monkeypatch.setattr(pd, "read_csv", _boom("pd.read_csv"))

    inputs = pipeline.ProjectionInputs(
        players=_players(),
        teams=_teams(),
        fixtures_forecast=pd.DataFrame([{"id": 10, "event": 1}]),
        minutes_inputs=MinutesRunInputs(
            manual_inputs=(
                (
                    PlayerMinutesState(
                        gameweek=1,
                        player_id=101,
                        likely_minutes=75,
                        start_probability=0.8,
                        chance_of_playing=0.9,
                    ),
                ),
            ),
            overrides=(MinuteOverrideState(gameweek=1, fixture_in_week=1, player_id=101, minutes=15),),
        ),
        minutes_model_bundle=_fake_bundle(),
    )

    stages = pipeline.run_projection_stages(inputs, config=_offline_config(), include_mc=False)

    haaland = stages["player_fixture"].loc[
        stages["player_fixture"]["player_id"] == 101
    ].iloc[0]
    assert haaland["expected_minutes"] == 15.0  # model -> manual -> override
    assert np.isclose(haaland["start_probability"], 0.8)
    assert haaland["minutes_model_source"] == "manual_player_minutes_input"

    saka = stages["player_fixture"].loc[
        stages["player_fixture"]["player_id"] == 202
    ].iloc[0]
    assert np.isclose(saka["expected_minutes"], 52.0)  # pure model

    assert stages["monte_carlo"].empty
    assert not stages["weekly"].empty


# ------------------------------------------------- boundary resolution rules


def test_states_and_paths_are_mutually_exclusive():
    config = _offline_config()
    with pytest.raises(ManualMinutesError, match="not both"):
        resolve_minutes_run_inputs(
            config, manual_paths=["a.csv"], manual_states=[], override_states=[]
        )
    with pytest.raises(ManualMinutesError, match="not both"):
        resolve_minutes_run_inputs(
            config, override_paths=["o.csv"], manual_states=[], override_states=[]
        )


def test_flat_states_become_a_single_layer():
    state = PlayerMinutesState(player_id=101, likely_minutes=60, start_probability=0.7)
    inputs = resolve_minutes_run_inputs(
        _offline_config(), manual_states=[state], override_states=[]
    )
    assert inputs.manual_inputs == ((state,),)
    assert inputs.overrides == ()


def test_layered_states_preserve_layer_order():
    a = PlayerMinutesState(player_id=101, likely_minutes=60, start_probability=0.7)
    b = PlayerMinutesState(player_id=101, likely_minutes=90, start_probability=1.0)
    inputs = resolve_minutes_run_inputs(
        _offline_config(), manual_states=[[a], [b]], override_states=[]
    )
    assert inputs.manual_inputs == ((a,), (b,))


def test_invalid_json_payload_fails_loudly():
    with pytest.raises(ValidationError):
        MinutesRunInputs(
            manual_inputs=[[{"player_id": 101, "likely_minutes": 120, "start_probability": 0.5}]]
        )


# ----------------------------------------------------------- CLI plumbing


def _run_cli(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["fpl-xpts"] + argv)
    captured = {}

    def _capture_run(**kwargs):
        captured["run"] = kwargs
        return {}

    def _capture_legacy(out, config, **kwargs):
        captured["legacy"] = kwargs
        captured["legacy_config"] = config
        return {}

    monkeypatch.setattr(cli, "run_live_projection", _capture_run)
    monkeypatch.setattr(cli, "write_legacy_outputs", _capture_legacy)
    cli.main()
    return captured


def test_cli_routes_flags_to_run_live_projection(monkeypatch, tmp_path):
    captured = _run_cli(
        monkeypatch,
        [
            "--format", "raw",
            "--out", str(tmp_path),
            "--no-mc",
            "--manual-minutes", "a.csv",
            "--manual-minutes", "b.csv",
            "--minute-overrides", "o.csv",
        ],
    )
    kwargs = captured["run"]
    assert kwargs["manual_minutes_paths"] == [Path("a.csv"), Path("b.csv")]
    assert kwargs["minute_override_paths"] == [Path("o.csv")]
    assert kwargs["include_mc"] is False


def test_cli_routes_flags_to_legacy_export(monkeypatch, tmp_path):
    captured = _run_cli(
        monkeypatch,
        [
            "--out", str(tmp_path),
            "--manual-minutes", "weekly.csv",
            "--minute-overrides", "o.csv",
        ],
    )
    kwargs = captured["legacy"]
    assert kwargs["manual_minutes_paths"] == [Path("weekly.csv")]
    assert kwargs["minute_override_paths"] == [Path("o.csv")]


def test_cli_omitted_flags_fall_back_to_legacy_discovery(monkeypatch, tmp_path):
    captured = _run_cli(monkeypatch, ["--out", str(tmp_path)])
    kwargs = captured["legacy"]
    assert kwargs["manual_minutes_paths"] is None
    assert kwargs["minute_override_paths"] is None


# ------------------------------------------------- --no-minutes-inputs flag


def test_cli_no_minutes_inputs_flag_reaches_the_boundary(monkeypatch, tmp_path):
    """--no-minutes-inputs -> config flag -> empty resolution at the boundary,
    even with legacy manual files sitting on disk."""
    captured = _run_cli(monkeypatch, ["--out", str(tmp_path), "--no-minutes-inputs"])
    config = captured["legacy_config"]
    assert config.use_player_minutes_input_file is False

    monkeypatch.chdir(tmp_path)
    (tmp_path / "player_minutes_inputs.csv").write_text(
        "GW,player_id,start,mins\n1,101,1.0,90\n", encoding="utf-8"
    )
    assert minutes_contract.resolve_manual_minutes_paths(config, None) == []


def test_no_minutes_inputs_run_ignores_manual_files_on_disk(monkeypatch, tmp_path):
    """Full pipeline proof: with the flag set, a manual CSV on disk that would
    pin Haaland to 90 minutes is never read (tripwired), and the heuristic
    baseline survives untouched."""
    _stub_pipeline_stages(monkeypatch)
    monkeypatch.chdir(tmp_path)
    # Discovery anchors to the backend root (not the CWD); re-anchor it to
    # tmp_path so real repo files cannot leak into this hermetic run.
    monkeypatch.setattr(
        minutes_contract,
        "LEGACY_MINUTE_OVERRIDE_FILENAMES",
        tuple(tmp_path / p.name for p in minutes_contract.LEGACY_MINUTE_OVERRIDE_FILENAMES),
    )
    monkeypatch.setattr(
        minutes_contract,
        "LEGACY_EXTRA_MANUAL_MINUTES_PATH",
        tmp_path / minutes_contract.LEGACY_EXTRA_MANUAL_MINUTES_PATH.name,
    )
    (tmp_path / "player_minutes_inputs.csv").write_text(
        "GW,player_id,start,mins\n1,101,1.0,90\n", encoding="utf-8"
    )
    monkeypatch.setattr(pd, "read_csv", _boom("pd.read_csv"))

    results = pipeline.run_live_projection(
        config=_offline_config(
            use_player_minutes_input_file=False,
            minutes_model_path=Path("nonexistent_model.pkl"),
        ),
        include_mc=False,
        write_snapshot=False,
    )

    haaland = results["player_fixture"].loc[
        results["player_fixture"]["player_id"] == 101
    ].iloc[0]
    assert haaland["expected_minutes"] == 45.0
    assert haaland["minutes_model_source"] == "heuristic_baseline"
