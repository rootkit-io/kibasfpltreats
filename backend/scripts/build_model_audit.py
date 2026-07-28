from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from fpl_xpts.backtest import (  # noqa: E402
    add_production_formula_predictions,
    add_rolling_features,
    build_player_gw_frame,
    load_vaastav_seasons,
)
from fpl_xpts.market_odds import _canon_team, _devig_decimal, _fit_lambdas  # noqa: E402
from fpl_xpts.shot_profiles import _aggregate_player_profiles  # noqa: E402


FPL_BASE_URL = "https://fantasy.premierleague.com/api"
UNDERSTAT_BASE_URL = "https://understat.com"
FOOTBALL_DATA_BASE = "https://www.football-data.co.uk/mmz4281"

FPL_HISTORY_DIR = ROOT / "data" / "fpl_history"
UNDERSTAT_HISTORICAL_DIR = ROOT / "data" / "understat" / "historical"
ODDS_HISTORICAL_DIR = ROOT / "data" / "odds_historical"
MODELLING_DIR = ROOT / "data" / "modelling"
AUDIT_DIR = ROOT / "outputs" / "model_audit"

VAASTAV_SEASONS = ["2022-23", "2023-24", "2024-25"]
ODDS_SEASONS = ["2122", "2223", "2324", "2425"]
UNDERSTAT_SEASON_BY_FPL_SEASON = {
    "2021-22": 2021,
    "2022-23": 2022,
    "2023-24": 2023,
    "2024-25": 2024,
    "2025-26": 2025,
}


def run_powershell(command: str, timeout: int = 90) -> str:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if powershell is None:
        raise RuntimeError("powershell.exe is required for HTTPS fetch fallback on this machine.")
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-Command",
            "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; " + command,
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if result.returncode != 0 and not result.stdout.strip():
        raise RuntimeError(result.stderr.strip() or f"PowerShell command failed with exit code {result.returncode}")
    return result.stdout


def fetch_text(url: str, timeout: int = 90) -> str:
    escaped = url.replace("'", "''")
    return run_powershell(
        f"(Invoke-WebRequest -UseBasicParsing '{escaped}' -TimeoutSec {timeout}).Content",
        timeout=timeout + 15,
    )


def fetch_json(url: str, timeout: int = 90) -> dict[str, Any]:
    return json.loads(fetch_text(url, timeout=timeout))


def fetch_understat_json(url: str, referer: str, timeout: int = 90) -> dict[str, Any]:
    escaped_url = url.replace("'", "''")
    escaped_referer = referer.replace("'", "''")
    command = (
        "$headers = @{"
        "'User-Agent'='fpl-xpts-understat-audit/0.1';"
        f"'Referer'='{escaped_referer}';"
        "'X-Requested-With'='XMLHttpRequest';"
        "'Accept'='application/json, text/javascript, */*; q=0.01'"
        "}; "
        f"(Invoke-WebRequest -UseBasicParsing '{escaped_url}' -Headers $headers -TimeoutSec {timeout}).Content"
    )
    return json.loads(run_powershell(command, timeout=timeout + 15))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_fpl_history(max_players: int | None = None, refresh: bool = False, workers: int = 4) -> dict[str, Any]:
    FPL_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    status: dict[str, Any] = {
        "source": FPL_BASE_URL,
        "note": (
            "The public FPL API exposes current-season element-summary histories only. "
            "Archived per-GW element-summary histories for prior seasons are not available from these endpoints."
        ),
        "bootstrap_players": 0,
        "summary_files": 0,
        "history_rows": 0,
        "history_past_rows": 0,
        "errors": [],
    }

    bootstrap_path = FPL_HISTORY_DIR / "bootstrap-static.json"
    fixtures_path = FPL_HISTORY_DIR / "fixtures.json"
    if refresh or not bootstrap_path.exists():
        bootstrap = fetch_json(f"{FPL_BASE_URL}/bootstrap-static/")
        write_json(bootstrap_path, bootstrap)
    else:
        bootstrap = read_json(bootstrap_path)
    if refresh or not fixtures_path.exists():
        write_json(fixtures_path, fetch_json(f"{FPL_BASE_URL}/fixtures/"))

    players = bootstrap.get("elements", [])
    if max_players is not None:
        players = players[: int(max_players)]
    status["bootstrap_players"] = len(players)

    def fetch_one(player: dict[str, Any]) -> tuple[int, bool, str]:
        player_id = int(player["id"])
        out_path = FPL_HISTORY_DIR / f"player_{player_id}_history.json"
        if out_path.exists() and not refresh:
            return player_id, True, ""
        try:
            payload = fetch_json(f"{FPL_BASE_URL}/element-summary/{player_id}/", timeout=45)
            write_json(out_path, payload)
            time.sleep(0.02)
            return player_id, True, ""
        except Exception as exc:  # noqa: BLE001
            return player_id, False, str(exc)

    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        futures = [pool.submit(fetch_one, player) for player in players]
        for future in as_completed(futures):
            player_id, ok, error = future.result()
            if not ok:
                status["errors"].append({"player_id": player_id, "error": error})

    summary_files = sorted(FPL_HISTORY_DIR.glob("player_*_history.json"))
    status["summary_files"] = len(summary_files)
    for path in summary_files:
        try:
            payload = read_json(path)
        except json.JSONDecodeError:
            continue
        status["history_rows"] += len(payload.get("history", []))
        status["history_past_rows"] += len(payload.get("history_past", []))

    write_json(FPL_HISTORY_DIR / "fetch_manifest.json", status)
    return status


