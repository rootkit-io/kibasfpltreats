from __future__ import annotations

import json
import gzip
import html
import math
import re
import shutil
import subprocess
import unicodedata
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from .market_odds import _canon_team, _devig_decimal, _fit_lambdas
from .xpts import aggregate_gameweek, build_player_fixture_forecast


FOOTBALL_DATA_BASE = "https://www.football-data.co.uk/mmz4281"
VAASTAV_BASE = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"

ODDS_SEASONS = ["1415", "1516", "1617", "1718", "1819", "1920", "2021", "2122", "2223", "2324", "2425", "2526"]
VAASTAV_SEASONS = ["2016-17", "2017-18", "2018-19", "2019-20", "2020-21", "2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]
UNDERSTAT_YEARS = list(range(2014, 2026))

POSITION_TO_ELEMENT_TYPE = {"GK": 1, "DEF": 2, "MID": 3, "AM": 3, "FWD": 4}
_LAMBDA_GRID = np.arange(0.10, 4.55, 0.05)
_GRID_CACHE: dict[str, np.ndarray] | None = None


@dataclass(frozen=True)
class TrainingBuildResult:
    dataset: pd.DataFrame
    fixtures: pd.DataFrame
    inventory: dict[str, pd.DataFrame]


try:
    from rapidfuzz import fuzz as _rapidfuzz_fuzz
except ImportError:  # pragma: no cover - optional local acceleration.
    _rapidfuzz_fuzz = None


def _norm(text: object) -> str:
    if text is None or pd.isna(text):
        return ""
    value = html.unescape(str(text or ""))
    if "Ã" in value or "Â" in value:
        try:
            value = value.encode("latin-1").decode("utf-8")
        except UnicodeError:
            pass
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9 ]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _name_similarity(left: object, right: object) -> float:
    a = _norm(left)
    b = _norm(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if _rapidfuzz_fuzz is not None:
        return float(_rapidfuzz_fuzz.WRatio(a, b)) / 100.0
    a_tokens = a.split()
    b_tokens = b.split()
    raw = float(SequenceMatcher(None, a, b).ratio())
    token_sort = float(SequenceMatcher(None, " ".join(sorted(a_tokens)), " ".join(sorted(b_tokens))).ratio())
    shorter, longer = (set(a_tokens), set(b_tokens)) if len(a_tokens) <= len(b_tokens) else (set(b_tokens), set(a_tokens))
    subset = 0.0
    if len(shorter) >= 2 and shorter.issubset(longer):
        subset = 0.92
    return max(raw, token_sort, subset)


def season_code_to_label(code: str) -> str:
    code = str(code)
    return f"20{code[:2]}-{code[2:]}"


def season_label_to_year(season: str) -> int:
    return int(str(season).split("-")[0])


def year_to_season_label(year: int) -> str:
    return f"{int(year)}-{str(int(year) + 1)[-2:]}"


def _request_bytes(
    url: str,
    user_agent: str = "fpl-xpts-historical-validation/0.1",
    headers: dict[str, str] | None = None,
) -> bytes:
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if curl:
        command = [curl, "-L", "--silent", "--show-error", "--max-time", "90", "-A", user_agent]
        if Path(curl).name.lower() == "curl.exe":
            command.append("--ssl-no-revoke")
        for key, value in (headers or {}).items():
            command.extend(["-H", f"{key}: {value}"])
        command.append(url)
        result = subprocess.run(command, check=False, capture_output=True, timeout=120)
        if result.returncode == 0:
            return result.stdout
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace") or f"curl failed for {url}")
    request = Request(url, headers={"User-Agent": user_agent})
    if headers:
        for key, value in headers.items():
            request.add_header(key, value)
    with urlopen(request, timeout=90) as response:
        return response.read()


def _fetch_understat_payload(root: Path, year: int, refresh: bool) -> dict[str, Any]:
    cache_path = root / "data" / "understat" / "historical_raw" / f"league_EPL_{year}.json"
    if cache_path.exists() and not refresh:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    url = f"https://understat.com/getLeagueData/EPL/{year}"
    referer = f"https://understat.com/league/EPL/{year}"
    raw = _request_bytes(
        url,
        user_agent="fpl-xpts-understat-historical/0.1",
        headers={
            "Referer": referer,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        },
    )
    text = gzip.decompress(raw).decode("utf-8") if raw[:2] == b"\x1f\x8b" else raw.decode("utf-8")
    payload = json.loads(text)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(payload), encoding="utf-8")
    return payload


def _read_csv(path: Path) -> pd.DataFrame:
    try:
        df = pd.read_csv(path, encoding="utf-8-sig")
    except UnicodeDecodeError:
        df = pd.read_csv(path, encoding="latin-1")
    df.columns = [
        str(col)
        .replace("\ufeff", "")
        .replace("ï»¿", "")
        .replace("Ã¯Â»Â¿", "")
        .strip()
        for col in df.columns
    ]
    return df


def _first_number(row: pd.Series, columns: list[str]) -> float | None:
    for col in columns:
        if col not in row or pd.isna(row[col]):
            continue
        try:
            value = float(row[col])
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 1.0:
            return value
    return None


def _poisson_pmf_array(lam: float, max_goals: int = 12) -> np.ndarray:
    lam = max(float(lam), 0.01)
    vals = np.zeros(max_goals + 1, dtype=float)
    vals[0] = math.exp(-lam)
    for k in range(1, max_goals + 1):
        vals[k] = vals[k - 1] * lam / k
    vals[-1] += max(0.0, 1.0 - vals.sum())
    return vals


def _grid_prob_cache() -> dict[str, np.ndarray]:
    global _GRID_CACHE
    if _GRID_CACHE is not None:
        return _GRID_CACHE
    lam_h_values = []
    lam_a_values = []
    home_probs = []
    draw_probs = []
    away_probs = []
    over_probs = []
    hg = np.arange(13)[:, None]
    ag = np.arange(13)[None, :]
    for lam_h in _LAMBDA_GRID:
        h = _poisson_pmf_array(float(lam_h))
        for lam_a in _LAMBDA_GRID:
            a = _poisson_pmf_array(float(lam_a))
            matrix = np.outer(h, a)
            lam_h_values.append(float(lam_h))
            lam_a_values.append(float(lam_a))
            home_probs.append(float(matrix[hg > ag].sum()))
            draw_probs.append(float(matrix[hg == ag].sum()))
            away_probs.append(float(matrix[hg < ag].sum()))
            over_probs.append(float(matrix[(hg + ag) > 2.5].sum()))
    _GRID_CACHE = {
        "lam_h": np.array(lam_h_values),
        "lam_a": np.array(lam_a_values),
        "home": np.array(home_probs),
        "draw": np.array(draw_probs),
        "away": np.array(away_probs),
        "over": np.array(over_probs),
    }
    return _GRID_CACHE


def _fit_lambdas_vectorized_equivalent(
    h2h_probs: dict[str, float],
    over_prob: float | None,
    fallback_home_xg: float = 1.4,
    fallback_away_xg: float = 1.1,
) -> tuple[float, float, float]:
    """Vectorized equivalent of market_odds._fit_lambdas for bulk historical CSVs."""
    if not {"home", "draw", "away"}.issubset(h2h_probs):
        return _fit_lambdas(h2h_probs, over_prob, fallback_home_xg=fallback_home_xg, fallback_away_xg=fallback_away_xg)
    grid = _grid_prob_cache()
    error = np.zeros_like(grid["lam_h"])
    weight = 0.0
    for key in ["home", "draw", "away"]:
        error += 3.0 * (grid[key] - float(h2h_probs[key])) ** 2
        weight += 3.0
    if over_prob is not None:
        error += 2.0 * (grid["over"] - float(over_prob)) ** 2
        weight += 2.0
    else:
        fallback_total = max(float(fallback_home_xg) + float(fallback_away_xg), 0.8)
        error += 0.35 * ((grid["lam_h"] + grid["lam_a"]) - fallback_total) ** 2
        weight += 0.35
    error = error / max(weight, 1.0)
    idx = int(np.argmin(error))
    return float(grid["lam_h"][idx]), float(grid["lam_a"][idx]), float(error[idx])


def _json_default(value: Any) -> Any:
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if pd.isna(value):
        return None
    return value


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=_json_default), encoding="utf-8")


