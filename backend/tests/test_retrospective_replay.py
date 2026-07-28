import numpy as np
import pandas as pd

import scripts.retrospective_replay as replay
from scripts.retrospective_replay import (
    apply_ml_weighting,
    build_calibration,
    build_metrics,
    pad_missing_mc_sides,
)
import fpl_xpts.rulebook as rulebook_module
from fpl_xpts.rulebook import rulebook_for_season


def test_replay_era_rules_come_from_the_library():
    """Phase 3 migration: the replay resolves era rules via rulebook_for_season
    (the library port of the old get_season_scoring_config) instead of
    mutating engine module globals."""
    # Same era boundaries the old config test pinned, now via the library.
    old = rulebook_for_season("2023-24")
    current_gk = rulebook_for_season("2024-25")
    future = rulebook_for_season("2025-26")

    assert old.goal_points_for("GK") == 6
    assert old.defcon_active is False
    assert old.assist_rules_version == "pre_2025_26_assists"
    assert old.bps_version == "pre_2024_25_bps"
    assert current_gk.goal_points_for("GK") == 10
    assert current_gk.defcon_active is False
    assert current_gk.bps_version == "2024_25_updated_bps"
    assert future.goal_points_for("GK") == 10
    assert future.defcon_active is True
    assert future.assist_rules_version == "2025_26_simplified_assists"
    assert future.bps_version == "2025_26_plus_penalty_goal_equalized"

    # The replay imports the library function -- one source of era truth.
    assert replay.rulebook_for_season is rulebook_module.rulebook_for_season


def test_monkeypatch_mechanism_is_dead():
    """season_scoring_context and its helpers were deleted; the engines no
    longer expose patchable scoring globals."""
    for symbol in (
        "season_scoring_context",
        "get_season_scoring_config",
        "CURRENT_MC_GOAL_BPS",
        "CURRENT_MC_CLEAN_SHEET_BPS",
        "OUTFIELD_GOAL_POINTS",
    ):
        assert not hasattr(replay, symbol), symbol

    from fpl_xpts import monte_carlo, xpts

    for module in (xpts, monte_carlo):
        for name in ("goal_points", "clean_sheet_points", "defcon_threshold", "appearance_points"):
            assert not hasattr(module, name), f"{module.__name__}.{name}"


def test_legacy_orchestration_is_gone():
    """Candidate #3 Phase 4: the replay drives the unified core exclusively."""
    for dead in ("derive_replay_minutes", "apply_replay_minutes", "shadow_core_diff_for_gw"):
        assert not hasattr(replay, dead), dead


def test_historical_adapter_builds_valid_projection_inputs():
    """The historical Data Adapter regression: vaastav-shaped frames become a
    core-ready ProjectionInputs with era rules, empty manual minutes,
    element-summary-shaped history, and prepared minutes features."""
    season_frame = pd.DataFrame(
        {
            "player_id": [101, 101, 202],
            "GW": [1, 2, 1],
            "actual_minutes": [90, 85, 60],
            "starts": [1, 1, 0],
            "match_date": ["2023-08-12", "2023-08-19", "2023-08-12"],
            "team_id": [1, 1, 2],
            "team_key": ["manchester city", "manchester city", "arsenal"],
        }
    )
    players = pd.DataFrame({"id": [101, 202], "web_name": ["H", "S"], "team": [1, 2]})
    fixture_frame = pd.DataFrame([{"id": 10, "event": 3}])
    minutes_features = pd.DataFrame(
        {"GW": [3, 3, 3], "player_id": [101, 101, 202]}  # duplicate on purpose
    )

    inputs = replay.build_historical_inputs(
        players,
        fixture_frame,
        season_frame,
        "2023-24",
        3,
        minutes_bundle=None,
        minutes_features=minutes_features,
    )

    # Era rules travel with the inputs (GK goals were worth 6 in 2023-24).
    assert inputs.rulebook.goal_points_for("GK") == 6
    # No Manual Minutes Inputs existed historically.
    assert inputs.minutes_inputs.manual_inputs == ()
    assert inputs.minutes_inputs.overrides == ()
    # History frames are element-summary shaped (round/kickoff for windows).
    assert set(inputs.history_by_player) == {101, 202}
    assert list(inputs.history_by_player[101].columns) == [
        "round", "minutes", "starts", "kickoff_time",
    ]
    assert len(inputs.history_by_player[101]) == 2  # both pre-GW3 rows
    # Teams dimension carries the columns the minutes features require.
    for column in ("position", "points", "played"):
        assert column in inputs.teams.columns
    # Prepared features: integer event key, deduped keep-first.
    assert inputs.minutes_features["player_id"].tolist() == [101, 202]
    assert str(inputs.minutes_features["event"].dtype) == "int64"


def test_minutes_report_reads_core_resolved_frame():
    """Reporting columns come off the core's player_fixture (DGW deduped)."""
    player_fixture = pd.DataFrame(
        {
            "event": [3, 3, 3],
            "player_id": [101, 101, 202],
            "fixture": [10, 12, 11],
            "play_probability": [0.9, 0.9, 0.8],
            "start_probability": [0.85, 0.85, 0.7],
            "likely_minutes": [80.0, 80.0, 70.0],
            "expected_minutes": [72.0, 72.0, 56.0],
            "minutes_model_source": ["trained_four_output_model"] * 3,
        }
    )
    report = replay._minutes_report_from_player_fixture(player_fixture)

    assert report["player_id"].tolist() == [101, 202]
    assert list(report.columns) == [
        "GW", "player_id", "pred_play_prob", "pred_start_prob",
        "pred_mins_if_play", "replay_expected_minutes", "minutes_model_source",
    ]
    haaland = report.loc[report["player_id"] == 101].iloc[0]
    assert haaland["replay_expected_minutes"] == 72.0
    assert haaland["pred_mins_if_play"] == 80.0


