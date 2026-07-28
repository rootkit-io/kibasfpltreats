from __future__ import annotations

import gzip
import json
import logging
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from .config import DATA_DIR


UNDERSTAT_BASE_URL = "https://understat.com"
BIG_CHANCE_XG_THRESHOLD = 0.30
FOREIGN_UNDERSTAT_LEAGUES = ("La_liga", "Bundesliga", "Serie_A", "Ligue_1")
LOGGER = logging.getLogger(__name__)


def understat_season_from_date(today: date | None = None) -> int:
    today = today or date.today()
    return today.year if today.month >= 7 else today.year - 1


def _norm(text: object) -> str:
    s = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _canon_team(name: object) -> str:
    s = _norm(name)
    aliases = {
        "man city": "manchester city",
        "manchester city": "manchester city",
        "man utd": "manchester united",
        "man united": "manchester united",
        "manchester united": "manchester united",
        "newcastle": "newcastle united",
        "newcastle united": "newcastle united",
        "nott m forest": "nottingham forest",
        "nottingham forest": "nottingham forest",
        "spurs": "tottenham",
        "tottenham hotspur": "tottenham",
        "tottenham": "tottenham",
        "wolves": "wolverhampton wanderers",
        "wolverhampton": "wolverhampton wanderers",
        "wolverhampton wanderers": "wolverhampton wanderers",
        "brighton hove albion": "brighton",
        "brighton and hove albion": "brighton",
    }
    return aliases.get(s, s)


def _request_json(url: str, referer: str, cache_path: Path | None = None, refresh: bool = False) -> dict[str, Any]:
    if cache_path is not None and cache_path.exists() and not refresh:
        return json.loads(cache_path.read_text(encoding="utf-8"))

    request = Request(
        url,
        headers={
            "User-Agent": "fpl-xpts-understat/0.1",
            "Referer": referer,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Encoding": "gzip",
        },
    )
    with urlopen(request, timeout=60) as response:
        raw = response.read()
    text = gzip.decompress(raw).decode("utf-8") if raw[:2] == b"\x1f\x8b" else raw.decode("utf-8")
    data = json.loads(text)
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(data), encoding="utf-8")
    return data


def fetch_understat_league(
    league: str = "EPL",
    season: int | None = None,
    cache_dir: Path = DATA_DIR / "understat",
    refresh: bool = False,
) -> dict[str, Any]:
    season = int(season or understat_season_from_date())
    cache_path = cache_dir / f"league_{league}_{season}.json"
    return _request_json(
        f"{UNDERSTAT_BASE_URL}/getLeagueData/{league}/{season}",
        f"{UNDERSTAT_BASE_URL}/league/{league}/{season}",
        cache_path=cache_path,
        refresh=refresh,
    )


def fetch_foreign_understat_player_seasons(
    seasons: list[int] | tuple[int, ...] | None = None,
    cache_dir: Path = DATA_DIR / "understat" / "foreign_leagues",
    refresh: bool = False,
) -> dict[tuple[str, int], int]:
    """Fetch foreign player seasons while tolerating isolated league failures."""
    if seasons is None:
        latest = understat_season_from_date()
        seasons = [latest - 2, latest - 1, latest]

    counts: dict[tuple[str, int], int] = {}
    for league in FOREIGN_UNDERSTAT_LEAGUES:
        for season_value in seasons:
            season = int(season_value)
            try:
                payload = _request_json(
                    f"{UNDERSTAT_BASE_URL}/getLeagueData/{league}/{season}",
                    f"{UNDERSTAT_BASE_URL}/league/{league}/{season}",
                    cache_path=cache_dir / f"{league}_{season}.json",
                    refresh=refresh,
                )
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                LOGGER.warning("Understat fetch failed for %s %s: %s", league, season, exc)
                continue
            counts[(league, season)] = len(payload.get("players", []))
    return counts


def _num(frame: pd.DataFrame, col: str) -> pd.Series:
    if col not in frame.columns:
        return pd.Series(0.0, index=frame.index)
    return pd.to_numeric(frame[col], errors="coerce").fillna(0.0)


def _safe_div(num: pd.Series, den: pd.Series) -> pd.Series:
    numerator = num.to_numpy(float)
    denominator = den.to_numpy(float)
    return np.divide(numerator, denominator, out=np.zeros_like(numerator, dtype=float), where=denominator > 0)