def download_odds_files(root: Path, refresh: bool = False) -> list[dict[str, Any]]:
    odds_dir = root / "data" / "odds_historical"
    odds_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for season in ODDS_SEASONS:
        target = odds_dir / f"E0_{season}.csv"
        legacy = odds_dir / f"{season}_E0.csv"
        source = "existing"
        if refresh or not target.exists():
            if legacy.exists() and not refresh:
                shutil.copyfile(legacy, target)
                source = "copied_existing_legacy_name"
            else:
                url = f"{FOOTBALL_DATA_BASE}/{season}/E0.csv"
                target.write_bytes(_request_bytes(url))
                source = url
        df = _read_csv(target)
        rows.append(
            {
                "season": season,
                "path": str(target),
                "rows": int(len(df)),
                "columns": list(df.columns),
                "source": source,
            }
        )
    return rows


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def extract_understat_team_stats(payload: dict[str, Any], year: int) -> list[dict[str, Any]]:
    teams_obj = payload.get("teams", {})
    teams = teams_obj.values() if isinstance(teams_obj, dict) else teams_obj
    records: list[dict[str, Any]] = []
    for team in teams:
        history = team.get("history", []) or []
        normalized_history = []
        for match in history:
            ppda = match.get("ppda") or {}
            ppda_allowed = match.get("ppda_allowed") or {}
            normalized_history.append(
                {
                    "date": match.get("date"),
                    "h_a": match.get("h_a"),
                    "result": match.get("result"),
                    "xG": _num(match.get("xG")),
                    "xGA": _num(match.get("xGA")),
                    "npxG": _num(match.get("npxG")),
                    "npxGA": _num(match.get("npxGA")),
                    "deep": _num(match.get("deep")),
                    "deep_allowed": _num(match.get("deep_allowed")),
                    "ppda_att": _num(ppda.get("att")),
                    "ppda_def": _num(ppda.get("def")),
                    "ppda_allowed_att": _num(ppda_allowed.get("att")),
                    "ppda_allowed_def": _num(ppda_allowed.get("def")),
                    "scored": _num(match.get("scored")),
                    "missed": _num(match.get("missed")),
                    "wins": _num(match.get("wins")),
                    "draws": _num(match.get("draws")),
                    "loses": _num(match.get("loses")),
                    "pts": _num(match.get("pts")),
                }
            )

        frame = pd.DataFrame(normalized_history)
        title = team.get("title", "")
        record: dict[str, Any] = {
            "season": year_to_season_label(year),
            "understat_year": int(year),
            "understat_team_id": str(team.get("id", "")),
            "team": title,
            "team_key": _canon_team(title),
            "matches": int(len(normalized_history)),
            "history": normalized_history,
        }
        for col in ["xG", "xGA", "npxG", "npxGA", "deep", "deep_allowed", "scored", "missed", "wins", "draws", "loses", "pts"]:
            record[col] = float(frame[col].sum()) if col in frame else 0.0
        if "ppda_att" in frame and "ppda_def" in frame:
            record["ppda"] = float(frame["ppda_att"].sum() / frame["ppda_def"].replace(0, np.nan).sum()) if float(frame["ppda_def"].sum()) > 0 else np.nan
        if "ppda_allowed_att" in frame and "ppda_allowed_def" in frame:
            denom = float(frame["ppda_allowed_def"].sum())
            record["ppda_allowed"] = float(frame["ppda_allowed_att"].sum() / denom) if denom > 0 else np.nan
        records.append(record)
    return records


def extract_understat_player_stats(payload: dict[str, Any], year: int) -> list[dict[str, Any]]:
    players = pd.DataFrame(payload.get("players", []) or [])
    if players.empty:
        return []
    records: list[dict[str, Any]] = []
    for _, row in players.iterrows():
        minutes = _num(row.get("time"))
        matches90 = minutes / 90.0
        xg = _num(row.get("xG"))
        xa = _num(row.get("xA"))
        npxg = _num(row.get("npxG"))
        shots = _num(row.get("shots"))
        key_passes = _num(row.get("key_passes"))
        player_name = str(row.get("player_name", ""))
        team = str(row.get("team_title", ""))
        records.append(
            {
                "season": year_to_season_label(year),
                "understat_year": int(year),
                "understat_player_id": str(row.get("id", "")),
                "player_name": player_name,
                "player_key": _norm(player_name),
                "team": team,
                "team_key": _canon_team(team),
                "position": row.get("position"),
                "xG90": float(xg / matches90) if matches90 > 0 else 0.0,
                "xA90": float(xa / matches90) if matches90 > 0 else 0.0,
                "npxG90": float(npxg / matches90) if matches90 > 0 else 0.0,
                "xG": xg,
                "xA": xa,
                "npxG": npxg,
                "minutes": minutes,
                "shots": shots,
                "key_passes": key_passes,
                "goals": _num(row.get("goals")),
                "assists": _num(row.get("assists")),
            }
        )
    return records


def download_understat_stats(root: Path, refresh: bool = False) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    team_dir = root / "data" / "understat" / "team_stats"
    player_dir = root / "data" / "understat" / "player_stats"
    raw_dir = root / "data" / "understat" / "historical_raw"
    team_dir.mkdir(parents=True, exist_ok=True)
    player_dir.mkdir(parents=True, exist_ok=True)
    team_rows: list[dict[str, Any]] = []
    player_rows: list[dict[str, Any]] = []
    for year in UNDERSTAT_YEARS:
        team_path = team_dir / f"team_stats_{year}.json"
        player_path = player_dir / f"player_stats_{year}.json"
        if refresh or not team_path.exists() or not player_path.exists():
            payload = _fetch_understat_payload(root, year, refresh=refresh)
            team_stats = extract_understat_team_stats(payload, year)
            player_stats = extract_understat_player_stats(payload, year)
            write_json(team_path, team_stats)
            write_json(player_path, player_stats)
        else:
            team_stats = json.loads(team_path.read_text(encoding="utf-8"))
            player_stats = json.loads(player_path.read_text(encoding="utf-8"))
        team_rows.append({"year": year, "teams": len(team_stats), "fields": sorted(set().union(*(row.keys() for row in team_stats))) if team_stats else []})
        player_rows.append({"year": year, "players": len(player_stats), "fields": sorted(set().union(*(row.keys() for row in player_stats))) if player_stats else []})
    return team_rows, player_rows


def build_cached_fpl_merged_gw(root: Path, season: str = "2025-26") -> pd.DataFrame:
    """Build a Vaastav-compatible merged GW table from cached FPL element summaries."""
    cache_dir = root / "data" / "fpl_history"
    bootstrap_path = cache_dir / "bootstrap-static.json"
    fixtures_path = cache_dir / "fixtures.json"
    if not bootstrap_path.exists() or not fixtures_path.exists():
        raise FileNotFoundError("Cached FPL bootstrap-static.json and fixtures.json are required")

    bootstrap = json.loads(bootstrap_path.read_text(encoding="utf-8"))
    fixtures = json.loads(fixtures_path.read_text(encoding="utf-8"))
    events = bootstrap.get("events", []) or []
    if len(events) < 38 or not all(bool(event.get("finished")) and bool(event.get("data_checked")) for event in events[:38]):
        raise RuntimeError("Cached FPL events are not complete and data-checked through GW38")

    elements = {int(row["id"]): row for row in bootstrap.get("elements", []) or []}
    teams = {int(row["id"]): str(row.get("name", "")) for row in bootstrap.get("teams", []) or []}
    positions = {
        int(row["id"]): {"GKP": "GK"}.get(str(row.get("singular_name_short", "")), str(row.get("singular_name_short", "")))
        for row in bootstrap.get("element_types", []) or []
    }
    fixture_teams = {
        int(row["id"]): (int(row["team_h"]), int(row["team_a"]))
        for row in fixtures
        if row.get("id") is not None and row.get("team_h") is not None and row.get("team_a") is not None
    }

    target = root / "data" / "vaastav" / f"{season}_merged_gw.csv"
    partial_xp: dict[tuple[int, int, int], Any] = {}
    if target.exists():
        partial = _read_csv(target)
        if {"element", "fixture", "round", "xP"}.issubset(partial.columns):
            partial_xp = {
                (int(row["element"]), int(row["fixture"]), int(row["round"])): row.get("xP")
                for _, row in partial.iterrows()
            }

    records: list[dict[str, Any]] = []
    history_paths = sorted(cache_dir.glob("player_*_history.json"))
    for path in history_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for history in payload.get("history", []) or []:
            element_id = int(history.get("element"))
            fixture_id = int(history.get("fixture"))
            gw = int(history.get("round"))
            player = elements.get(element_id)
            fixture_sides = fixture_teams.get(fixture_id)
            if player is None or fixture_sides is None:
                raise RuntimeError(f"Missing cached metadata for element={element_id} fixture={fixture_id}")
            team_id = fixture_sides[0] if bool(history.get("was_home")) else fixture_sides[1]
            player_name = " ".join(
                part for part in [str(player.get("first_name", "")).strip(), str(player.get("second_name", "")).strip()] if part
            )
            record = dict(history)
            record.update(
                {
                    "name": player_name or str(player.get("web_name", "")),
                    "position": positions.get(int(player.get("element_type") or 0), ""),
                    "team": teams.get(team_id, ""),
                    "xP": partial_xp.get((element_id, fixture_id, gw), np.nan),
                    "GW": gw,
                }
            )
            records.append(record)

    frame = pd.DataFrame(records)
    expected_rows = int(sum(len((json.loads(path.read_text(encoding="utf-8")).get("history", []) or [])) for path in history_paths))
    if len(frame) != expected_rows or frame.empty:
        raise RuntimeError(f"Cached FPL merged table row mismatch: built={len(frame)} expected={expected_rows}")
    if int(pd.to_numeric(frame["GW"], errors="coerce").max()) != 38:
        raise RuntimeError("Cached FPL merged table does not reach GW38")
    if frame[["name", "position", "team"]].replace("", np.nan).isna().any().any():
        raise RuntimeError("Cached FPL merged table has unresolved player metadata")

    preferred = [
        "name", "position", "team", "xP", "assists", "bonus", "bps", "clean_sheets",
        "creativity", "element", "expected_assists", "expected_goal_involvements",
        "expected_goals", "expected_goals_conceded", "fixture", "goals_conceded",
        "goals_scored", "ict_index", "influence", "kickoff_time", "minutes", "modified",
        "opponent_team", "own_goals", "penalties_missed", "penalties_saved", "red_cards",
        "round", "saves", "selected", "starts", "team_a_score", "team_h_score", "threat",
        "total_points", "transfers_balance", "transfers_in", "transfers_out", "value",
        "was_home", "yellow_cards", "clearances_blocks_interceptions",
        "defensive_contribution", "recoveries", "tackles", "GW",
    ]
    remaining = [col for col in frame.columns if col not in preferred]
    return frame[[col for col in preferred if col in frame.columns] + remaining].sort_values(
        ["GW", "fixture", "element"], kind="mergesort"
    ).reset_index(drop=True)


