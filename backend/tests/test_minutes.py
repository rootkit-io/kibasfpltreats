import numpy as np
import pandas as pd

from fpl_xpts.minutes import estimate_expected_minutes, minute_outcomes
from fpl_xpts.minutes_model import MINUTES_FEATURE_COLUMNS, score_minutes


def test_minute_outcomes_preserve_high_expected_minutes():
    vals, probs = minute_outcomes(90)
    assert np.isclose(probs.sum(), 1.0)
    assert np.dot(vals, probs) >= 88


def test_minute_outcomes_handles_zero():
    vals, probs = minute_outcomes(0)
    assert vals.tolist() == [0]
    assert probs.tolist() == [1.0]


def test_expected_minutes_uses_start_rate_and_minutes_per_appearance():
    player = pd.Series({"chance_of_playing_next_round": None})
    history = pd.DataFrame({"minutes": [90, 80, 20, 0], "starts": [1, 1, 0, 0]})
    start_rate = 2 / 3
    avg_mins = 190 / 3
    assert np.isclose(estimate_expected_minutes(player, history), avg_mins * (0.85 + 0.15 * start_rate))


def test_manual_minute_outcomes_use_play_and_start_probabilities():
    vals, probs = minute_outcomes(75, start_probability=0.8, play_probability=0.9)
    assert vals.tolist() == [0, 26, 75]
    assert np.allclose(probs, [0.1, 0.1, 0.8])
    assert np.isclose(np.dot(vals, probs), 62.6)


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


def test_four_output_minutes_scorer_composes_conditional_outputs():
    bundle = {
        "play_classifier": _component(_ConstantClassifier(0.8), classifier=True),
        "start_classifier": _component(_ConstantClassifier(0.75), classifier=True),
        "mins_if_start_regressor": _component(_ConstantRegressor(80.0)),
        "mins_if_sub_regressor": _component(_ConstantRegressor(20.0)),
    }

    scored = score_minutes(pd.DataFrame({"player_id": [1]}), bundle).iloc[0]

    assert np.isclose(scored["pred_play_prob"], 0.8)
    assert np.isclose(scored["pred_start_given_play_prob"], 0.75)
    assert np.isclose(scored["pred_start_prob"], 0.6)
    assert np.isclose(scored["pred_mins_if_play"], 65.0)
    assert np.isclose(scored["expected_minutes"], 52.0)
