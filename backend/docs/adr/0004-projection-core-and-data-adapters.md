# ADR-0004: One projection core, many data adapters

- **Status:** Accepted
- **Date:** 2026-07-27
- **Scope:** projection orchestration — `pipeline.run_projection_stages`,
  the `ProjectionInputs` contract, `run_live_projection` (live adapter),
  `scripts/retrospective_replay.py` (historical adapter), and any future
  consumer that wants a projection computed
- **Related:** ADR-0001 (Minutes Engine), ADR-0002 (persistence), ADR-0003
  (Rulebook DI) — the `ProjectionInputs` contract is where all three
  converge: it carries a `MinutesRunInputs`, a `Rulebook`, and produces what
  the repository persists.

## Context

The live pipeline and the historical replay computed "the same" projection
through two independent implementations:

1. **Duplicated orchestration.** `run_live_projection` and the replay's
   `_run_gw` each hand-sequenced forecast → minutes → recompute → aggregate
   → Monte Carlo (~435 lines of parallel pipeline concerns in a 1,163-line
   script), with the replay importing underscore-private helpers across
   module seams to rebuild acquisition.
2. **A parallel minutes implementation.** The replay predated the Minutes
   Engine and reassembled layer 2 from `minutes_model` primitives —
   bypassing the precedence stack and every contract test built in the
   minutes arc. Validation results were structurally unprotected against
   engine drift.
3. **Measured, silent divergence.** The Phase 2 shadow test (legacy vs core,
   side by side on real data) found a systematic bias the moment the paths
   were compared: the core's live feature builder, fed adapter-minimal
   history, predicted ~3.5–4.6 fewer expected minutes per player
   (xPts −0.10 to −0.15 mean, max −0.97), replicated across seasons. Neither
   path was "wrong" — they were *different*, and nothing would ever have
   said so.

## Decision

1. **One core.** `run_projection_stages(inputs, config, include_mc)` is the
   single implementation of the stage sequence: forecast (L1) → Minutes
   Engine (L2–5) → component recompute (L6) → gameweek aggregation → Monte
   Carlo → tail attach. It is pure with respect to acquisition: no network,
   no file system; era rules come from `inputs.rulebook`, minutes inputs
   from `inputs.minutes_inputs`, the model bundle pre-loaded.
2. **One contract.** `ProjectionInputs` (frozen dataclass, column schemas
   documented on the class) is the only way in: `players`, `teams`,
   `fixtures_forecast`, `history_by_player`, `rulebook`, `minutes_inputs`,
   `minutes_model_bundle`, and `minutes_features`.
3. **Adapters build the contract; that is all they do.** The live adapter
   (`run_live_projection`) gathers FPL API + Understat + odds + Elevenify
   data, resolves minutes inputs at the ADR-0001 boundary, seals a
   `ProjectionInputs`, calls the core, and keeps live-only concerns (ML
   attach, snapshot) on its side. The historical adapter
   (`build_historical_inputs`) maps vaastav/odds-archive frames to the same
   shape: era rulebook via `rulebook_for_season`, empty manual minutes (none
   existed historically), element-summary-shaped history, and point-in-time
   minutes features.
4. **The L2 feature seam.** `ProjectionInputs.minutes_features` lets an
   adapter supply pre-built minutes-model features when it holds higher
   fidelity than the live builder can recover (the replay's
   `build_historical_minutes_features` output: availability, standings,
   congestion). `None` means the engine builds features live. This seam is
   what took the measured drift to exactly 0.0000 before any legacy code was
   deleted.
5. **Measure before cutting.** The migration protocol — shadow the new path
   beside the old, quantify the diff, close the gap, require ~zero, then
   delete — is part of this decision, not incidental history. It caught a
   5-minute systematic bias that a blind swap would have shipped into every
   validation metric.

## Consequences

- **All future projection orchestration happens inside the core.** A new
  stage (or stage reorder) is a change to `run_projection_stages`, made once
  and inherited by every adapter. Re-sequencing stages inside an adapter or
  script is a regression.
- **Adapters gather data and map features — nothing else.** An adapter that
  starts computing points, resolving minutes precedence, or re-deriving
  model scores is rebuilding the pre-arc world. If an adapter needs engine
  behaviour the core doesn't expose, extend the contract, not the adapter.
- **New consumers are cheap by construction.** A season-preview simulator, a
  what-if endpoint, or a third data source is: build `ProjectionInputs`,
  call the core. The Admin API's in-memory route already works this way.
- **Replay-unique analysis stays adapter-side deliberately.** The replay's
  padded/ML-weighted Monte Carlo variants (bespoke seeds) and its actuals
  join are analysis, not orchestration; they consume shared engines
  (`simulate_player_week` + rulebook) without duplicating stages. The core
  is called with `include_mc=False` there — that asymmetry is intentional.
- **Known residue:** the historical adapter still imports underscore-private
  acquisition helpers from `historical_validation`/`market_odds`. That is
  acquisition-side debt (Candidate #4: named historical-source interface +
  validation core split), explicitly out of scope for this ADR.
- Future reviews should not re-propose: per-consumer stage sequencing,
  engine logic inside adapters, or "temporary" parallel implementations
  without a shadow-diff gate. Those shapes were deliberately eliminated
  here, with the cost of their absence measured at −0.971 xPts (rulebook
  arc) and −0.15 xPts/player (this arc) respectively.