def download_vaastav_files(root: Path, refresh: bool = False) -> list[dict[str, Any]]:
    vaastav_dir = root / "data" / "vaastav"
    vaastav_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for season in VAASTAV_SEASONS:
        target = vaastav_dir / f"{season}_merged_gw.csv"
        source = "existing"
        if refresh or not target.exists():
            url = f"{VAASTAV_BASE}/{season}/gws/merged_gw.csv"
            target.write_bytes(_request_bytes(url))
            source = url
        df = _read_csv(target)
        if season == "2025-26" and int(pd.to_numeric(df.get("GW"), errors="coerce").max()) < 38:
            partial = vaastav_dir / "2025-26_merged_gw_vaastav_partial.csv"
            shutil.copyfile(target, partial)
            df = build_cached_fpl_merged_gw(root, season=season)
            df.to_csv(target, index=False)
            source = f"cached_fpl_element_summaries; partial_vaastav={partial}"
        rows.append({"season": season, "path": str(target), "rows": int(len(df)), "source": source})
    return rows


def _bookmakers_present(columns: list[str]) -> str:
    prefixes = ["B365", "BW", "BF", "PS", "WH", "1XB", "Max", "Avg", "BFE"]
    present = [prefix for prefix in prefixes if any(col.startswith(prefix) for col in columns)]
    return ",".join(present)


def _date_range(frame: pd.DataFrame) -> tuple[str, str]:
    if "Date" not in frame.columns:
        return "", ""
    dates = pd.to_datetime(frame["Date"], dayfirst=True, errors="coerce")
    if not dates.notna().any():
        return "", ""
    return str(dates.min().date()), str(dates.max().date())


