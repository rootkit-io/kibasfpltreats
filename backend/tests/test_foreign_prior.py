import json
from urllib.error import URLError

import numpy as np
import pandas as pd

import fpl_xpts.features as features
from fpl_xpts.config import LEAGUE_DIFFICULTY_FACTORS
from fpl_xpts.features import build_foreign_prior_rate, build_player_rates
from fpl_xpts.shot_profiles import fetch_foreign_understat_player_seasons


def _write_league(path, player_name, minutes, xg, xa):
    path.write_text(
        json.dumps(
            {
                "players": [
                    {
                        "player_name": player_name,
                        "time": str(minutes),
                        "xG": str(xg),
                        "xA": str(xa),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


def test_foreign_prior_uses_most_recent_season_and_shrinks(tmp_path):
    _write_league(tmp_path / "Bundesliga_2023.json", "Test Player", 900, 4.0, 2.0)
    _write_league(tmp_path / "Bundesliga_2024.json", "Test Player", 900, 9.0, 4.5)

    result = build_foreign_prior_rate(
        "Tést Player",
        "MID",
        positional_baseline={"xg90": 0.1, "xa90": 0.1},
        data_dir=tmp_path,
    )

    factor = LEAGUE_DIFFICULTY_FACTORS["Bundesliga"]
    assert result["prior_source"] == "Bundesliga_2024"
    assert np.isclose(result["foreign_xg90"], 0.9)
    assert np.isclose(result["foreign_xa90"], 0.45)
    assert np.isclose(result["adjusted_xg90"], 0.9 * factor)
    assert np.isclose(result["adjusted_xa90"], 0.45 * factor)
    assert np.isclose(result["xg90"], ((0.9 * factor * 900) + (0.1 * 900)) / 1800)
    assert np.isclose(result["xa90"], ((0.45 * factor * 900) + (0.1 * 900)) / 1800)


def test_foreign_prior_falls_back_to_position_default(tmp_path):
    result = build_foreign_prior_rate(
        "Missing Player",
        "FWD",
        positional_baseline={"xg90": 0.3, "xa90": 0.12},
        data_dir=tmp_path,
    )

    assert result["prior_source"] == "positional_default"
    assert result["xg90"] == 0.3
    assert result["xa90"] == 0.12


def test_established_player_rate_is_unchanged_and_skips_foreign_lookup(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("established players must not use the foreign prior")

    monkeypatch.setattr(features, "build_foreign_prior_rate", fail_if_called)
    players = pd.DataFrame(
        [
            {
                "id": 1,
                "first_name": "Established",
                "second_name": "Player",
                "element_type": 3,
                "minutes": 900,
                "expected_goals_per_90": 0.30,
                "expected_assists_per_90": 0.20,
            },
            {
                "id": 2,
                "first_name": "Established",
                "second_name": "Peer",
                "element_type": 3,
                "minutes": 1800,
                "expected_goals_per_90": 0.10,
                "expected_assists_per_90": 0.40,
            },
        ]
    )

    rates = build_player_rates(players)
    player = rates.loc[rates["id"] == 1].iloc[0]

    assert np.isclose(player["expected_goals_per_90_shrunk"], 0.25)
    assert np.isclose(player["expected_assists_per_90_shrunk"], 0.25)
    assert not bool(player["prior_based"])
    assert player["prior_source"] == "observed_pl"


def test_prior_pl_history_prevents_reset_established_player_from_using_foreign_lookup(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("prior PL history must preserve the established branch")

    monkeypatch.setattr(features, "build_foreign_prior_rate", fail_if_called)
    players = pd.DataFrame(
        [
            {
                "id": 1,
                "first_name": "Reset",
                "second_name": "Established",
                "element_type": 3,
                "minutes": 0,
                "pl_history_minutes": 1200,
                "expected_goals_per_90": 0.30,
                "expected_assists_per_90": 0.20,
            }
        ]
    )

    player = build_player_rates(players).iloc[0]

    assert not bool(player["prior_based"])
    assert player["prior_source"] == "observed_pl"
    assert player["pl_history_minutes"] == 1200


def test_new_foreign_player_uses_prior_without_changing_minutes(monkeypatch):
    monkeypatch.setattr(
        features,
        "build_foreign_prior_rate",
        lambda *args, **kwargs: {
            "xg90": 0.22,
            "xa90": 0.16,
            "prior_source": "La_liga_2024",
        },
    )
    players = pd.DataFrame(
        [
            {
                "id": 1,
                "first_name": "New",
                "second_name": "Signing",
                "element_type": 3,
                "minutes": 0,
                "expected_goals_per_90": 0.0,
                "expected_assists_per_90": 0.0,
            },
            {
                "id": 2,
                "first_name": "Established",
                "second_name": "Peer",
                "element_type": 3,
                "minutes": 900,
                "expected_goals_per_90": 0.10,
                "expected_assists_per_90": 0.10,
            },
        ]
    )

    rates = build_player_rates(players)
    player = rates.loc[rates["id"] == 1].iloc[0]

    assert np.isclose(player["expected_goals_per_90_shrunk"], 0.22)
    assert np.isclose(player["expected_assists_per_90_shrunk"], 0.16)
    assert player["minutes"] == 0
    assert bool(player["prior_based"])
    assert player["prior_source"] == "La_liga_2024"


def test_foreign_fetch_logs_and_continues_after_one_league_failure(tmp_path, monkeypatch, caplog):
    def fake_request(url, referer, cache_path=None, refresh=False):
        if "Bundesliga" in url:
            raise URLError("test failure")
        return {"players": [{"id": 1}, {"id": 2}]}

    monkeypatch.setattr("fpl_xpts.shot_profiles._request_json", fake_request)
    counts = fetch_foreign_understat_player_seasons(
        seasons=[2025],
        cache_dir=tmp_path,
        refresh=True,
    )

    assert counts[("La_liga", 2025)] == 2
    assert counts[("Serie_A", 2025)] == 2
    assert counts[("Ligue_1", 2025)] == 2
    assert ("Bundesliga", 2025) not in counts
    assert "Understat fetch failed for Bundesliga 2025" in caplog.text
