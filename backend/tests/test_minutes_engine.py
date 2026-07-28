"""Exhaustive precedence tests for the pure minutes engine (Phase 2).

Table-driven verification of the precedence stack the engine owns
(layers 2-5), layer-by-layer isolation, conflict resolution when multiple
layers touch the same player, and a purity guard proving the engine performs
no disk reads.

Layer 1 (heuristic baseline) is represented by the values pre-loaded on the
player-fixture frame; layer 6 (xPts recompute) is downstream and out of the
engine by design.
"""

import numpy as np
import pandas as pd
import pytest

from fpl_xpts.minutes_contract import MinuteOverrideState, PlayerMinutesState
from fpl_xpts.minutes_engine import (
    apply_manual_minutes_states,
    apply_minute_override_states,
    resolve_minutes,
)
from fpl_xpts.minutes_model import MINUTES_FEATURE_COLUMNS

# ------------------------------------------------------------ fake ML bundle
# Mirrors the pattern in tests/test_minutes.py: constant components produce
#   p_play=0.8, p_start_given_play=0.75 -> start_prob=0.6
#   mins_if_start=80, mins_if_sub=20    -> mins_if_play=65, expected=52.0


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


MODEL_EXPECTED = 52.0
MODEL_START = 0.6
MODEL_SOURCE = "trained_four_output_model"

HEURISTIC_EXPECTED = 45.0
HEURISTIC_START = 0.5
HEURISTIC_SOURCE = "heuristic_baseline"


# ------------------------------------------------------------ frame builders


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


def _teams(city_name="Manchester City"):
    return pd.DataFrame(
        {
            "id": [1, 2],
            "name": [city_name, "Arsenal"],
            "position": [1, 2],
            "points": [10, 8],
            "played": [4, 4],
        }
    )


def _row(player_id, team, event=1, fixture=10, kickoff="2025-08-16T14:00:00Z", **kw):
    row = {
        "player_id": player_id,
        "team": team,
        "opponent": 2 if team == 1 else 1,
        "position": "FWD" if team == 1 else "MID",
        "was_home": True,
        "event": event,
        "fixture": fixture,
        "kickoff_time": kickoff,
        "likely_minutes": HEURISTIC_EXPECTED,
        "start_probability": HEURISTIC_START,
        "play_probability": 0.6,
        "expected_minutes": HEURISTIC_EXPECTED,
        "minutes_model_source": HEURISTIC_SOURCE,
    }
    row.update(kw)
    return row


def _player_fixture(rows=None):
    rows = rows if rows is not None else [_row(101, 1, fixture=10), _row(202, 2, fixture=11)]
    return pd.DataFrame(rows)


# --------------------------------------------------------- contract fixtures

# minute_outcomes(75, start=0.8, play=0.9) -> E[minutes] = 62.6 (proven in
# tests/test_minutes.py); minute_outcomes(90, 1.0, 1.0) -> 90.0.
MANUAL_A = PlayerMinutesState(
    gameweek=1, player_id=101, likely_minutes=75, start_probability=0.8, chance_of_playing=0.9
)
MANUAL_A_EXPECTED = 62.6
MANUAL_B = PlayerMinutesState(
    gameweek=1, player_id=101, likely_minutes=90, start_probability=100, chance_of_playing=100
)
MANUAL_B_EXPECTED = 90.0
MANUAL_SOURCE = "manual_player_minutes_input"

OVERRIDE_15 = MinuteOverrideState(gameweek=1, fixture_in_week=1, player_id=101, minutes=15)


# ------------------------------------------- table-driven precedence ladder


PRECEDENCE_CASES = [
    # (case id, use_bundle, manual layers, overrides,
    #  expected minutes / source / start for player 101,
    #  expected minutes / source for untouched player 202)
    pytest.param(
        False, [], [],
        HEURISTIC_EXPECTED, HEURISTIC_SOURCE, HEURISTIC_START,
        HEURISTIC_EXPECTED, HEURISTIC_SOURCE,
        id="L1-heuristic-survives-empty-engine",
    ),
    pytest.param(
        True, [], [],
        MODEL_EXPECTED, MODEL_SOURCE, MODEL_START,
        MODEL_EXPECTED, MODEL_SOURCE,
        id="L2-model-beats-heuristic",
    ),
    pytest.param(
        True, [[MANUAL_A]], [],
        MANUAL_A_EXPECTED, MANUAL_SOURCE, 0.8,
        MODEL_EXPECTED, MODEL_SOURCE,
        id="L3-manual-beats-model",
    ),
    pytest.param(
        True, [[MANUAL_A], [MANUAL_B]], [],
        MANUAL_B_EXPECTED, MANUAL_SOURCE, 1.0,
        MODEL_EXPECTED, MODEL_SOURCE,
        id="L4-second-manual-file-beats-first",
    ),
    pytest.param(
        True, [[MANUAL_A], [MANUAL_B]], [OVERRIDE_15],
        15.0, MANUAL_SOURCE, 1.0,
        MODEL_EXPECTED, MODEL_SOURCE,
        id="L5-override-beats-all-pins-minutes-only",
    ),
]


