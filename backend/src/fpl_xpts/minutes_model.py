from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .config import MODELS_DIR
from .historical_validation import _read_csv
from .market_odds import _canon_team


MINUTES_WINDOWS = (1, 3, 5, 6, 10)
DEFAULT_MINUTES_MODEL_PATH = MODELS_DIR / "minutes_model.pkl"

MINUTES_NUMERIC_FEATURES = [
    *(f"rolling_played_{window}gw" for window in MINUTES_WINDOWS),
    *(f"rolling_started_{window}gw" for window in MINUTES_WINDOWS),
    *(f"rolling_minutes_{window}gw" for window in MINUTES_WINDOWS),
    "cumulative_season_starts",
    "cumulative_season_minutes",
    "chance_of_playing_this_round",
    "chance_of_playing_next_round",
    "chance_this_round_missing",
    "chance_next_round_missing",
    "availability_historical_unknown",
    "gws_since_last_appearance",
    "gws_since_last_start",
    "minutes_last_appearance",
    "missed_previous_gw",
    "GW",
    "is_home",
    "fixture_count",
    "days_since_team_last_fixture",
    "days_to_team_next_fixture",
    "team_fixtures_previous_14d",
    "is_double_gameweek",
    "is_blank_gameweek",
    "team_change_flag",
    "player_team_episode_id",
    "team_league_position_pre_gw",
    "team_points_pre_gw",
    "team_matches_played_pre_gw",
    "little_to_play_for",
    "season_phase",
    "season_phase_x_stakes",
]

MINUTES_CATEGORICAL_FEATURES = [
    "position",
    "team_key",
    "opponent_team_key",
    "fpl_status",
    "availability_category",
]

MINUTES_FEATURE_COLUMNS = [*MINUTES_NUMERIC_FEATURES, *MINUTES_CATEGORICAL_FEATURES]


def _numeric(values: object, index: pd.Index, default: float = np.nan) -> pd.Series:
    if isinstance(values, pd.Series):
        return pd.to_numeric(values, errors="coerce").reindex(index).fillna(default)
    return pd.Series(default if values is None else values, index=index, dtype=float)


def _utc_naive_datetime(values: object) -> object:
    parsed = pd.to_datetime(values, errors="coerce", utc=True)
    if isinstance(parsed, pd.Series):
        return parsed.dt.tz_convert(None)
    if isinstance(parsed, pd.DatetimeIndex):
        return parsed.tz_convert(None)
    if isinstance(parsed, pd.Timestamp):
        return parsed.tz_convert(None)
    return parsed


def _availability_category(value: object) -> str:
    if value is None or pd.isna(value):
        return "historical_unknown"
    try:
        chance = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if chance <= 0:
        return "chance_0"
    if chance <= 25:
        return "chance_25"
    if chance <= 50:
        return "chance_50"
    if chance <= 75:
        return "chance_75"
    return "chance_100"


def _target_started(frame: pd.DataFrame) -> pd.Series:
    minutes = pd.to_numeric(frame["actual_minutes"], errors="coerce").fillna(0.0)
    starts = pd.to_numeric(frame.get("starts"), errors="coerce")
    return pd.Series(np.where(starts.notna(), starts > 0, minutes >= 60), index=frame.index).astype(float)


def _shifted_rolling(
    frame: pd.DataFrame,
    values: pd.Series,
    windows: Iterable[int],
    aggregation: str,
) -> dict[int, pd.Series]:
    keys = [frame["season"], frame["player_id"]]
    shifted = values.groupby(keys, dropna=False, sort=False).shift(1)
    output: dict[int, pd.Series] = {}
    for window in windows:
        rolling = shifted.groupby(keys, dropna=False, sort=False).rolling(window, min_periods=1)
        result = rolling.mean() if aggregation == "mean" else rolling.sum()
        output[int(window)] = result.reset_index(level=[0, 1], drop=True)
    return output


