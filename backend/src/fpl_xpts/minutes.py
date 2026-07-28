from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .config import MODELS_DIR


def availability_multiplier(chance: float | int | None) -> float:
    if chance is None or pd.isna(chance):
        return 1.0
    return float(np.clip(float(chance) / 100.0, 0.0, 1.0))


def estimate_expected_minutes(player: pd.Series, history: pd.DataFrame | None = None) -> float:
    """Estimate xMins from start rate and average minutes per appearance."""
    _, exp = estimate_start_and_minutes(player, history)
    return exp


def estimate_start_and_minutes(player: pd.Series, history: pd.DataFrame | None = None) -> tuple[float, float]:
    """Return start probability and expected minutes from FPL API/history."""
    # Foreign priors estimate rates, not roles. For new preseason signings the
    # manual minutes CSV applied later in the pipeline is authoritative.
    if history is None or history.empty or "minutes" not in history.columns:
        total_minutes = float(player.get("minutes", 0) or 0)
        starts = float(player.get("starts", 0) or 0)
        appearances = float(
            player.get("appearances", player.get("apps", player.get("matches_played", 0))) or 0
        )
        appearances = max(appearances, starts, 1.0)
    else:
        mins = pd.to_numeric(history["minutes"], errors="coerce").fillna(0)
        active = history.loc[mins > 0].copy()
        appearances = float(len(active))
        total_minutes = float(pd.to_numeric(active.get("minutes", 0), errors="coerce").fillna(0).sum())
        if "starts" in active.columns:
            starts = float(pd.to_numeric(active["starts"], errors="coerce").fillna(0).sum())
        else:
            starts = float((pd.to_numeric(active.get("minutes", 0), errors="coerce").fillna(0) >= 60).sum())

    if appearances <= 0 or total_minutes <= 0:
        start_pct = 0.0
        exp = 0.0
    else:
        start_pct = float(np.clip(starts / appearances, 0.0, 1.0))
        avg_minutes = float(np.clip(total_minutes / appearances, 0.0, 90.0))
        exp = avg_minutes * (0.85 + 0.15 * start_pct)

    chance = player.get("chance_of_playing_next_round", None)
    if chance is None or pd.isna(chance):
        chance = player.get("chance_of_playing_this_round", None)
    availability = availability_multiplier(chance)
    return float(np.clip(start_pct * availability, 0.0, 1.0)), float(np.clip(exp * availability, 0.0, 90.0))


def apply_trained_minutes_model(
    player_fixture: pd.DataFrame,
    players: pd.DataFrame,
    teams: pd.DataFrame,
    history_by_player: dict[int, pd.DataFrame] | None = None,
    model_path: Path = MODELS_DIR / "minutes_model.pkl",
) -> pd.DataFrame:
    """Apply the trained minutes model, retaining the existing heuristic as fallback."""
    from .minutes_model import apply_live_minutes_model

    return apply_live_minutes_model(
        player_fixture,
        players,
        teams,
        history_by_player=history_by_player,
        model_path=model_path,
    )


def minute_outcomes(
    expected_minutes: float,
    start_probability: float | None = None,
    play_probability: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Return minute states.

    When play_probability is provided, expected_minutes is interpreted as the
    likely on-pitch minutes if the player appears. The resulting distribution is
    no-show/sub/start, with mean equal to simulation exposure.
    """
    m = float(np.clip(expected_minutes, 0.0, 90.0))
    if m <= 0:
        return np.array([0], dtype=int), np.array([1.0])

    if play_probability is not None and not pd.isna(play_probability):
        p_play = float(np.clip(play_probability, 0.0, 1.0))
        if p_play <= 0.0:
            return np.array([0], dtype=int), np.array([1.0])
        p_start = float(np.clip(start_probability if start_probability is not None and not pd.isna(start_probability) else p_play, 0.0, p_play))
        p_sub = max(0.0, p_play - p_start)
        p_zero = max(0.0, 1.0 - p_play)
        if p_start <= 0.0:
            vals = np.array([0, int(round(np.clip(m, 1.0, 45.0)))], dtype=int)
            probs = np.array([p_zero, p_play], dtype=float)
            return vals, probs / probs.sum()
        start_min = int(round(np.clip(m, 1.0, 90.0)))
        sub_min = int(round(np.clip(min(30.0, max(10.0, m * 0.35)), 1.0, max(1.0, start_min))))
        vals = np.array([0, sub_min, start_min], dtype=int)
        probs = np.array([p_zero, p_sub, p_start], dtype=float)
        return vals, probs / probs.sum()

    if start_probability is not None and not pd.isna(start_probability):
        p_start = float(np.clip(start_probability, 0.0, 1.0))
        if p_start <= 0.02:
            sub_min = int(np.clip(round(max(m, 1.0)), 1, 45))
            p_sub = float(np.clip(m / max(sub_min, 1), 0.0, 1.0))
            return np.array([0, sub_min], dtype=int), np.array([1.0 - p_sub, p_sub])
        if p_start < 0.95:
            start_min = float(np.clip(m / max(p_start, 1e-6), 60.0, 90.0))
            remaining = max(0.0, m - p_start * start_min)
            sub_min = 25.0
            p_sub = float(np.clip(remaining / sub_min, 0.0, max(0.0, 1.0 - p_start)))
            p_zero = max(0.0, 1.0 - p_start - p_sub)
            vals = np.array([0, int(round(sub_min)), int(round(start_min))], dtype=int)
            probs = np.array([p_zero, p_sub, p_start], dtype=float)
            probs = probs / probs.sum()
            return vals, probs

    if m >= 88:
        return np.array([75, 90], dtype=int), np.array([(90 - m) / 15, (m - 75) / 15])
    if m >= 60:
        vals = np.array([45, 60, 90], dtype=int)
    elif m >= 30:
        vals = np.array([0, 45, 70], dtype=int)
    else:
        vals = np.array([0, 20, 60], dtype=int)

    distances = np.abs(vals.astype(float) - m)
    weights = 1.0 / np.maximum(distances, 1.0)
    probs = weights / weights.sum()
    mean = float(np.dot(vals, probs))
    if mean > 0:
        probs = probs * (m / mean)
        excess = probs.sum() - 1.0
        if excess > 0:
            probs[np.argmax(vals)] = max(0.0, probs[np.argmax(vals)] - excess)
        probs = probs / probs.sum()
    return vals, probs