@pytest.mark.parametrize(
    "use_bundle,manual_inputs,overrides,exp_minutes,exp_source,exp_start,other_minutes,other_source",
    PRECEDENCE_CASES,
)
def test_precedence_stack(
    use_bundle,
    manual_inputs,
    overrides,
    exp_minutes,
    exp_source,
    exp_start,
    other_minutes,
    other_source,
):
    out = resolve_minutes(
        _player_fixture(),
        _players(),
        _teams(),
        model_bundle=_fake_bundle() if use_bundle else None,
        manual_inputs=manual_inputs,
        overrides=overrides,
    )

    haaland = out.loc[out["player_id"] == 101].iloc[0]
    assert np.isclose(haaland["expected_minutes"], exp_minutes)
    assert haaland["minutes_model_source"] == exp_source
    assert np.isclose(haaland["start_probability"], exp_start)

    saka = out.loc[out["player_id"] == 202].iloc[0]
    assert np.isclose(saka["expected_minutes"], other_minutes)
    assert saka["minutes_model_source"] == other_source


def test_override_preserves_probabilities_from_lower_layers():
    """Layer 5 pins expected_minutes and must not touch anything else."""
    out = resolve_minutes(
        _player_fixture(),
        _players(),
        _teams(),
        model_bundle=None,
        manual_inputs=[[MANUAL_A]],
        overrides=[OVERRIDE_15],
    )
    haaland = out.loc[out["player_id"] == 101].iloc[0]
    assert haaland["expected_minutes"] == 15.0
    assert haaland["likely_minutes"] == 75.0  # manual layer value survives
    assert haaland["start_probability"] == 0.8
    assert haaland["play_probability"] == 0.9
    assert haaland["minutes_model_source"] == MANUAL_SOURCE


# ------------------------------------------------- layer 3/4 targeting rules


def test_manual_gameweek_targeting_hits_only_that_gameweek():
    frame = _player_fixture(
        [_row(101, 1, event=1, fixture=10), _row(101, 1, event=2, fixture=20)]
    )
    state = PlayerMinutesState(
        gameweek=2, player_id=101, likely_minutes=90, start_probability=1.0, chance_of_playing=1.0
    )
    out = apply_manual_minutes_states(frame, _players(), _teams(), [state])
    gw1 = out.loc[out["event"] == 1].iloc[0]
    gw2 = out.loc[out["event"] == 2].iloc[0]
    assert gw1["expected_minutes"] == HEURISTIC_EXPECTED
    assert gw1["minutes_model_source"] == HEURISTIC_SOURCE
    assert gw2["expected_minutes"] == 90.0
    assert gw2["minutes_model_source"] == MANUAL_SOURCE


def test_manual_without_gameweek_applies_to_all_gameweeks():
    frame = _player_fixture(
        [_row(101, 1, event=1, fixture=10), _row(101, 1, event=2, fixture=20)]
    )
    state = PlayerMinutesState(
        gameweek=None, player_id=101, likely_minutes=90, start_probability=1.0, chance_of_playing=1.0
    )
    out = apply_manual_minutes_states(frame, _players(), _teams(), [state])
    assert (out["expected_minutes"] == 90.0).all()
    assert (out["minutes_model_source"] == MANUAL_SOURCE).all()


def test_manual_matches_by_player_key_with_team_alias():
    """player_key matching normalises team aliases: 'Man City' rows match a
    'manchester city' key."""
    state = PlayerMinutesState(
        gameweek=1,
        player_key="erling haaland|manchester city",
        likely_minutes=90,
        start_probability=1.0,
        chance_of_playing=1.0,
    )
    out = apply_manual_minutes_states(
        _player_fixture(), _players(), _teams(city_name="Man City"), [state]
    )
    haaland = out.loc[out["player_id"] == 101].iloc[0]
    saka = out.loc[out["player_id"] == 202].iloc[0]
    assert haaland["expected_minutes"] == 90.0
    assert saka["expected_minutes"] == HEURISTIC_EXPECTED


