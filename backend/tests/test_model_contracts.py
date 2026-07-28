import numpy as np
import pandas as pd

from fpl_xpts.legacy_export import MC_WEEKLY_COLS, WEEKLY_COLS, _website_player_week_contract
from fpl_xpts.market_odds import _fit_lambdas
from fpl_xpts.monte_carlo import simulate_player_week
from fpl_xpts.projections import load_elevenify_team_projections


def test_market_lambda_fit_prefers_home_favourite():
    home_xg, away_xg, fit_error = _fit_lambdas(
        {"home": 0.50, "draw": 0.25, "away": 0.25},
        over_prob=0.52,
        fallback_home_xg=1.5,
        fallback_away_xg=1.0,
    )
    assert home_xg > away_xg
    assert fit_error < 0.01


def test_monte_carlo_brackets_sum_to_one_on_synthetic_fixture():
    rows = []
    for team, opponent in [(1, 2), (2, 1)]:
        for i, position in enumerate(["GK", "DEF", "MID", "FWD"]):
            player_id = team * 10 + i
            rows.append(
                {
                    "fixture": 1,
                    "event": 1,
                    "team": team,
                    "opponent": opponent,
                    "player_id": player_id,
                    "web_name": f"p{player_id}",
                    "position": position,
                    "expected_minutes": 90,
                    "team_xg": 1.4 if team == 1 else 1.1,
                    "team_xa": 1.0 if team == 1 else 0.8,
                    "opponent_xg": 1.1 if team == 1 else 1.4,
                    "cs_prob": 0.33,
                    "xG": 0.2 if position != "GK" else 0.0,
                    "xA": 0.1 if position != "GK" else 0.0,
                    "xPts": 3.0,
                    "defcon90": 8.0,
                    "saves90": 3.0,
                    "yc_rate": 0.1,
                    "rc_rate": 0.01,
                    "pen_xG": 0.05 if position == "FWD" else 0.0,
                    "penalty_share": 1.0 if position == "FWD" else 0.0,
                }
            )
    weekly = simulate_player_week(pd.DataFrame(rows), n_sim=300, seed=3)
    bracket_cols = ["Bracket_LE_2", "Bracket_3_to_6", "Bracket_7_to_9", "Bracket_10_to_14", "Bracket_15_plus"]
    assert np.allclose(weekly[bracket_cols].sum(axis=1), 1.0)
    assert np.allclose(weekly["P_haul"], weekly["Bracket_10_to_14"] + weekly["Bracket_15_plus"])
    assert ((weekly["P_return"] >= 0.0) & (weekly["P_return"] <= 1.0)).all()


def test_elevenify_projection_sheet_parser(tmp_path):
    csv_path = tmp_path / "elevenify.com 25_26 Subscriber Season Long Data - Sheet1.csv"
    csv_path.write_text(
        "\n".join(
            [
                ",,,",
                ",,Projected Goals,",
                ",,Team,GW37,GW38",
                ",,Man City,1.91,2.21",
                ",,Nott'm Forest,1.06,1.37",
                ",,,",
                ",,Projected Clean Sheets,",
                ",,Team,GW37,GW38",
                ",,Man City,0.25,0.37",
                ",,Nott'm Forest,18%,24%",
            ]
        ),
        encoding="utf-8",
    )
    parsed = load_elevenify_team_projections(csv_path)
    city_gw38 = parsed.loc[(parsed["team_key"] == "manchester city") & (parsed["GW"] == 38)].iloc[0]
    forest_gw37 = parsed.loc[(parsed["team_key"] == "nottingham forest") & (parsed["GW"] == 37)].iloc[0]
    assert np.isclose(city_gw38["projected_goals"], 2.21)
    assert np.isclose(city_gw38["cs_prob"], 0.37)
    assert np.isclose(forest_gw37["cs_prob"], 0.18)


def test_website_player_week_contract_carries_live_fields_and_dgw_context():
    players = pd.DataFrame(
        [
            {
                "id": 1,
                "first_name": "Test",
                "second_name": "Player",
                "web_name": "Test",
                "now_cost": 75,
                "selected_by_percent": "12.3",
                "status": "d",
                "chance_of_playing_this_round": 75,
                "news": "Late test",
            }
        ]
    )
    teams = pd.DataFrame(
        [
            {"id": 10, "name": "Arsenal"},
            {"id": 20, "name": "Chelsea"},
            {"id": 30, "name": "Everton"},
        ]
    )
    player_fixture = pd.DataFrame(
        [
            {
                "event": 1,
                "player_id": 1,
                "team": 10,
                "opponent": 20,
                "position": "MID",
                "fixture": 101,
                "kickoff_time": "2026-01-01",
                "expected_minutes": 70,
                "play_probability": 0.8,
                "start_probability": 0.7,
                "minutes_model_source": "trained_four_output_model",
                "was_home": True,
                "prior_based": True,
                "prior_source": "Bundesliga_2024",
            },
            {
                "event": 1,
                "player_id": 1,
                "team": 10,
                "opponent": 30,
                "position": "MID",
                "fixture": 102,
                "kickoff_time": "2026-01-02",
                "expected_minutes": 60,
                "play_probability": 0.6,
                "start_probability": 0.5,
                "minutes_model_source": "manual_player_minutes_input",
                "was_home": False,
                "prior_based": True,
                "prior_source": "Bundesliga_2024",
            },
        ]
    )
    raw_weekly = pd.DataFrame(
        [
            {
                "event": 1,
                "player_id": 1,
                "team": 10,
                "position": "MID",
                "ml_xpts": 5.5,
                "P_return": 0.4,
                "P_haul": 0.2,
            }
        ]
    )

    contract = _website_player_week_contract(raw_weekly, player_fixture, players, teams)
    row = contract.iloc[0]

    assert np.isclose(row["expected_minutes"], 130.0)
    assert np.isclose(row["pred_play_prob"], 0.7)
    assert np.isclose(row["pred_start_prob"], 0.6)
    assert row["minutes_model_source"] == "trained_four_output_model|manual_player_minutes_input"
    assert row["ml_xpts"] == 5.5
    assert row["P_haul"] == 0.2
    assert row["opponent"] == "Chelsea|Everton"
    assert row["was_home"] == "True|False"
    assert row["now_cost"] == 75
    assert row["chance_of_playing_this_round"] == 75
    assert row["news"] == "Late test"
    assert bool(row["prior_based"])
    assert row["prior_source"] == "Bundesliga_2024"
    for col in [
        "expected_minutes",
        "pred_play_prob",
        "pred_start_prob",
        "minutes_model_source",
        "ml_xpts",
        "P_return",
        "P_haul",
        "opponent",
        "was_home",
        "now_cost",
        "selected_by_percent",
        "status",
        "chance_of_playing_this_round",
        "news",
        "prior_based",
        "prior_source",
    ]:
        assert col in WEEKLY_COLS
        assert col in MC_WEEKLY_COLS
