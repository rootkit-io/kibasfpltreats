"""Candidate #2 Phase 1: Rulebook equivalence and drift tripwires.

Three jobs:

1. **Pinned equivalence** -- the legacy hardcoded values are restated here as
   test data (independent of any implementation); both the legacy
   ``scoring.py``/``bonus.py`` interfaces and ``CURRENT_RULEBOOK`` must
   reproduce them exactly. This proves the wrap changed nothing.
2. **Drift tripwires** -- the constants still duplicated inside ``xpts.py``
   and ``monte_carlo.py`` (untouched this phase, by directive) must equal
   the Rulebook's values. When Phase 2 deletes the duplicates, these
   tripwires retire with them.
3. **Era-port equivalence** -- ``rulebook_for_season`` must agree with the
   replay script's ``get_season_scoring_config`` for every replay season.
"""

import dataclasses

import pytest

from fpl_xpts.rulebook import CURRENT_RULEBOOK, Rulebook, rulebook_for_season
from fpl_xpts import bonus, scoring

# ------------------------------------------------------- pinned legacy truth

GOAL_POINTS = {"GK": 10, "DEF": 6, "MID": 5, "FWD": 4}
CLEAN_SHEET_POINTS = {"GK": 4, "DEF": 4, "MID": 1, "FWD": 0}
DEFCON_THRESHOLDS = {"GK": None, "DEF": 10, "MID": 12, "FWD": 12}
APPEARANCE_CASES = [(-5, 0.0), (0, 0.0), (1, 1.0), (59, 1.0), (60, 2.0), (90, 2.0)]
BONUS_WEIGHTS = {
    "BONUS_PER_GOAL": 0.85,
    "BONUS_PER_ASSIST": 0.40,
    "BONUS_CS_GK_DEF": 0.25,
    "BONUS_PER_SAVE3": 0.20,
    "BONUS_PER_DEFCON": 0.15,
}


@pytest.mark.parametrize("position", ["GK", "DEF", "MID", "FWD", "UNKNOWN"])
def test_goal_and_cs_points_match_pinned_values(position):
    expected_goal = GOAL_POINTS.get(position, 0)
    expected_cs = CLEAN_SHEET_POINTS.get(position, 0)

    assert scoring.goal_points(position) == expected_goal
    assert CURRENT_RULEBOOK.goal_points_for(position) == expected_goal
    assert scoring.clean_sheet_points(position) == expected_cs
    assert CURRENT_RULEBOOK.clean_sheet_points_for(position) == expected_cs


@pytest.mark.parametrize("position", ["GK", "DEF", "MID", "FWD", "UNKNOWN"])
def test_defcon_thresholds_match_pinned_values(position):
    expected = DEFCON_THRESHOLDS.get(position)
    assert scoring.defcon_threshold(position) == expected
    assert CURRENT_RULEBOOK.defcon_threshold_for(position) == expected


@pytest.mark.parametrize("minutes,expected", APPEARANCE_CASES)
def test_appearance_points_match_pinned_values(minutes, expected):
    assert scoring.appearance_points(minutes) == expected
    assert CURRENT_RULEBOOK.appearance_points_for(minutes) == expected


def test_bonus_weights_match_pinned_values():
    for name, expected in BONUS_WEIGHTS.items():
        assert getattr(bonus, name) == expected, name
    assert CURRENT_RULEBOOK.bonus_per_goal == BONUS_WEIGHTS["BONUS_PER_GOAL"]
    assert CURRENT_RULEBOOK.bonus_per_assist == BONUS_WEIGHTS["BONUS_PER_ASSIST"]
    assert CURRENT_RULEBOOK.bonus_cs_gk_def == BONUS_WEIGHTS["BONUS_CS_GK_DEF"]
    assert CURRENT_RULEBOOK.bonus_per_save3 == BONUS_WEIGHTS["BONUS_PER_SAVE3"]
    assert CURRENT_RULEBOOK.bonus_per_defcon == BONUS_WEIGHTS["BONUS_PER_DEFCON"]


# --------------------------------------------------------- drift tripwires
# Phase 2 deleted the duplicated constants inside xpts.py and monte_carlo.py
# (they now read the injected rulebook), so their tripwires retired with
# them. The replay script is untouched until Phase 3, so its hand-maintained
# BPS mirror keeps its tripwire.


def test_engine_duplicated_constants_are_gone():
    from fpl_xpts import monte_carlo, xpts

    for module in (xpts, monte_carlo):
        assert not hasattr(module, "PENALTY_XG_PER_ATTEMPT")
        assert not hasattr(module, "PENALTY_MISS_POINTS")
    assert not hasattr(xpts, "MAX_TEAM_PENALTY_XG")


