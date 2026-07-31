from __future__ import annotations

import json
import logging
import math
import os
import re
import ssl
import unicodedata
from dataclasses import dataclass
from datetime import timedelta
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd
try:
    import requests
except ImportError:  # pragma: no cover - requests is optional in this project.
    requests = None

from .config import AppConfig


ODDS_BASE_URL = "https://api.the-odds-api.com/v4"
MAX_GOALS = 12
logger = logging.getLogger(__name__)


def _norm(text: object) -> str:
    s = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _canon_team(name: object) -> str:
    s = _norm(name)
    aliases = {
        "arsenal": "arsenal",
        "aston villa": "aston villa",
        "bournemouth": "bournemouth",
        "afc bournemouth": "bournemouth",
        "brentford": "brentford",
        "brighton": "brighton",
        "brighton hove albion": "brighton",
        "brighton and hove albion": "brighton",
        "burnley": "burnley",
        "chelsea": "chelsea",
        "crystal palace": "crystal palace",
        "everton": "everton",
        "fulham": "fulham",
        "leeds": "leeds",
        "leeds united": "leeds",
        "liverpool": "liverpool",
        "man city": "manchester city",
        "manchester city": "manchester city",
        "man utd": "manchester united",
        "man united": "manchester united",
        "manchester united": "manchester united",
        "newcastle": "newcastle united",
        "newcastle united": "newcastle united",
        "nottingham forest": "nottingham forest",
        "nott m forest": "nottingham forest",
        "nottm forest": "nottingham forest",
        "sunderland": "sunderland",
        "spurs": "tottenham",
        "tottenham": "tottenham",
        "tottenham hotspur": "tottenham",
        "west ham": "west ham",
        "west ham united": "west ham",
        "wolves": "wolverhampton wanderers",
        "wolverhampton": "wolverhampton wanderers",
        "wolverhampton wanderers": "wolverhampton wanderers",
    }
    return aliases.get(s, s)


def _median_price(prices: list[float]) -> float | None:
    clean = [float(x) for x in prices if x and math.isfinite(float(x)) and float(x) > 1.0]
    return float(np.median(clean)) if clean else None


def _devig_decimal(prices: dict[str, float | None]) -> dict[str, float]:
    inv = {k: (1.0 / float(v)) for k, v in prices.items() if v and float(v) > 1.0}
    total = sum(inv.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in inv.items()}


def _poisson_pmf(lam: float, max_goals: int = MAX_GOALS) -> np.ndarray:
    lam = max(float(lam), 0.01)
    vals = np.zeros(max_goals + 1, dtype=float)
    vals[0] = math.exp(-lam)
    for k in range(1, max_goals + 1):
        vals[k] = vals[k - 1] * lam / k
    vals[-1] += max(0.0, 1.0 - vals.sum())
    return vals


def _match_probs(lam_h: float, lam_a: float, total_line: float = 2.5) -> dict[str, float]:
    h = _poisson_pmf(lam_h)
    a = _poisson_pmf(lam_a)
    matrix = np.outer(h, a)
    hg = np.arange(len(h))[:, None]
    ag = np.arange(len(a))[None, :]
    return {
        "home": float(matrix[hg > ag].sum()),
        "draw": float(matrix[hg == ag].sum()),
        "away": float(matrix[hg < ag].sum()),
        "over": float(matrix[(hg + ag) > total_line].sum()),
    }


def _fit_lambdas(
    h2h_probs: dict[str, float],
    over_prob: float | None,
    total_line: float = 2.5,
    fallback_home_xg: float = 1.4,
    fallback_away_xg: float = 1.1,
) -> tuple[float, float, float]:
    """Fit independent Poisson lambdas to available 1X2 and total-goals markets."""
    fallback_total = max(float(fallback_home_xg) + float(fallback_away_xg), 0.8)
    grid = np.arange(0.10, 4.55, 0.05)
    best_error = float("inf")
    best = (max(float(fallback_home_xg), 0.1), max(float(fallback_away_xg), 0.1))

    for lam_h in grid:
        for lam_a in grid:
            probs = _match_probs(lam_h, lam_a, total_line)
            error = 0.0
            weight = 0.0
            for key in ["home", "draw", "away"]:
                if key in h2h_probs:
                    error += 3.0 * (probs[key] - float(h2h_probs[key])) ** 2
                    weight += 3.0
            if over_prob is not None:
                error += 2.0 * (probs["over"] - float(over_prob)) ** 2
                weight += 2.0
            else:
                error += 0.35 * ((lam_h + lam_a) - fallback_total) ** 2
                weight += 0.35
            error = error / max(weight, 1.0)
            if error < best_error:
                best_error = error
                best = (float(lam_h), float(lam_a))

    return best[0], best[1], best_error


