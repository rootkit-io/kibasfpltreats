from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import BACKEND_ROOT
from .minutes import estimate_start_and_minutes
from .minutes_contract import load_manual_minutes_csv, load_minute_overrides_csv
from .minutes_engine import (
    _canonical_team,
    _norm,
    _player_name,
    apply_manual_minutes_states,
    apply_minute_override_states,
)


def find_minutes_override_file(root: Path = BACKEND_ROOT) -> Path | None:
    candidates = [
        root / "minute_overrides.csv",
        root / "minutes_overrides.csv",
        root / "xmins_overrides.csv",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def find_player_minutes_input_file(root: Path = BACKEND_ROOT) -> Path | None:
    candidates = [
        root / "player_minutes_inputs_gw37_to_38.csv",
        root / "player_minutes_inputs.csv",
        root / "minutes_inputs.csv",
        root / "xmins_inputs.csv",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


def build_player_minutes_inputs(
    players: pd.DataFrame,
    teams: pd.DataFrame,
    history_by_player: dict[int, pd.DataFrame] | None = None,
    gameweeks: list[int] | None = None,
) -> pd.DataFrame:
    """Build the editable player start/minutes CSV from live API/history data."""
    history_by_player = history_by_player or {}
    team_meta = teams[["id", "name"]].rename(columns={"id": "team", "name": "team_name"}).copy()
    out = players.merge(team_meta, on="team", how="left").copy()
    out["player"] = out.apply(_player_name, axis=1)
    out["team"] = out["team_name"]
    out["player_key"] = out.apply(lambda row: f"{_norm(row['player'])}|{_canonical_team(row['team'])}", axis=1)
    out["Pos"] = out["element_type"].map({1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}).fillna("")

    starts = []
    mins = []
    appearances = []
    total_minutes = []
    for _, row in out.iterrows():
        history = history_by_player.get(int(row["id"])) if not pd.isna(row.get("id")) else None
        start, minute_value = estimate_start_and_minutes(row, history)
        starts.append(start)
        mins.append(minute_value)
        if history is not None and not history.empty and "minutes" in history.columns:
            hist_mins = pd.to_numeric(history["minutes"], errors="coerce").fillna(0)
            active = history.loc[hist_mins > 0]
            appearances.append(int(len(active)))
            total_minutes.append(float(pd.to_numeric(active.get("minutes", 0), errors="coerce").fillna(0).sum()))
        else:
            appearances.append("")
            total_minutes.append(float(row.get("minutes", 0) or 0))

    out["start"] = pd.Series(starts).round(3)
    out["mins"] = pd.Series(mins).round(1)
    out["api_start"] = out["start"]
    out["api_mins"] = out["mins"]
    out["appearances"] = appearances
    out["total_minutes"] = total_minutes
    out["chance_of_playing"] = out.get("chance_of_playing_next_round", out.get("chance_of_playing_this_round", ""))

    cols = [
        "GW", "player_id", "player_key", "player", "team", "Pos", "start", "mins",
        "api_start", "api_mins", "appearances", "total_minutes", "chance_of_playing",
    ]
    base = out.rename(columns={"id": "player_id"})
    frames = []
    weeks = gameweeks or [None]
    for gw in weeks:
        frame = base.copy()
        frame["GW"] = gw if gw is not None else ""
        frames.append(frame)
    result = pd.concat(frames, ignore_index=True)
    return result[cols].sort_values(["GW", "team", "Pos", "player"]).reset_index(drop=True)


def write_player_minutes_inputs(
    path: Path,
    players: pd.DataFrame,
    teams: pd.DataFrame,
    history_by_player: dict[int, pd.DataFrame] | None = None,
    gameweeks: list[int] | None = None,
    overwrite: bool = False,
) -> Path:
    if path.exists() and not overwrite:
        return path
    df = build_player_minutes_inputs(players, teams, history_by_player=history_by_player, gameweeks=gameweeks)
    df.to_csv(path, index=False, float_format="%.6f")
    return path


def load_player_minutes_inputs(path: Path | None = None) -> pd.DataFrame:
    path = path or find_player_minutes_input_file()
    if path is None or not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    required = {"start", "mins"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path} must contain columns: {', '.join(sorted(missing))}")
    if "player_id" not in df.columns and "player_key" not in df.columns:
        raise ValueError(f"{path} must contain either player_id or player_key")
    df["start"] = pd.to_numeric(df["start"], errors="coerce").fillna(0.0).clip(0.0, 1.0)
    df["mins"] = pd.to_numeric(df["mins"], errors="coerce").fillna(0.0).clip(0.0, 90.0)
    if "GW" in df.columns:
        df["GW"] = pd.to_numeric(df["GW"], errors="coerce").astype("Int64")
    if "player_id" in df.columns:
        df["player_id"] = pd.to_numeric(df["player_id"], errors="coerce").astype("Int64")
    if "player_key" in df.columns:
        df["player_key_norm"] = df["player_key"].apply(_norm)
    return df


def apply_player_minutes_inputs(
    player_fixture: pd.DataFrame,
    players: pd.DataFrame,
    teams: pd.DataFrame,
    path: Path | None = None,
) -> pd.DataFrame:
    """Compatibility adapter: contract-load the CSV, delegate to the engine.

    Since Phase 2 the precedence logic lives in
    ``minutes_engine.apply_manual_minutes_states``; this wrapper only resolves
    the legacy ``path=None`` auto-discovery and validates the file. Invalid
    files now fail loudly (contract) instead of being silently coerced.
    """
    path = path or find_player_minutes_input_file()
    if path is None or not path.exists():
        return player_fixture
    manual = load_manual_minutes_csv(path)
    if not manual.states:
        return player_fixture
    return apply_manual_minutes_states(player_fixture, players, teams, manual.states)


def load_minute_overrides(path: Path | None = None) -> pd.DataFrame:
    path = path or find_minutes_override_file()
    if path is None or not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if "GW" not in df.columns:
        raise ValueError(f"{path} must contain a GW column")
    if "mins" not in df.columns:
        raise ValueError(f"{path} must contain a mins column")
    if "player_id" not in df.columns and "player_key" not in df.columns:
        raise ValueError(f"{path} must contain either player_id or player_key")
    if "fixture_in_week" not in df.columns:
        df["fixture_in_week"] = 1

    df["GW"] = pd.to_numeric(df["GW"], errors="coerce").astype("Int64")
    df["fixture_in_week"] = pd.to_numeric(df["fixture_in_week"], errors="coerce").fillna(1).astype(int)
    df["mins"] = pd.to_numeric(df["mins"], errors="coerce").fillna(0.0).clip(0, 90)
    if "player_key" in df.columns:
        df["player_key_norm"] = df["player_key"].apply(_norm)
    if "player_id" in df.columns:
        df["player_id"] = pd.to_numeric(df["player_id"], errors="coerce").astype("Int64")
    return df


def apply_minute_overrides(
    player_fixture: pd.DataFrame,
    players: pd.DataFrame,
    teams: pd.DataFrame,
    path: Path | None = None,
) -> pd.DataFrame:
    """Compatibility adapter: contract-load the CSV, delegate to the engine.

    Since Phase 2 the precedence logic lives in
    ``minutes_engine.apply_minute_override_states``; this wrapper only
    resolves the legacy ``path=None`` auto-discovery and validates the file.
    """
    path = path or find_minutes_override_file()
    if path is None or not path.exists():
        return player_fixture
    overrides = load_minute_overrides_csv(path)
    if not overrides.states:
        return player_fixture
    return apply_minute_override_states(player_fixture, players, teams, overrides.states)