def test_engine_entry_points_accept_a_rulebook():
    import inspect

    from fpl_xpts.backtest import (
        add_production_formula_predictions,
        apply_production_formula_by_season,
        run_holdout_backtest,
        sweep_form_weight,
        write_backtest_outputs,
    )
    from fpl_xpts.monte_carlo import simulate_player_week
    from fpl_xpts.xpts import build_player_fixture_forecast, recompute_player_fixture_components

    # Live engines and the single-frame primitive default to the current rules.
    for fn in (
        build_player_fixture_forecast,
        recompute_player_fixture_components,
        simulate_player_week,
        add_production_formula_predictions,
    ):
        parameter = inspect.signature(fn).parameters.get("rulebook")
        assert parameter is not None, fn.__name__
        assert parameter.default is CURRENT_RULEBOOK, fn.__name__

    # Season-looping backtest functions default to None = era-aware
    # (rulebook_for_season resolved per season group) since Phase 4.
    for fn in (
        apply_production_formula_by_season,
        run_holdout_backtest,
        sweep_form_weight,
        write_backtest_outputs,
    ):
        parameter = inspect.signature(fn).parameters.get("rulebook")
        assert parameter is not None, fn.__name__
        assert parameter.default is None, fn.__name__


# ------------------------------------------------------ era-rule pins
# Phase 3 note: originally these compared rulebook_for_season against the
# replay script's get_season_scoring_config; that function (and the replay's
# BPS mirror constants) were deleted once the library became the single
# source of era truth, so the era expectations are pinned directly here.

ERA_EXPECTATIONS = {
    # season: (gk_goal_points, defcon_active, assist_rules_version, bps_version)
    "2019-20": (6, False, "pre_2025_26_assists", "pre_2024_25_bps"),
    "2020-21": (6, False, "pre_2025_26_assists", "pre_2024_25_bps"),
    "2021-22": (6, False, "pre_2025_26_assists", "pre_2024_25_bps"),
    "2022-23": (6, False, "pre_2025_26_assists", "pre_2024_25_bps"),
    "2023-24": (6, False, "pre_2025_26_assists", "pre_2024_25_bps"),
    "2024-25": (10, False, "pre_2025_26_assists", "2024_25_updated_bps"),
    "2025-26": (10, True, "2025_26_simplified_assists", "2025_26_plus_penalty_goal_equalized"),
}


@pytest.mark.parametrize("season", sorted(ERA_EXPECTATIONS))
def test_rulebook_for_season_matches_pinned_era_rules(season):
    gk_points, defcon_active, assist_version, bps_version = ERA_EXPECTATIONS[season]
    book = rulebook_for_season(season)

    assert book.goal_points_for("GK") == gk_points
    assert book.defcon_active == defcon_active
    assert book.assist_rules_version == assist_version
    assert book.bps_version == bps_version

    if defcon_active:
        assert book.defcon_threshold_for("DEF") == 10
        assert book.defcon_threshold_for("MID") == 12
    else:
        assert book.defcon_threshold_for("DEF") is None
        assert book.defcon_threshold_for("MID") is None
    # Outfield goal points never varied across eras.
    assert book.goal_points_for("DEF") == 6
    assert book.goal_points_for("MID") == 5
    assert book.goal_points_for("FWD") == 4


def test_specific_era_boundaries():
    assert rulebook_for_season("2023-24").goal_points_for("GK") == 6
    assert rulebook_for_season("2024-25").goal_points_for("GK") == 10
    assert rulebook_for_season("2024-25").defcon_active is False
    assert rulebook_for_season("2025-26").defcon_active is True


def test_current_season_rulebook_equals_current_rulebook():
    assert rulebook_for_season("2025-26") == CURRENT_RULEBOOK


def test_unsupported_season_label_raises():
    with pytest.raises(ValueError, match="unsupported season label"):
        rulebook_for_season("not-a-season")


# ---------------------------------------------------------------- frozenness


def test_rulebook_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        CURRENT_RULEBOOK.penalty_miss_points = 0.0  # type: ignore[misc]


def test_rulebook_mappings_are_read_only():
    with pytest.raises(TypeError):
        CURRENT_RULEBOOK.goal_points["GK"] = 99  # type: ignore[index]
    with pytest.raises(TypeError):
        CURRENT_RULEBOOK.mc_goal_bps["FWD"] = 0  # type: ignore[index]
