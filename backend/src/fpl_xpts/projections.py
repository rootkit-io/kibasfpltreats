from __future__ import annotations

import csv
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

from .config import BACKEND_ROOT


def _canon_team(name: object) -> str:
    s = str(name).strip().lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
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


def _parse_number(value: object, percent: bool = False) -> float | None:
    if value is None or pd.isna(value):
        return None
    s = str(value).strip()
    if not s:
        return None
    if s.endswith("%"):
        try:
            return float(s[:-1].strip()) / 100.0
        except ValueError:
            return None
    try:
        out = float(s)
    except ValueError:
        return None
    if percent and out > 1.0:
        out = out / 100.0
    return out


def _parse_multi(value: object, percent: bool = False) -> list[float]:
    if value is None or pd.isna(value) or str(value).strip() == "":
        return []
    vals = []
    for part in str(value).split(";"):
        parsed = _parse_number(part, percent=percent)
        if parsed is not None:
            vals.append(parsed)
    return vals


def find_projection_files(root: Path = BACKEND_ROOT) -> tuple[Path | None, Path | None]:
    files = list(root.glob("*.csv"))
    attack = next((p for p in files if "attack_projection" in p.name.lower()), None)
    defense = next((p for p in files if "defense-projection" in p.name.lower() or "defense_projection" in p.name.lower()), None)
    return attack, defense


def find_elevenify_projection_file(root: Path = BACKEND_ROOT) -> Path | None:
    preferred = [
        root / "data" / "elevenify_com_25_26_Subscriber_Season_Long_Data_-_Sheet1.csv",
        root / "elevenify.com 25_26 Subscriber Season Long Data - Sheet1.csv",
    ]
    for path in preferred:
        if path.exists():
            return path
    files = sorted([*root.glob("*.csv"), *(root / "data").glob("*.csv")], key=lambda p: p.stat().st_mtime, reverse=True)
    return next((p for p in files if "elevenify" in p.name.lower() and "season long" in p.name.lower()), None)


def _row_contains(row: pd.Series, text: str) -> bool:
    needle = text.lower()
    return any(needle in str(value).strip().lower() for value in row.tolist() if not pd.isna(value))


def _find_section_title(raw: pd.DataFrame, title: str) -> int | None:
    for idx, row in raw.iterrows():
        if _row_contains(row, title):
            return int(idx)
    return None


def _find_section_header(raw: pd.DataFrame, title_row: int) -> int | None:
    for idx in range(title_row + 1, len(raw)):
        row = raw.iloc[idx]
        labels = [str(value).strip().lower() for value in row.tolist() if not pd.isna(value)]
        has_team = any(label == "team" for label in labels)
        has_gw = any(re.fullmatch(r"gw\d+", label) for label in labels)
        if has_team and has_gw:
            return idx
    return None


def _parse_elevenify_section(raw: pd.DataFrame, title: str, value_name: str, percent: bool = False) -> pd.DataFrame:
    title_row = _find_section_title(raw, title)
    if title_row is None:
        raise ValueError(f"Could not find Elevenify section: {title}")
    header_row = _find_section_header(raw, title_row)
    if header_row is None:
        raise ValueError(f"Could not find Elevenify GW header for section: {title}")

    header = raw.iloc[header_row].tolist()
    team_col = next((idx for idx, value in enumerate(header) if str(value).strip().lower() == "team"), None)
    if team_col is None:
        raise ValueError(f"Could not find Team column in section: {title}")

    gw_cols: dict[int, int] = {}
    for idx, value in enumerate(header):
        match = re.fullmatch(r"gw(\d+)", str(value).strip().lower())
        if match:
            gw_cols[int(match.group(1))] = idx
    if not gw_cols:
        raise ValueError(f"Could not find GW columns in section: {title}")

    rows = []
    for row_idx in range(header_row + 1, len(raw)):
        row = raw.iloc[row_idx]
        team_value = row.iloc[team_col] if team_col < len(row) else None
        team = str(team_value).strip() if team_value is not None and not pd.isna(team_value) else ""
        if not team:
            break
        if team.lower().startswith("projected "):
            break
        for gw, col_idx in gw_cols.items():
            value = _parse_number(row.iloc[col_idx] if col_idx < len(row) else None, percent=percent)
            if value is None:
                continue
            rows.append({"team": team, "team_key": _canon_team(team), "GW": int(gw), value_name: float(value)})
    return pd.DataFrame(rows)


