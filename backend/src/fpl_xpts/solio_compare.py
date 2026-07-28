from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from urllib.request import Request, urlopen

import pandas as pd

from .config import AppConfig
from .legacy_export import build_legacy_outputs


SOLIO_URL = "https://fpl.solioanalytics.com/api/data/latest.json"
TEAM_ABBR = {
    "ARS": "Arsenal",
    "AVL": "Aston Villa",
    "BOU": "Bournemouth",
    "BRE": "Brentford",
    "BHA": "Brighton",
    "BUR": "Burnley",
    "CHE": "Chelsea",
    "CRY": "Crystal Palace",
    "EVE": "Everton",
    "FUL": "Fulham",
    "LEE": "Leeds",
    "LIV": "Liverpool",
    "MCI": "Manchester City",
    "MUN": "Manchester United",
    "NEW": "Newcastle United",
    "NFO": "Nottingham Forest",
    "SUN": "Sunderland",
    "TOT": "Tottenham",
    "WHU": "West Ham",
    "WOL": "Wolverhampton Wanderers",
}


def _norm(text: object) -> str:
    s = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _last_name(text: object) -> str:
    parts = _norm(text).split()
    return parts[-1] if parts else ""


def _position(pos: object) -> str:
    return {"GKP": "GK"}.get(str(pos), str(pos))


def _name_score(solio_name: object, player_name: object) -> float:
    solio = _norm(solio_name)
    player = _norm(player_name)
    if not solio or not player:
        return 0.0
    if solio == player or solio == _last_name(player):
        return 1.0
    if solio in player:
        return 0.9

    solio_tokens = solio.split()
    player_tokens = player.split()
    hits = 0
    for token in solio_tokens:
        if len(token) == 1:
            hits += int(any(p.startswith(token) for p in player_tokens))
        else:
            hits += int(any(p == token or p.startswith(token) or token.startswith(p) for p in player_tokens))
    return hits / max(len(solio_tokens), 1)


def fetch_solio_latest() -> dict:
    request = Request(SOLIO_URL, headers={"User-Agent": "fpl-xpts-solio-compare/0.1"})
    with urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def solio_top_projected_frame(data: dict) -> pd.DataFrame:
    rows = []
    for rank, row in enumerate(data.get("topProjected", []), start=1):
        rows.append(
            {
                "solio_rank": rank,
                "solio_gameweek": data.get("gameweek"),
                "solio_generatedAt": data.get("generatedAt"),
                "solio_name": row.get("name"),
                "solio_team": TEAM_ABBR.get(row.get("team"), row.get("team")),
                "solio_position": _position(row.get("position")),
                "solio_prPoints": row.get("prPoints"),
                "solio_ownership": row.get("ownership"),
                "solio_price": row.get("price"),
                "match_name": _last_name(row.get("name")),
            }
        )
    return pd.DataFrame(rows)


def compare_to_solio(
    config: AppConfig = AppConfig(),
    manual_minutes_paths=None,
    minute_override_paths=None,
) -> pd.DataFrame:
    solio_data = fetch_solio_latest()
    solio = solio_top_projected_frame(solio_data)
    # Minutes inputs are stated explicitly at the boundary: None preserves
    # the legacy defaults so comparison runs match the weekly workflow.
    legacy = build_legacy_outputs(
        config,
        manual_minutes_paths=manual_minutes_paths,
        minute_override_paths=minute_override_paths,
    )
    weekly = legacy["weekly_player_week.csv"].copy()
    gw = int(solio_data.get("gameweek") or weekly["GW"].min())
    weekly = weekly.loc[weekly["GW"] == gw].copy()
    weekly["model_rank"] = weekly["xPts"].rank(ascending=False, method="first").astype(int)

    rows = []
    for _, solio_row in solio.iterrows():
        candidates = weekly.loc[
            (weekly["team"] == solio_row["solio_team"]) &
            (weekly["Pos"] == solio_row["solio_position"])
        ].copy()
        if candidates.empty:
            rows.append(solio_row.to_dict())
            continue
        candidates["name_score"] = candidates["player"].apply(lambda name: _name_score(solio_row["solio_name"], name))
        candidates = candidates.sort_values(["name_score", "xPts"], ascending=[False, False])
        best = candidates.iloc[0]
        if float(best["name_score"]) < 0.5:
            rows.append(solio_row.to_dict())
            continue
        combined = {**solio_row.to_dict(), **best.to_dict()}
        rows.append(combined)

    merged = pd.DataFrame(rows)
    merged["model_minus_solio"] = merged["xPts"] - pd.to_numeric(merged["solio_prPoints"], errors="coerce")
    cols = [
        "solio_rank", "model_rank", "solio_gameweek", "solio_generatedAt", "solio_name", "player",
        "solio_team", "team", "solio_position", "Pos", "solio_prPoints", "xPts", "model_minus_solio",
        "mins", "xG_scaled", "xA_scaled", "P1_GA", "fixtures_in_week", "solio_ownership", "solio_price",
    ]
    return merged[cols]


def write_solio_comparison(
    out_dir: Path,
    config: AppConfig = AppConfig(),
    manual_minutes_paths=None,
    minute_override_paths=None,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "solio_comparison.csv"
    compare_to_solio(
        config,
        manual_minutes_paths=manual_minutes_paths,
        minute_override_paths=minute_override_paths,
    ).to_csv(path, index=False, float_format="%.6f")
    return path