def _aggregate_player_profiles(league_data: dict[str, Any], league: str, season: int) -> pd.DataFrame:
    players = pd.DataFrame(league_data.get("players", []))
    if players.empty:
        return pd.DataFrame()

    out = players.rename(
        columns={
            "id": "understat_player_id",
            "player_name": "player",
            "team_title": "team",
            "time": "understat_minutes",
            "games": "understat_games",
            "goals": "understat_goals",
            "assists": "understat_assists",
            "shots": "understat_shots",
            "key_passes": "understat_key_passes",
        }
    ).copy()
    for col in [
        "understat_player_id", "understat_games", "understat_minutes", "understat_goals",
        "understat_assists", "xG", "npxG", "xA", "understat_shots", "understat_key_passes",
        "xGChain", "xGBuildup",
    ]:
        out[col] = _num(out, col)

    minutes = out["understat_minutes"]
    matches90 = minutes / 90.0
    shots = out["understat_shots"]
    chances = out["understat_key_passes"]
    out["understat_league"] = league
    out["understat_season"] = season
    out["team_key"] = out["team"].apply(_canon_team)
    out["player_key"] = out["player"].apply(_norm) + "|" + out["team_key"]
    out["understat_xG90"] = _safe_div(out["xG"], matches90)
    out["understat_npxG90"] = _safe_div(out["npxG"], matches90)
    out["understat_xA90"] = _safe_div(out["xA"], matches90)
    out["understat_shots90"] = _safe_div(shots, matches90)
    out["understat_chances_created90"] = _safe_div(chances, matches90)
    out["understat_key_passes90"] = out["understat_chances_created90"]
    out["understat_xG_per_shot"] = _safe_div(out["xG"], shots)
    out["understat_npxG_per_shot"] = _safe_div(out["npxG"], shots)
    out["understat_xA_per_chance"] = _safe_div(out["xA"], chances)
    out["understat_xA_per_key_pass"] = out["understat_xA_per_chance"]
    out["understat_xGChain90"] = _safe_div(out["xGChain"], matches90)
    out["understat_xGBuildup90"] = _safe_div(out["xGBuildup"], matches90)
    return out


