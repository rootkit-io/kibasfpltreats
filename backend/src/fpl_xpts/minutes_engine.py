"""Pure minutes-precedence engine (Phase 2 of deepening the Minutes module).

The engine owns layers 2-5 of the minutes precedence stack and operates
exclusively on already-validated contract objects and in-memory frames:

    layer 2  trained model        -- ``model_bundle`` (already loaded, no I/O here)
    layer 3+4  manual inputs      -- ``PlayerMinutesState`` sequences, in order,
                                     later sequences win
    layer 5  hard overrides       -- ``MinuteOverrideState`` sequences

Layer 1 (the heuristic baseline inside ``xpts.build_player_fixture_forecast``)
produces the base predictions this engine receives; layer 6
(``xpts.recompute_player_fixture_components``) is downstream xPts
recomputation. Both stay in the pipeline.

PURITY RULE: nothing in this module may touch the file system, the network,
or global state. All I/O happens at the seam (``minutes_contract`` loaders,
``minutes_model.load_minutes_bundle``) before the engine is called.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd

from .minutes import minute_outcomes
from .minutes_contract import MinuteOverrideState, PlayerMinutesState

MANUAL_SOURCE_LABEL = "manual_player_minutes_input"


# ------------------------------------------------------------- matching keys
# Moved here from minute_overrides.py so the one implementation of player/team
# matching lives beside the precedence logic that depends on it.


def _norm(text: object) -> str:
    s = str(text).strip().lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _canonical_team(name: object) -> str:
    aliases = {
        "man city": "manchester city",
        "man utd": "manchester united",
        "man united": "manchester united",
        "newcastle": "newcastle united",
        "nott m forest": "nottingham forest",
        "spurs": "tottenham",
        "wolves": "wolverhampton wanderers",
    }
    key = _norm(name)
    return aliases.get(key, key)


def _player_name(row: pd.Series) -> str:
    name = f"{row.get('first_name', '')} {row.get('second_name', '')}".strip()
    return name or str(row.get("web_name", ""))


def _lookup_tables(players: pd.DataFrame, teams: pd.DataFrame) -> tuple[dict, dict]:
    team_names = teams[["id", "name"]].copy()
    team_names["team_key"] = team_names["name"].apply(_canonical_team)
    team_key_by_id = dict(zip(team_names["id"], team_names["team_key"]))
    player_meta = players[["id", "first_name", "second_name", "web_name"]].copy()
    player_meta["player_name"] = player_meta.apply(_player_name, axis=1)
    player_name_by_id = dict(zip(player_meta["id"], player_meta["player_name"]))
    return team_key_by_id, player_name_by_id


def _attach_match_columns(
    out: pd.DataFrame,
    team_key_by_id: Mapping[Any, str],
    player_name_by_id: Mapping[Any, str],
) -> pd.DataFrame:
    out["_gw"] = pd.to_numeric(out["event"], errors="coerce").astype("Int64")
    out["_player_key_norm"] = out.apply(
        lambda r: _norm(
            f"{player_name_by_id.get(r['player_id'], r.get('web_name', ''))}"
            f"|{team_key_by_id.get(r['team'], r['team'])}"
        ),
        axis=1,
    )
    return out


# ----------------------------------------------------------- layers 3 and 4


def apply_manual_minutes_states(
    player_fixture: pd.DataFrame,
    players: pd.DataFrame,
    teams: pd.DataFrame,
    states: Sequence[PlayerMinutesState],
) -> pd.DataFrame:
    """Apply Manual Minutes Inputs (layers 3/4) to the player-fixture frame.

    Pure port of the legacy ``minute_overrides.apply_player_minutes_inputs``
    loop, consuming validated ``PlayerMinutesState`` objects instead of
    re-reading a CSV. Later states win over earlier ones on conflict.
    """
    if player_fixture.empty or not states:
        return player_fixture.copy()

    out = player_fixture.copy()
    team_key_by_id, player_name_by_id = _lookup_tables(players, teams)
    out = _attach_match_columns(out, team_key_by_id, player_name_by_id)

    for state in states:
        mask = pd.Series(True, index=out.index)
        if state.gameweek is not None:
            mask &= out["_gw"] == int(state.gameweek)
        if state.player_id is not None:
            mask &= out["player_id"] == int(state.player_id)
        else:
            mask &= out["_player_key_norm"] == _norm(state.player_key)

        likely_minutes = float(state.likely_minutes)
        start_probability = float(state.start_probability)
        # The contract already normalised probabilities to 0..1; only the
        # default-play rule from the legacy layer remains.
        play_default = 1.0 if likely_minutes > 0 or start_probability > 0 else 0.0
        play_probability = (
            float(state.chance_of_playing)
            if state.chance_of_playing is not None
            else play_default
        )
        start_probability = min(start_probability, play_probability)

        vals, probs = minute_outcomes(
            likely_minutes,
            start_probability=start_probability,
            play_probability=play_probability,
        )
        expected_minutes = float((vals * probs).sum())

        out.loc[mask, "likely_minutes"] = likely_minutes
        out.loc[mask, "start_probability"] = start_probability
        out.loc[mask, "play_probability"] = play_probability
        out.loc[mask, "expected_minutes"] = expected_minutes
        out.loc[mask, "minutes_model_source"] = MANUAL_SOURCE_LABEL

    return out.drop(columns=["_gw", "_player_key_norm"])


# ------------------------------------------------------------------ layer 5


def apply_minute_override_states(
    player_fixture: pd.DataFrame,
    players: pd.DataFrame,
    teams: pd.DataFrame,
    states: Sequence[MinuteOverrideState],
) -> pd.DataFrame:
    """Apply hard minute overrides (layer 5) to the player-fixture frame.

    Pure port of the legacy ``minute_overrides.apply_minute_overrides`` loop.
    Overrides pin ``expected_minutes`` for one player-fixture and touch
    nothing else (start/play probabilities keep their layer 2-4 values).
    """
    if player_fixture.empty or not states:
        return player_fixture.copy()

    out = player_fixture.copy()
    team_key_by_id, player_name_by_id = _lookup_tables(players, teams)
    out = _attach_match_columns(out, team_key_by_id, player_name_by_id)

    out["_kickoff_sort"] = pd.to_datetime(out.get("kickoff_time"), errors="coerce")
    fixture_order = (
        out[["team", "_gw", "fixture", "_kickoff_sort"]]
        .drop_duplicates()
        .sort_values(["team", "_gw", "_kickoff_sort", "fixture"])
    )
    fixture_order["_fixture_in_week"] = fixture_order.groupby(["team", "_gw"]).cumcount() + 1
    out = out.merge(
        fixture_order[["team", "_gw", "fixture", "_fixture_in_week"]],
        on=["team", "_gw", "fixture"],
        how="left",
    )

    for state in states:
        mask = (out["_gw"] == int(state.gameweek)) & (
            out["_fixture_in_week"] == int(state.fixture_in_week)
        )
        if state.player_id is not None:
            mask &= out["player_id"] == int(state.player_id)
        else:
            mask &= out["_player_key_norm"] == _norm(state.player_key)
        out.loc[mask, "expected_minutes"] = float(state.minutes)

    return out.drop(columns=["_gw", "_player_key_norm", "_kickoff_sort", "_fixture_in_week"])


# --------------------------------------------------------------- the engine


def resolve_minutes(
    player_fixture: pd.DataFrame,
    players: pd.DataFrame,
    teams: pd.DataFrame,
    *,
    history_by_player: dict[int, pd.DataFrame] | None = None,
    model_bundle: dict[str, Any] | None = None,
    minutes_features: pd.DataFrame | None = None,
    manual_inputs: Sequence[Sequence[PlayerMinutesState]] = (),
    overrides: Sequence[MinuteOverrideState] = (),
) -> pd.DataFrame:
    """Resolve minutes through the precedence stack (layers 2-5). Pure.

    ``player_fixture`` arrives carrying the layer 1 heuristic baseline.
    Precedence, lowest to highest:

        heuristic baseline (already on the frame)
        -> trained model (``model_bundle``, skipped when ``None``)
        -> each sequence in ``manual_inputs``, in order (later wins)
        -> ``overrides`` (pin ``expected_minutes`` only)

    ``minutes_features`` optionally supplies pre-built model features for
    layer 2 (one row per event/player_id); ``None`` means build them live.
    Everything arrives validated and in memory; the engine performs no I/O.
    """
    out = player_fixture.copy()

    if model_bundle is not None and not out.empty:
        # Lazy import keeps the engine importable without the model stack's
        # heavier dependency chain; apply_minutes_bundle itself is pure.
        from .minutes_model import apply_minutes_bundle

        out = apply_minutes_bundle(
            out,
            players,
            teams,
            history_by_player=history_by_player,
            bundle=model_bundle,
            features=minutes_features,
        )

    for states in manual_inputs:
        out = apply_manual_minutes_states(out, players, teams, tuple(states))

    out = apply_minute_override_states(out, players, teams, tuple(overrides))
    return out