def _add_usage_and_recency(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out["GW"] = pd.to_numeric(out["GW"], errors="coerce")
    out["actual_minutes"] = pd.to_numeric(out["actual_minutes"], errors="coerce").fillna(0.0)
    out["_played_target"] = (out["actual_minutes"] > 0).astype(float)
    out["_started_target"] = _target_started(out)
    out["_sort_date"] = _utc_naive_datetime(out.get("match_date", out.get("date")))
    out = out.sort_values(["season", "player_id", "GW", "_sort_date"], kind="mergesort").copy()

    played_rolls = _shifted_rolling(out, out["_played_target"], MINUTES_WINDOWS, "mean")
    started_rolls = _shifted_rolling(out, out["_started_target"], MINUTES_WINDOWS, "mean")
    minute_rolls = _shifted_rolling(out, out["actual_minutes"], MINUTES_WINDOWS, "sum")
    for window in MINUTES_WINDOWS:
        out[f"rolling_played_{window}gw"] = played_rolls[window]
        out[f"rolling_started_{window}gw"] = started_rolls[window]
        out[f"rolling_minutes_{window}gw"] = minute_rolls[window]

    group_keys = [out["season"], out["player_id"]]
    shifted_minutes = out["actual_minutes"].groupby(group_keys, dropna=False, sort=False).shift(1).fillna(0.0)
    shifted_starts = out["_started_target"].groupby(group_keys, dropna=False, sort=False).shift(1).fillna(0.0)
    out["cumulative_season_minutes"] = shifted_minutes.groupby(group_keys, dropna=False, sort=False).cumsum()
    out["cumulative_season_starts"] = shifted_starts.groupby(group_keys, dropna=False, sort=False).cumsum()

    played_gw = out["GW"].where(out["_played_target"] > 0)
    started_gw = out["GW"].where(out["_started_target"] > 0)
    last_played_gw = played_gw.groupby(group_keys, dropna=False, sort=False).ffill()
    last_started_gw = started_gw.groupby(group_keys, dropna=False, sort=False).ffill()
    last_played_minutes = out["actual_minutes"].where(out["_played_target"] > 0).groupby(
        group_keys, dropna=False, sort=False
    ).ffill()
    previous_played_gw = last_played_gw.groupby(group_keys, dropna=False, sort=False).shift(1)
    previous_started_gw = last_started_gw.groupby(group_keys, dropna=False, sort=False).shift(1)
    out["gws_since_last_appearance"] = (out["GW"] - previous_played_gw).fillna(99.0).clip(lower=0.0)
    out["gws_since_last_start"] = (out["GW"] - previous_started_gw).fillna(99.0).clip(lower=0.0)
    out["minutes_last_appearance"] = last_played_minutes.groupby(
        group_keys, dropna=False, sort=False
    ).shift(1).fillna(0.0)
    previous_played = out["_played_target"].groupby(group_keys, dropna=False, sort=False).shift(1)
    out["missed_previous_gw"] = previous_played.eq(0).fillna(False).astype(float)

    previous_team = out["team_key"].groupby(group_keys, dropna=False, sort=False).shift(1)
    out["team_change_flag"] = (
        previous_team.notna() & out["team_key"].notna() & previous_team.ne(out["team_key"])
    ).astype(float)
    out["player_team_episode_id"] = out["team_change_flag"].groupby(
        group_keys, dropna=False, sort=False
    ).cumsum() + 1.0
    return out


def _fixture_side_rows(root: Path, seasons: set[str]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for season in sorted(seasons):
        path = root / "data" / "vaastav" / f"{season}_merged_gw.csv"
        if not path.exists():
            continue
        raw = _read_csv(path)
        needed = {
            "GW",
            "fixture",
            "team",
            "was_home",
            "team_h_score",
            "team_a_score",
            "kickoff_time",
        }
        if not needed.issubset(raw.columns):
            continue
        raw["GW"] = pd.to_numeric(raw["GW"], errors="coerce")
        for (gw, fixture), group in raw.groupby(["GW", "fixture"], dropna=False):
            home = group.loc[group["was_home"] == True]  # noqa: E712
            away = group.loc[group["was_home"] == False]  # noqa: E712
            if home.empty or away.empty:
                continue
            home_team = str(home["team"].dropna().iloc[0]) if home["team"].notna().any() else ""
            away_team = str(away["team"].dropna().iloc[0]) if away["team"].notna().any() else ""
            if not home_team or not away_team:
                continue
            kickoff = _utc_naive_datetime(group["kickoff_time"].dropna().iloc[0])
            home_goals = pd.to_numeric(group["team_h_score"], errors="coerce").dropna()
            away_goals = pd.to_numeric(group["team_a_score"], errors="coerce").dropna()
            home_score = float(home_goals.iloc[0]) if not home_goals.empty else np.nan
            away_score = float(away_goals.iloc[0]) if not away_goals.empty else np.nan
            common = {
                "season": season,
                "GW": int(gw),
                "fixture": fixture,
                "kickoff_time": kickoff,
                "home_goals": home_score,
                "away_goals": away_score,
            }
            rows.append(
                {
                    **common,
                    "team_key": _canon_team(home_team),
                    "opponent_team_key": _canon_team(away_team),
                    "is_home": 1.0,
                    "goals_for": home_score,
                    "goals_against": away_score,
                }
            )
            rows.append(
                {
                    **common,
                    "team_key": _canon_team(away_team),
                    "opponent_team_key": _canon_team(home_team),
                    "is_home": 0.0,
                    "goals_for": away_score,
                    "goals_against": home_score,
                }
            )
    return pd.DataFrame(rows)


def _fixture_context(side_rows: pd.DataFrame) -> pd.DataFrame:
    if side_rows.empty:
        return pd.DataFrame()
    side = side_rows.sort_values(["season", "team_key", "kickoff_time", "fixture"], kind="mergesort").copy()
    context_rows: list[dict[str, Any]] = []
    for (season, team_key), team in side.groupby(["season", "team_key"], sort=False):
        team = team.sort_values(["kickoff_time", "fixture"], kind="mergesort").reset_index(drop=True)
        dates = _utc_naive_datetime(team["kickoff_time"])
        for gw, gw_rows in team.groupby("GW", sort=True):
            indices = gw_rows.index.to_numpy()
            first_idx = int(indices.min())
            last_idx = int(indices.max())
            first_date = dates.iloc[first_idx]
            last_date = dates.iloc[last_idx]
            previous_dates = dates.iloc[:first_idx].dropna()
            future_dates = dates.iloc[last_idx + 1 :].dropna()
            previous_14d = int(((first_date - previous_dates).dt.days.between(0, 14)).sum()) if pd.notna(first_date) else 0
            context_rows.append(
                {
                    "season": season,
                    "GW": int(gw),
                    "team_key": team_key,
                    "opponent_team_key": str(gw_rows.iloc[0]["opponent_team_key"]),
                    "is_home": float(gw_rows.iloc[0]["is_home"]),
                    "fixture_count_schedule": int(len(gw_rows)),
                    "match_date_schedule": first_date,
                    "days_since_team_last_fixture": (
                        float((first_date - previous_dates.iloc[-1]).days)
                        if pd.notna(first_date) and not previous_dates.empty
                        else np.nan
                    ),
                    "days_to_team_next_fixture": (
                        float((future_dates.iloc[0] - last_date).days)
                        if pd.notna(last_date) and not future_dates.empty
                        else np.nan
                    ),
                    "team_fixtures_previous_14d": previous_14d,
                }
            )
    return pd.DataFrame(context_rows)


def _standings_context(side_rows: pd.DataFrame) -> pd.DataFrame:
    if side_rows.empty:
        return pd.DataFrame()
    fixtures = side_rows.loc[side_rows["is_home"] == 1.0].copy()
    rows: list[dict[str, Any]] = []
    for season, season_fixtures in fixtures.groupby("season", sort=False):
        teams = sorted(set(side_rows.loc[side_rows["season"] == season, "team_key"].dropna().astype(str)))
        table = {
            team: {"points": 0.0, "played": 0.0, "gf": 0.0, "ga": 0.0}
            for team in teams
        }
        for gw in sorted(pd.to_numeric(season_fixtures["GW"], errors="coerce").dropna().astype(int).unique()):
            ranked = sorted(
                teams,
                key=lambda team: (
                    -table[team]["points"],
                    -(table[team]["gf"] - table[team]["ga"]),
                    -table[team]["gf"],
                    team,
                ),
            )
            positions = {team: idx + 1 for idx, team in enumerate(ranked)}
            second_points = table[ranked[1]]["points"] if len(ranked) > 1 else 0.0
            safety_points = table[ranked[16]]["points"] if len(ranked) > 16 else 0.0
            for team in teams:
                state = table[team]
                position = positions[team]
                remaining_points = max(0.0, (38.0 - state["played"]) * 3.0)
                champion_locked = position == 1 and state["points"] - second_points > remaining_points
                relegated_locked = position >= 18 and safety_points - state["points"] > remaining_points
                midtable = 8 <= position <= 15
                little_to_play = float(gw >= 32 and (midtable or champion_locked or relegated_locked))
                phase = float(np.clip(gw / 38.0, 0.0, 1.0))
                rows.append(
                    {
                        "season": season,
                        "GW": int(gw),
                        "team_key": team,
                        "team_league_position_pre_gw": float(position),
                        "team_points_pre_gw": float(state["points"]),
                        "team_matches_played_pre_gw": float(state["played"]),
                        "little_to_play_for": little_to_play,
                        "season_phase": phase,
                        "season_phase_x_stakes": phase * little_to_play,
                    }
                )
            gw_fixtures = season_fixtures.loc[pd.to_numeric(season_fixtures["GW"], errors="coerce") == gw]
            for _, fixture in gw_fixtures.iterrows():
                home = str(fixture["team_key"])
                away = str(fixture["opponent_team_key"])
                home_goals = pd.to_numeric(pd.Series([fixture["home_goals"]]), errors="coerce").iloc[0]
                away_goals = pd.to_numeric(pd.Series([fixture["away_goals"]]), errors="coerce").iloc[0]
                if pd.isna(home_goals) or pd.isna(away_goals):
                    continue
                table[home]["played"] += 1
                table[away]["played"] += 1
                table[home]["gf"] += float(home_goals)
                table[home]["ga"] += float(away_goals)
                table[away]["gf"] += float(away_goals)
                table[away]["ga"] += float(home_goals)
                if home_goals > away_goals:
                    table[home]["points"] += 3
                elif away_goals > home_goals:
                    table[away]["points"] += 3
                else:
                    table[home]["points"] += 1
                    table[away]["points"] += 1
    return pd.DataFrame(rows)


def _add_availability(frame: pd.DataFrame, historical: bool) -> pd.DataFrame:
    out = frame.copy()
    for col in ["chance_of_playing_this_round", "chance_of_playing_next_round"]:
        if col not in out.columns:
            out[col] = np.nan
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out["chance_this_round_missing"] = out["chance_of_playing_this_round"].isna().astype(float)
    out["chance_next_round_missing"] = out["chance_of_playing_next_round"].isna().astype(float)
    if "fpl_status" not in out.columns:
        out["fpl_status"] = out.get("status", "historical_unknown" if historical else "unknown")
    out["fpl_status"] = out["fpl_status"].fillna("historical_unknown" if historical else "unknown").astype(str)
    if "availability_category" not in out.columns:
        out["availability_category"] = out["chance_of_playing_this_round"].apply(_availability_category)
    out["availability_category"] = out["availability_category"].fillna(
        "historical_unknown" if historical else "unknown"
    ).astype(str)
    out["availability_historical_unknown"] = (
        out["fpl_status"].eq("historical_unknown")
        | out["availability_category"].eq("historical_unknown")
    ).astype(float)
    return out


def ensure_minutes_features(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for col in MINUTES_NUMERIC_FEATURES:
        if col not in out.columns:
            out[col] = np.nan
        out[col] = pd.to_numeric(out[col], errors="coerce")
    for col in MINUTES_CATEGORICAL_FEATURES:
        if col not in out.columns:
            out[col] = "unknown"
        out[col] = out[col].fillna("unknown").astype(str)
    return out


def build_historical_minutes_features(root: Path, dataset: pd.DataFrame | None = None) -> pd.DataFrame:
    source = dataset.copy() if dataset is not None else pd.read_csv(
        root / "data" / "modelling" / "full_training_dataset.csv",
        low_memory=False,
    )
    source = source.loc[source["position"].isin(["GK", "DEF", "MID", "FWD"])].copy()
    source["team_key"] = source["team_key"].fillna(source.get("derived_team_key")).astype(str)
    out = _add_usage_and_recency(source)
    seasons = set(out["season"].dropna().astype(str))
    side_rows = _fixture_side_rows(root, seasons)
    fixture_context = _fixture_context(side_rows)
    standings = _standings_context(side_rows)
    if not fixture_context.empty:
        out = out.drop(
            columns=[
                "opponent_team_key",
                "is_home",
                "days_since_team_last_fixture",
                "days_to_team_next_fixture",
                "team_fixtures_previous_14d",
                "match_date_schedule",
                "fixture_count_schedule",
            ],
            errors="ignore",
        ).merge(fixture_context, on=["season", "GW", "team_key"], how="left")
    if not standings.empty:
        out = out.drop(
            columns=[
                "team_league_position_pre_gw",
                "team_points_pre_gw",
                "team_matches_played_pre_gw",
                "little_to_play_for",
                "season_phase",
                "season_phase_x_stakes",
            ],
            errors="ignore",
        ).merge(standings, on=["season", "GW", "team_key"], how="left")
    scheduled_count = pd.to_numeric(out.get("fixture_count_schedule"), errors="coerce")
    existing_count = pd.to_numeric(out.get("fixture_count"), errors="coerce")
    out["fixture_count"] = scheduled_count.fillna(existing_count).fillna(0.0)
    out["is_double_gameweek"] = (out["fixture_count"] > 1).astype(float)
    out["is_blank_gameweek"] = (out["fixture_count"] <= 0).astype(float)
    out["season_phase"] = pd.to_numeric(out.get("season_phase"), errors="coerce").fillna(
        pd.to_numeric(out["GW"], errors="coerce").div(38.0).clip(0.0, 1.0)
    )
    out["little_to_play_for"] = pd.to_numeric(out.get("little_to_play_for"), errors="coerce").fillna(0.0)
    out["season_phase_x_stakes"] = out["season_phase"] * out["little_to_play_for"]
    out = _add_availability(out, historical=True)
    return ensure_minutes_features(out).drop(columns=["_sort_date"], errors="ignore")


def _history_weekly(history: pd.DataFrame) -> pd.DataFrame:
    if history is None or history.empty:
        return pd.DataFrame(columns=["GW", "minutes", "started", "kickoff_time"])
    out = history.copy()
    out["GW"] = pd.to_numeric(out.get("round", out.get("event")), errors="coerce")
    out["minutes"] = pd.to_numeric(out.get("minutes"), errors="coerce").fillna(0.0)
    starts = pd.to_numeric(out.get("starts"), errors="coerce")
    out["started"] = np.where(starts.notna(), starts > 0, out["minutes"] >= 60).astype(float)
    out["kickoff_time"] = _utc_naive_datetime(out.get("kickoff_time"))
    return (
        out.groupby("GW", as_index=False)
        .agg(minutes=("minutes", "sum"), started=("started", "max"), kickoff_time=("kickoff_time", "max"))
        .sort_values("GW")
    )


def build_live_minutes_features(
    player_fixture: pd.DataFrame,
    players: pd.DataFrame,
    teams: pd.DataFrame,
    history_by_player: dict[int, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    history_by_player = history_by_player or {}
    if player_fixture.empty:
        return pd.DataFrame()
    team_names = teams[["id", "name"]].copy()
    team_names["team_key"] = team_names["name"].apply(_canon_team)
    team_key_by_id = dict(zip(team_names["id"], team_names["team_key"]))
    team_position_by_id = dict(
        zip(teams["id"], pd.to_numeric(teams.get("position"), errors="coerce"))
    )
    team_points_by_id = dict(zip(teams["id"], pd.to_numeric(teams.get("points"), errors="coerce")))
    team_played_by_id = dict(zip(teams["id"], pd.to_numeric(teams.get("played"), errors="coerce")))
    fixture = player_fixture.copy()
    fixture["kickoff_time"] = _utc_naive_datetime(fixture.get("kickoff_time"))
    grouped = (
        fixture.groupby(["event", "player_id"], as_index=False)
        .agg(
            team=("team", "first"),
            opponent=("opponent", "first"),
            position=("position", "first"),
            is_home=("was_home", "mean"),
            fixture_count=("fixture", "count"),
            first_kickoff=("kickoff_time", "min"),
            last_kickoff=("kickoff_time", "max"),
        )
    )
    player_columns = [
        col
        for col in [
            "id",
            "chance_of_playing_this_round",
            "chance_of_playing_next_round",
            "status",
        ]
        if col in players.columns
    ]
    grouped = grouped.merge(players[player_columns].rename(columns={"id": "player_id"}), on="player_id", how="left")
    rows: list[dict[str, Any]] = []
    for _, row in grouped.iterrows():
        player_id = int(row["player_id"])
        gw = int(row["event"])
        history = _history_weekly(history_by_player.get(player_id, pd.DataFrame()))
        prior = history.loc[pd.to_numeric(history["GW"], errors="coerce") < gw].copy()
        played = (pd.to_numeric(prior["minutes"], errors="coerce").fillna(0.0) > 0).astype(float)
        started = pd.to_numeric(prior["started"], errors="coerce").fillna(0.0)
        feature_row: dict[str, Any] = {
            "event": gw,
            "GW": gw,
            "player_id": player_id,
            "position": row.get("position", "unknown"),
            "team_key": team_key_by_id.get(row.get("team"), "unknown"),
            "opponent_team_key": team_key_by_id.get(row.get("opponent"), "unknown"),
            "is_home": row.get("is_home"),
            "fixture_count": row.get("fixture_count", 0),
            "is_double_gameweek": float(row.get("fixture_count", 0) > 1),
            "is_blank_gameweek": float(row.get("fixture_count", 0) <= 0),
            "chance_of_playing_this_round": row.get("chance_of_playing_this_round"),
            "chance_of_playing_next_round": row.get("chance_of_playing_next_round"),
            "fpl_status": row.get("status", "unknown"),
            "team_change_flag": 0.0,
            "player_team_episode_id": 1.0,
            "team_league_position_pre_gw": team_position_by_id.get(row.get("team")),
            "team_points_pre_gw": team_points_by_id.get(row.get("team")),
            "team_matches_played_pre_gw": team_played_by_id.get(row.get("team")),
        }
        for window in MINUTES_WINDOWS:
            feature_row[f"rolling_played_{window}gw"] = float(played.tail(window).mean()) if len(played) else np.nan
            feature_row[f"rolling_started_{window}gw"] = float(started.tail(window).mean()) if len(started) else np.nan
            feature_row[f"rolling_minutes_{window}gw"] = float(
                pd.to_numeric(prior["minutes"], errors="coerce").tail(window).sum()
            ) if len(prior) else np.nan
        feature_row["cumulative_season_starts"] = float(started.sum())
        feature_row["cumulative_season_minutes"] = float(
            pd.to_numeric(prior["minutes"], errors="coerce").fillna(0.0).sum()
        )
        played_prior = prior.loc[played > 0]
        started_prior = prior.loc[started > 0]
        feature_row["gws_since_last_appearance"] = (
            float(gw - pd.to_numeric(played_prior["GW"], errors="coerce").iloc[-1])
            if not played_prior.empty
            else 99.0
        )
        feature_row["gws_since_last_start"] = (
            float(gw - pd.to_numeric(started_prior["GW"], errors="coerce").iloc[-1])
            if not started_prior.empty
            else 99.0
        )
        feature_row["minutes_last_appearance"] = (
            float(pd.to_numeric(played_prior["minutes"], errors="coerce").iloc[-1])
            if not played_prior.empty
            else 0.0
        )
        feature_row["missed_previous_gw"] = float(
            not prior.empty and pd.to_numeric(prior["minutes"], errors="coerce").iloc[-1] <= 0
        )
        prior_dates = _utc_naive_datetime(prior["kickoff_time"]).dropna()
        first_kickoff = _utc_naive_datetime(row.get("first_kickoff"))
        feature_row["days_since_team_last_fixture"] = (
            float((first_kickoff - prior_dates.iloc[-1]).days)
            if pd.notna(first_kickoff) and not prior_dates.empty
            else np.nan
        )
        feature_row["team_fixtures_previous_14d"] = (
            float(((first_kickoff - prior_dates).dt.days.between(0, 14)).sum())
            if pd.notna(first_kickoff)
            else np.nan
        )
        future = grouped.loc[
            (grouped["team"] == row["team"])
            & (pd.to_numeric(grouped["event"], errors="coerce") > gw)
        ]
        future_dates = _utc_naive_datetime(future["first_kickoff"]).dropna()
        last_kickoff = _utc_naive_datetime(row.get("last_kickoff"))
        feature_row["days_to_team_next_fixture"] = (
            float((future_dates.min() - last_kickoff).days)
            if pd.notna(last_kickoff) and not future_dates.empty
            else np.nan
        )
        phase = float(np.clip(gw / 38.0, 0.0, 1.0))
        position = feature_row["team_league_position_pre_gw"]
        little = float(gw >= 32 and pd.notna(position) and 8 <= float(position) <= 15)
        feature_row["little_to_play_for"] = little
        feature_row["season_phase"] = phase
        feature_row["season_phase_x_stakes"] = phase * little
        rows.append(feature_row)
    return ensure_minutes_features(_add_availability(pd.DataFrame(rows), historical=False))


def load_minutes_bundle(path: Path = DEFAULT_MINUTES_MODEL_PATH) -> dict[str, Any]:
    import joblib

    return joblib.load(path)


def save_minutes_bundle(bundle: dict[str, Any], path: Path = DEFAULT_MINUTES_MODEL_PATH) -> None:
    import joblib

    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, path)


def _predict_component(component: dict[str, Any], frame: pd.DataFrame, classifier: bool) -> np.ndarray:
    features = list(component["feature_columns"])
    data = ensure_minutes_features(frame)
    matrix = component["preprocessor"].transform(data[features])
    if classifier:
        xgb = component["xgb_model"].predict_proba(matrix)[:, 1]
        rf = component["rf_model"].predict_proba(matrix)[:, 1]
        raw = (xgb + rf) / 2.0
        calibrator = component.get("calibrator")
        return np.clip(calibrator.predict(raw) if calibrator is not None else raw, 0.0, 1.0)
    xgb = component["xgb_model"].predict(matrix)
    rf = component["rf_model"].predict(matrix)
    return (xgb + rf) / 2.0


def score_minutes(frame: pd.DataFrame, bundle: dict[str, Any]) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    out = frame.copy()
    p_play = _predict_component(bundle["play_classifier"], out, classifier=True)
    p_start_given_play = _predict_component(bundle["start_classifier"], out, classifier=True)
    mins_if_start = np.clip(
        _predict_component(bundle["mins_if_start_regressor"], out, classifier=False),
        1.0,
        90.0,
    )
    mins_if_sub = np.clip(
        _predict_component(bundle["mins_if_sub_regressor"], out, classifier=False),
        1.0,
        60.0,
    )
    mins_if_play = p_start_given_play * mins_if_start + (1.0 - p_start_given_play) * mins_if_sub
    expected = p_play * mins_if_play
    out["pred_play_prob"] = p_play
    out["pred_start_given_play_prob"] = p_start_given_play
    out["pred_start_prob"] = p_play * p_start_given_play
    out["pred_mins_if_start"] = mins_if_start
    out["pred_mins_if_sub"] = mins_if_sub
    out["pred_mins_if_play"] = np.clip(mins_if_play, 0.0, 90.0)
    out["expected_minutes"] = np.clip(expected, 0.0, 90.0)
    out["minutes_model_source"] = "trained_four_output_model"
    return out


def apply_minutes_bundle(
    player_fixture: pd.DataFrame,
    players: pd.DataFrame,
    teams: pd.DataFrame,
    history_by_player: dict[int, pd.DataFrame] | None = None,
    bundle: dict[str, Any] | None = None,
    features: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Apply an already-loaded minutes model bundle. Pure: no file system.

    ``features`` is the L2 feature seam (Candidate #3 Phase 3): when an
    adapter already holds higher-fidelity model features (e.g. the replay's
    point-in-time historical features), pass them here -- they must carry
    one row per (``event``, ``player_id``) plus the bundle's feature
    columns. When ``None``, features are built live from the frames exactly
    as before.
    """
    if player_fixture.empty or bundle is None:
        return player_fixture.copy()
    if features is None:
        features = build_live_minutes_features(player_fixture, players, teams, history_by_player)
    if features.empty:
        return player_fixture.copy()
    scored = score_minutes(features, bundle)
    values = scored.set_index(["event", "player_id"])
    out = player_fixture.copy()
    keys = pd.MultiIndex.from_frame(out[["event", "player_id"]])
    mappings = {
        "pred_mins_if_play": "likely_minutes",
        "pred_start_prob": "start_probability",
        "pred_play_prob": "play_probability",
        "expected_minutes": "expected_minutes",
    }
    for source, target in mappings.items():
        mapped = values[source].reindex(keys).to_numpy()
        out[target] = pd.Series(mapped, index=out.index).where(
            pd.notna(mapped),
            pd.to_numeric(out.get(target), errors="coerce"),
        )
    out["minutes_model_source"] = pd.Series(
        values["minutes_model_source"].reindex(keys).to_numpy(),
        index=out.index,
    ).fillna("rolling_heuristic_fallback")
    return out


def apply_live_minutes_model(
    player_fixture: pd.DataFrame,
    players: pd.DataFrame,
    teams: pd.DataFrame,
    history_by_player: dict[int, pd.DataFrame] | None = None,
    model_path: Path = DEFAULT_MINUTES_MODEL_PATH,
) -> pd.DataFrame:
    """Load the bundle from disk (I/O edge) and delegate to the pure apply."""
    if player_fixture.empty or not model_path.exists():
        return player_fixture.copy()
    return apply_minutes_bundle(
        player_fixture,
        players,
        teams,
        history_by_player=history_by_player,
        bundle=load_minutes_bundle(model_path),
    )