def fetch_understat_historical(
    start_season: int = 2014,
    end_season: int = 2025,
    refresh: bool = False,
    fetch_match_details: bool = False,
    workers: int = 6,
) -> dict[str, Any]:
    UNDERSTAT_HISTORICAL_DIR.mkdir(parents=True, exist_ok=True)
    matches_dir = UNDERSTAT_HISTORICAL_DIR / "matches"
    matches_dir.mkdir(parents=True, exist_ok=True)
    status: dict[str, Any] = {
        "source": UNDERSTAT_BASE_URL,
        "league": "EPL",
        "seasons_requested": list(range(int(start_season), int(end_season) + 1)),
        "league_files": 0,
        "player_season_rows": 0,
        "match_files": 0,
        "player_match_rows": 0,
        "errors": [],
    }
    player_season_frames = []
    player_match_frames = []

    for season in status["seasons_requested"]:
        league_path = UNDERSTAT_HISTORICAL_DIR / f"league_EPL_{season}.json"
        legacy_league_path = ROOT / "data" / "understat" / f"league_EPL_{season}.json"
        try:
            if refresh or not league_path.exists():
                if legacy_league_path.exists() and not refresh:
                    payload = read_json(legacy_league_path)
                else:
                    payload = fetch_understat_json(
                        f"{UNDERSTAT_BASE_URL}/getLeagueData/EPL/{season}",
                        f"{UNDERSTAT_BASE_URL}/league/EPL/{season}",
                        timeout=90,
                    )
                write_json(league_path, payload)
            else:
                payload = read_json(league_path)
            status["league_files"] += 1
            profiles = _aggregate_player_profiles(payload, league="EPL", season=season)
            if not profiles.empty:
                player_season_frames.append(profiles)
                profiles.to_csv(
                    UNDERSTAT_HISTORICAL_DIR / f"player_season_EPL_{season}.csv",
                    index=False,
                    float_format="%.6f",
                )
            if fetch_match_details:
                match_frame, match_errors = fetch_understat_match_details(payload, season, matches_dir, refresh, workers)
                status["errors"].extend(match_errors)
                if not match_frame.empty:
                    player_match_frames.append(match_frame)
                    match_frame.to_csv(
                        UNDERSTAT_HISTORICAL_DIR / f"player_match_EPL_{season}.csv",
                        index=False,
                        float_format="%.6f",
                    )
        except Exception as exc:  # noqa: BLE001
            status["errors"].append({"season": season, "error": str(exc)})

    if player_season_frames:
        player_seasons = pd.concat(player_season_frames, ignore_index=True)
        status["player_season_rows"] = len(player_seasons)
        player_seasons.to_csv(UNDERSTAT_HISTORICAL_DIR / "player_seasons_all.csv", index=False, float_format="%.6f")
    if player_match_frames:
        player_matches = pd.concat(player_match_frames, ignore_index=True)
        status["player_match_rows"] = len(player_matches)
        player_matches.to_csv(UNDERSTAT_HISTORICAL_DIR / "player_matches_all.csv", index=False, float_format="%.6f")

    status["match_files"] = len(list(matches_dir.glob("*.json")))
    write_json(UNDERSTAT_HISTORICAL_DIR / "fetch_manifest.json", status)
    return status