def test_ml_weighting_scales_player_rates_and_caps_multiplier():
    player_fixture = pd.DataFrame(
        [
            {
                "fixture": 1,
                "event": 1,
                "team": 1,
                "player_id": player_id,
                "expected_minutes": 90,
                "xG": 0.20,
                "xA": 0.10,
                "pen_xG": 0.0,
                "team_xg": 1.5,
            }
            for player_id in range(1, 12)
        ]
    )
    ml = pd.DataFrame(
        {
            "GW": [1] * 11,
            "player_id": list(range(1, 12)),
            "ml_xpts": [100.0] + [1.0] * 10,
        }
    )

    weighted = apply_ml_weighting(player_fixture, ml)

    assert np.isclose(weighted.loc[weighted["player_id"] == 1, "ml_weight_multiplier"].iloc[0], 2.5)
    assert weighted["team_xg"].tolist() == [1.5] * 11
    assert weighted.loc[weighted["player_id"] == 1, "xG"].iloc[0] > player_fixture.loc[0, "xG"]
    assert weighted.loc[weighted["player_id"] == 2, "xG"].iloc[0] < player_fixture.loc[1, "xG"]


def test_pad_missing_mc_sides_adds_zero_minute_dummy_opponent():
    player_fixture = pd.DataFrame(
        [
            {
                "fixture": "fx1",
                "event": 1,
                "team": 1,
                "opponent": 2,
                "was_home": True,
                "player_id": 1,
                "web_name": "real",
                "position": "MID",
                "expected_minutes": 90,
                "team_xg": 1.5,
                "team_xa": 1.0,
                "opponent_xg": 1.0,
                "cs_prob": 0.30,
                "xG": 0.2,
                "xA": 0.1,
                "pen_xG": 0.0,
            }
        ]
    )
    fixture_frame = pd.DataFrame(
        [
            {
                "id": "fx1",
                "event": 1,
                "team_h": 1,
                "team_a": 2,
                "home_xg": 1.5,
                "away_xg": 1.0,
                "home_xa": 1.0,
                "away_xa": 0.7,
                "home_cs_prob": 0.30,
                "away_cs_prob": 0.22,
            }
        ]
    )

    padded = pad_missing_mc_sides(player_fixture, fixture_frame)

    assert set(padded["team"]) == {1, 2}
    dummy = padded.loc[padded["player_id"] < 0].iloc[0]
    assert dummy["expected_minutes"] == 0.0
    assert dummy["team_xg"] == 1.0


def test_calibration_contains_probability_and_percentile_rows():
    predictions = pd.DataFrame(
        {
            "actual_points": [0, 2, 6, 10, 15],
            "mc_baseline_P_haul": [0.01, 0.04, 0.08, 0.12, 0.30],
            "mc_baseline_P_return": [0.05, 0.10, 0.25, 0.50, 0.80],
            "mc_baseline_MC_Floor": [0, 1, 1, 2, 3],
            "mc_baseline_MC_Upside": [2, 4, 7, 10, 15],
            "mc_ml_weighted_P_haul": [0.02, 0.05, 0.09, 0.15, 0.35],
            "mc_ml_weighted_P_return": [0.06, 0.11, 0.30, 0.55, 0.85],
            "mc_ml_weighted_MC_Floor": [0, 1, 1, 3, 4],
            "mc_ml_weighted_MC_Upside": [3, 4, 8, 11, 16],
        }
    )

    calibration = build_calibration(predictions)

    assert {"P_haul", "P_return", "MC_Floor", "MC_Upside"}.issubset(set(calibration["stat"]))
    assert {"mc_baseline", "mc_ml_weighted"}.issubset(set(calibration["mc_version"]))
    assert {"mean_predicted", "actual_value", "rows", "gap"}.issubset(calibration.columns)


def test_build_metrics_uses_per_gw_aggregation():
    predictions = pd.DataFrame(
        {
            "season": ["2024-25", "2024-25", "2024-25", "2024-25"],
            "GW": [1, 1, 2, 2],
            "position": ["MID", "FWD", "MID", "FWD"],
            "actual_points": [2, 10, 3, 8],
            "kft_xpts": [3.0, 4.0, 3.0, 5.0],
            "ml_xpts": [2.5, 7.0, 3.5, 6.0],
            "mc_baseline_MC_MeanPts": [2.0, 5.0, 3.0, 5.5],
            "mc_ml_weighted_MC_MeanPts": [2.5, 6.0, 3.5, 6.5],
        }
    )

    metrics = build_metrics(predictions)
    overall_kft = metrics.loc[(metrics["scope"] == "overall") & (metrics["model"] == "KFT rules")].iloc[0]

    assert overall_kft["gw_count"] == 2
    assert overall_kft["rows"] == 4
    assert np.isfinite(overall_kft["overall_mae_mean"])