def _played_match_ids(league_data: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for row in league_data.get("dates", []):
        if row.get("isResult") is True and row.get("id") is not None:
            ids.append(str(row["id"]))
    return ids


def _fetch_match_shots(match_id: str, cache_dir: Path, refresh: bool) -> list[dict[str, Any]]:
    cache_path = cache_dir / "matches" / f"{match_id}.json"
    data = _request_json(
        f"{UNDERSTAT_BASE_URL}/getMatchData/{match_id}",
        f"{UNDERSTAT_BASE_URL}/match/{match_id}",
        cache_path=cache_path,
        refresh=refresh,
    )
    rows: list[dict[str, Any]] = []
    for side in ["h", "a"]:
        for shot in data.get("shots", {}).get(side, []):
            rows.append(dict(shot))
    return rows


def _big_chance_profiles(
    league_data: dict[str, Any],
    cache_dir: Path,
    refresh: bool = False,
    threshold: float = BIG_CHANCE_XG_THRESHOLD,
    max_workers: int = 8,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    match_ids = _played_match_ids(league_data)
    shots: list[dict[str, Any]] = []
    if not match_ids:
        return pd.DataFrame(), pd.DataFrame()

    def fetch(match_id: str) -> list[dict[str, Any]]:
        try:
            return _fetch_match_shots(match_id, cache_dir=cache_dir, refresh=refresh)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
            return []

    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as pool:
        futures = [pool.submit(fetch, match_id) for match_id in match_ids]
        for future in as_completed(futures):
            shots.extend(future.result())
            time.sleep(0.001)

    frame = pd.DataFrame(shots)
    if frame.empty:
        return pd.DataFrame(), pd.DataFrame()

    frame["xG_num"] = _num(frame, "xG")
    frame = frame.loc[frame["xG_num"] >= float(threshold)].copy()
    if frame.empty:
        return pd.DataFrame(), pd.DataFrame()

    frame["team"] = np.where(frame["h_a"] == "h", frame["h_team"], frame["a_team"])
    frame["team_key"] = frame["team"].apply(_canon_team)
    frame["understat_player_id"] = _num(frame, "player_id").astype(int)

    received = (
        frame.groupby(["understat_player_id"], as_index=False)
        .agg(
            understat_big_chance_received=("id", "count"),
            understat_big_chance_xG=("xG_num", "sum"),
            understat_avg_big_chance_xG=("xG_num", "mean"),
        )
    )

    created_frame = frame.loc[frame["player_assisted"].fillna("").astype(str).str.strip() != ""].copy()
    if created_frame.empty:
        created = pd.DataFrame()
    else:
        created_frame["creator_key"] = created_frame["player_assisted"].apply(_norm) + "|" + created_frame["team_key"]
        created = (
            created_frame.groupby(["creator_key"], as_index=False)
            .agg(
                understat_big_chance_created=("id", "count"),
                understat_big_chance_created_xG=("xG_num", "sum"),
            )
        )
    return received, created


def build_understat_shot_profiles(
    league: str = "EPL",
    season: int | None = None,
    cache_dir: Path = DATA_DIR / "understat",
    include_big_chances: bool = False,
    refresh: bool = False,
    big_chance_threshold: float = BIG_CHANCE_XG_THRESHOLD,
) -> pd.DataFrame:
    season = int(season or understat_season_from_date())
    league_data = fetch_understat_league(league=league, season=season, cache_dir=cache_dir, refresh=refresh)
    profiles = _aggregate_player_profiles(league_data, league=league, season=season)
    if profiles.empty:
        return profiles

    profiles["understat_big_chance_threshold_xg"] = big_chance_threshold
    for col in [
        "understat_big_chance_received", "understat_big_chance_xG", "understat_avg_big_chance_xG",
        "understat_big_chance_created", "understat_big_chance_created_xG",
    ]:
        profiles[col] = 0.0

    if include_big_chances:
        received, created = _big_chance_profiles(
            league_data,
            cache_dir=cache_dir,
            refresh=refresh,
            threshold=big_chance_threshold,
        )
        if not received.empty:
            profiles = profiles.merge(received, on="understat_player_id", how="left", suffixes=("", "_detail"))
            for col in ["understat_big_chance_received", "understat_big_chance_xG", "understat_avg_big_chance_xG"]:
                detail = f"{col}_detail"
                if detail in profiles.columns:
                    profiles[col] = profiles[detail].fillna(profiles[col])
            profiles = profiles.drop(columns=[c for c in profiles.columns if c.endswith("_detail")])
        if not created.empty:
            profiles = profiles.merge(created, left_on="player_key", right_on="creator_key", how="left")
            for col in ["understat_big_chance_created", "understat_big_chance_created_xG"]:
                profiles[col] = profiles[f"{col}_y"].fillna(profiles[f"{col}_x"]) if f"{col}_y" in profiles.columns else profiles[col]
            profiles = profiles.drop(columns=[c for c in profiles.columns if c.endswith("_x") or c.endswith("_y") or c == "creator_key"])

    matches90 = profiles["understat_minutes"] / 90.0
    profiles["understat_big_chance_received90"] = _safe_div(profiles["understat_big_chance_received"], matches90)
    profiles["understat_big_chance_created90"] = _safe_div(profiles["understat_big_chance_created"], matches90)
    profiles["understat_big_chance_xG_share"] = _safe_div(profiles["understat_big_chance_xG"], profiles["xG"])
    profiles["understat_profile_source"] = "understat_league_json"
    if include_big_chances:
        profiles["understat_profile_source"] = "understat_league_and_match_json"

    cols = [
        "understat_league", "understat_season", "understat_player_id", "player", "team", "team_key",
        "player_key", "position", "understat_games", "understat_minutes", "understat_goals",
        "understat_assists", "xG", "npxG", "xA", "understat_shots", "understat_key_passes",
        "understat_xG90", "understat_npxG90", "understat_xA90", "understat_shots90",
        "understat_chances_created90", "understat_key_passes90", "understat_xG_per_shot",
        "understat_npxG_per_shot", "understat_xA_per_chance", "understat_xA_per_key_pass",
        "understat_big_chance_threshold_xg", "understat_big_chance_received",
        "understat_big_chance_received90", "understat_big_chance_xG",
        "understat_big_chance_xG_share", "understat_avg_big_chance_xG",
        "understat_big_chance_created", "understat_big_chance_created90",
        "understat_big_chance_created_xG", "understat_xGChain90", "understat_xGBuildup90",
        "understat_profile_source",
    ]
    return profiles[[c for c in cols if c in profiles.columns]].sort_values(["team", "player"]).reset_index(drop=True)


def attach_understat_profiles_to_players(
    players: pd.DataFrame,
    teams: pd.DataFrame,
    profiles: pd.DataFrame,
) -> pd.DataFrame:
    if players.empty or teams.empty or profiles.empty:
        out = players.copy()
        out["understat_profile_matched"] = False
        return out

    out = players.copy()
    team_names = teams[["id", "name"]].rename(columns={"id": "team", "name": "team_name"})
    out = out.merge(team_names, on="team", how="left")
    out["team_key"] = out["team_name"].apply(_canon_team)
    out["full_name_key"] = (out.get("first_name", "").fillna("").astype(str) + " " + out.get("second_name", "").fillna("").astype(str)).apply(_norm)
    out["web_name_key"] = out.get("web_name", "").fillna("").astype(str).apply(_norm)
    out["full_player_key"] = out["full_name_key"] + "|" + out["team_key"]
    out["web_player_key"] = out["web_name_key"] + "|" + out["team_key"]

    profile_cols = [c for c in profiles.columns if c not in {"player_key", "player", "team", "team_key"}]
    profile_lookup = profiles[["player_key", "player", "team", *profile_cols]].drop_duplicates("player_key")
    merged = out.merge(profile_lookup, left_on="full_player_key", right_on="player_key", how="left", suffixes=("", "_understat"))
    missing = merged["understat_player_id"].isna() if "understat_player_id" in merged.columns else pd.Series(True, index=merged.index)

    if missing.any():
        fallback = out.loc[missing, ["id", "web_player_key"]].merge(
            profile_lookup,
            left_on="web_player_key",
            right_on="player_key",
            how="left",
            suffixes=("", "_web"),
        )
        fallback = fallback.set_index("id")
        for col in ["player_key", "player", "team", *profile_cols]:
            if col in fallback.columns:
                merged.loc[missing, col] = merged.loc[missing, "id"].map(fallback[col])

    merged["understat_profile_matched"] = merged.get("understat_player_id", pd.Series(index=merged.index)).notna()
    return merged.drop(
        columns=[
            "team_name", "team_key", "full_name_key", "web_name_key", "full_player_key",
            "web_player_key", "player_key", "player_understat", "team_understat",
        ],
        errors="ignore",
    )
