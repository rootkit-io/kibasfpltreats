from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .config import BACKEND_ROOT, LEAGUE_DIFFICULTY_FACTORS
from .scoring import POSITION_BY_ELEMENT_TYPE


PL_HISTORY_MINUTES_THRESHOLD = 450.0
FOREIGN_PRIOR_WEIGHT_MINUTES = 900.0
REPO_ROOT = BACKEND_ROOT
FOREIGN_UNDERSTAT_DIR = REPO_ROOT / "data" / "understat" / "foreign_leagues"
PL_UNDERSTAT_PLAYER_DIR = REPO_ROOT / "data" / "understat" / "player_stats"
FALLBACK_PL_POSITIONAL_BASELINES = {
    "GK": {"xg90": 0.0, "xa90": 0.000209},
    "DEF": {"xg90": 0.054641, "xa90": 0.049168},
    "MID": {"xg90": 0.097972, "xa90": 0.111943},
    "FWD": {"xg90": 0.310321, "xa90": 0.125700},
}


def numeric(series: pd.Series, default: float = 0.0) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").fillna(default)


def _normalise_player_name(text: object) -> str:
    value = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^a-z0-9 ]", " ", value.lower().strip())
    return re.sub(r"\s+", " ", value).strip()


def _shrink_rate(raw: float, minutes: float, prior: float, prior_minutes: float) -> float:
    observed_minutes = float(max(minutes, 0.0))
    return (
        (float(raw) * observed_minutes) + (float(prior) * float(prior_minutes))
    ) / max(observed_minutes + float(prior_minutes), 1.0)


def _understat_position(value: object) -> str:
    tokens = str(value or "").upper().split()
    for marker, position in [("G", "GK"), ("D", "DEF"), ("M", "MID"), ("F", "FWD")]:
        if any(token.startswith(marker) for token in tokens):
            return position
    return ""


def _float_or_zero(value: object) -> float:
    number = pd.to_numeric(value, errors="coerce")
    return float(number) if pd.notna(number) else 0.0