def _fetch_odds(config: AppConfig) -> list[dict]:
    api_key = os.getenv(config.odds_api_key_env, "").strip()
    if not api_key:
        return []
    params = {
        "apiKey": api_key,
        "regions": config.odds_api_regions,
        "markets": "h2h,totals",
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }
    if config.odds_api_bookmakers:
        params["bookmakers"] = config.odds_api_bookmakers
        params.pop("regions", None)
    url = f"{ODDS_BASE_URL}/sports/{config.odds_api_sport}/odds?{urlencode(params)}"
    if requests is not None:
        response = requests.get(url, headers={"User-Agent": "fpl-xpts-market-odds/0.1"}, timeout=45)
        response.raise_for_status()
        return response.json()
    request = Request(url, headers={"User-Agent": "fpl-xpts-market-odds/0.1"})
    # No context: urllib uses Python's default verified CA context.
    with urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def _is_certificate_verification_error(exc: BaseException) -> bool:
    if isinstance(exc, ssl.SSLCertVerificationError):
        return True
    if requests is not None and isinstance(exc, requests.exceptions.SSLError):
        return True
    return isinstance(exc, URLError) and isinstance(exc.reason, ssl.SSLCertVerificationError)


@dataclass(frozen=True)
class OddsProjection:
    home_key: str
    away_key: str
    commence_time: pd.Timestamp
    home_xg: float
    away_xg: float
    fit_error: float
    source: str
    home_win_prob: float | None = None
    draw_prob: float | None = None
    away_win_prob: float | None = None
    over_prob: float | None = None
    total_line: float | None = None


def _projection_from_event(event: dict, fallback: pd.DataFrame | None = None) -> OddsProjection | None:
    home_key = _canon_team(event.get("home_team"))
    away_key = _canon_team(event.get("away_team"))
    if not home_key or not away_key:
        return None

    h2h_prices: dict[str, list[float]] = {"home": [], "draw": [], "away": []}
    totals_by_line: dict[float, dict[str, list[float]]] = {}
    for bookmaker in event.get("bookmakers", []) or []:
        for market in bookmaker.get("markets", []) or []:
            key = market.get("key")
            if key == "h2h":
                for outcome in market.get("outcomes", []) or []:
                    name_key = _canon_team(outcome.get("name"))
                    price = outcome.get("price")
                    if name_key == home_key:
                        h2h_prices["home"].append(price)
                    elif name_key == away_key:
                        h2h_prices["away"].append(price)
                    elif _norm(outcome.get("name")) == "draw":
                        h2h_prices["draw"].append(price)
            elif key == "totals":
                for outcome in market.get("outcomes", []) or []:
                    point = outcome.get("point")
                    price = outcome.get("price")
                    name = _norm(outcome.get("name"))
                    if point is None or name not in {"over", "under"}:
                        continue
                    line = float(point)
                    totals_by_line.setdefault(line, {"over": [], "under": []})[name].append(price)

    h2h_price = {k: _median_price(v) for k, v in h2h_prices.items()}
    h2h_probs = _devig_decimal(h2h_price)
    if not {"home", "draw", "away"}.issubset(h2h_probs):
        return None

    total_line = 2.5
    over_prob = None
    if totals_by_line:
        total_line = min(totals_by_line, key=lambda line: abs(float(line) - 2.5))
        total_prices = {k: _median_price(v) for k, v in totals_by_line[total_line].items()}
        total_probs = _devig_decimal(total_prices)
        over_prob = total_probs.get("over")

    fallback_home = 1.4
    fallback_away = 1.1
    if fallback is not None and not fallback.empty:
        matched = fallback.loc[(fallback["home_key"] == home_key) & (fallback["away_key"] == away_key)]
        if not matched.empty:
            fallback_home = float(matched.iloc[0]["home_xg"])
            fallback_away = float(matched.iloc[0]["away_xg"])

    home_xg, away_xg, fit_error = _fit_lambdas(
        h2h_probs,
        over_prob,
        total_line=float(total_line),
        fallback_home_xg=fallback_home,
        fallback_away_xg=fallback_away,
    )
    return OddsProjection(
        home_key=home_key,
        away_key=away_key,
        commence_time=pd.to_datetime(event.get("commence_time"), utc=True, errors="coerce"),
        home_xg=home_xg,
        away_xg=away_xg,
        fit_error=fit_error,
        source="the_odds_api_h2h_totals" if over_prob is not None else "the_odds_api_h2h",
        home_win_prob=h2h_probs.get("home"),
        draw_prob=h2h_probs.get("draw"),
        away_win_prob=h2h_probs.get("away"),
        over_prob=over_prob,
        total_line=float(total_line),
    )


