import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from fpl_xpts.config import AppConfig
from fpl_xpts.ml_features import (
    POSITIONS,
    add_shifted_rolling_features,
    add_understat_team_features,
    build_live_ml_frame,
)
from fpl_xpts.ml_models import predict_with_bundle, save_bundle, team_group_folds
from scripts.train_position_ml_models import _classify_improvement, _per_gw_metric_block


class IdentityPreprocessor:
    def fit_transform(self, frame):
        return np.asarray(frame, dtype=float)

    def transform(self, frame):
        return np.asarray(frame, dtype=float)


class ConstantModel:
    def __init__(self, value):
        self.value = float(value)
        self.feature_importances_ = np.array([1.0])

    def predict(self, matrix):
        return np.full(len(matrix), self.value)


def test_shifted_10gw_rolling_points_excludes_current_gw():
    frame = pd.DataFrame(
        [
            {"season": "2024-25", "GW": 1, "player_id": 1, "actual_points": 2, "actual_minutes": 90, "expected_goals": 0.2, "expected_assists": 0.1, "starts": 1},
            {"season": "2024-25", "GW": 2, "player_id": 1, "actual_points": 20, "actual_minutes": 90, "expected_goals": 5.0, "expected_assists": 5.0, "starts": 1},
        ]
    )

    enriched = add_shifted_rolling_features(frame, windows=(10,))
    gw2 = enriched.loc[enriched["GW"] == 2].iloc[0]

    assert math.isclose(gw2["rolling_points_10gw"], 2.0)
    assert math.isclose(gw2["rolling_xg90_10gw"], 0.2)
    assert math.isclose(gw2["rolling_xa90_10gw"], 0.1)


def test_understat_team_features_are_shifted_to_previous_match(tmp_path):
    stats_dir = tmp_path / "data" / "understat" / "team_stats"
    stats_dir.mkdir(parents=True)
    payload = [
        {
            "season": "2024-25",
            "team": "Arsenal",
            "team_key": "arsenal",
            "history": [
                {"date": "2024-08-01", "xG": 1.0, "xGA": 0.5, "deep": 4, "deep_allowed": 2, "ppda_att": 80, "ppda_def": 10, "ppda_allowed_att": 60, "ppda_allowed_def": 10},
                {"date": "2024-08-08", "xG": 9.0, "xGA": 9.0, "deep": 99, "deep_allowed": 99, "ppda_att": 999, "ppda_def": 9, "ppda_allowed_att": 999, "ppda_allowed_def": 9},
            ],
        },
        {
            "season": "2024-25",
            "team": "Chelsea",
            "team_key": "chelsea",
            "history": [
                {"date": "2024-08-01", "xG": 0.7, "xGA": 1.1, "deep": 3, "deep_allowed": 5, "ppda_att": 90, "ppda_def": 10, "ppda_allowed_att": 70, "ppda_allowed_def": 10},
                {"date": "2024-08-08", "xG": 8.0, "xGA": 8.0, "deep": 88, "deep_allowed": 88, "ppda_att": 888, "ppda_def": 8, "ppda_allowed_att": 888, "ppda_allowed_def": 8},
            ],
        },
    ]
    (stats_dir / "team_stats_2024.json").write_text(json.dumps(payload), encoding="utf-8")
    frame = pd.DataFrame(
        [
            {
                "season": "2024-25",
                "team_key": "arsenal",
                "opponent_team_key": "chelsea",
                "match_date": "2024-08-08",
            }
        ]
    )

    enriched = add_understat_team_features(frame, tmp_path)
    row = enriched.iloc[0]

    assert math.isclose(row["team_understat_xg_3"], 1.0)
    assert math.isclose(row["team_understat_deep_3"], 4.0)
    assert math.isclose(row["opponent_understat_xg_3"], 0.7)
    assert math.isclose(row["opponent_understat_deep_3"], 3.0)


def test_team_group_folds_have_no_team_overlap():
    pytest.importorskip("sklearn")
    pytest.importorskip("xgboost")
    frame = pd.DataFrame(
        {
            "team_key": ["a", "a", "b", "b", "c", "c", "d", "d", "e", "e"],
            "actual_points": [1, 2, 3, 4, 2, 1, 5, 6, 1, 0],
        }
    )

    folds = team_group_folds(frame, n_splits=5)

    assert folds
    for train_idx, valid_idx in folds:
        train_teams = set(frame.iloc[train_idx]["team_key"])
        valid_teams = set(frame.iloc[valid_idx]["team_key"])
        assert train_teams.isdisjoint(valid_teams)