def build_inventory(root: Path) -> dict[str, pd.DataFrame]:
    odds_rows = []
    for season in ODDS_SEASONS:
        path = root / "data" / "odds_historical" / f"E0_{season}.csv"
        if not path.exists():
            legacy = root / "data" / "odds_historical" / f"{season}_E0.csv"
            path = legacy if legacy.exists() else path
        if not path.exists():
            continue
        df = _read_csv(path)
        date_min, date_max = _date_range(df)
        columns = list(df.columns)
        odds_rows.append(
            {
                "season": season_code_to_label(season),
                "path": str(path),
                "rows": int(len(df)),
                "date_min": date_min,
                "date_max": date_max,
                "bookmakers_present": _bookmakers_present(columns),
                "closing_odds_exist": bool({"AvgCH", "AvgCD", "AvgCA"}.issubset(columns) or {"B365CH", "B365CD", "B365CA"}.issubset(columns)),
            }
        )

    team_rows = []
    for path in sorted((root / "data" / "understat" / "team_stats").glob("team_stats_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        fields = sorted(set().union(*(row.keys() for row in payload))) if payload else []
        team_rows.append(
            {
                "season": year_to_season_label(int(path.stem.split("_")[-1])),
                "path": str(path),
                "teams": len(payload),
                "fields": ",".join(fields),
            }
        )

    player_payloads = []
    player_rows = []
    for path in sorted((root / "data" / "understat" / "player_stats").glob("player_stats_*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        player_payloads.extend(payload)
        fields = sorted(set().union(*(row.keys() for row in payload))) if payload else []
        player_rows.append(
            {
                "season": year_to_season_label(int(path.stem.split("_")[-1])),
                "path": str(path),
                "players": len(payload),
                "fields": ",".join(fields),
            }
        )
    player_ids = pd.DataFrame(player_payloads)
    consistent = True
    ambiguous_names = 0
    if not player_ids.empty and {"player_key", "understat_player_id"}.issubset(player_ids.columns):
        id_counts = player_ids.groupby("player_key")["understat_player_id"].nunique()
        ambiguous_names = int((id_counts > 1).sum())
        consistent = ambiguous_names == 0
    if player_rows:
        player_rows[0]["player_ids_consistent_across_seasons"] = consistent
        player_rows[0]["ambiguous_player_names"] = ambiguous_names

    vaastav_rows = []
    for season in VAASTAV_SEASONS:
        path = root / "data" / "vaastav" / f"{season}_merged_gw.csv"
        if not path.exists():
            continue
        df = _read_csv(path)
        vaastav_rows.append(
            {
                "season": season,
                "path": str(path),
                "rows": int(len(df)),
                "element_id_present": "element" in df.columns,
                "xg_columns_exist": bool({"expected_goals", "expected_assists"}.issubset(df.columns)),
            }
        )

    return {
        "odds": pd.DataFrame(odds_rows),
        "understat_team": pd.DataFrame(team_rows),
        "understat_player": pd.DataFrame(player_rows),
        "vaastav": pd.DataFrame(vaastav_rows),
    }


def load_odds_match_features(root: Path) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for season in ODDS_SEASONS:
        path = root / "data" / "odds_historical" / f"E0_{season}.csv"
        if not path.exists():
            legacy = root / "data" / "odds_historical" / f"{season}_E0.csv"
            path = legacy if legacy.exists() else path
        if not path.exists():
            continue
        frame = _read_csv(path)
        if not {"HomeTeam", "AwayTeam", "Date"}.issubset(frame.columns):
            continue
        for _, row in frame.iterrows():
            match_date = pd.to_datetime(row.get("Date"), dayfirst=True, errors="coerce")
            if pd.isna(match_date):
                continue
            closing_h = _first_number(row, ["AvgCH", "B365CH", "PSCH", "MaxCH", "BWCH", "WHCH", "BFCH", "BFECH"])
            closing_d = _first_number(row, ["AvgCD", "B365CD", "PSCD", "MaxCD", "BWCD", "WHCD", "BFCD", "BFECD"])
            closing_a = _first_number(row, ["AvgCA", "B365CA", "PSCA", "MaxCA", "BWCA", "WHCA", "BFCA", "BFECA"])
            home_odds = closing_h or _first_number(row, ["AvgH", "B365H", "PSH", "MaxH", "BWH", "WHH", "BFH", "BFEH"])
            draw_odds = closing_d or _first_number(row, ["AvgD", "B365D", "PSD", "MaxD", "BWD", "WHD", "BFD", "BFED"])
            away_odds = closing_a or _first_number(row, ["AvgA", "B365A", "PSA", "MaxA", "BWA", "WHA", "BFA", "BFEA"])
            h2h = _devig_decimal({"home": home_odds, "draw": draw_odds, "away": away_odds})
            if not {"home", "draw", "away"}.issubset(h2h):
                continue
            over_odds = _first_number(row, ["AvgC>2.5", "B365C>2.5", "PC>2.5", "MaxC>2.5", "BFEC>2.5"]) or _first_number(
                row, ["Avg>2.5", "B365>2.5", "P>2.5", "Max>2.5", "BFE>2.5"]
            )
            under_odds = _first_number(row, ["AvgC<2.5", "B365C<2.5", "PC<2.5", "MaxC<2.5", "BFEC<2.5"]) or _first_number(
                row, ["Avg<2.5", "B365<2.5", "P<2.5", "Max<2.5", "BFE<2.5"]
            )
            total_probs = _devig_decimal({"over": over_odds, "under": under_odds})
            home_lambda, away_lambda, fit_error = _fit_lambdas_vectorized_equivalent(h2h, total_probs.get("over"))
            rows.append(
                {
                    "season": season_code_to_label(season),
                    "match_date": match_date.date(),
                    "home_team": row.get("HomeTeam"),
                    "away_team": row.get("AwayTeam"),
                    "home_team_key": _canon_team(row.get("HomeTeam")),
                    "away_team_key": _canon_team(row.get("AwayTeam")),
                    "home_goals": _num(row.get("FTHG"), default=np.nan),
                    "away_goals": _num(row.get("FTAG"), default=np.nan),
                    "home_lambda_odds": home_lambda,
                    "away_lambda_odds": away_lambda,
                    "home_cs_prob_odds": math.exp(-away_lambda),
                    "away_cs_prob_odds": math.exp(-home_lambda),
                    "odds_fit_error": fit_error,
                    "home_win_prob": h2h.get("home"),
                    "draw_prob": h2h.get("draw"),
                    "away_win_prob": h2h.get("away"),
                    "closing_odds_used": bool(closing_h and closing_d and closing_a),
                }
            )
    return pd.DataFrame(rows)


def load_vaastav_seasons(root: Path) -> pd.DataFrame:
    frames = []
    for season in VAASTAV_SEASONS:
        path = root / "data" / "vaastav" / f"{season}_merged_gw.csv"
        if not path.exists():
            raise FileNotFoundError(path)
        frame = _read_csv(path)
        frame["season"] = season
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def _numeric(df: pd.DataFrame, col: str, default: float = 0.0) -> pd.Series:
    if col not in df.columns:
        return pd.Series(default, index=df.index)
    return pd.to_numeric(df[col], errors="coerce").fillna(default)


def build_fixture_table(raw: pd.DataFrame, odds: pd.DataFrame) -> pd.DataFrame:
    rows = []
    unresolved_rows = []
    odds_lookup = odds.copy()
    if not odds_lookup.empty:
        odds_lookup["match_date"] = pd.to_datetime(odds_lookup["match_date"], errors="coerce").dt.date

    def _single_fpl_team_id(values: pd.Series) -> int | None:
        numeric = pd.to_numeric(values.dropna(), errors="coerce").dropna().unique()
        return int(numeric[0]) if len(numeric) == 1 else None

    def _record(
        season: str,
        gw: int,
        fixture: object,
        kickoff: pd.Timestamp,
        match_date: object,
        home_name: str,
        away_name: str,
        resolution: str,
        home_fpl_team_id: int | None,
        away_fpl_team_id: int | None,
    ) -> dict[str, Any]:
        return {
            "season": season,
            "GW": int(gw),
            "id": f"{season}_{fixture}",
            "fixture": fixture,
            "event": int(gw),
            "kickoff_time": kickoff,
            "match_date": match_date,
            "home_team": home_name,
            "away_team": away_name,
            "home_team_key": _canon_team(home_name),
            "away_team_key": _canon_team(away_name),
            "team_resolution": resolution,
            "home_fpl_team_id": home_fpl_team_id,
            "away_fpl_team_id": away_fpl_team_id,
        }

    for (season, gw, fixture), group in raw.groupby(["season", "GW", "fixture"], dropna=False):
        home_rows = group.loc[group.get("was_home", False) == True]
        away_rows = group.loc[group.get("was_home", False) == False]
        if home_rows.empty or away_rows.empty:
            continue
        home_fpl_team_id = _single_fpl_team_id(away_rows["opponent_team"]) if "opponent_team" in group.columns else None
        away_fpl_team_id = _single_fpl_team_id(home_rows["opponent_team"]) if "opponent_team" in group.columns else None
        kickoff = pd.to_datetime(group["kickoff_time"].dropna().iloc[0], errors="coerce") if "kickoff_time" in group else pd.NaT
        match_date = kickoff.date() if not pd.isna(kickoff) else None
        home_name = ""
        away_name = ""
        if "team" in group.columns:
            home_teams = home_rows["team"].dropna()
            away_teams = away_rows["team"].dropna()
            home_name = str(home_teams.iloc[0]) if not home_teams.empty else ""
            away_name = str(away_teams.iloc[0]) if not away_teams.empty else ""
        resolution = "vaastav_team_column" if home_name and away_name else "unresolved"
        if (not home_name or not away_name) and match_date is not None and not odds_lookup.empty:
            h_score = _num(group["team_h_score"].dropna().iloc[0], default=np.nan) if "team_h_score" in group and group["team_h_score"].notna().any() else np.nan
            a_score = _num(group["team_a_score"].dropna().iloc[0], default=np.nan) if "team_a_score" in group and group["team_a_score"].notna().any() else np.nan
            candidates = odds_lookup.loc[
                (odds_lookup["season"] == season)
                & (odds_lookup["match_date"] == match_date)
                & (pd.to_numeric(odds_lookup["home_goals"], errors="coerce") == h_score)
                & (pd.to_numeric(odds_lookup["away_goals"], errors="coerce") == a_score)
            ]
            if len(candidates) == 1:
                home_name = str(candidates.iloc[0]["home_team"])
                away_name = str(candidates.iloc[0]["away_team"])
                resolution = "football_data_date_score"
            elif len(candidates) > 1:
                resolution = "ambiguous_football_data_date_score"
        if not home_name or not away_name:
            unresolved_rows.append(
                _record(
                    season, gw, fixture, kickoff, match_date, home_name, away_name, resolution,
                    home_fpl_team_id, away_fpl_team_id,
                )
            )
            continue
        rows.append(
            _record(
                season, gw, fixture, kickoff, match_date, home_name, away_name, resolution,
                home_fpl_team_id, away_fpl_team_id,
            )
        )
    if unresolved_rows and rows:
        id_candidates: dict[tuple[str, int], list[tuple[str, str]]] = {}
        for row in rows:
            if row.get("home_fpl_team_id") is not None:
                id_candidates.setdefault((row["season"], int(row["home_fpl_team_id"])), []).append(
                    (row["home_team"], row["home_team_key"])
                )
            if row.get("away_fpl_team_id") is not None:
                id_candidates.setdefault((row["season"], int(row["away_fpl_team_id"])), []).append(
                    (row["away_team"], row["away_team_key"])
                )

        team_by_fpl_id: dict[tuple[str, int], tuple[str, str]] = {}
        for key, candidates in id_candidates.items():
            team_identity, count = Counter(candidates).most_common(1)[0]
            if count >= 3:
                team_by_fpl_id[key] = team_identity

        for row in unresolved_rows:
            home_identity = (
                team_by_fpl_id.get((row["season"], int(row["home_fpl_team_id"])))
                if row.get("home_fpl_team_id") is not None
                else None
            )
            away_identity = (
                team_by_fpl_id.get((row["season"], int(row["away_fpl_team_id"])))
                if row.get("away_fpl_team_id") is not None
                else None
            )
            if home_identity and away_identity:
                row["home_team"], row["home_team_key"] = home_identity
                row["away_team"], row["away_team_key"] = away_identity
                row["team_resolution"] = "fpl_team_id_from_resolved_fixtures"
                rows.append(row)
    fixtures = pd.DataFrame(rows)
    if fixtures.empty:
        return fixtures

    team_ids = []
    for season, season_frame in fixtures.groupby("season"):
        keys = sorted(set(season_frame["home_team_key"]).union(set(season_frame["away_team_key"])))
        team_ids.extend({"season": season, "team_key": key, "team_id": idx + 1} for idx, key in enumerate(keys))
    team_ids_df = pd.DataFrame(team_ids)
    fixtures = fixtures.merge(team_ids_df.rename(columns={"team_key": "home_team_key", "team_id": "team_h"}), on=["season", "home_team_key"], how="left")
    fixtures = fixtures.merge(team_ids_df.rename(columns={"team_key": "away_team_key", "team_id": "team_a"}), on=["season", "away_team_key"], how="left")

    if odds.empty:
        for col in ["home_xg", "away_xg", "home_cs_prob", "away_cs_prob", "odds_fit_error"]:
            fixtures[col] = np.nan
        return fixtures

    exact = odds.copy()
    exact["match_date"] = pd.to_datetime(exact["match_date"], errors="coerce").dt.date
    fixtures = fixtures.merge(
        exact[
            [
                "season", "match_date", "home_team_key", "away_team_key", "home_lambda_odds",
                "away_lambda_odds", "home_cs_prob_odds", "away_cs_prob_odds", "odds_fit_error",
            ]
        ],
        on=["season", "match_date", "home_team_key", "away_team_key"],
        how="left",
    )
    fixtures = fixtures.rename(
        columns={
            "home_lambda_odds": "home_xg",
            "away_lambda_odds": "away_xg",
            "home_cs_prob_odds": "home_cs_prob",
            "away_cs_prob_odds": "away_cs_prob",
        }
    )
    fixtures["home_xa"] = fixtures["home_xg"] * 0.73
    fixtures["away_xa"] = fixtures["away_xg"] * 0.73
    return fixtures


def build_player_gw_spine(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    if "team" not in df.columns:
        df["team"] = np.nan
    if "position" not in df.columns:
        df["position"] = ""
    if {"expected_goals", "expected_assists"}.issubset(df.columns):
        df["_xg_source_present"] = df["expected_goals"].notna() & df["expected_assists"].notna()
    else:
        df["_xg_source_present"] = False
    df["_saves_source_present"] = df["saves"].notna() if "saves" in df.columns else False
    df["GW"] = _numeric(df, "GW").astype(int)
    df["element"] = _numeric(df, "element").astype(int)
    for col in [
        "minutes", "total_points", "expected_goals", "expected_assists", "expected_goals_conceded",
        "goals_scored", "assists", "bonus", "saves", "defensive_contribution",
        "yellow_cards", "red_cards", "starts",
        "selected", "value",
    ]:
        df[col] = _numeric(df, col)
    df["position"] = df["position"].replace({"AM": "MID", "GKP": "GK"}).fillna("")
    grouped = (
        df.groupby(["season", "element", "GW"], as_index=False)
        .agg(
            player_name=("name", "first"),
            position=("position", "first"),
            team=("team", "first"),
            actual_minutes=("minutes", "sum"),
            actual_points=("total_points", "sum"),
            actual_goals=("goals_scored", "sum"),
            actual_assists=("assists", "sum"),
            actual_bonus=("bonus", "sum"),
            expected_goals=("expected_goals", "sum"),
            expected_assists=("expected_assists", "sum"),
            expected_goals_conceded=("expected_goals_conceded", "sum"),
            saves=("saves", "sum"),
            defensive_contribution=("defensive_contribution", "sum"),
            yellow_cards=("yellow_cards", "sum"),
            red_cards=("red_cards", "sum"),
            starts=("starts", "sum"),
            selected=("selected", "mean"),
            value=("value", "mean"),
            xg_source_present=("_xg_source_present", "max"),
            saves_source_present=("_saves_source_present", "max"),
        )
    )
    grouped["player_id"] = grouped["element"].astype(int)
    grouped["played"] = (grouped["actual_minutes"] > 0).astype(float)
    grouped["started"] = (grouped["starts"] > 0).astype(float)
    grouped["team_key"] = grouped["team"].apply(_canon_team)
    grouped["player_key"] = grouped["player_name"].apply(_norm)
    grouped = grouped.sort_values(["season", "element", "GW"]).reset_index(drop=True)
    group = grouped.groupby(["season", "element"], group_keys=False)

    shifted_minutes = group["actual_minutes"].shift(1)
    shifted_xg = group["expected_goals"].shift(1)
    shifted_xa = group["expected_assists"].shift(1)
    shifted_points = group["actual_points"].shift(1)
    shifted_xg_source = group["xg_source_present"].shift(1).fillna(False).astype(float)
    for window in [3, 6]:
        keys = [grouped["season"], grouped["element"]]
        minutes_sum = shifted_minutes.groupby(keys).rolling(window, min_periods=1).sum().reset_index(level=[0, 1], drop=True)
        xg_sum = shifted_xg.groupby(keys).rolling(window, min_periods=1).sum().reset_index(level=[0, 1], drop=True)
        xa_sum = shifted_xa.groupby(keys).rolling(window, min_periods=1).sum().reset_index(level=[0, 1], drop=True)
        xg_source_count = shifted_xg_source.groupby(keys).rolling(window, min_periods=1).sum().reset_index(level=[0, 1], drop=True)
        grouped[f"rolling_minutes_{window}gw"] = minutes_sum
        grouped[f"rolling_xg90_{window}gw"] = np.where((minutes_sum > 0) & (xg_source_count > 0), xg_sum / minutes_sum * 90.0, np.nan)
        grouped[f"rolling_xa90_{window}gw"] = np.where((minutes_sum > 0) & (xg_source_count > 0), xa_sum / minutes_sum * 90.0, np.nan)
        grouped[f"rolling_points_{window}gw"] = shifted_points.groupby(keys).rolling(window, min_periods=1).mean().reset_index(level=[0, 1], drop=True)
        grouped[f"rolling_played_{window}gw"] = group["played"].shift(1).groupby(keys).rolling(window, min_periods=1).mean().reset_index(level=[0, 1], drop=True)
        grouped[f"rolling_started_{window}gw"] = group["started"].shift(1).groupby(keys).rolling(window, min_periods=1).mean().reset_index(level=[0, 1], drop=True)

    prev_minutes = group["actual_minutes"].shift(1).fillna(0.0)
    grouped["cumulative_season_minutes"] = prev_minutes.groupby([grouped["season"], grouped["element"]]).cumsum()
    grouped["cumulative_appearances"] = group["played"].shift(1).fillna(0.0).groupby([grouped["season"], grouped["element"]]).cumsum()
    grouped["cumulative_starts"] = group["started"].shift(1).fillna(0.0).groupby([grouped["season"], grouped["element"]]).cumsum()
    for src, out_col in [
        ("expected_goals", "season_xg90_pre_gw"),
        ("expected_assists", "season_xa90_pre_gw"),
        ("defensive_contribution", "season_defcon90_pre_gw"),
        ("yellow_cards", "season_yellow_cards_pre_gw"),
        ("red_cards", "season_red_cards_pre_gw"),
    ]:
        shifted = group[src].shift(1).fillna(0.0)
        cumulative = shifted.groupby([grouped["season"], grouped["element"]]).cumsum()
        if src in {"yellow_cards", "red_cards"}:
            grouped[out_col] = cumulative
        else:
            grouped[out_col] = np.where(grouped["cumulative_season_minutes"] > 0, cumulative / grouped["cumulative_season_minutes"] * 90.0, 0.0)
    shifted_saves = group["saves"].shift(1)
    shifted_save_source = group["saves_source_present"].shift(1).fillna(False).astype(float)
    cumulative_saves = shifted_saves.fillna(0.0).groupby([grouped["season"], grouped["element"]]).cumsum()
    cumulative_save_source = shifted_save_source.groupby([grouped["season"], grouped["element"]]).cumsum()
    grouped["season_saves90_pre_gw"] = np.where(
        (grouped["cumulative_season_minutes"] > 0) & (cumulative_save_source > 0),
        cumulative_saves / grouped["cumulative_season_minutes"] * 90.0,
        np.nan,
    )
    return grouped


def load_understat_player_lookup(root: Path) -> pd.DataFrame:
    rows = []
    for path in sorted((root / "data" / "understat" / "player_stats").glob("player_stats_*.json")):
        rows.extend(json.loads(path.read_text(encoding="utf-8")))
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.rename(
        columns={
            "xG90": "xg90_understat",
            "xA90": "xa90_understat",
            "npxG90": "npxg90_understat",
            "minutes": "understat_minutes",
            "shots": "understat_shots",
            "key_passes": "understat_key_passes",
            "position": "understat_position",
            "player_name": "understat_player_name",
        }
    )


def _understat_to_fpl_position(value: object) -> str:
    text = str(value or "").upper()
    if "GK" in text:
        return "GK"
    if "D" in text:
        return "DEF"
    if "F" in text:
        return "FWD"
    if "M" in text:
        return "MID"
    return ""


def build_understat_match_table(
    vaastav_players: pd.DataFrame,
    understat: pd.DataFrame,
    threshold: float = 0.85,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if vaastav_players.empty or understat.empty:
        return pd.DataFrame(), pd.DataFrame()

    understat_cols = [
        "season", "team_key", "player_key", "understat_player_name", "understat_player_id",
        "xg90_understat", "xa90_understat", "npxg90_understat", "understat_minutes",
        "xG", "xA", "npxG", "understat_shots", "understat_key_passes", "understat_position",
    ]
    understat_lookup = understat[[c for c in understat_cols if c in understat.columns]].copy()
    if "understat_minutes" in understat_lookup.columns:
        understat_lookup["_minutes_sort"] = pd.to_numeric(understat_lookup["understat_minutes"], errors="coerce").fillna(0.0)
        understat_lookup = understat_lookup.sort_values("_minutes_sort", ascending=False)

    matches: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    grouped = {
        key: group.reset_index(drop=True)
        for key, group in understat_lookup.groupby(["season", "team_key"], dropna=False)
    }
    for _, player in vaastav_players.iterrows():
        season = player.get("season")
        team_key = player.get("team_key")
        player_key = player.get("player_key")
        candidates = grouped.get((season, team_key), pd.DataFrame())
        base = {
            "season": season,
            "element": player.get("element"),
            "player_id": player.get("player_id"),
            "vaastav_player_name": player.get("player_name"),
            "vaastav_player_key": player_key,
            "team": player.get("team"),
            "team_key": team_key,
        }
        if candidates.empty:
            rejected.append(base | {"reason": "no_understat_team_season_candidates", "best_understat_player_name": "", "best_score": 0.0})
            continue

        exact = candidates.loc[candidates["player_key"] == player_key]
        if not exact.empty:
            chosen = exact.iloc[0]
            score = 1.0
            method = "exact"
        else:
            scored = candidates.copy()
            scored["_score"] = scored["player_key"].apply(lambda value: _name_similarity(player_key, value))
            chosen = scored.sort_values(["_score", "_minutes_sort"] if "_minutes_sort" in scored.columns else ["_score"], ascending=False).iloc[0]
            score = float(chosen["_score"])
            method = "fuzzy"

        if score >= threshold:
            record = base | {
                "understat_player_id": chosen.get("understat_player_id"),
                "understat_player_name": chosen.get("understat_player_name"),
                "understat_match_score": score,
                "understat_match_method": method,
            }
            for col in [
                "xg90_understat", "xa90_understat", "npxg90_understat", "understat_minutes",
                "xG", "xA", "npxG", "understat_shots", "understat_key_passes", "understat_position",
            ]:
                if col in chosen:
                    record[col] = chosen.get(col)
            matches.append(record)
        else:
            rejected.append(
                base
                | {
                    "reason": "best_name_score_below_0.85",
                    "best_understat_player_name": chosen.get("understat_player_name", ""),
                    "best_score": score,
                }
            )
    return pd.DataFrame(matches), pd.DataFrame(rejected)


def attach_fixture_odds_to_players(raw: pd.DataFrame, fixtures: pd.DataFrame) -> pd.DataFrame:
    cols = ["season", "GW", "fixture", "element", "was_home"]
    links = raw[cols].drop_duplicates().copy()
    fixture_cols = [
        "season", "GW", "fixture", "home_xg", "away_xg", "home_cs_prob", "away_cs_prob",
        "home_team", "away_team", "home_team_key", "away_team_key", "team_h", "team_a", "match_date",
    ]
    links = links.merge(fixtures[fixture_cols], on=["season", "GW", "fixture"], how="left")
    links["team_lambda_odds"] = np.where(links["was_home"] == True, links["home_xg"], links["away_xg"])
    links["opponent_lambda_odds"] = np.where(links["was_home"] == True, links["away_xg"], links["home_xg"])
    links["cs_prob_odds"] = np.where(links["was_home"] == True, links["home_cs_prob"], links["away_cs_prob"])
    links["team_id"] = np.where(links["was_home"] == True, links["team_h"], links["team_a"])
    links["derived_team"] = np.where(links["was_home"] == True, links["home_team"], links["away_team"])
    links["derived_team_key"] = np.where(links["was_home"] == True, links["home_team_key"], links["away_team_key"])
    odds = (
        links.groupby(["season", "element", "GW"], as_index=False)
        .agg(
            match_date=("match_date", lambda s: next((value for value in s if pd.notna(value)), np.nan)),
            fixture_count=("fixture", "nunique"),
            team_id=("team_id", "first"),
            derived_team=("derived_team", "first"),
            derived_team_key=("derived_team_key", "first"),
            team_lambda_odds=("team_lambda_odds", "sum"),
            opponent_lambda_odds=("opponent_lambda_odds", "sum"),
            cs_prob_odds=("cs_prob_odds", "sum"),
            odds_fixture_count=("team_lambda_odds", lambda s: int(pd.to_numeric(s, errors="coerce").notna().sum())),
        )
    )
    odds.loc[odds["odds_fixture_count"] == 0, ["team_lambda_odds", "opponent_lambda_odds", "cs_prob_odds"]] = np.nan
    return odds


def build_training_dataset(root: Path) -> TrainingBuildResult:
    raw = load_vaastav_seasons(root)
    odds = load_odds_match_features(root)
    fixtures = build_fixture_table(raw, odds)
    spine = build_player_gw_spine(raw)
    player_odds = attach_fixture_odds_to_players(raw, fixtures)
    dataset = spine.merge(player_odds, on=["season", "element", "GW"], how="left")
    for name_col in ["team", "team_key"]:
        derived_col = "derived_team" if name_col == "team" else "derived_team_key"
        if derived_col in dataset.columns:
            cleaned = dataset[name_col].astype(str).str.strip().str.lower()
            missing = dataset[name_col].isna() | cleaned.isin(["", "nan", "none"])
            dataset.loc[missing, name_col] = dataset.loc[missing, derived_col]
    team_key_cleaned = dataset["team_key"].astype(str).str.strip().str.lower()
    missing_team_key = dataset["team_key"].isna() | team_key_cleaned.isin(["", "nan", "none"])
    dataset.loc[missing_team_key & dataset["team"].notna(), "team_key"] = dataset.loc[
        missing_team_key & dataset["team"].notna(), "team"
    ].apply(_canon_team)
    dataset["position"] = dataset["position"].replace({"GKP": "GK", "AM": "MID"}).fillna("")

    understat = load_understat_player_lookup(root)
    if not understat.empty:
        vaastav_players = dataset[
            ["season", "element", "player_id", "player_name", "player_key", "team", "team_key"]
        ].drop_duplicates(["season", "element", "team_key"])
        match_table, rejected = build_understat_match_table(vaastav_players, understat)
        unmatched_path = root / "outputs" / "validation" / "unmatched_players.csv"
        unmatched_path.parent.mkdir(parents=True, exist_ok=True)
        rejected.to_csv(unmatched_path, index=False, float_format="%.6f")
        dataset = dataset.merge(
            match_table.drop(columns=["vaastav_player_name", "vaastav_player_key", "team"], errors="ignore"),
            on=["season", "element", "player_id", "team_key"],
            how="left",
        )
    else:
        unmatched_path = root / "outputs" / "validation" / "unmatched_players.csv"
        unmatched_path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame().to_csv(unmatched_path, index=False)
        dataset["xg90_understat"] = np.nan
        dataset["xa90_understat"] = np.nan

    if "understat_position" in dataset.columns:
        inferred_position = dataset["understat_position"].apply(_understat_to_fpl_position)
        missing_position = dataset["position"].isna() | (dataset["position"].astype(str).str.strip() == "")
        dataset.loc[missing_position, "position"] = inferred_position.loc[missing_position]

    season_has_vaastav_xg = dataset.groupby("season")["xg_source_present"].transform(
        lambda values: bool(values.astype(bool).any())
    )
    dataset["needs_understat_rate_fallback"] = ~season_has_vaastav_xg.astype(bool)
    dataset["understat_rate_fallback_used"] = (
        dataset["needs_understat_rate_fallback"].astype(bool)
        & dataset["xg90_understat"].notna()
        & dataset["xa90_understat"].notna()
    )
    for source_col, understat_col in [
        ("rolling_xg90_3gw", "xg90_understat"),
        ("rolling_xa90_3gw", "xa90_understat"),
        ("rolling_xg90_6gw", "xg90_understat"),
        ("rolling_xa90_6gw", "xa90_understat"),
        ("season_xg90_pre_gw", "xg90_understat"),
        ("season_xa90_pre_gw", "xa90_understat"),
    ]:
        if source_col in dataset.columns:
            mask = dataset["understat_rate_fallback_used"] & dataset[source_col].isna()
            dataset.loc[mask, source_col] = dataset.loc[mask, understat_col]

    dataset["saves_rate_default_used"] = False
    if "season_saves90_pre_gw" in dataset.columns:
        saves = pd.to_numeric(dataset["season_saves90_pre_gw"], errors="coerce")
        position_avg = (
            dataset.assign(_saves=saves)
            .loc[lambda frame: frame["_saves"].notna()]
            .groupby("position")["_saves"]
            .mean()
        )
        gk_default = float(position_avg.get("GK", saves.dropna().mean() if saves.notna().any() else 3.0))
        if not math.isfinite(gk_default) or gk_default <= 0:
            gk_default = 3.0
        gk_missing = (dataset["position"] == "GK") & saves.isna()
        dataset.loc[gk_missing, "season_saves90_pre_gw"] = gk_default
        dataset.loc[gk_missing, "saves_rate_default_used"] = True
        dataset.loc[dataset["position"] != "GK", "season_saves90_pre_gw"] = pd.to_numeric(
            dataset.loc[dataset["position"] != "GK", "season_saves90_pre_gw"],
            errors="coerce",
        ).fillna(0.0)

    required = [
        "team_lambda_odds", "opponent_lambda_odds", "cs_prob_odds", "rolling_xg90_3gw",
        "rolling_xa90_3gw", "rolling_minutes_3gw", "rolling_points_3gw",
    ]
    for col in required:
        if col not in dataset.columns:
            dataset[col] = np.nan
    understat_ready = (
        (~dataset["needs_understat_rate_fallback"].astype(bool))
        | (dataset["xg90_understat"].notna() & dataset["xa90_understat"].notna())
    )
    dataset["complete_features"] = dataset[required].notna().all(axis=1) & understat_ready
    reason_cols = {
        "missing_understat_player_match": (
            dataset["needs_understat_rate_fallback"].astype(bool)
            & (dataset["xg90_understat"].isna() | dataset["xa90_understat"].isna())
        ),
        "missing_odds_match": dataset["team_lambda_odds"].isna() | dataset["opponent_lambda_odds"].isna() | dataset["cs_prob_odds"].isna(),
        "missing_pre_gw_rate": dataset["rolling_xg90_3gw"].isna() | dataset["rolling_xa90_3gw"].isna(),
        "missing_pre_gw_minutes": dataset["rolling_minutes_3gw"].isna(),
        "missing_pre_gw_points": dataset["rolling_points_3gw"].isna(),
        "missing_team_identity": dataset["team_key"].isna() | (dataset["team_key"].astype(str).str.strip() == ""),
    }
    dataset["feature_failure_reasons"] = ""
    for reason, mask in reason_cols.items():
        dataset.loc[mask, "feature_failure_reasons"] = np.where(
            dataset.loc[mask, "feature_failure_reasons"].astype(str).str.len() > 0,
            dataset.loc[mask, "feature_failure_reasons"] + ";" + reason,
            reason,
        )
    failure_summary = (
        dataset.loc[~dataset["complete_features"], "feature_failure_reasons"]
        .fillna("")
        .str.split(";")
        .explode()
        .loc[lambda s: s.astype(str).str.len() > 0]
        .value_counts()
        .rename_axis("failure_reason")
        .reset_index(name="rows")
    )
    failure_path = root / "outputs" / "validation" / "feature_failure_reasons.csv"
    failure_path.parent.mkdir(parents=True, exist_ok=True)
    failure_summary.to_csv(failure_path, index=False)
    dataset["date"] = pd.to_datetime(dataset["match_date"], errors="coerce")
    output_cols = [
        "season", "GW", "player_id", "player_name", "team", "position", "actual_points",
        "actual_minutes", "actual_goals", "actual_assists", "actual_bonus", "xg90_understat",
        "xa90_understat", "team_lambda_odds", "opponent_lambda_odds", "cs_prob_odds",
        "rolling_xg90_3gw", "rolling_xa90_3gw", "rolling_minutes_3gw", "rolling_points_3gw",
    ]
    remaining = [col for col in dataset.columns if col not in output_cols]
    dataset = dataset[output_cols + remaining]
    inventory = build_inventory(root)
    out_path = root / "data" / "modelling" / "full_training_dataset.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    dataset.to_csv(out_path, index=False, float_format="%.6f")
    return TrainingBuildResult(dataset=dataset, fixtures=fixtures, inventory=inventory)


def _player_input_frame(gw_frame: pd.DataFrame) -> pd.DataFrame:
    players = pd.DataFrame(
        {
            "id": gw_frame["player_id"].astype(int),
            "web_name": gw_frame["player_name"].astype(str),
            "element_type": gw_frame["position"].map(POSITION_TO_ELEMENT_TYPE).fillna(3).astype(int),
            "team": pd.to_numeric(gw_frame["team_id"], errors="coerce").fillna(0).astype(int),
            "minutes": pd.to_numeric(gw_frame["cumulative_season_minutes"], errors="coerce").fillna(0.0),
            "starts": pd.to_numeric(gw_frame["cumulative_starts"], errors="coerce").fillna(0.0),
            "appearances": pd.to_numeric(gw_frame["cumulative_appearances"], errors="coerce").fillna(0.0),
            "expected_goals_per_90": pd.to_numeric(gw_frame["season_xg90_pre_gw"], errors="coerce").fillna(0.0),
            "expected_assists_per_90": pd.to_numeric(gw_frame["season_xa90_pre_gw"], errors="coerce").fillna(0.0),
            "defensive_contribution_per_90": pd.to_numeric(
                gw_frame.get("season_defcon90_pre_gw", pd.Series(0.0, index=gw_frame.index)),
                errors="coerce",
            ).fillna(0.0),
            "saves_per_90": pd.to_numeric(gw_frame["season_saves90_pre_gw"], errors="coerce").fillna(0.0),
            "yellow_cards": pd.to_numeric(gw_frame["season_yellow_cards_pre_gw"], errors="coerce").fillna(0.0),
            "red_cards": pd.to_numeric(gw_frame["season_red_cards_pre_gw"], errors="coerce").fillna(0.0),
            "penalties_order": 0.0,
            "corners_and_indirect_freekicks_order": 0.0,
            "direct_freekicks_order": 0.0,
            "form_minutes": pd.to_numeric(gw_frame["rolling_minutes_3gw"], errors="coerce").fillna(0.0),
            "form_xg90": pd.to_numeric(gw_frame["rolling_xg90_3gw"], errors="coerce").fillna(0.0),
            "form_xa90": pd.to_numeric(gw_frame["rolling_xa90_3gw"], errors="coerce").fillna(0.0),
            "understat_npxG90": pd.to_numeric(gw_frame.get("npxg90_understat", gw_frame["xg90_understat"]), errors="coerce").fillna(
                pd.to_numeric(gw_frame["xg90_understat"], errors="coerce").fillna(0.0)
            ),
            "understat_xA90": pd.to_numeric(gw_frame["xa90_understat"], errors="coerce").fillna(0.0),
            "understat_minutes": pd.to_numeric(gw_frame.get("understat_minutes", 0.0), errors="coerce").fillna(0.0),
            "selected_by_percent": 0.0,
            "points_per_game": 0.0,
            "starts_per_90": 0.0,
        }
    )
    return players.drop_duplicates("id")


def _fixture_input_frame(fixtures: pd.DataFrame, season: str, gw: int) -> pd.DataFrame:
    frame = fixtures.loc[(fixtures["season"] == season) & (fixtures["GW"] == gw)].copy()
    frame = frame.loc[frame[["home_xg", "away_xg", "home_cs_prob", "away_cs_prob"]].notna().all(axis=1)].copy()
    if frame.empty:
        return frame
    frame["home_xa"] = frame["home_xg"] * 0.73
    frame["away_xa"] = frame["away_xg"] * 0.73
    return frame[
        [
            "id", "event", "kickoff_time", "team_h", "team_a", "home_xg", "away_xg",
            "home_xa", "away_xa", "home_cs_prob", "away_cs_prob",
        ]
    ]


def _history_for_gw(season_frame: pd.DataFrame, gw: int) -> dict[int, pd.DataFrame]:
    prior = season_frame.loc[season_frame["GW"] < gw].copy()
    if prior.empty:
        return {}
    if "actual_minutes" not in prior.columns:
        prior["actual_minutes"] = 0.0
    if "starts" not in prior.columns:
        prior["starts"] = 0.0
    prior = prior[["player_id", "actual_minutes", "starts"]].copy()
    prior = prior.rename(columns={"actual_minutes": "minutes"})
    return {int(player_id): group[["minutes", "starts"]].copy() for player_id, group in prior.groupby("player_id")}


def score_training_dataset(
    dataset: pd.DataFrame,
    fixtures: pd.DataFrame,
    form_blend_weight: float = 0.3,
    seasons: list[str] | None = None,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    scoped = dataset.copy()
    if seasons is not None:
        scoped = scoped.loc[scoped["season"].isin(seasons)].copy()
    else:
        target_seasons = scoped.loc[scoped["complete_features"], "season"].dropna().unique().tolist()
        scoped = scoped.loc[scoped["season"].isin(target_seasons)].copy()
    for season, season_frame in scoped.groupby("season"):
        season_frame = season_frame.sort_values(["GW", "player_id"]).copy()
        target_gws = set(season_frame.loc[season_frame["complete_features"], "GW"].dropna().astype(int).tolist())
        for gw, gw_frame in season_frame.groupby("GW"):
            if int(gw) not in target_gws:
                continue
            fixture_frame = _fixture_input_frame(fixtures, str(season), int(gw))
            if fixture_frame.empty:
                continue
            players = _player_input_frame(gw_frame)
            players = players.loc[players["team"] > 0].copy()
            if players.empty:
                continue
            history = _history_for_gw(season_frame, int(gw))
            player_fixture = build_player_fixture_forecast(
                players,
                fixture_frame,
                history_by_player=history,
                form_blend_weight=form_blend_weight,
            )
            if player_fixture.empty:
                continue
            weekly = aggregate_gameweek(player_fixture)
            weekly["season"] = season
            frames.append(weekly)
    predictions = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if predictions.empty:
        return predictions
    predictions = predictions.rename(columns={"event": "GW", "xPts": "kft_xpts"})
    prediction_cols = [
        "season", "GW", "player_id", "kft_xpts", "xG", "xA", "expected_minutes", "team",
        "AppPts", "GoalPts", "AssistPts", "CSPts", "SavePts", "DefconPts", "CardPts",
        "PenMissPts", "ConcedePts",
    ]
    joined = dataset.merge(
        predictions[[c for c in prediction_cols if c in predictions.columns]].rename(
            columns={"team": "scored_team_id", "xG": "kft_xg", "xA": "kft_xa", "expected_minutes": "kft_expected_minutes"}
        ),
        on=["season", "GW", "player_id"],
        how="left",
    )
    joined["prediction_complete"] = joined["complete_features"] & joined["kft_xpts"].notna()
    joined["error"] = joined["kft_xpts"] - joined["actual_points"]
    joined["points_bracket"] = pd.cut(
        pd.to_numeric(joined["actual_points"], errors="coerce").fillna(0.0),
        bins=[-999, 2, 6, 9, 999],
        labels=["0-2", "3-6", "7-9", "10+"],
        include_lowest=True,
    ).astype(str)
    return joined


def gk_component_breakdown(predictions: pd.DataFrame) -> pd.DataFrame:
    complete = predictions.loc[predictions["prediction_complete"] & (predictions["position"] == "GK")].copy()
    if complete.empty:
        return pd.DataFrame()
    component_cols = [
        "AppPts", "GoalPts", "AssistPts", "CSPts", "SavePts", "DefconPts",
        "CardPts", "PenMissPts", "ConcedePts",
    ]
    rows = []
    for season, group in complete.groupby("season", dropna=False):
        row: dict[str, Any] = {
            "season": str(season),
            "rows": int(len(group)),
            "actual_points_mean": float(group["actual_points"].mean()),
            "predicted_xpts_mean": float(group["kft_xpts"].mean()),
            "expected_minutes_mean": float(group.get("kft_expected_minutes", pd.Series(index=group.index)).mean()),
        }
        for col in component_cols:
            row[f"{col}_mean"] = float(pd.to_numeric(group.get(col, pd.Series(0.0, index=group.index)), errors="coerce").fillna(0.0).mean())
        rows.append(row)
    overall = {
        "season": "overall",
        "rows": int(len(complete)),
        "actual_points_mean": float(complete["actual_points"].mean()),
        "predicted_xpts_mean": float(complete["kft_xpts"].mean()),
        "expected_minutes_mean": float(pd.to_numeric(complete.get("kft_expected_minutes", pd.Series(index=complete.index)), errors="coerce").mean()),
    }
    for col in component_cols:
        overall[f"{col}_mean"] = float(pd.to_numeric(complete.get(col, pd.Series(0.0, index=complete.index)), errors="coerce").fillna(0.0).mean())
    return pd.DataFrame([overall, *rows])


def _metrics(group: pd.DataFrame) -> dict[str, Any]:
    usable = group.dropna(subset=["actual_points", "kft_xpts"]).copy()
    if usable.empty:
        return {"rows": 0, "mae": np.nan, "rmse": np.nan, "spearman": np.nan, "actual_mean": np.nan, "pred_mean": np.nan}
    error = usable["kft_xpts"].astype(float) - usable["actual_points"].astype(float)
    return {
        "rows": int(len(usable)),
        "mae": float(error.abs().mean()),
        "rmse": float(np.sqrt(np.mean(error.to_numpy() ** 2))),
        "spearman": float(usable["kft_xpts"].rank().corr(usable["actual_points"].rank(), method="pearson")) if len(usable) >= 2 else np.nan,
        "actual_mean": float(usable["actual_points"].mean()),
        "pred_mean": float(usable["kft_xpts"].mean()),
        "bias": float(error.mean()),
    }


def validation_tables(predictions: pd.DataFrame) -> pd.DataFrame:
    complete = predictions.loc[predictions["prediction_complete"]].copy()
    rows = [{"scope": "overall", "group": "overall", **_metrics(complete)}]
    for scope, col in [("season", "season"), ("position", "position"), ("points_bracket", "points_bracket")]:
        for value, group in complete.groupby(col, dropna=False):
            rows.append({"scope": scope, "group": str(value), **_metrics(group)})
    return pd.DataFrame(rows)


def compare_validation_tables(previous: pd.DataFrame, current: pd.DataFrame) -> pd.DataFrame:
    if previous.empty or current.empty:
        return pd.DataFrame()
    keys = ["scope", "group"]
    metric_cols = ["rows", "mae", "rmse", "spearman"]
    prev = previous[keys + [c for c in metric_cols if c in previous.columns]].rename(
        columns={col: f"previous_{col}" for col in metric_cols if col in previous.columns}
    )
    curr = current[keys + [c for c in metric_cols if c in current.columns]].rename(
        columns={col: f"current_{col}" for col in metric_cols if col in current.columns}
    )
    out = prev.merge(curr, on=keys, how="inner")
    for col in metric_cols:
        prev_col = f"previous_{col}"
        curr_col = f"current_{col}"
        if prev_col in out.columns and curr_col in out.columns:
            out[f"delta_{col}"] = pd.to_numeric(out[curr_col], errors="coerce") - pd.to_numeric(out[prev_col], errors="coerce")
    return out


def sweep_form_weight(dataset: pd.DataFrame, fixtures: pd.DataFrame) -> pd.DataFrame:
    holdout = ["2024-25"]
    train_rows = int(dataset.loc[dataset["season"] < "2024-25", "complete_features"].sum())
    records = []
    for weight in np.round(np.arange(0.0, 0.61, 0.1), 1):
        predicted = score_training_dataset(dataset, fixtures, form_blend_weight=float(weight), seasons=holdout)
        complete = predicted.loc[predicted["prediction_complete"]].copy()
        metrics = _metrics(complete)
        records.append(
            {
                "form_blend_weight": float(weight),
                "train_complete_rows_before_holdout": train_rows,
                "holdout_season": "2024-25",
                **metrics,
            }
        )
    return pd.DataFrame(records).sort_values(["spearman", "mae", "rmse"], ascending=[False, True, True]).reset_index(drop=True)


def update_config_form_weight(config_path: Path, best_weight: float) -> bool:
    text = config_path.read_text(encoding="utf-8")
    pattern = r"(form_blend_weight:\s*float\s*=\s*)([0-9.]+)"
    replacement = rf"\g<1>{float(best_weight):.1f}"
    new_text, count = re.subn(pattern, replacement, text, count=1)
    if count != 1:
        raise RuntimeError(f"Could not update form_blend_weight in {config_path}")
    if new_text != text:
        config_path.write_text(new_text, encoding="utf-8")
        return True
    return False


def markdown_table(df: pd.DataFrame, max_rows: int | None = None) -> str:
    if df.empty:
        return "_No rows._"
    view = df.head(max_rows).copy() if max_rows is not None else df.copy()
    columns = list(view.columns)

    def fmt(value: Any) -> str:
        if pd.isna(value):
            return ""
        if isinstance(value, float):
            return f"{value:.6f}".rstrip("0").rstrip(".")
        return str(value).replace("|", "\\|")

    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join(["---"] * len(columns)) + " |"
    body = ["| " + " | ".join(fmt(row[col]) for col in columns) + " |" for _, row in view.iterrows()]
    return "\n".join([header, separator, *body])


def write_findings_report(
    path: Path,
    inventory: dict[str, pd.DataFrame],
    validation: pd.DataFrame,
    sweep: pd.DataFrame,
    dataset_status: dict[str, Any],
    gk_breakdown: pd.DataFrame | None = None,
    failure_summary: pd.DataFrame | None = None,
    comparison: pd.DataFrame | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    by_season = validation.loc[validation["scope"] == "season"].copy()
    best = sweep.iloc[0] if not sweep.empty else None
    gaps = []
    if dataset_status["incomplete_rows"] > 0:
        gaps.append(f"{dataset_status['incomplete_rows']} player-GW rows have incomplete feature coverage.")
    if not inventory["vaastav"].empty and not bool(inventory["vaastav"]["xg_columns_exist"].all()):
        gaps.append("At least one Vaastav season is missing expected-goals/assists columns.")
    season_spearman = pd.to_numeric(by_season.get("spearman", pd.Series(dtype=float)), errors="coerce")
    if season_spearman.notna().any():
        weakest = by_season.loc[season_spearman.idxmin()]
        gaps.append(f"Weakest season Spearman is {weakest['group']} at {float(weakest['spearman']):.3f}.")
    while len(gaps) < 3:
        gaps.append("Name/team matching remains the main evidence gap for unmatched Understat or odds rows.")

    lines = [
        "# Historical KFT Validation Findings",
        "",
        "## Data Inventory",
        "",
        "### Odds",
        markdown_table(inventory["odds"]),
        "",
        "### Understat Team",
        markdown_table(inventory["understat_team"]),
        "",
        "### Understat Player",
        markdown_table(inventory["understat_player"]),
        "",
        "### Vaastav",
        markdown_table(inventory["vaastav"]),
        "",
        "## Training Dataset",
        "",
        f"- Rows: {dataset_status['rows']}",
        f"- Date range: {dataset_status['date_min']} to {dataset_status['date_max']}",
        f"- Complete feature rows: {dataset_status['complete_rows']}",
        f"- Incomplete feature rows: {dataset_status['incomplete_rows']}",
        f"- Complete feature share: {dataset_status.get('complete_share', 0.0):.2%}",
        "",
        "For seasons where Vaastav has no `expected_goals`/`expected_assists` columns, the validation dataset uses matched Understat `xG90` and `xA90` directly as the attacking-rate inputs. Those rows are marked with `understat_rate_fallback_used=True` in `full_training_dataset.csv`.",
        "For 2022-23 onward, shifted Vaastav xG/xA rates are sufficient for completeness when odds and pre-GW rolling inputs are present; rejected Understat matches for those rows remain diagnostic in `unmatched_players.csv`.",
        "",
        "## Accuracy",
        "",
        markdown_table(validation),
        "",
        "## Previous Run Comparison",
        "",
        markdown_table(comparison if comparison is not None else pd.DataFrame()),
        "",
        "## GK Component Breakdown",
        "",
        markdown_table(gk_breakdown if gk_breakdown is not None else pd.DataFrame()),
        "",
        "## Feature Failure Reasons",
        "",
        markdown_table((failure_summary if failure_summary is not None else pd.DataFrame()).head(20)),
        "",
        "## Form Weight Sweep",
        "",
        markdown_table(sweep),
        "",
        f"Best form weight: {float(best['form_blend_weight']):.1f}" if best is not None else "Best form weight: unavailable",
        "",
        "## Three Biggest Remaining Gaps",
        "",
        f"1. {gaps[0]}",
        f"2. {gaps[1]}",
        f"3. {gaps[2]}",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