def load_elevenify_projection_tables(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Read Elevenify's goals and clean-sheet tables as team/GW indexed frames."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    width = max((len(row) for row in rows), default=0)
    raw = pd.DataFrame([row + [None] * (width - len(row)) for row in rows])

    elevenify_goals = _parse_elevenify_section(raw, "Projected Goals", "projected_goals", percent=False)
    elevenify_cs = _parse_elevenify_section(raw, "Projected Clean Sheets", "cs_prob", percent=True)
    if not elevenify_goals.empty:
        elevenify_goals["projected_goals"] = pd.to_numeric(elevenify_goals["projected_goals"], errors="coerce")
        elevenify_goals = elevenify_goals.dropna(subset=["projected_goals"]).set_index(["team_key", "GW"]).sort_index()
    if not elevenify_cs.empty:
        elevenify_cs["cs_prob"] = pd.to_numeric(elevenify_cs["cs_prob"], errors="coerce").clip(0.01, 0.90)
        elevenify_cs = elevenify_cs.dropna(subset=["cs_prob"]).set_index(["team_key", "GW"]).sort_index()
    return elevenify_goals, elevenify_cs


def load_elevenify_team_projections(path: Path) -> pd.DataFrame:
    """Read Elevenify's season-long sheet into team/GW goals and clean-sheet inputs."""
    goals, cs = load_elevenify_projection_tables(path)
    goals = goals.reset_index()
    cs = cs.reset_index()
    out = goals.merge(cs, on=["team", "team_key", "GW"], how="outer")
    out["projected_goals"] = pd.to_numeric(out["projected_goals"], errors="coerce")
    out["cs_prob"] = pd.to_numeric(out["cs_prob"], errors="coerce").clip(0.01, 0.90)
    return out.sort_values(["GW", "team_key"]).reset_index(drop=True)


def add_team_scoring_probabilities(fixtures: pd.DataFrame) -> pd.DataFrame:
    """Add per-side Poisson scoring probabilities implied by fixture lambdas."""
    out = fixtures.copy()
    for prefix in ["home", "away"]:
        if f"{prefix}_xg" not in out.columns:
            continue
        lam = pd.to_numeric(out[f"{prefix}_xg"], errors="coerce").fillna(0.0).clip(lower=0.0)
        p0 = np.exp(-lam)
        p1 = lam * p0
        p2 = (lam ** 2) * p0 / 2.0
        out[f"{prefix}_p_score_0"] = p0
        out[f"{prefix}_p_score_1"] = p1
        out[f"{prefix}_p_score_2"] = p2
        out[f"{prefix}_p_score_2plus"] = (1.0 - p0 - p1).clip(0.0, 1.0)
        out[f"{prefix}_p_score_3plus"] = (1.0 - p0 - p1 - p2).clip(0.0, 1.0)
    return out


def _lookup_indexed_value(frame: pd.DataFrame, team_key: object, gw: object, column: str) -> float | None:
    if frame.empty or column not in frame.columns or team_key is None or pd.isna(gw):
        return None
    key = (str(team_key), int(gw))
    if key not in frame.index:
        return None
    value = frame.at[key, column]
    if pd.isna(value):
        return None
    return float(value)


def apply_elevenify_team_projections(
    fixtures: pd.DataFrame,
    teams: pd.DataFrame,
    path: Path | None = None,
    start_gw: int | None = None,
    end_gw: int | None = None,
    assist_factor: float = 0.73,
    team_assist_factors: dict[int, float] | None = None,
) -> pd.DataFrame:
    """Override fixture goals/CS with the uploaded Elevenify team projection sheet.

    The Elevenify file has one projected-goals and one projected-clean-sheets value
    per team/gameweek. For teams with multiple fixtures in the same GW, goals are
    split by the existing fixture lambda proportions and clean sheets by the
    existing fixture CS proportions so the GW-level totals stay conserved.
    """
    path = path or find_elevenify_projection_file()
    if path is None or not path.exists():
        return add_team_scoring_probabilities(fixtures)

    projections = load_elevenify_team_projections(path)
    out = fixtures.copy()
    events = pd.to_numeric(out["event"], errors="coerce")
    if start_gw is not None:
        projections = projections.loc[projections["GW"] >= int(start_gw)].copy()
    if end_gw is not None:
        projections = projections.loc[projections["GW"] <= int(end_gw)].copy()
    if projections.empty:
        return add_team_scoring_probabilities(out)

    team_names = teams[["id", "name"]].copy()
    team_names["team_key"] = team_names["name"].apply(_canon_team)
    key_by_id = dict(zip(team_names["id"], team_names["team_key"]))

    side_rows = []
    for _, fixture in out.iterrows():
        if pd.isna(fixture.get("event")):
            continue
        gw = int(fixture["event"])
        if start_gw is not None and gw < int(start_gw):
            continue
        if end_gw is not None and gw > int(end_gw):
            continue
        for side in ["home", "away"]:
            team_id = int(fixture["team_h"] if side == "home" else fixture["team_a"])
            prefix = "home" if side == "home" else "away"
            fallback_xg = float(fixture.get(f"{prefix}_xg", 0.0) or 0.0)
            fallback_cs = float(fixture.get(f"{prefix}_cs_prob", 0.0) or 0.0)
            side_rows.append(
                {
                    "fixture_id": int(fixture["id"]),
                    "side": side,
                    "team_id": team_id,
                    "team_key": key_by_id.get(team_id),
                    "GW": gw,
                    "fallback_xg": max(fallback_xg, 0.0),
                    "fallback_cs": max(fallback_cs, 0.0),
                }
            )
    side_frame = pd.DataFrame(side_rows)
    if side_frame.empty:
        return out

    merged = side_frame.merge(projections, on=["team_key", "GW"], how="left")
    for _, side in merged.iterrows():
        if pd.isna(side.get("projected_goals")) and pd.isna(side.get("cs_prob")):
            continue
        mask = out["id"] == int(side["fixture_id"])
        prefix = "home" if side["side"] == "home" else "away"
        if not pd.isna(side.get("projected_goals")):
            xg = max(0.0, float(side["projected_goals"]))
            team_assist_factor = (
                float(team_assist_factors.get(int(side["team_id"]), assist_factor))
                if team_assist_factors is not None
                else float(assist_factor)
            )
            out.loc[mask, f"{prefix}_xg"] = xg
            out.loc[mask, f"{prefix}_xa"] = xg * float(np.clip(team_assist_factor, 0.50, 0.95))
            out.loc[mask, "projection_source"] = "elevenify_team_projection"
        if not pd.isna(side.get("cs_prob")):
            cs = float(np.clip(float(side["cs_prob"]), 0.01, 0.90))
            out.loc[mask, f"{prefix}_cs_prob"] = cs
            out.loc[mask, "projection_source"] = "elevenify_team_projection"

    return add_team_scoring_probabilities(out)


def apply_external_team_projections(
    fixtures: pd.DataFrame,
    teams: pd.DataFrame,
    attack_path: Path | None = None,
    defense_path: Path | None = None,
    start_gw: int | None = None,
) -> pd.DataFrame:
    """Override fixture team xG/xA/CS using market/manual projection CSVs.

    This supports the old notebook files:
    - attack_projections: projected_team_xG_W1..W6 and projected_team_xA_W1..W6
    - defense-projections: CS_ODDS_W1..W6

    For DGWs, semicolon values are assigned by fixture order inside the GW.
    """
    if attack_path is None or defense_path is None:
        found_attack, found_defense = find_projection_files()
        attack_path = attack_path or found_attack
        defense_path = defense_path or found_defense
    if attack_path is None or defense_path is None or not attack_path.exists() or not defense_path.exists():
        out = fixtures.copy()
        out["projection_source"] = "fpl_strength_fallback"
        return out

    attack = pd.read_csv(attack_path)
    defense = pd.read_csv(defense_path)
    attack["team_key"] = attack["team"].apply(_canon_team)
    defense["team_key"] = defense["team"].apply(_canon_team)

    attack_by_team = attack.set_index("team_key")
    defense_by_team = defense.set_index("team_key")

    out = fixtures.copy()
    out["projection_source"] = "fpl_strength_fallback"
    team_names = teams[["id", "name"]].copy()
    team_names["team_key"] = team_names["name"].apply(_canon_team)
    key_by_id = dict(zip(team_names["id"], team_names["team_key"]))

    events = pd.to_numeric(out["event"], errors="coerce")
    start = int(start_gw or events.dropna().min())
    out["_event_num"] = events.astype("Int64")
    out["_kickoff_sort"] = pd.to_datetime(out.get("kickoff_time"), errors="coerce")

    long_rows = []
    for _, fx in out.sort_values(["event", "_kickoff_sort", "id"]).iterrows():
        if pd.isna(fx["_event_num"]):
            continue
        gw = int(fx["_event_num"])
        week = gw - start + 1
        if week < 1 or week > 6:
            continue
        for side in ["home", "away"]:
            team_id = int(fx["team_h"] if side == "home" else fx["team_a"])
            team_key = key_by_id.get(team_id)
            long_rows.append({"fixture_id": fx["id"], "side": side, "team_id": team_id, "team_key": team_key, "GW": gw, "week": week})

    side_frame = pd.DataFrame(long_rows)
    if side_frame.empty:
        return out.drop(columns=["_event_num", "_kickoff_sort"], errors="ignore")
    side_frame["fixture_in_week"] = side_frame.groupby(["team_id", "GW"]).cumcount() + 1

    for _, side in side_frame.iterrows():
        team_key = side["team_key"]
        week = int(side["week"])
        idx = int(side["fixture_in_week"]) - 1
        if team_key not in attack_by_team.index or team_key not in defense_by_team.index:
            continue

        xg_vals = _parse_multi(attack_by_team.at[team_key, f"projected_team_xG_W{week}"], percent=False) if f"projected_team_xG_W{week}" in attack_by_team.columns else []
        xa_vals = _parse_multi(attack_by_team.at[team_key, f"projected_team_xA_W{week}"], percent=False) if f"projected_team_xA_W{week}" in attack_by_team.columns else []
        cs_vals = _parse_multi(defense_by_team.at[team_key, f"CS_ODDS_W{week}"], percent=True) if f"CS_ODDS_W{week}" in defense_by_team.columns else []

        xg = xg_vals[idx] if idx < len(xg_vals) else (xg_vals[0] if len(xg_vals) == 1 else None)
        xa = xa_vals[idx] if idx < len(xa_vals) else (xa_vals[0] if len(xa_vals) == 1 else None)
        cs = cs_vals[idx] if idx < len(cs_vals) else (cs_vals[0] if len(cs_vals) == 1 else None)

        mask = out["id"] == side["fixture_id"]
        prefix = "home" if side["side"] == "home" else "away"
        if xg is not None and not math.isnan(xg):
            out.loc[mask, f"{prefix}_xg"] = max(0.0, float(xg))
            out.loc[mask, "projection_source"] = "external_team_projection"
        if xa is not None and not math.isnan(xa):
            out.loc[mask, f"{prefix}_xa"] = max(0.0, float(xa))
            out.loc[mask, "projection_source"] = "external_team_projection"
        if cs is not None and not math.isnan(cs):
            out.loc[mask, f"{prefix}_cs_prob"] = float(np.clip(cs, 0.01, 0.90))
            out.loc[mask, "projection_source"] = "external_team_projection"

    return out.drop(columns=["_event_num", "_kickoff_sort"], errors="ignore")
