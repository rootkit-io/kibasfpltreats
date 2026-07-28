"""Legacy scoring interface: thin reads of the current Rulebook.

Since Candidate #2 Phase 1 the rule values live in ``rulebook.py``
(:data:`~fpl_xpts.rulebook.CURRENT_RULEBOOK`); these wrappers keep the
original signatures so ``xpts.py``, ``monte_carlo.py``, ``backtest.py``, and
the replay's ``season_scoring_context`` keep working unchanged until the
rulebook is threaded through the engines as an explicit parameter.
"""

from __future__ import annotations

from .rulebook import CURRENT_RULEBOOK

POSITION_BY_ELEMENT_TYPE = {
    1: "GK",
    2: "DEF",
    3: "MID",
    4: "FWD",
}


def goal_points(position: str) -> int:
    return CURRENT_RULEBOOK.goal_points_for(position)


def clean_sheet_points(position: str) -> int:
    return CURRENT_RULEBOOK.clean_sheet_points_for(position)


def defcon_threshold(position: str) -> int | None:
    return CURRENT_RULEBOOK.defcon_threshold_for(position)


def appearance_points(minutes: float) -> float:
    return CURRENT_RULEBOOK.appearance_points_for(minutes)