def test_manual_zero_minutes_zero_start_means_no_show():
    state = PlayerMinutesState(
        gameweek=1, player_id=101, likely_minutes=0, start_probability=0
    )
    out = apply_manual_minutes_states(_player_fixture(), _players(), _teams(), [state])
    haaland = out.loc[out["player_id"] == 101].iloc[0]
    assert haaland["expected_minutes"] == 0.0
    assert haaland["play_probability"] == 0.0


# ------------------------------------------------------ layer 5 targeting


def test_override_targets_second_fixture_of_double_gameweek():
    frame = _player_fixture(
        [
            _row(101, 1, event=1, fixture=10, kickoff="2025-08-16T14:00:00Z"),
            _row(101, 1, event=1, fixture=12, kickoff="2025-08-19T19:00:00Z"),
        ]
    )
    override = MinuteOverrideState(gameweek=1, fixture_in_week=2, player_id=101, minutes=0)
    out = apply_minute_override_states(frame, _players(), _teams(), [override])
    first = out.loc[out["fixture"] == 10].iloc[0]
    second = out.loc[out["fixture"] == 12].iloc[0]
    assert first["expected_minutes"] == HEURISTIC_EXPECTED
    assert second["expected_minutes"] == 0.0


def test_override_matches_by_player_key():
    override = MinuteOverrideState(
        gameweek=1, player_key="erling haaland|manchester city", minutes=30
    )
    out = apply_minute_override_states(_player_fixture(), _players(), _teams(), [override])
    haaland = out.loc[out["player_id"] == 101].iloc[0]
    saka = out.loc[out["player_id"] == 202].iloc[0]
    assert haaland["expected_minutes"] == 30.0
    assert saka["expected_minutes"] == HEURISTIC_EXPECTED


def test_override_with_no_matching_row_changes_nothing():
    frame = _player_fixture()
    override = MinuteOverrideState(gameweek=5, player_id=101, minutes=0)
    out = apply_minute_override_states(frame, _players(), _teams(), [override])
    pd.testing.assert_frame_equal(
        out.reset_index(drop=True), frame.reset_index(drop=True), check_dtype=False
    )


# ------------------------------------------------- L2 feature seam (C#3 P3)


def test_engine_scores_adapter_supplied_features_instead_of_building_live(monkeypatch):
    """When minutes_features is provided, the engine scores THOSE rows (the
    historical-adapter path); live feature building must not run."""
    from fpl_xpts import minutes_model

    def _boom(*args, **kwargs):  # pragma: no cover - guard
        raise AssertionError("build_live_minutes_features must not be called")

    monkeypatch.setattr(minutes_model, "build_live_minutes_features", _boom)

    features = pd.DataFrame(
        {"event": [1, 1], "player_id": [101, 202], "position": ["FWD", "MID"]}
    )
    out = resolve_minutes(
        _player_fixture(),
        _players(),
        _teams(),
        model_bundle=_fake_bundle(),
        minutes_features=features,
    )
    haaland = out.loc[out["player_id"] == 101].iloc[0]
    assert np.isclose(haaland["expected_minutes"], MODEL_EXPECTED)
    assert haaland["minutes_model_source"] == MODEL_SOURCE


def test_engine_builds_live_features_when_none_supplied():
    """minutes_features=None preserves the pre-seam behavior exactly."""
    out = resolve_minutes(
        _player_fixture(),
        _players(),
        _teams(),
        model_bundle=_fake_bundle(),
        minutes_features=None,
    )
    assert np.isclose(
        out.loc[out["player_id"] == 101, "expected_minutes"].iloc[0], MODEL_EXPECTED
    )


# ------------------------------------------------------------------- purity


def test_engine_performs_no_disk_reads(monkeypatch):
    """The whole stack resolves in memory: any read_csv call is a failure."""

    def _boom(*args, **kwargs):  # pragma: no cover - guard
        raise AssertionError("minutes engine touched the file system")

    monkeypatch.setattr(pd, "read_csv", _boom)

    out = resolve_minutes(
        _player_fixture(),
        _players(),
        _teams(),
        model_bundle=_fake_bundle(),
        manual_inputs=[[MANUAL_A], [MANUAL_B]],
        overrides=[OVERRIDE_15],
    )
    assert out.loc[out["player_id"] == 101, "expected_minutes"].iloc[0] == 15.0


def test_engine_with_empty_frame_returns_empty():
    empty = _player_fixture().iloc[0:0]
    out = resolve_minutes(
        empty,
        _players(),
        _teams(),
        model_bundle=None,
        manual_inputs=[[MANUAL_A]],
        overrides=[OVERRIDE_15],
    )
    assert out.empty
    assert list(out.columns) == list(empty.columns)
