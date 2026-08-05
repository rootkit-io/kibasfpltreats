"""Pure parsing / validation / identity-resolution for precomputed run CSVs.

Deliberately free of FastAPI and psycopg imports so the whole preflight can be
unit-tested against real model output without a database or an HTTP stack.

The two files are the native local-model exports:

``weekly_player_week.csv``
    player-gameweek grain -> ``player_gameweek_projections``
``mc_brackets_full_player_week.csv``
    Monte Carlo distribution -> ``player_gameweek_simulations``

Neither file carries ``season`` or numeric FPL ids; both key on
``player_key`` (``"<normalised name>|<canonical team>"``). Identity is
therefore resolved against the ``players`` / ``teams`` dimension tables using
the *same* normalisation the model used to build the key, imported from
``fpl_xpts.shot_profiles`` so the two can never drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

import pandas as pd

try:  # the modelling package is the source of truth for key normalisation
    from fpl_xpts.shot_profiles import _canon_team, _norm
except ModuleNotFoundError:  # pragma: no cover - source checkout without install
    import sys
    from pathlib import Path

    _SRC = Path(__file__).resolve().parents[2] / "src"
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    from fpl_xpts.shot_profiles import _canon_team, _norm


class IngestValidationError(Exception):
    """Preflight failure. Carries structured detail for a 400 response."""

    def __init__(self, message: str, errors: list[dict] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.errors = errors or []

    def as_detail(self) -> dict:
        return {"message": self.message, "errors": self.errors}


# --------------------------------------------------------------- column maps
# Left: CSV header as the model actually emits it. Right: DB column.
#
# NOTE the real headers are `mins`, `xG_scaled`, `xA_scaled` -- not
# `expected_minutes` / `xG` / `xA`. The internal pipeline frame uses the
# latter; these CSVs are the *export* view and differ.
WEEKLY_COLUMN_MAP: dict[str, str] = {
    "mins": "expected_minutes",
    "xG_scaled": "xg",
    "xA_scaled": "xa",
    "xGA_exp": "xga_expected",
    "xPts": "xpts",
    "P1_GA": "p1_ga",
    "AppPts": "appearance_pts",
    "GoalPts": "goal_pts",
    "AssistPts": "assist_pts",
    "CSPts": "cs_pts",
    "SavePts": "save_pts",
    "DefconPts": "defcon_pts",
    "CardPts": "card_pts",
    "PenMissPts": "pen_miss_pts",
    "ConcedePts": "concede_pts",
    "fixtures_in_week": "fixtures_in_week",
}

MC_COLUMN_MAP: dict[str, str] = {
    "MC_MeanPts": "mean_pts",
    "MC_StdPts": "std_pts",
    "MC_MinPts": "min_pts",
    "MC_MaxPts": "max_pts",
    "MC_Floor": "floor_p10",
    "MC_P25": "p25",
    "MC_P75": "p75",
    "MC_Upside": "upside_p90",
    "MC_P1_Return": "p1_return",
    "MC_P2_Return": "p2_return",
    "Bracket_LE_2": "bracket_le_2",
    "Bracket_3_to_6": "bracket_3_6",
    "Bracket_7_to_9": "bracket_7_9",
    "Bracket_10_to_14": "bracket_10_14",
    "Bracket_15_plus": "bracket_15_plus",
}

WEEKLY_REQUIRED: tuple[str, ...] = (
    "GW", "player_key", "player", "team", "Pos", "mins", "xPts",
    "fixtures_in_week",
)
MC_REQUIRED: tuple[str, ...] = (
    "GW", "player_key", "MC_MeanPts", "MC_StdPts", "MC_Floor", "MC_P25",
    "MC_P75", "MC_Upside", "MC_P1_Return", "MC_P2_Return", "Bracket_LE_2",
    "Bracket_3_to_6", "Bracket_7_to_9", "Bracket_10_to_14", "Bracket_15_plus",
)

#: DB CHECK constraints require these in [0, 1]; validate before INSERT so a
#: bad export is a 400 with a row reference, not an opaque IntegrityError.
MC_PROBABILITY_COLUMNS: tuple[str, ...] = (
    "MC_P1_Return", "MC_P2_Return", "Bracket_LE_2", "Bracket_3_to_6",
    "Bracket_7_to_9", "Bracket_10_to_14", "Bracket_15_plus",
)

VALID_POSITIONS = frozenset({"GK", "DEF", "MID", "FWD"})

#: Present in the export but with no column in the current schema. Recorded in
#: the run's `inputs` JSON so the drop is visible rather than silent.
UNMAPPED_WEEKLY = ("cs_prob",)
UNMAPPED_MC = ("MC_CaptainMean", "MC_CaptainUpside")


# ------------------------------------------------------------------ parsing
def read_projection_csv(raw: bytes, *, label: str) -> pd.DataFrame:
    """Parse raw upload bytes into a DataFrame, or raise a 400-shaped error."""
    if not raw.strip():
        raise IngestValidationError(f"{label} is empty")
    from io import BytesIO

    try:
        frame = pd.read_csv(BytesIO(raw))
    except Exception as exc:  # pandas raises a zoo of parser errors
        raise IngestValidationError(
            f"{label} is not valid CSV", [{"file": label, "detail": str(exc)}]
        ) from exc
    if frame.empty:
        raise IngestValidationError(f"{label} contains no data rows")
    return frame


def require_columns(frame: pd.DataFrame, required: Iterable[str], *, label: str) -> None:
    missing = [c for c in required if c not in frame.columns]
    if missing:
        raise IngestValidationError(
            f"{label} is missing required column(s): {', '.join(missing)}",
            [{"file": label, "missing_columns": missing}],
        )


# ------------------------------------------------------- symmetry preflight
@dataclass(frozen=True)
class SymmetryReport:
    gameweeks: list[int]
    row_count: int
    player_count: int


def check_symmetry(weekly: pd.DataFrame, mc: pd.DataFrame) -> SymmetryReport:
    """Reject stale-file mixing.

    The two exports come from one model execution, so they must agree on both
    the gameweek horizon and the exact ``(GW, player_key)`` key set. Comparing
    only headers or row counts would let a *different run's* file through.
    """
    weekly_gws = sorted({int(g) for g in weekly["GW"].unique()})
    mc_gws = sorted({int(g) for g in mc["GW"].unique()})
    if weekly_gws != mc_gws:
        raise IngestValidationError(
            "the two files cover different gameweeks -- they are not from the "
            "same model run",
            [{"weekly_gameweeks": weekly_gws, "mc_gameweeks": mc_gws}],
        )

    weekly_keys = set(zip(weekly["GW"].astype(int), weekly["player_key"].astype(str)))
    mc_keys = set(zip(mc["GW"].astype(int), mc["player_key"].astype(str)))
    only_weekly = weekly_keys - mc_keys
    only_mc = mc_keys - weekly_keys
    if only_weekly or only_mc:
        raise IngestValidationError(
            "the two files do not cover the same player-gameweeks -- they are "
            "not from the same model run",
            [{
                "missing_from_mc": _sample_keys(only_weekly),
                "missing_from_weekly": _sample_keys(only_mc),
                "missing_from_mc_count": len(only_weekly),
                "missing_from_weekly_count": len(only_mc),
            }],
        )

    duplicated = weekly.duplicated(subset=["GW", "player_key"]).sum()
    if duplicated:
        raise IngestValidationError(
            f"weekly file has {int(duplicated)} duplicate (GW, player_key) rows"
        )

    return SymmetryReport(
        gameweeks=weekly_gws,
        row_count=int(len(weekly)),
        player_count=int(weekly["player_key"].nunique()),
    )


def _sample_keys(keys: set[tuple[int, str]], limit: int = 5) -> list[str]:
    return [f"GW{gw}:{key}" for gw, key in sorted(keys)[:limit]]


def check_value_ranges(mc: pd.DataFrame) -> None:
    """Probabilities must satisfy the DB CHECK (col BETWEEN 0 AND 1)."""
    errors: list[dict] = []
    for column in MC_PROBABILITY_COLUMNS:
        if column not in mc.columns:
            continue
        series = pd.to_numeric(mc[column], errors="coerce")
        bad = series[(series < 0) | (series > 1)]
        if not bad.empty:
            errors.append({
                "column": column,
                "out_of_range_rows": int(len(bad)),
                "example_value": float(bad.iloc[0]),
            })
    if errors:
        raise IngestValidationError(
            "Monte Carlo probability columns must lie in [0, 1]", errors
        )


def check_positions(weekly: pd.DataFrame) -> None:
    bad = sorted(set(weekly["Pos"].astype(str).str.upper()) - VALID_POSITIONS)
    if bad:
        raise IngestValidationError(
            f"unrecognised position value(s): {', '.join(bad)}",
            [{"column": "Pos", "allowed": sorted(VALID_POSITIONS), "found": bad}],
        )


# ------------------------------------------------------ identity resolution
@dataclass
class IdentityIndex:
    """name-key -> player id, and canonical-team-key -> team id."""

    players: Mapping[str, int]
    ambiguous_players: frozenset[str]
    teams: Mapping[str, int]

    @property
    def is_empty(self) -> bool:
        return not self.players or not self.teams


@dataclass
class ResolutionResult:
    resolved: pd.DataFrame
    unmatched: list[dict] = field(default_factory=list)


def build_identity_index(
    player_rows: Iterable[tuple[int, str | None, str | None, str]],
    team_rows: Iterable[tuple[int, str]],
) -> IdentityIndex:
    """Build the lookup from ``players`` / ``teams`` dimension rows.

    ``player_rows``: (id, first_name, second_name, web_name)
    ``team_rows``:   (id, name)

    Both the full name and the web name are indexed, mirroring how
    ``shot_profiles`` builds ``full_player_key`` with a ``web_player_key``
    fallback. A name that maps to more than one id is recorded as ambiguous
    and is *rejected* at resolution time rather than guessed at.
    """
    candidates: dict[str, set[int]] = {}
    for player_id, first, second, web in player_rows:
        keys = {
            _norm(f"{first or ''} {second or ''}"),
            _norm(web),
        }
        for key in keys:
            if key:
                candidates.setdefault(key, set()).add(int(player_id))

    players = {k: next(iter(v)) for k, v in candidates.items() if len(v) == 1}
    ambiguous = frozenset(k for k, v in candidates.items() if len(v) > 1)
    teams = {_canon_team(name): int(tid) for tid, name in team_rows}
    return IdentityIndex(players=players, ambiguous_players=ambiguous, teams=teams)


def split_player_key(player_key: str) -> tuple[str, str]:
    """``"bukayo saka|arsenal"`` -> ``("bukayo saka", "arsenal")``.

    Retained as a utility, but resolution deliberately does NOT go through it
    -- see :func:`resolve_identities`.
    """
    name, _, team = str(player_key).partition("|")
    return name.strip(), team.strip()


def resolve_identities(
    weekly: pd.DataFrame, index: IdentityIndex
) -> ResolutionResult:
    """Attach ``player_id`` / ``team_id``; collect every failure explicitly.

    Resolution reads the ``player`` and ``team`` columns rather than parsing
    ``player_key``. The export builds that key with a plain
    ``lower(player)|lower(team)`` -- verified 832/832 against real output --
    so it preserves accents and hyphens (``enzo fernández``,
    ``dominic calvert-lewin``) and cannot be compared against a
    ``_norm``-folded dimension table without losing ~22% of the squad.

    Canonicalising both sides from the raw columns instead is both lossless
    and more forgiving: ``_canon_team`` reconciles the export's full club
    names with FPL's abbreviations (``Spurs`` / ``Man City`` / ``Nott'm
    Forest``), which a literal key comparison would miss entirely.
    """
    unmatched: list[dict] = []
    player_ids: list[int | None] = []
    team_ids: list[int | None] = []

    # Resolve once per distinct player, then map back over the rows: the same
    # player appears once per gameweek, so this is ~4x less work and makes the
    # unmatched report one entry per player rather than per player-gameweek.
    pairs = weekly[["player", "team"]].astype(str)
    row_keys = list(zip(pairs["player"], pairs["team"]))
    per_key: dict[tuple[str, str], tuple[int | None, int | None]] = {}
    for key in dict.fromkeys(row_keys):
        raw_player, raw_team = key
        name_key = _norm(raw_player)
        team_key = _canon_team(raw_team)
        reason = None
        if name_key in index.ambiguous_players:
            reason = "player name matches more than one FPL id"
        pid = index.players.get(name_key)
        tid = index.teams.get(team_key)
        if reason is None and pid is None:
            reason = "no player in the season's squad matches this name"
        if reason is None and tid is None:
            reason = "no team in the season matches this club"
        if reason is not None:
            unmatched.append({
                "player": raw_player,
                "team": raw_team,
                "name_key": name_key,
                "team_key": team_key,
                "reason": reason,
            })
            per_key[key] = (None, None)
        else:
            per_key[key] = (pid, tid)

    for key in row_keys:
        pid, tid = per_key[key]
        player_ids.append(pid)
        team_ids.append(tid)

    resolved = weekly.copy()
    resolved["player_id"] = player_ids
    resolved["team_id"] = team_ids
    return ResolutionResult(resolved=resolved, unmatched=unmatched)


# --------------------------------------------------------------- fixtures
# The fixture grain comes from the model's INTERNAL frame export
# (`fixtures_forecast.csv`), not from the presentation exports the player
# grains use. That is not a preference -- the presentation family carries no
# fixture identity at all: `qc_team_week_fixture.csv` has a team name and a
# gameweek but no opponent, no home/away flag and no fixture id, so
# `fixtures` rows simply cannot be reconstructed from it.
FIXTURE_REQUIRED: tuple[str, ...] = ("id", "event", "team_h", "team_a")

#: CSV header -> fixture_forecasts column.
FIXTURE_FORECAST_MAP: dict[str, str] = {
    "home_xg": "home_goals_lambda",
    "away_xg": "away_goals_lambda",
    "home_cs_prob": "home_cs_prob",
    "away_cs_prob": "away_cs_prob",
    "projection_source": "projection_source",
}

#: DB CHECK constraints require these in [0, 1].
FIXTURE_PROBABILITY_COLUMNS: tuple[str, ...] = ("home_cs_prob", "away_cs_prob")

#: CSV header -> fixtures (dimension) column. Migration 0002 added these, and
#: the public ticker reads
#:     COALESCE(team_h_fdr_override, team_h_fdr_fpl)
#: so leaving team_h_fdr_fpl NULL is exactly what renders the ticker as empty
#: dashes. Populating it here is the fix.
FIXTURE_DIMENSION_MAP: dict[str, str] = {
    "team_h_difficulty": "team_h_fdr_fpl",
    "team_a_difficulty": "team_a_fdr_fpl",
    "finished": "finished",
}

#: Difficulty columns carry a CHECK (BETWEEN 1 AND 5).
FIXTURE_FDR_COLUMNS: tuple[str, ...] = ("team_h_difficulty", "team_a_difficulty")

#: Everything in the export that the schema can hold is now mapped. The
#: per-team admin FDR overrides are deliberately NOT ingested -- they are
#: human edits and re-ingesting a run must never silently revert them.
UNMAPPED_FIXTURE: tuple[str, ...] = ()


@dataclass(frozen=True)
class FixtureReport:
    row_count: int
    gameweeks: list[int]
    team_ids: list[int]


def check_fixture_frame(
    fixtures: pd.DataFrame, run_gameweeks: Iterable[int]
) -> FixtureReport:
    """Validate the fixture export and guard against stale-file mixing."""
    duplicated = int(fixtures.duplicated(subset=["id"]).sum())
    if duplicated:
        raise IngestValidationError(
            f"fixtures file has {duplicated} duplicate fixture id(s)"
        )

    for column in FIXTURE_PROBABILITY_COLUMNS:
        if column not in fixtures.columns:
            continue
        series = pd.to_numeric(fixtures[column], errors="coerce")
        bad = series[(series < 0) | (series > 1)]
        if not bad.empty:
            raise IngestValidationError(
                f"fixtures column {column!r} must lie in [0, 1]",
                [{"column": column, "out_of_range_rows": int(len(bad))}],
            )

    for column in FIXTURE_FDR_COLUMNS:
        if column not in fixtures.columns:
            continue
        series = pd.to_numeric(fixtures[column], errors="coerce").dropna()
        bad = series[(series < 1) | (series > 5)]
        if not bad.empty:
            raise IngestValidationError(
                f"fixtures column {column!r} must lie in 1..5 (FPL difficulty)",
                [{"column": column, "out_of_range_rows": int(len(bad)),
                  "example_value": float(bad.iloc[0])}],
            )

    gameweeks = sorted({int(g) for g in pd.to_numeric(fixtures["event"], errors="coerce").dropna()})
    if not gameweeks:
        raise IngestValidationError("fixtures file has no usable 'event' values")

    known = set(int(g) for g in run_gameweeks)
    stray = [g for g in gameweeks if g not in known]
    if stray:
        # Subset rather than equality: blanks and double gameweeks legitimately
        # make fixture coverage narrower than the player horizon. A gameweek
        # OUTSIDE the horizon, though, means a different model run.
        raise IngestValidationError(
            f"fixtures cover gameweek(s) {stray} outside the run horizon "
            f"{sorted(known)} -- the files are not from the same model run",
            [{"fixture_gameweeks": gameweeks, "run_gameweeks": sorted(known)}],
        )

    team_ids = sorted(
        {int(t) for t in pd.to_numeric(fixtures["team_h"], errors="coerce").dropna()}
        | {int(t) for t in pd.to_numeric(fixtures["team_a"], errors="coerce").dropna()}
    )
    return FixtureReport(
        row_count=int(len(fixtures)), gameweeks=gameweeks, team_ids=team_ids
    )


def unknown_fixture_teams(
    report: FixtureReport, known_team_ids: Iterable[int]
) -> list[int]:
    """Team ids referenced by fixtures but absent from the season's dimension."""
    known = {int(t) for t in known_team_ids}
    return [t for t in report.team_ids if t not in known]
