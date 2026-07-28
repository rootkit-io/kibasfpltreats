import math
import json

import pandas as pd

from fpl_xpts.historical_validation import (
    ODDS_SEASONS,
    VAASTAV_SEASONS,
    build_cached_fpl_merged_gw,
    build_understat_match_table,
    build_player_gw_spine,
    extract_understat_player_stats,
    extract_understat_team_stats,
    load_odds_match_features,
    score_training_dataset,
)


def test_historical_season_registries_include_2025_26():
    assert "2526" in ODDS_SEASONS
    assert "2025-26" in VAASTAV_SEASONS


def test_cached_fpl_history_builds_vaastav_compatible_rows(tmp_path):
    cache = tmp_path / "data" / "fpl_history"
    cache.mkdir(parents=True)
    events = [{"id": gw, "finished": True, "data_checked": True} for gw in range(1, 39)]
    bootstrap = {
        "events": events,
        "elements": [{"id": 1, "first_name": "Test", "second_name": "Player", "web_name": "Player", "element_type": 3}],
        "teams": [{"id": 1, "name": "Home"}, {"id": 2, "name": "Away"}],
        "element_types": [{"id": 3, "singular_name_short": "MID"}],
    }
    fixtures = [{"id": 10, "team_h": 1, "team_a": 2}]
    history = {
        "history": [
            {
                "element": 1,
                "fixture": 10,
                "round": 38,
                "was_home": True,
                "minutes": 90,
                "total_points": 4,
                "defensive_contribution": 12,
            }
        ]
    }
    (cache / "bootstrap-static.json").write_text(json.dumps(bootstrap), encoding="utf-8")
    (cache / "fixtures.json").write_text(json.dumps(fixtures), encoding="utf-8")
    (cache / "player_1_history.json").write_text(json.dumps(history), encoding="utf-8")

    built = build_cached_fpl_merged_gw(tmp_path)

    assert len(built) == 1
    assert built.iloc[0]["GW"] == 38
    assert built.iloc[0]["name"] == "Test Player"
    assert built.iloc[0]["position"] == "MID"
    assert built.iloc[0]["team"] == "Home"
    assert built.iloc[0]["defensive_contribution"] == 12


def test_football_data_parser_prefers_closing_odds(tmp_path):
    odds_dir = tmp_path / "data" / "odds_historical"
    odds_dir.mkdir(parents=True)
    (odds_dir / "E0_1415.csv").write_text(
        ",".join(
            [
                "Div", "Date", "HomeTeam", "AwayTeam",
                "B365H", "B365D", "B365A",
                "AvgCH", "AvgCD", "AvgCA",
                "AvgC>2.5", "AvgC<2.5",
            ]
        )
        + "\n"
        + "E0,16/08/2014,Arsenal,Crystal Palace,6.00,4.00,1.50,1.50,4.00,6.00,1.90,1.90\n",
        encoding="utf-8",
    )

    parsed = load_odds_match_features(tmp_path)
    row = parsed.iloc[0]

    assert bool(row["closing_odds_used"])
    assert row["home_lambda_odds"] > row["away_lambda_odds"]


def test_understat_team_and_player_extractors():
    payload = {
        "teams": {
            "1": {
                "id": "1",
                "title": "Arsenal",
                "history": [
                    {
                        "date": "2014-08-16 12:30:00",
                        "h_a": "h",
                        "xG": 2.0,
                        "xGA": 0.5,
                        "npxG": 1.2,
                        "npxGA": 0.5,
                        "deep": 8,
                        "deep_allowed": 2,
                        "ppda": {"att": 120, "def": 10},
                        "ppda_allowed": {"att": 80, "def": 8},
                        "scored": 2,
                        "missed": 0,
                        "wins": 1,
                        "draws": 0,
                        "loses": 0,
                        "pts": 3,
                        "result": "w",
                    }
                ],
            }
        },
        "players": [
            {
                "id": "10",
                "player_name": "Test Player",
                "team_title": "Arsenal",
                "position": "M",
                "time": "180",
                "xG": "1.0",
                "xA": "0.5",
                "npxG": "0.8",
                "shots": "5",
                "key_passes": "3",
                "goals": "1",
                "assists": "1",
            }
        ],
    }

    team = extract_understat_team_stats(payload, 2014)[0]
    player = extract_understat_player_stats(payload, 2014)[0]

    assert team["team_key"] == "arsenal"
    assert team["xG"] == 2.0
    assert team["deep"] == 8.0
    assert math.isclose(team["ppda"], 12.0)
    assert player["understat_player_id"] == "10"
    assert math.isclose(player["xG90"], 0.5)
    assert math.isclose(player["xA90"], 0.25)


def test_rolling_features_are_shifted_to_pre_gw():
    raw = pd.DataFrame(
        [
            {
                "season": "2024-25",
                "element": 1,
                "GW": 1,
                "name": "Leak Check",
                "position": "MID",
                "team": "Arsenal",
                "minutes": 90,
                "total_points": 2,
                "expected_goals": 0.2,
                "expected_assists": 0.1,
                "expected_goals_conceded": 1.0,
                "goals_scored": 0,
                "assists": 0,
                "bonus": 0,
                "defensive_contribution": 8,
                "saves": 0,
                "yellow_cards": 0,
                "red_cards": 0,
                "starts": 1,
                "selected": 1,
                "value": 50,
            },
            {
                "season": "2024-25",
                "element": 1,
                "GW": 2,
                "name": "Leak Check",
                "position": "MID",
                "team": "Arsenal",
                "minutes": 90,
                "total_points": 20,
                "expected_goals": 9.0,
                "expected_assists": 9.0,
                "expected_goals_conceded": 1.0,
                "goals_scored": 4,
                "assists": 4,
                "bonus": 3,
                "defensive_contribution": 12,
                "saves": 0,
                "yellow_cards": 0,
                "red_cards": 0,
                "starts": 1,
                "selected": 1,
                "value": 50,
            },
        ]
    )

    spine = build_player_gw_spine(raw)
    gw2 = spine.loc[spine["GW"] == 2].iloc[0]

    assert math.isclose(gw2["rolling_xg90_3gw"], 0.2)
    assert math.isclose(gw2["rolling_xa90_3gw"], 0.1)
    assert math.isclose(gw2["rolling_points_3gw"], 2.0)
    assert math.isclose(gw2["season_defcon90_pre_gw"], 8.0)


