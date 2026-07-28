# ADR-0003: Immutable scoring Rulebook, injected — never global, never patched

- **Status:** Accepted
- **Date:** 2026-07-27
- **Scope:** FPL scoring rules everywhere they are consumed — `rulebook.py`,
  `scoring.py`, `bonus.py`, `xpts.py`, `monte_carlo.py`, `backtest.py`,
  `scripts/retrospective_replay.py`, and any future engine or historical
  script
- **Related:** ADR-0001 (Minutes Engine purity), ADR-0002 (CQRS-lite
  persistence) — this ADR applies the same value-not-state discipline to
  scoring rules.

## Context

FPL changes its scoring rules between seasons (GK goals 6 → 10 in 2024-25;
DEFCON introduced in 2025-26; BPS and assist-rule refreshes). The codebase
computes points in three engines (live xPts, Monte Carlo, backtest) and one
historical replay, so "which season's rules?" is a question every points
computation must answer. Before this arc it was answered badly:

1. **Global mutation.** The replay's `season_scoring_context` rebound
   `goal_points`/`defcon_threshold` *module globals* on `xpts` and
   `monte_carlo` to swap eras, restoring them afterwards. This was
   process-wide state: any code computing points concurrently (the Admin API
   server is a warm process) would silently use the wrong era. A test
   asserted the monkeypatch mechanism itself, making the workaround
   load-bearing.
2. **Silent era-drift from missed consumers.** Patching is per-consumer:
   every module that imports the rule functions must be individually
   rebound. `backtest.py` was the consumer nobody remembered — it replayed
   2022-25 seasons entirely on current rules. The shadow test
   (`scripts/quantify_gk_bug.py`) quantified the damage at **−0.971 xPts
   across all 2022-23 goalkeepers** (bounded because the backtest formula's
   only era-sensitive channel is GK goal points × GK xG); the *mechanism*
   allowed unbounded drift through any future consumer.
3. **Duplicated constants.** `PENALTY_XG_PER_ATTEMPT` / `PENALTY_MISS_POINTS`
   lived independently in `xpts.py` and `monte_carlo.py` (already drifted in
   type: `-2.0` vs `-2`); the Monte Carlo goal-BPS weights were inline in the
   simulator and hand-mirrored in the replay for reporting.

## Decision

1. **Rules are a value.** `Rulebook` (`rulebook.py`) is a deeply immutable
   value object — `@dataclass(frozen=True)` with every mapping wrapped in
   `MappingProxyType`, so both attribute reassignment and key mutation raise.
   It holds all point values, penalty constants, expected-bonus proxy
   weights, MC goal-BPS weights, and era flags
   (`assist_rules_version`, `bps_version`).
2. **Era knowledge lives in the library.** `rulebook_for_season(season)` is
   the single source of rule history (ported from the replay script and
   pinned by the `ERA_EXPECTATIONS` table in `tests/test_rulebook.py`).
   `CURRENT_RULEBOOK` is the live 2025-26 value;
   `rulebook_for_season("2025-26") == CURRENT_RULEBOOK` is tested.
3. **Injection, not lookup.** Every engine entry point takes
   `rulebook: Rulebook` in its signature and reads rules only from it:
   - live engines (`build_player_fixture_forecast`,
     `recompute_player_fixture_components`, `simulate_player_week`) default
     to `CURRENT_RULEBOOK`;
   - season-looping backtest functions (`run_holdout_backtest`,
     `sweep_form_weight`, `write_backtest_outputs`,
     `apply_production_formula_by_season`) default to `rulebook=None`,
     meaning **era-aware**: `rulebook_for_season(season)` is resolved per
     season group, because a single run legitimately spans rule eras
     (train 2022-24 straddles the GK boundary). An explicit rulebook forces
     one rule set (shadow-comparison use).
   - the replay resolves one rulebook per season and passes it to every
     engine call. `season_scoring_context` and all its helpers are deleted;
     `tests/test_retrospective_replay.py::test_monkeypatch_mechanism_is_dead`
     asserts the engines expose no patchable scoring globals.
4. **Legacy interface kept thin.** `scoring.py` and `bonus.py` survive as
   current-rules conveniences that read `CURRENT_RULEBOOK` with unchanged
   signatures; they contain no values of their own.

## Consequences

- **No global scoring constants in engine modules.** A new rule value goes
  into `Rulebook` as a field; engines receive it through their `rulebook`
  parameter. Re-introducing a module-level `PENALTY_*`-style constant in an
  engine is a regression (`test_engine_duplicated_constants_are_gone`).
- **No context managers or monkeypatching may mutate scoring rules** — in
  application code *or in tests*. Era behaviour is exercised by passing a
  different `Rulebook` value, which is concurrency-safe by construction.
- **Any new historical script must pass the season-aware rulebook
  explicitly** (`rulebook_for_season(season)` per season, or `None` defaults
  on the season-looping backtest helpers). "It defaults to current rules"
  is only acceptable for live-path code.
- **Rule changes are one-file diffs.** When FPL changes scoring for 2026-27:
  update `CURRENT_RULEBOOK` and extend `rulebook_for_season`'s era logic +
  the pinned `ERA_EXPECTATIONS` — engines, backtests, and the replay follow
  automatically.
- **Known residue, accepted for now:** several Monte Carlo BPS heuristics
  remain inline in the simulator (assist=9, CS=12, save=2, appearance 6/3,
  positional baselines). They are simulation calibration, not official FPL
  values, and were not part of the drift problem; promoting them to
  `Rulebook` fields is a future candidate, not a requirement. The backtest's
  own bonus-lambda weights (0.9/0.42/0.28) are its calibration, not
  duplicates — do not "unify" them into the Rulebook.
- Future reviews should not re-propose: patchable module-level rule
  functions, era handling inside scripts, or merging the era-aware `None`
  default with the live `CURRENT_RULEBOOK` default. These shapes were
  deliberately removed or separated here.
