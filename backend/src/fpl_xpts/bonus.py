"""Expected-bonus proxy: weights from the current Rulebook, math kept here.

Since Candidate #2 Phase 1 the weight values live in ``rulebook.py``; the
module-level constant names are preserved because ``xpts.py`` and the replay
script import them directly. ``expected_capped_poisson`` is pure math, not a
rule, and stays as-is.
"""

from __future__ import annotations

import math

from .rulebook import CURRENT_RULEBOOK

BONUS_PER_GOAL = CURRENT_RULEBOOK.bonus_per_goal
BONUS_PER_ASSIST = CURRENT_RULEBOOK.bonus_per_assist
BONUS_CS_GK_DEF = CURRENT_RULEBOOK.bonus_cs_gk_def
BONUS_PER_SAVE3 = CURRENT_RULEBOOK.bonus_per_save3
BONUS_PER_DEFCON = CURRENT_RULEBOOK.bonus_per_defcon


def expected_capped_poisson(lam: float, cap: int = 3) -> float:
    """Return E[min(Poisson(lam), cap)] using the tail-sum identity."""
    lam = max(float(lam), 0.0)
    if lam <= 0.0 or cap <= 0:
        return 0.0

    total = 0.0
    pmf = math.exp(-lam)
    cdf = pmf
    for k in range(1, cap + 1):
        if k > 1:
            pmf *= lam / (k - 1)
            cdf += pmf
        total += 1.0 - cdf
    return min(float(cap), max(0.0, total))