@lru_cache(maxsize=4)
def _load_pl_positional_baselines(data_dir: str) -> dict[str, dict[str, float]]:
    baselines = {
        position: dict(values)
        for position, values in FALLBACK_PL_POSITIONAL_BASELINES.items()
    }
    paths = sorted(
        Path(data_dir).glob("player_stats_*.json"),
        key=lambda path: int(path.stem.rsplit("_", 1)[-1]),
        reverse=True,
    )
    if not paths:
        return baselines

    try:
        rows = json.loads(paths[0].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return baselines
    frame = pd.DataFrame(rows)
    if frame.empty:
        return baselines

    frame["minutes"] = numeric(frame.get("minutes", pd.Series(0.0, index=frame.index)))
    frame["fpl_position"] = frame.get("position", pd.Series("", index=frame.index)).apply(_understat_position)
    frame["xg90"] = numeric(frame.get("xG90", pd.Series(np.nan, index=frame.index)), default=np.nan)
    frame["xa90"] = numeric(frame.get("xA90", pd.Series(np.nan, index=frame.index)), default=np.nan)
    eligible = frame.loc[frame["minutes"] >= PL_HISTORY_MINUTES_THRESHOLD]
    medians = eligible.groupby("fpl_position")[["xg90", "xa90"]].median()
    for position in baselines:
        if position in medians.index:
            for rate in ["xg90", "xa90"]:
                value = medians.loc[position, rate]
                if pd.notna(value):
                    baselines[position][rate] = float(value)
    return baselines


@lru_cache(maxsize=4)
def _load_pl_history_minutes(data_dir: str) -> dict[str, float]:
    minutes_by_name_and_id: dict[str, dict[str, float]] = {}
    for path in Path(data_dir).glob("player_stats_*.json"):
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for row in rows:
            name_key = _normalise_player_name(row.get("player_name"))
            player_id = str(row.get("understat_player_id", "")).strip()
            if not name_key or not player_id:
                continue
            player_totals = minutes_by_name_and_id.setdefault(name_key, {})
            player_totals[player_id] = player_totals.get(player_id, 0.0) + _float_or_zero(row.get("minutes"))
    return {
        name_key: max(player_totals.values(), default=0.0)
        for name_key, player_totals in minutes_by_name_and_id.items()
    }


@lru_cache(maxsize=4)
def _load_foreign_player_index(data_dir: str) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for path in Path(data_dir).glob("*.json"):
        match = re.fullmatch(r"(.+)_(\d{4})\.json", path.name)
        if not match:
            continue
        league, season_text = match.groups()
        if league not in LEAGUE_DIFFICULTY_FACTORS:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for row in payload.get("players", []):
            name_key = _normalise_player_name(row.get("player_name"))
            minutes = _float_or_zero(row.get("time"))
            if not name_key or minutes <= 0:
                continue
            xg = _float_or_zero(row.get("xG"))
            xa = _float_or_zero(row.get("xA"))
            index.setdefault(name_key, []).append(
                {
                    "league": league,
                    "season": int(season_text),
                    "minutes": minutes,
                    "xg90": xg * 90.0 / minutes,
                    "xa90": xa * 90.0 / minutes,
                }
            )
    for candidates in index.values():
        candidates.sort(key=lambda row: (row["season"], row["minutes"]), reverse=True)
    return index


def build_foreign_prior_rate(
    player_name: object,
    fpl_position: str,
    positional_baseline: dict[str, float] | None = None,
    data_dir: Path = FOREIGN_UNDERSTAT_DIR,
) -> dict[str, Any]:
    """Return a PL-equivalent attacking-rate prior from recent foreign data.

    This estimates xG90/xA90 only. New-player minutes remain a separate,
    manually reviewed preseason input until Premier League history accumulates.
    """
    position = str(fpl_position or "").upper()
    baseline = positional_baseline or _load_pl_positional_baselines(str(PL_UNDERSTAT_PLAYER_DIR)).get(
        position,
        {"xg90": 0.0, "xa90": 0.0},
    )
    candidates = _load_foreign_player_index(str(Path(data_dir).resolve())).get(
        _normalise_player_name(player_name),
        [],
    )
    if not candidates:
        return {
            "xg90": float(baseline["xg90"]),
            "xa90": float(baseline["xa90"]),
            "prior_source": "positional_default",
            "foreign_xg90": np.nan,
            "foreign_xa90": np.nan,
            "adjusted_xg90": np.nan,
            "adjusted_xa90": np.nan,
            "source_minutes": 0.0,
            "league": "",
            "season": None,
            "difficulty_factor": 1.0,
        }

    source = candidates[0]
    factor = float(LEAGUE_DIFFICULTY_FACTORS[source["league"]])
    adjusted_xg90 = float(source["xg90"]) * factor
    adjusted_xa90 = float(source["xa90"]) * factor
    return {
        "xg90": _shrink_rate(
            adjusted_xg90,
            source["minutes"],
            float(baseline["xg90"]),
            FOREIGN_PRIOR_WEIGHT_MINUTES,
        ),
        "xa90": _shrink_rate(
            adjusted_xa90,
            source["minutes"],
            float(baseline["xa90"]),
            FOREIGN_PRIOR_WEIGHT_MINUTES,
        ),
        "prior_source": f"{source['league']}_{source['season']}",
        "foreign_xg90": float(source["xg90"]),
        "foreign_xa90": float(source["xa90"]),
        "adjusted_xg90": adjusted_xg90,
        "adjusted_xa90": adjusted_xa90,
        "source_minutes": float(source["minutes"]),
        "league": str(source["league"]),
        "season": int(source["season"]),
        "difficulty_factor": factor,
    }


def _player_name(row: pd.Series) -> str:
    full_name = f"{row.get('first_name', '')} {row.get('second_name', '')}".strip()
    return full_name or str(row.get("web_name", row.get("name", "")))


def _order_share(series: pd.Series) -> pd.Series:
    weights = {1.0: 1.0, 2.0: 0.45, 3.0: 0.18}
    return numeric(series).map(weights).fillna(0.0)


def attach_recent_player_form(
    players: pd.DataFrame,
    history_by_player: dict[int, pd.DataFrame],
    half_life: float = 4.0,
) -> pd.DataFrame:
    """Attach recent weighted xG/xA and role features from FPL element history."""
    if not history_by_player:
        return players
    rows = []
    for player_id, history in history_by_player.items():
        if history is None or history.empty or "minutes" not in history.columns:
            continue
        frame = history.tail(10).copy()
        mins = numeric(frame.get("minutes", pd.Series(0, index=frame.index)))
        if float(mins.sum()) <= 0:
            continue
        age = np.arange(len(frame) - 1, -1, -1)
        weights = np.power(0.5, age / max(float(half_life), 0.1)) * mins.clip(lower=0)
        weight_sum = float(weights.sum())
        if weight_sum <= 0:
            continue
        xg = numeric(frame.get("expected_goals", pd.Series(0, index=frame.index)))
        xa = numeric(frame.get("expected_assists", pd.Series(0, index=frame.index)))
        starts = numeric(frame.get("starts", pd.Series(0, index=frame.index)))
        rows.append(
            {
                "id": int(player_id),
                "form_minutes": float(mins.sum()),
                "form_xg90": float((xg * weights).sum() / weight_sum * 90.0),
                "form_xa90": float((xa * weights).sum() / weight_sum * 90.0),
                "form_start_rate": float(starts.tail(6).mean()) if len(starts) else 0.0,
            }
        )
    if not rows:
        return players
    return players.merge(pd.DataFrame(rows), on="id", how="left")


def build_player_rates(
    players: pd.DataFrame,
    prior_minutes: float = 900.0,
    form_blend_weight: float = 0.3,
) -> pd.DataFrame:
    """Build shrunken player rates from current FPL player rows."""
    df = players.copy()
    df["position"] = df["element_type"].map(POSITION_BY_ELEMENT_TYPE).fillna("")
    df["minutes"] = numeric(df.get("minutes", pd.Series(0, index=df.index)))
    if "pl_history_minutes" in df.columns:
        supplied_history = numeric(df["pl_history_minutes"])
    else:
        history_lookup = _load_pl_history_minutes(str(PL_UNDERSTAT_PLAYER_DIR))
        supplied_history = df.apply(
            lambda row: history_lookup.get(_normalise_player_name(_player_name(row)), 0.0),
            axis=1,
        )
    df["pl_history_minutes"] = np.maximum(df["minutes"], supplied_history)

    for col in [
        "expected_goals_per_90",
        "expected_assists_per_90",
        "defensive_contribution_per_90",
        "saves_per_90",
        "yellow_cards",
        "red_cards",
        "penalties_order",
        "corners_and_indirect_freekicks_order",
        "direct_freekicks_order",
    ]:
        if col not in df.columns:
            df[col] = 0.0
        df[col] = numeric(df[col])

    fpl_xg90 = df["expected_goals_per_90"].copy()
    fpl_xa90 = df["expected_assists_per_90"].copy()
    if "form_xg90" in df.columns or "form_xa90" in df.columns:
        form_minutes = numeric(df.get("form_minutes", pd.Series(0, index=df.index)))
        form_weight = (form_minutes / 540.0).clip(0.0, 1.0) * float(form_blend_weight)
        position_form_multiplier = df["position"].map({"GK": 0.0, "DEF": 0.25, "MID": 1.0, "FWD": 1.0}).fillna(0.7)
        form_weight = form_weight * position_form_multiplier
        form_xg90 = numeric(df.get("form_xg90", pd.Series(0, index=df.index)))
        form_xa90 = numeric(df.get("form_xa90", pd.Series(0, index=df.index)))
        has_form_xg = form_xg90 > 0
        has_form_xa = form_xa90 > 0
        df["expected_goals_per_90"] = np.where(
            has_form_xg,
            fpl_xg90 * (1.0 - form_weight) + form_xg90 * form_weight,
            fpl_xg90,
        )
        df["expected_assists_per_90"] = np.where(
            has_form_xa,
            fpl_xa90 * (1.0 - form_weight) + form_xa90 * form_weight,
            fpl_xa90,
        )
        df["form_blend_weight"] = np.where(has_form_xg | has_form_xa, form_weight, 0.0)
    else:
        df["form_blend_weight"] = 0.0

    fpl_xg90 = df["expected_goals_per_90"].copy()
    fpl_xa90 = df["expected_assists_per_90"].copy()
    if "understat_npxG90" in df.columns or "understat_xA90" in df.columns:
        understat_minutes = numeric(df.get("understat_minutes", pd.Series(0, index=df.index)))
        understat_weight = (understat_minutes / 900.0).clip(0.0, 1.0) * 0.65
        understat_xg90 = numeric(df.get("understat_npxG90", df.get("understat_xG90", pd.Series(0, index=df.index))))
        understat_xa90 = numeric(df.get("understat_xA90", pd.Series(0, index=df.index)))
        has_understat_xg = understat_xg90 > 0
        has_understat_xa = understat_xa90 > 0
        df["expected_goals_per_90"] = np.where(
            has_understat_xg,
            fpl_xg90 * (1.0 - understat_weight) + understat_xg90 * understat_weight,
            fpl_xg90,
        )
        df["expected_assists_per_90"] = np.where(
            has_understat_xa,
            fpl_xa90 * (1.0 - understat_weight) + understat_xa90 * understat_weight,
            fpl_xa90,
        )
        df["fpl_expected_goals_per_90_raw"] = fpl_xg90
        df["fpl_expected_assists_per_90_raw"] = fpl_xa90
        df["understat_blend_weight"] = np.where(has_understat_xg | has_understat_xa, understat_weight, 0.0)
    else:
        df["understat_blend_weight"] = 0.0

    priors = (
        df.loc[df["minutes"] >= PL_HISTORY_MINUTES_THRESHOLD]
        .groupby("position", as_index=True)[["expected_goals_per_90", "expected_assists_per_90", "defensive_contribution_per_90", "saves_per_90"]]
        .median()
    )

    df["prior_based"] = df["pl_history_minutes"] < PL_HISTORY_MINUTES_THRESHOLD
    df["prior_source"] = "observed_pl"
    foreign_priors: dict[Any, dict[str, Any]] = {}
    for index, row in df.loc[df["prior_based"]].iterrows():
        foreign_prior = build_foreign_prior_rate(_player_name(row), str(row["position"]))
        foreign_priors[index] = foreign_prior
        df.at[index, "prior_source"] = foreign_prior["prior_source"]

    def shrink(row: pd.Series, col: str) -> float:
        pos = row["position"]
        prior = float(priors.loc[pos, col]) if pos in priors.index else float(df[col].median())
        if bool(row["prior_based"]) and col in {"expected_goals_per_90", "expected_assists_per_90"}:
            foreign_prior = foreign_priors[row.name]
            prior = float(
                foreign_prior["xg90"]
                if col == "expected_goals_per_90"
                else foreign_prior["xa90"]
            )
        mins = float(max(row["minutes"], 0.0))
        raw = float(row[col])
        return _shrink_rate(raw, mins, prior, prior_minutes)

    for col in ["expected_goals_per_90", "expected_assists_per_90", "defensive_contribution_per_90", "saves_per_90"]:
        df[f"{col}_shrunk"] = df.apply(lambda row: shrink(row, col), axis=1)

    xg_caps = {"GK": 0.01, "DEF": 0.18, "MID": 0.58, "FWD": 1.00}
    xa_caps = {"GK": 0.02, "DEF": 0.22, "MID": 0.70, "FWD": 0.45}
    df["expected_goals_per_90_shrunk"] = df.apply(
        lambda row: min(float(row["expected_goals_per_90_shrunk"]), xg_caps.get(row["position"], 0.70)),
        axis=1,
    )
    df["expected_assists_per_90_shrunk"] = df.apply(
        lambda row: min(float(row["expected_assists_per_90_shrunk"]), xa_caps.get(row["position"], 0.65)),
        axis=1,
    )

    matches_90 = np.maximum(df["minutes"] / 90.0, 1.0)
    df["yc_rate90"] = (df["yellow_cards"] / matches_90).clip(0, 1)
    df["rc_rate90"] = (df["red_cards"] / matches_90).clip(0, 1)
    penalty_weights = {1.0: 0.78, 2.0: 0.17, 3.0: 0.05}
    df["penalty_share"] = df["penalties_order"].map(penalty_weights).fillna(0.0)
    df["set_piece_share"] = (
        _order_share(df["corners_and_indirect_freekicks_order"]) * 0.75
        + _order_share(df["direct_freekicks_order"]) * 0.35
    ).clip(0.0, 1.25)
    return df


def build_team_assist_factors(
    players: pd.DataFrame,
    teams: pd.DataFrame,
    league_prior: float = 0.73,
    form_blend_weight: float = 0.3,
) -> dict[int, float]:
    """Estimate team assisted-goal ratio from current player xA/xG ecology."""
    rates = build_player_rates(players, form_blend_weight=form_blend_weight)
    active = rates.loc[rates["minutes"] > 0].copy()
    if active.empty:
        return {int(team_id): float(league_prior) for team_id in teams["id"]}
    grouped = (
        active.groupby("team", as_index=False)
        .agg(
            xg90=("expected_goals_per_90_shrunk", "sum"),
            xa90=("expected_assists_per_90_shrunk", "sum"),
            minutes=("minutes", "sum"),
        )
    )
    grouped["raw_factor"] = np.divide(
        grouped["xa90"],
        grouped["xg90"].clip(lower=0.15),
    )
    shrink = (grouped["minutes"] / 18000.0).clip(0.0, 1.0)
    grouped["assist_factor"] = (league_prior * (1.0 - shrink) + grouped["raw_factor"] * shrink).clip(0.62, 0.82)
    return {
        int(row["team"]): float(row["assist_factor"])
        for _, row in grouped.iterrows()
    }


def build_team_strength(
    players: pd.DataFrame,
    teams: pd.DataFrame,
    form_blend_weight: float = 0.3,
) -> pd.DataFrame:
    rates = build_player_rates(players, form_blend_weight=form_blend_weight)
    active = rates.loc[rates["minutes"] > 0].copy()
    team_attack = (
        active.groupby("team", as_index=False)
        .agg(
            player_xg90=("expected_goals_per_90_shrunk", "sum"),
            player_xa90=("expected_assists_per_90_shrunk", "sum"),
            player_minutes=("minutes", "sum"),
        )
    )
    out = teams[["id", "name", "short_name", "strength_attack_home", "strength_attack_away", "strength_defence_home", "strength_defence_away"]].copy()
    out = out.merge(team_attack, left_on="id", right_on="team", how="left")
    for col in ["player_xg90", "player_xa90"]:
        out[col] = numeric(out[col], default=float(team_attack[col].median() if not team_attack.empty else 1.3))
    return out