def fetch_understat_match_details(
    league_payload: dict[str, Any],
    season: int,
    matches_dir: Path,
    refresh: bool,
    workers: int,
) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    dates = [row for row in league_payload.get("dates", []) if row.get("isResult") is True and row.get("id") is not None]
    date_by_id = {str(row["id"]): row for row in dates}
    errors: list[dict[str, Any]] = []

    def fetch_one(match_id: str) -> tuple[str, dict[str, Any] | None, str]:
        path = matches_dir / f"{season}_{match_id}.json"
        legacy_path = ROOT / "data" / "understat" / "matches" / f"{match_id}.json"
        try:
            if path.exists() and not refresh:
                return match_id, read_json(path), ""
            if legacy_path.exists() and not refresh:
                payload = read_json(legacy_path)
                write_json(path, payload)
                return match_id, payload, ""
            payload = fetch_understat_json(
                f"{UNDERSTAT_BASE_URL}/getMatchData/{match_id}",
                f"{UNDERSTAT_BASE_URL}/match/{match_id}",
                timeout=90,
            )
            write_json(path, payload)
            time.sleep(0.01)
            return match_id, payload, ""
        except Exception as exc:  # noqa: BLE001
            return match_id, None, str(exc)

    rows = []
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        futures = [pool.submit(fetch_one, str(row["id"])) for row in dates]
        for future in as_completed(futures):
            match_id, payload, error = future.result()
            if error:
                errors.append({"season": season, "match_id": match_id, "error": error})
                continue
            rows.extend(understat_player_match_rows(payload or {}, date_by_id.get(match_id, {}), season))
    return pd.DataFrame(rows), errors


def understat_player_match_rows(payload: dict[str, Any], fixture_row: dict[str, Any], season: int) -> list[dict[str, Any]]:
    match_id = fixture_row.get("id")
    match_date = fixture_row.get("datetime")
    rows: dict[tuple[str, str], dict[str, Any]] = {}

    def add(player: object, team: object, field: str, value: float) -> None:
        if not player or str(player).strip() == "":
            return
        key = (str(player), _canon_team(team))
        if key not in rows:
            rows[key] = {
                "understat_season": season,
                "match_id": match_id,
                "match_datetime": match_date,
                "player": str(player),
                "team_key": key[1],
                "shots": 0.0,
                "xG": 0.0,
                "key_passes": 0.0,
                "xA": 0.0,
            }
        rows[key][field] += float(value)

    for side in ["h", "a"]:
        for shot in payload.get("shots", {}).get(side, []):
            team = shot.get("h_team") if shot.get("h_a") == "h" else shot.get("a_team")
            add(shot.get("player"), team, "shots", 1.0)
            add(shot.get("player"), team, "xG", float(shot.get("xG") or 0.0))
            assisted = shot.get("player_assisted")
            if assisted:
                add(assisted, team, "key_passes", 1.0)
                add(assisted, team, "xA", float(shot.get("xG") or 0.0))
    return list(rows.values())


def fetch_odds_historical(refresh: bool = False) -> dict[str, Any]:
    ODDS_HISTORICAL_DIR.mkdir(parents=True, exist_ok=True)
    status: dict[str, Any] = {"source": FOOTBALL_DATA_BASE, "seasons": {}, "errors": []}
    for season in ODDS_SEASONS:
        path = ODDS_HISTORICAL_DIR / f"{season}_E0.csv"
        try:
            if refresh or not path.exists():
                text = fetch_text(f"{FOOTBALL_DATA_BASE}/{season}/E0.csv", timeout=60)
                path.write_text(text, encoding="utf-8")
            rows = max(0, len(path.read_text(encoding="utf-8", errors="ignore").splitlines()) - 1)
            status["seasons"][season] = {"path": str(path.relative_to(ROOT)), "rows": rows}
        except Exception as exc:  # noqa: BLE001
            status["errors"].append({"season": season, "error": str(exc)})
    write_json(ODDS_HISTORICAL_DIR / "fetch_manifest.json", status)
    return status