def test_fuzzy_understat_matching_accepts_close_same_team_name():
    vaastav = pd.DataFrame(
        [
            {
                "season": "2024-25",
                "element": 1,
                "player_id": 1,
                "player_name": "Mohamed Salah",
                "player_key": "mohamed salah",
                "team": "Liverpool",
                "team_key": "liverpool",
            }
        ]
    )
    understat = pd.DataFrame(
        [
            {
                "season": "2024-25",
                "team_key": "liverpool",
                "player_key": "mohammed salah",
                "understat_player_name": "Mohammed Salah",
                "understat_player_id": "123",
                "xg90_understat": 0.3,
                "xa90_understat": 0.2,
                "understat_minutes": 1000,
            }
        ]
    )

    matches, rejected = build_understat_match_table(vaastav, understat, threshold=0.85)

    assert rejected.empty
    assert matches.iloc[0]["understat_player_id"] == "123"
    assert matches.iloc[0]["understat_match_method"] == "fuzzy"


def test_pre_2022_missing_xg_can_use_understat_direct_rates():
    raw = pd.DataFrame(
        [
            {
                "season": "2021-22",
                "element": 1,
                "GW": 1,
                "name": "No XG",
                "position": "MID",
                "team": "Arsenal",
                "minutes": 90,
                "total_points": 2,
                "goals_scored": 0,
                "assists": 0,
                "bonus": 0,
                "saves": 0,
                "yellow_cards": 0,
                "red_cards": 0,
                "starts": 1,
                "selected": 1,
                "value": 50,
            },
            {
                "season": "2021-22",
                "element": 1,
                "GW": 2,
                "name": "No XG",
                "position": "MID",
                "team": "Arsenal",
                "minutes": 90,
                "total_points": 3,
                "goals_scored": 0,
                "assists": 0,
                "bonus": 0,
                "saves": 0,
                "yellow_cards": 0,
                "red_cards": 0,
                "starts": 1,
                "selected": 1,
                "value": 50,
            },
        ]
    )

    spine = build_player_gw_spine(raw)
    gw2 = spine.loc[spine["GW"] == 2].iloc[0]

    assert pd.isna(gw2["rolling_xg90_3gw"])
    assert pd.isna(gw2["rolling_xa90_3gw"])


def test_historical_scoring_uses_xpts_engine_on_synthetic_fixture():
    dataset = pd.DataFrame(
        [
            {
                "season": "2024-25",
                "GW": 5,
                "player_id": 1,
                "player_name": "Home Mid",
                "position": "MID",
                "team_id": 1,
                "actual_points": 5,
                "complete_features": True,
                "cumulative_season_minutes": 360,
                "cumulative_starts": 4,
                "cumulative_appearances": 4,
                "season_xg90_pre_gw": 0.25,
                "season_xa90_pre_gw": 0.15,
                "season_saves90_pre_gw": 0,
                "season_yellow_cards_pre_gw": 1,
                "season_red_cards_pre_gw": 0,
                "rolling_minutes_3gw": 270,
                "rolling_xg90_3gw": 0.3,
                "rolling_xa90_3gw": 0.2,
                "xg90_understat": 0.35,
                "xa90_understat": 0.2,
                "npxg90_understat": 0.3,
                "understat_minutes": 900,
            },
            {
                "season": "2024-25",
                "GW": 5,
                "player_id": 2,
                "player_name": "Away Mid",
                "position": "MID",
                "team_id": 2,
                "actual_points": 2,
                "complete_features": True,
                "cumulative_season_minutes": 360,
                "cumulative_starts": 4,
                "cumulative_appearances": 4,
                "season_xg90_pre_gw": 0.2,
                "season_xa90_pre_gw": 0.1,
                "season_saves90_pre_gw": 0,
                "season_yellow_cards_pre_gw": 1,
                "season_red_cards_pre_gw": 0,
                "rolling_minutes_3gw": 270,
                "rolling_xg90_3gw": 0.2,
                "rolling_xa90_3gw": 0.1,
                "xg90_understat": 0.2,
                "xa90_understat": 0.1,
                "npxg90_understat": 0.2,
                "understat_minutes": 900,
            },
        ]
    )
    fixtures = pd.DataFrame(
        [
            {
                "season": "2024-25",
                "GW": 5,
                "id": "fx1",
                "fixture": 1,
                "event": 5,
                "kickoff_time": "2024-09-01T14:00:00Z",
                "team_h": 1,
                "team_a": 2,
                "home_xg": 1.6,
                "away_xg": 1.1,
                "home_cs_prob": 0.33,
                "away_cs_prob": 0.20,
            }
        ]
    )

    scored = score_training_dataset(dataset, fixtures)

    assert scored["kft_xpts"].notna().all()
    assert (scored["kft_xpts"] > 0).all()