def test_ensemble_prediction_is_simple_average():
    bundle = {
        "position": "MID",
        "feature_columns": ["kft_xpts"],
        "preprocessor": IdentityPreprocessor(),
        "xgb_model": ConstantModel(2.0),
        "rf_model": ConstantModel(4.0),
    }
    frame = pd.DataFrame({"kft_xpts": [1.0, 5.0]})

    predicted = predict_with_bundle(bundle, frame)

    assert np.allclose(predicted["ml_xpts_xgb"], 2.0)
    assert np.allclose(predicted["ml_xpts_rf"], 4.0)
    assert np.allclose(predicted["ml_xpts"], 3.0)


def test_ensemble_prediction_is_composed_with_play_probability():
    bundle = {
        "position": "MID",
        "feature_columns": ["kft_xpts"],
        "preprocessor": IdentityPreprocessor(),
        "xgb_model": ConstantModel(2.0),
        "rf_model": ConstantModel(4.0),
    }
    frame = pd.DataFrame({"kft_xpts": [1.0], "pred_play_prob": [0.25]})

    predicted = predict_with_bundle(bundle, frame)

    assert np.isclose(predicted.iloc[0]["ml_xpts_pre_minutes"], 3.0)
    assert np.isclose(predicted.iloc[0]["ml_xpts"], 0.75)


def test_model_bundles_save_reload_and_predict_for_all_positions(tmp_path):
    pytest.importorskip("joblib")
    pytest.importorskip("sklearn")
    pytest.importorskip("xgboost")
    import joblib

    for position in POSITIONS:
        bundle = {
            "position": position,
            "feature_columns": ["kft_xpts"],
            "numeric_columns": ["kft_xpts"],
            "categorical_columns": [],
            "preprocessor": IdentityPreprocessor(),
            "xgb_model": ConstantModel(1.0),
            "rf_model": ConstantModel(3.0),
        }
        path = tmp_path / f"{position.lower()}_model.pkl"
        save_bundle(bundle, path)
        loaded = joblib.load(path)
        predicted = predict_with_bundle(loaded, pd.DataFrame({"kft_xpts": [2.0]}))
        assert np.isclose(predicted.iloc[0]["ml_xpts"], 2.0)


def test_default_config_leaves_ml_predictions_disabled():
    assert AppConfig().use_ml_predictions is False


def test_live_ml_frame_uses_real_fpl_availability_not_historical_unknown():
    weekly = pd.DataFrame(
        [{"event": 1, "player_id": 10, "web_name": "Live Player", "position": "MID", "team": 1, "expected_minutes": 70, "xG": 0.2, "xA": 0.1, "xPts": 4.0}]
    )
    players = pd.DataFrame(
        [
            {
                "id": 10,
                "element_type": 3,
                "chance_of_playing_this_round": 75,
                "chance_of_playing_next_round": 100,
                "status": "d",
            }
        ]
    )
    player_fixture = pd.DataFrame(
        [{"event": 1, "player_id": 10, "fixture": 1, "opponent": 2, "was_home": True, "team_xg": 1.5, "opponent_xg": 1.0, "cs_prob": 0.35}]
    )
    teams = pd.DataFrame([{"id": 1, "name": "Arsenal"}, {"id": 2, "name": "Chelsea"}])

    live = build_live_ml_frame(weekly, players, player_fixture, teams)
    row = live.iloc[0]

    assert row["chance_of_playing_this_round"] == 75
    assert row["chance_of_playing_next_round"] == 100
    assert row["fpl_status"] == "d"
    assert row["availability_category"] == "chance_75"
    assert row["availability_category"] != "historical_unknown"


def test_retrain_metrics_average_gameweek_level_values():
    frame = pd.DataFrame(
        {
            "GW": [1, 1, 2, 2],
            "actual_points": [0.0, 10.0, 2.0, 4.0],
            "prediction": [1.0, 8.0, 2.0, 6.0],
        }
    )

    metrics = _per_gw_metric_block(frame, "prediction")

    assert metrics["gw_count"] == 2
    assert metrics["rows"] == 4
    assert np.isclose(metrics["mae"], 1.25)


def test_retrain_improvement_classification_requires_better_mae_and_spearman():
    clear = pd.Series({"delta_spearman": 0.03, "delta_mae": -0.05})
    marginal = pd.Series({"delta_spearman": 0.01, "delta_mae": -0.01})
    worse = pd.Series({"delta_spearman": -0.01, "delta_mae": -0.05})

    assert _classify_improvement(clear) == "clear improvement"
    assert _classify_improvement(marginal) == "marginal improvement"
    assert _classify_improvement(worse) == "made things worse"