def load_odds_features() -> pd.DataFrame:
    frames = []
    season_map = {"2122": "2021-22", "2223": "2022-23", "2324": "2023-24", "2425": "2024-25"}
    for season_code, fpl_season in season_map.items():
        path = ODDS_HISTORICAL_DIR / f"{season_code}_E0.csv"
        if not path.exists():
            continue
        raw = pd.read_csv(path)
        if raw.empty or "HomeTeam" not in raw.columns or "AwayTeam" not in raw.columns:
            continue
        date_col = "Date"
        raw["match_date"] = pd.to_datetime(raw[date_col], dayfirst=True, errors="coerce").dt.date
        rows = []
        for _, row in raw.iterrows():
            home_odds = first_number(row, ["AvgH", "B365H", "MaxH", "PSH"])
            draw_odds = first_number(row, ["AvgD", "B365D", "MaxD", "PSD"])
            away_odds = first_number(row, ["AvgA", "B365A", "MaxA", "PSA"])
            h2h = _devig_decimal({"home": home_odds, "draw": draw_odds, "away": away_odds})
            over_odds = first_number(row, ["Avg>2.5", "B365>2.5", "P>2.5", "Max>2.5"])
            under_odds = first_number(row, ["Avg<2.5", "B365<2.5", "P<2.5", "Max<2.5"])
            total_probs = _devig_decimal({"over": over_odds, "under": under_odds})
            over_prob = total_probs.get("over")
            home_xg, away_xg, fit_error = _fit_lambdas(h2h, over_prob)
            rows.append(
                {
                    "season": fpl_season,
                    "match_date": row["match_date"],
                    "home_team_key": _canon_team(row["HomeTeam"]),
                    "away_team_key": _canon_team(row["AwayTeam"]),
                    "home_odds_lambda": home_xg,
                    "away_odds_lambda": away_xg,
                    "odds_fit_error": fit_error,
                    "home_win_prob": h2h.get("home", np.nan),
                    "draw_prob": h2h.get("draw", np.nan),
                    "away_win_prob": h2h.get("away", np.nan),
                }
            )
        frames.append(pd.DataFrame(rows))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def first_number(row: pd.Series, columns: list[str]) -> float | None:
    for col in columns:
        if col in row and pd.notna(row[col]):
            try:
                value = float(row[col])
            except (TypeError, ValueError):
                continue
            if math.isfinite(value) and value > 1.0:
                return value
    return None


def fixture_opponents(raw: pd.DataFrame) -> pd.DataFrame:
    fixture_teams = (
        raw[["season", "GW", "fixture", "team"]]
        .drop_duplicates()
        .groupby(["season", "GW", "fixture"])["team"]
        .apply(list)
        .reset_index(name="fixture_teams")
    )
    out = raw.merge(fixture_teams, on=["season", "GW", "fixture"], how="left")
    out["opponent_team_name"] = out.apply(
        lambda row: next((team for team in row["fixture_teams"] if team != row["team"]), np.nan)
        if isinstance(row.get("fixture_teams"), list)
        else np.nan,
        axis=1,
    )
    out["team_key"] = out["team"].apply(_canon_team)
    out["opponent_team_key"] = out["opponent_team_name"].apply(_canon_team)
    return out.drop(columns=["fixture_teams"], errors="ignore")


def attach_odds_to_raw(raw: pd.DataFrame, odds: pd.DataFrame) -> pd.DataFrame:
    out = fixture_opponents(raw)
    out["match_date"] = pd.to_datetime(out["kickoff_time"], errors="coerce").dt.date
    if odds.empty:
        out["team_odds_lambda"] = np.nan
        out["opponent_odds_lambda"] = np.nan
        out["odds_fit_error"] = np.nan
        return out

    home = odds.rename(
        columns={
            "home_team_key": "team_key",
            "away_team_key": "opponent_team_key",
            "home_odds_lambda": "team_odds_lambda",
            "away_odds_lambda": "opponent_odds_lambda",
        }
    )
    away = odds.rename(
        columns={
            "away_team_key": "team_key",
            "home_team_key": "opponent_team_key",
            "away_odds_lambda": "team_odds_lambda",
            "home_odds_lambda": "opponent_odds_lambda",
        }
    )
    lookup_cols = [
        "season", "match_date", "team_key", "opponent_team_key", "team_odds_lambda",
        "opponent_odds_lambda", "odds_fit_error",
    ]
    lookup = pd.concat([home[lookup_cols], away[lookup_cols]], ignore_index=True)
    return out.merge(lookup, on=["season", "match_date", "team_key", "opponent_team_key"], how="left")