def _fallback_frame(fixtures: pd.DataFrame, teams: pd.DataFrame) -> pd.DataFrame:
    team_names = teams[["id", "name"]].copy()
    team_names["team_key"] = team_names["name"].apply(_canon_team)
    key_by_id = dict(zip(team_names["id"], team_names["team_key"]))
    out = fixtures[["id", "team_h", "team_a", "home_xg", "away_xg"]].copy()
    out["home_key"] = out["team_h"].map(key_by_id)
    out["away_key"] = out["team_a"].map(key_by_id)
    return out


def apply_market_odds_projections(
    fixtures: pd.DataFrame,
    teams: pd.DataFrame,
    config: AppConfig = AppConfig(),
) -> pd.DataFrame:
    """Override fixture lambdas with market-implied goals when an odds API key is available.

    The model remains runnable without credentials. If `ODDS_API_KEY` is absent or no
    fixture match is found, the existing fixture lambdas stay in place and the source
    column explains the fallback.
    """
    out = fixtures.copy()
    if out.empty or not config.use_market_odds:
        out["market_odds_source"] = "disabled"
        return out

    fallback = _fallback_frame(out, teams)
    try:
        odds_events = _fetch_odds(config)
    except Exception as exc:
        if _is_certificate_verification_error(exc):
            # TLS failures are security failures, not a reason to silently use stale fallback data.
            logger.error("TLS certificate verification failed while fetching market odds")
            raise
        logger.warning(
            "Market odds request failed (%s); using FPL-strength fallback",
            type(exc).__name__,
        )
        out["market_odds_source"] = f"odds_fetch_failed:{type(exc).__name__}"
        return out
    if not odds_events:
        out["market_odds_source"] = "no_odds_api_key_or_no_events"
        return out

    projections = [
        proj for event in odds_events
        if (proj := _projection_from_event(event, fallback=fallback)) is not None
    ]
    if not projections:
        out["market_odds_source"] = "odds_unmatched"
        return out

    team_names = teams[["id", "name"]].copy()
    team_names["team_key"] = team_names["name"].apply(_canon_team)
    key_by_id = dict(zip(team_names["id"], team_names["team_key"]))
    out["_home_key"] = out["team_h"].map(key_by_id)
    out["_away_key"] = out["team_a"].map(key_by_id)
    out["_kickoff_utc"] = pd.to_datetime(out.get("kickoff_time"), utc=True, errors="coerce")
    out["market_odds_source"] = "fpl_strength_fallback"

    for idx, fixture in out.iterrows():
        kickoff = fixture["_kickoff_utc"]
        candidates = [
            p for p in projections
            if p.home_key == fixture["_home_key"] and p.away_key == fixture["_away_key"]
        ]
        if kickoff is not pd.NaT and not pd.isna(kickoff):
            candidates = [
                p for p in candidates
                if p.commence_time is not pd.NaT
                and not pd.isna(p.commence_time)
                and abs(p.commence_time - kickoff) <= timedelta(days=3)
            ] or candidates
        if not candidates:
            continue
        best = min(
            candidates,
            key=lambda p: abs(p.commence_time - kickoff).total_seconds()
            if kickoff is not pd.NaT and not pd.isna(kickoff) and p.commence_time is not pd.NaT and not pd.isna(p.commence_time)
            else 0.0,
        )
        out.loc[idx, "home_xg"] = best.home_xg
        out.loc[idx, "away_xg"] = best.away_xg
        out.loc[idx, "home_xa"] = max(0.0, best.home_xg * 0.72)
        out.loc[idx, "away_xa"] = max(0.0, best.away_xg * 0.72)
        out.loc[idx, "home_cs_prob"] = math.exp(-best.away_xg)
        out.loc[idx, "away_cs_prob"] = math.exp(-best.home_xg)
        out.loc[idx, "market_odds_source"] = best.source
        out.loc[idx, "odds_fit_error"] = best.fit_error
        out.loc[idx, "odds_home_win_prob"] = best.home_win_prob
        out.loc[idx, "odds_draw_prob"] = best.draw_prob
        out.loc[idx, "odds_away_win_prob"] = best.away_win_prob
        out.loc[idx, "odds_over_prob"] = best.over_prob
        out.loc[idx, "odds_total_line"] = best.total_line

    return out.drop(columns=["_home_key", "_away_key", "_kickoff_utc"], errors="ignore")
