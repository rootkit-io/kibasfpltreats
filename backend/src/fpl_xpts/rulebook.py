"""The scoring Rulebook: FPL scoring rules as one frozen value (Candidate #2, Phase 1).

Before this module, the rules were scattered: point values in ``scoring.py``,
bonus proxy weights in ``bonus.py``, penalty constants duplicated in both
``xpts.py`` and ``monte_carlo.py`` (already drifted in type: ``-2`` vs
``-2.0``), Monte Carlo BPS weights inline in the simulator and hand-mirrored
in the replay script, and all era knowledge trapped inside
``scripts/retrospective_replay.py``.

Phase 1 consolidates every rule into one immutable value and moves era
knowledge into the library. It is a PURE STRUCTURAL refactor:

- ``scoring.py`` / ``bonus.py`` become thin reads of :data:`CURRENT_RULEBOOK`
  with unchanged signatures;
- ``xpts.py``, ``monte_carlo.py``, ``backtest.py``, and the replay's
  ``season_scoring_context`` monkeypatch are untouched (including the known
  era bug in ``backtest.py`` -- preserved deliberately until the rulebook is
  threaded through the engines in a later phase);
- drift tripwires in ``tests/test_rulebook.py`` pin the engines' duplicated
  constants to this module until those duplicates can be deleted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping


def _frozen(mapping: dict) -> Mapping:
    return MappingProxyType(dict(mapping))


@dataclass(frozen=True)
class Rulebook:
    """One season-era's FPL scoring rules, as an immutable value.

    Engines will take this as an explicit parameter (Phase 2); nothing may
    mutate it, monkeypatch around it, or read rule values from anywhere else.
    """

    era: str  # human label, e.g. "2025-26"

    # -- core point values (per position; missing position = 0/None)
    goal_points: Mapping[str, int]
    clean_sheet_points: Mapping[str, int]
    defcon_thresholds: Mapping[str, int]  # empty when DEFCON is inactive
    defcon_active: bool

    # -- appearance points
    appearance_full_minutes: int = 60
    appearance_points_short: float = 1.0
    appearance_points_full: float = 2.0

    # -- penalties (single source; previously duplicated in xpts/monte_carlo)
    penalty_xg_per_attempt: float = 0.79
    penalty_miss_points: float = -2.0
    max_team_penalty_xg: float = 0.16

    # -- expected-bonus proxy weights (previously bonus.py constants)
    bonus_per_goal: float = 0.85
    bonus_per_assist: float = 0.40
    bonus_cs_gk_def: float = 0.25
    bonus_per_save3: float = 0.20
    bonus_per_defcon: float = 0.15

    # -- Monte Carlo BPS proxy weights (inline in monte_carlo._simulate_fixture,
    #    mirrored by the replay's CURRENT_MC_GOAL_BPS; pinned here by tripwire)
    mc_goal_bps: Mapping[str, int] = field(
        default_factory=lambda: _frozen({"GK": 12, "DEF": 12, "MID": 18, "FWD": 24})
    )

    # -- era flags (consumed by the replay today; by the engines in Phase 2+)
    assist_rules_version: str = "2025_26_simplified_assists"
    bps_version: str = "2025_26_plus_penalty_goal_equalized"

    # ------------------------------------------------- rule lookups
    # Exact ports of the legacy scoring.py functions -- same fallbacks,
    # same types, same case-sensitivity.

    def goal_points_for(self, position: str) -> int:
        return self.goal_points.get(position, 0)

    def clean_sheet_points_for(self, position: str) -> int:
        return self.clean_sheet_points.get(position, 0)

    def defcon_threshold_for(self, position: str) -> int | None:
        if not self.defcon_active:
            return None
        return self.defcon_thresholds.get(position)

    def appearance_points_for(self, minutes: float) -> float:
        if minutes <= 0:
            return 0.0
        if minutes < self.appearance_full_minutes:
            return self.appearance_points_short
        return self.appearance_points_full


#: The live 2025-26 rules -- exactly the values previously hardcoded across
#: scoring.py, bonus.py, xpts.py, and monte_carlo.py.
CURRENT_RULEBOOK = Rulebook(
    era="2025-26",
    goal_points=_frozen({"GK": 10, "DEF": 6, "MID": 5, "FWD": 4}),
    clean_sheet_points=_frozen({"GK": 4, "DEF": 4, "MID": 1, "FWD": 0}),
    defcon_thresholds=_frozen({"DEF": 10, "MID": 12, "FWD": 12}),
    defcon_active=True,
)


# ---------------------------------------------------------------------------
# Era adapter: the port of scripts/retrospective_replay.get_season_scoring_config.
# ---------------------------------------------------------------------------

_OUTFIELD_GOAL_POINTS = {"DEF": 6, "MID": 5, "FWD": 4}


def _season_start_year(season: str | int) -> int:
    """Parse the season's start year from ``'2023-24'``-style labels (or ints)."""
    if isinstance(season, int):
        return season
    try:
        return int(str(season).split("-", maxsplit=1)[0])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unsupported season label: {season!r}") from exc


def rulebook_for_season(season: str | int) -> Rulebook:
    """Return the Rulebook that was in force at the start of a season.

    Faithful port of the replay script's ``get_season_scoring_config``:

    - GK goals: 6 points before 2024-25, 10 from 2024-25;
    - DEFCON: did not exist before the 2025-26 scoring refresh;
    - BPS/assist rule versions follow the 2024-25 and 2025-26 refreshes.

    Values not varied by the legacy era config (clean sheets, penalties,
    bonus proxy weights, MC BPS weights) carry the current values, exactly
    as the replay behaves today.
    """
    start_year = _season_start_year(season)
    gk_goal_points = 10 if start_year >= 2024 else 6
    defcon_active = start_year >= 2025
    if start_year >= 2025:
        bps_version = "2025_26_plus_penalty_goal_equalized"
        assist_rules_version = "2025_26_simplified_assists"
    elif start_year >= 2024:
        bps_version = "2024_25_updated_bps"
        assist_rules_version = "pre_2025_26_assists"
    else:
        bps_version = "pre_2024_25_bps"
        assist_rules_version = "pre_2025_26_assists"

    return Rulebook(
        era=f"{start_year}-{str(start_year + 1)[-2:]}",
        goal_points=_frozen({"GK": gk_goal_points, **_OUTFIELD_GOAL_POINTS}),
        clean_sheet_points=CURRENT_RULEBOOK.clean_sheet_points,
        defcon_thresholds=(
            CURRENT_RULEBOOK.defcon_thresholds if defcon_active else _frozen({})
        ),
        defcon_active=defcon_active,
        assist_rules_version=assist_rules_version,
        bps_version=bps_version,
    )