def add_custom_pre_gw_features(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.sort_values(["season", "element", "GW"]).copy()
    group = out.groupby(["season", "element"], group_keys=False)
    shifted_minutes = group["minutes"].shift(1)
    shifted_xg = group["expected_goals"].shift(1)
    shifted_xa = group["expected_assists"].shift(1)
    shifted_points = group["total_points"].shift(1)
    shifted_started = group["started"].shift(1)
    shifted_played = group["played"].shift(1)

    for window in [3, 6]:
        minutes_sum = shifted_minutes.groupby([out["season"], out["element"]]).rolling(window, min_periods=1).sum().reset_index(level=[0, 1], drop=True)
        xg_sum = shifted_xg.groupby([out["season"], out["element"]]).rolling(window, min_periods=1).sum().reset_index(level=[0, 1], drop=True)
        xa_sum = shifted_xa.groupby([out["season"], out["element"]]).rolling(window, min_periods=1).sum().reset_index(level=[0, 1], drop=True)
        out[f"rolling_minutes_{window}"] = minutes_sum
        out[f"rolling_xg90_{window}"] = np.where(minutes_sum > 0, xg_sum / minutes_sum * 90.0, np.nan)
        out[f"rolling_xa90_{window}"] = np.where(minutes_sum > 0, xa_sum / minutes_sum * 90.0, np.nan)
        out[f"form_points_{window}"] = shifted_points.groupby([out["season"], out["element"]]).rolling(window, min_periods=1).mean().reset_index(level=[0, 1], drop=True)
        out[f"rolling_started_{window}"] = shifted_started.groupby([out["season"], out["element"]]).rolling(window, min_periods=1).mean().reset_index(level=[0, 1], drop=True)
        out[f"rolling_played_{window}"] = shifted_played.groupby([out["season"], out["element"]]).rolling(window, min_periods=1).mean().reset_index(level=[0, 1], drop=True)

    out["cumulative_season_minutes"] = shifted_minutes.groupby([out["season"], out["element"]]).cumsum()
    return out


def build_full_dataset(allow_vaastav_fallback: bool = True) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not allow_vaastav_fallback:
        raise RuntimeError(
            "The public FPL API does not expose last-3-season per-GW element-summary archives; "
            "use --allow-vaastav-fallback to build the historical modelling table from cached Vaastav rows."
        )
    raw = load_vaastav_seasons(VAASTAV_SEASONS, cache_dir=ROOT / "data" / "vaastav")
    raw = attach_odds_to_raw(raw, load_odds_features())

    odds_agg = (
        raw.groupby(["season", "element", "GW"], as_index=False)
        .agg(
            match_date=("match_date", "min"),
            fixture_count=("fixture", "nunique"),
            was_home=("was_home", "mean"),
            home_fixture_count=("was_home", "sum"),
            team_odds_lambda=("team_odds_lambda", "sum"),
            opponent_odds_lambda=("opponent_odds_lambda", "sum"),
            odds_fit_error=("odds_fit_error", "mean"),
            odds_fixture_count=("team_odds_lambda", lambda s: int(pd.to_numeric(s, errors="coerce").notna().sum())),
        )
    )
    odds_agg.loc[odds_agg["odds_fixture_count"] == 0, ["team_odds_lambda", "opponent_odds_lambda"]] = np.nan

    base = add_rolling_features(build_player_gw_frame(raw))
    base = add_custom_pre_gw_features(base)
    dataset = base.merge(odds_agg, on=["season", "element", "GW"], how="left")
    dataset["match_date"] = pd.to_datetime(dataset["match_date"], errors="coerce")

    required_features = [
        "rolling_xg90_3", "rolling_xa90_3", "rolling_minutes_3", "form_points_3",
        "rolling_xg90_6", "rolling_xa90_6", "rolling_minutes_6", "form_points_6",
        "team_odds_lambda", "opponent_odds_lambda", "cumulative_season_minutes", "was_home",
    ]
    dataset["complete_feature_row"] = dataset[required_features].notna().all(axis=1)
    dataset["data_source"] = "vaastav_fallback_after_fpl_api_archive_gap"

    MODELLING_DIR.mkdir(parents=True, exist_ok=True)
    dataset.to_csv(MODELLING_DIR / "full_dataset.csv", index=False, float_format="%.6f")
    status = {
        "source": "Vaastav fallback because public FPL API does not expose archived per-GW histories",
        "rows": int(len(dataset)),
        "date_min": str(dataset["match_date"].min().date()) if dataset["match_date"].notna().any() else "",
        "date_max": str(dataset["match_date"].max().date()) if dataset["match_date"].notna().any() else "",
        "complete_feature_rows": int(dataset["complete_feature_row"].sum()),
        "missing_feature_rows": int((~dataset["complete_feature_row"]).sum()),
        "required_features": required_features,
    }
    return dataset, status


def prepare_kft_predictions(dataset: pd.DataFrame) -> pd.DataFrame:
    frame = dataset.copy()
    played = pd.to_numeric(frame["rolling_played_6"], errors="coerce")
    frame["pred_play_prob"] = played.fillna(0.0).clip(0.0, 1.0)
    frame["pred_start_prob"] = pd.to_numeric(frame["rolling_started_6"], errors="coerce").fillna(0.0).clip(0.0, 1.0)
    frame["pred_start_prob"] = np.minimum(frame["pred_start_prob"], frame["pred_play_prob"])
    minutes_if_play = np.divide(
        pd.to_numeric(frame["rolling_minutes_6"], errors="coerce").fillna(0.0),
        played.replace(0, np.nan),
    )
    frame["pred_mins_if_play"] = pd.Series(minutes_if_play, index=frame.index).replace([np.inf, -np.inf], np.nan).fillna(0.0).clip(0.0, 90.0)

    odds_mask = frame["team_odds_lambda"].notna()
    frame.loc[odds_mask, "team_xg_l4"] = frame.loc[odds_mask, "team_odds_lambda"].clip(0.25, 3.5)
    frame.loc[odds_mask, "team_xa_l4"] = (frame.loc[odds_mask, "team_odds_lambda"] * 0.73).clip(0.1, 3.0)
    frame.loc[frame["opponent_odds_lambda"].notna(), "team_xgc_l4"] = frame.loc[
        frame["opponent_odds_lambda"].notna(), "opponent_odds_lambda"
    ].clip(0.05, 4.0)

    enough = frame.loc[
        (frame["GW"] >= 5)
        & frame["rolling_minutes_6"].notna()
        & frame["expected_goals_season90"].notna()
        & frame["expected_assists_season90"].notna()
    ].copy()
    predicted = add_production_formula_predictions(enough)
    predicted["kft_error"] = predicted["production_xPts"] - predicted["total_points"]
    predicted["points_bracket"] = pd.cut(
        predicted["total_points"],
        bins=[-999, 2, 6, 9, 999],
        labels=["0-2", "3-6", "7-9", "10+"],
        include_lowest=True,
    ).astype(str)
    return predicted


def metric_block(frame: pd.DataFrame, group_col: str | None = None) -> pd.DataFrame:
    rows = []
    groups = [("overall", frame)] if group_col is None else frame.groupby(group_col, dropna=False)
    for key, group in groups:
        if group.empty:
            continue
        err = group["kft_error"].astype(float)
        rows.append(
            {
                "group": str(key),
                "rows": int(len(group)),
                "actual_mean": float(group["total_points"].mean()),
                "pred_mean": float(group["production_xPts"].mean()),
                "mae": float(err.abs().mean()),
                "rmse": float(np.sqrt(np.mean(err.to_numpy() ** 2))),
                "bias": float(err.mean()),
                "spearman": float(group["production_xPts"].rank().corr(group["total_points"].rank(), method="pearson"))
                if len(group) >= 2
                else np.nan,
            }
        )
    return pd.DataFrame(rows).sort_values("mae", ascending=False).reset_index(drop=True)


def feature_correlations(dataset: pd.DataFrame) -> pd.DataFrame:
    exclude = {
        "season", "element", "GW", "complete_feature_row",
        "total_points", "actual_points", "minutes", "goals_scored", "assists", "bonus", "bps",
        "clean_sheets", "goals_conceded", "saves", "expected_goals", "expected_assists",
        "expected_goals_conceded", "creativity", "influence", "threat", "ict_index",
        "starts", "started", "played",
        "production_xPts", "kft_error",
    }
    rows = []
    for col in dataset.columns:
        if col in exclude:
            continue
        series = pd.to_numeric(dataset[col], errors="coerce")
        if series.notna().sum() < 100 or series.nunique(dropna=True) < 2:
            continue
        corr = series.rank().corr(dataset["total_points"].rank(), method="pearson")
        if pd.notna(corr):
            rows.append(
                {
                    "feature": col,
                    "spearman": float(corr),
                    "abs_spearman": float(abs(corr)),
                    "non_null_rows": int(series.notna().sum()),
                }
            )
    return pd.DataFrame(rows).sort_values("abs_spearman", ascending=False).reset_index(drop=True)


def write_findings(
    fetch_status: dict[str, Any],
    dataset_status: dict[str, Any],
    overall: pd.DataFrame,
    by_position: pd.DataFrame,
    by_bracket: pd.DataFrame,
    correlations: pd.DataFrame,
) -> Path:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    worst_bracket = by_bracket.iloc[0] if not by_bracket.empty else None
    top_features = correlations.head(20)

    gaps = [
        "The official FPL API fetch succeeded for current public element-summary files, but the API does not expose archived per-GW element-summary histories for the prior three seasons. The modelling table therefore uses Vaastav as a clearly labelled fallback, not as the primary fetched source.",
        f"KFT error is worst in the `{worst_bracket['group']}` actual-points bracket by MAE ({worst_bracket['mae']:.3f}) over {int(worst_bracket['rows'])} rows." if worst_bracket is not None else "KFT bracket error could not be computed because no prediction rows were available.",
        "Odds-derived team and opponent lambdas are now in the modelling table, but rows without matched football-data odds are marked incomplete; this is a data-contract gap before relying on odds features for calibration.",
    ]

    text = [
        "# KFT Model Audit Findings",
        "",
        "## Data Fetch Summary",
        "",
        f"- FPL API bootstrap players: {fetch_status['fpl'].get('bootstrap_players', 0)}",
        f"- FPL element-summary files stored: {fetch_status['fpl'].get('summary_files', 0)}",
        f"- FPL current-season history rows stored: {fetch_status['fpl'].get('history_rows', 0)}",
        f"- FPL `history_past` aggregate rows stored: {fetch_status['fpl'].get('history_past_rows', 0)}",
        f"- Understat league files stored: {fetch_status['understat'].get('league_files', 0)}",
        f"- Understat player-season rows: {fetch_status['understat'].get('player_season_rows', 0)}",
        f"- Understat match files stored: {fetch_status['understat'].get('match_files', 0)}",
        f"- Understat player-match rows: {fetch_status['understat'].get('player_match_rows', 0)}",
        f"- Football-data odds seasons stored: {len(fetch_status['odds'].get('seasons', {}))}",
        "",
        "## Modelling Dataset",
        "",
        f"- Source: {dataset_status['source']}",
        f"- Rows: {dataset_status['rows']}",
        f"- Date range: {dataset_status['date_min']} to {dataset_status['date_max']}",
        f"- Complete feature rows: {dataset_status['complete_feature_rows']}",
        f"- Rows with missing required fields: {dataset_status['missing_feature_rows']}",
        "",
        "## KFT Prediction Error",
        "",
        "Overall:",
        "",
        markdown_table(overall),
        "",
        "By position:",
        "",
        markdown_table(by_position),
        "",
        "By actual points bracket:",
        "",
        markdown_table(by_bracket),
        "",
    ]
    if worst_bracket is not None:
        text.extend(
            [
                f"**Worst bracket:** `{worst_bracket['group']}` by MAE ({worst_bracket['mae']:.3f}).",
                "",
            ]
        )
    text.extend(
        [
            "## Feature Correlations With Actual Points",
            "",
            markdown_table(top_features),
            "",
            "## Three Biggest Evidence Gaps",
            "",
        ]
    )
    for idx, gap in enumerate(gaps, start=1):
        text.append(f"{idx}. {gap}")
    text.append("")

    path = AUDIT_DIR / "findings.md"
    path.write_text("\n".join(text), encoding="utf-8")
    return path


def markdown_table(df: pd.DataFrame, max_rows: int | None = None) -> str:
    if df.empty:
        return "_No rows._"
    view = df.head(max_rows).copy() if max_rows is not None else df.copy()
    cols = list(view.columns)

    def fmt(value: Any) -> str:
        if pd.isna(value):
            return ""
        if isinstance(value, float):
            return f"{value:.6f}".rstrip("0").rstrip(".")
        return str(value).replace("|", "\\|")

    header = "| " + " | ".join(cols) + " |"
    separator = "| " + " | ".join(["---"] * len(cols)) + " |"
    body = ["| " + " | ".join(fmt(row[col]) for col in cols) + " |" for _, row in view.iterrows()]
    return "\n".join([header, separator, *body])


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch, join, and audit KFT FPL prediction data.")
    parser.add_argument("--refresh", action="store_true", help="Refetch cached source files")
    parser.add_argument("--max-fpl-players", type=int, default=None, help="Debug cap for FPL element-summary fetches")
    parser.add_argument("--fpl-workers", type=int, default=4)
    parser.add_argument("--understat-start-season", type=int, default=2014)
    parser.add_argument("--understat-end-season", type=int, default=2025)
    parser.add_argument("--fetch-understat-match-details", action="store_true")
    parser.add_argument("--allow-vaastav-fallback", action="store_true", default=True)
    args = parser.parse_args()

    fetch_status = {
        "fpl": fetch_fpl_history(max_players=args.max_fpl_players, refresh=args.refresh, workers=args.fpl_workers),
        "understat": fetch_understat_historical(
            start_season=args.understat_start_season,
            end_season=args.understat_end_season,
            refresh=args.refresh,
            fetch_match_details=args.fetch_understat_match_details,
        ),
        "odds": fetch_odds_historical(refresh=args.refresh),
    }

    dataset, dataset_status = build_full_dataset(allow_vaastav_fallback=args.allow_vaastav_fallback)
    predicted = prepare_kft_predictions(dataset)
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    predicted.to_csv(AUDIT_DIR / "kft_historical_predictions.csv", index=False, float_format="%.6f")

    overall = metric_block(predicted)
    by_position = metric_block(predicted, "position")
    by_bracket = metric_block(predicted, "points_bracket")
    correlations = feature_correlations(dataset)
    overall.to_csv(AUDIT_DIR / "kft_error_overall.csv", index=False, float_format="%.6f")
    by_position.to_csv(AUDIT_DIR / "kft_error_by_position.csv", index=False, float_format="%.6f")
    by_bracket.to_csv(AUDIT_DIR / "kft_error_by_bracket.csv", index=False, float_format="%.6f")
    correlations.to_csv(AUDIT_DIR / "feature_correlations.csv", index=False, float_format="%.6f")
    findings_path = write_findings(fetch_status, dataset_status, overall, by_position, by_bracket, correlations)

    print("Data fetch summary")
    print(json.dumps(fetch_status, indent=2)[:4000])
    print("\nModelling dataset")
    print(
        f"rows={dataset_status['rows']} date_range={dataset_status['date_min']}..{dataset_status['date_max']} "
        f"complete={dataset_status['complete_feature_rows']} missing={dataset_status['missing_feature_rows']}"
    )
    print("\nKFT overall error")
    print(overall.to_string(index=False))
    print("\nKFT error by bracket")
    print(by_bracket.to_string(index=False))
    if not by_bracket.empty:
        worst = by_bracket.iloc[0]
        print(f"\nWorst bracket by MAE: {worst['group']} (MAE {worst['mae']:.3f}, rows {int(worst['rows'])})")
    print("\nTop feature correlations")
    print(correlations.head(20).to_string(index=False))
    print(f"\nWrote {MODELLING_DIR / 'full_dataset.csv'}")
    print(f"Wrote {findings_path}")


if __name__ == "__main__":
    main()
