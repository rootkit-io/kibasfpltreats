# ADR-0001: Isolate the Minutes Engine and push all I/O to the boundary

- **Status:** Accepted
- **Date:** 2026-07-27
- **Scope:** the Minutes module — `minutes_engine.py`, `minutes_contract.py`,
  `minutes.py`, `minutes_model.py`, `minute_overrides.py`, and the minutes
  stage of `pipeline.run_live_projection`

## Context

Minutes estimation is the highest-leverage input to xPts, and it is fed by a
**permanent weekly human workflow**: every gameweek, Manual Minutes Inputs
(hand-edited CSVs) and hard minute overrides are applied on top of the model,
and the resulting Weekly CSV Drop is uploaded to the Admin Panel by hand
(see `CONTEXT.md`).

Before this arc, that workflow ran through an architecture with three
recurring failure modes:

1. **Hardcoded and hidden file I/O.** The pipeline body contained the magic
   string `player_minutes_inputs_gw37_to_38.csv`; layer 5 auto-discovered
   `minute_overrides.csv` from the current working directory at call time.
   Which files influenced a run depended on where you ran it from.
2. **An untested precedence stack.** Six layers (heuristic baseline → trained
   model → manual CSV #1 → manual CSV #2 → overrides → component recompute)
   existed only as call order inside `run_live_projection`. The extracted
   pure functions were tested; the stacking — where the real bugs live — was
   not. Bad weekly edits were silently coerced (`errors="coerce"` →
   `fillna(0)` → `clip`) instead of rejected.
3. **A web constraint with no seam.** The planned Admin Panel sends JSON from
   a warm server process. The old shape offered no way in: inputs had to be
   CSV files on disk, the model bundle was loaded from disk on every run, and
   a snapshot file was written unconditionally.

## Decision

Deepen the Minutes module: one pure engine behind one validated boundary,
with all I/O in adapters at the edges.

1. **Pure engine** (`minutes_engine.py`). `resolve_minutes()` owns the
   precedence core (layers 2–5): trained model → manual input layers in
   order (later layers win) → hard overrides (pin `expected_minutes` only;
   probabilities keep their lower-layer values). The engine performs **no
   file, network, or global-state access** — the model bundle arrives
   pre-loaded, and all manual/override inputs arrive as validated contract
   states. Layer 1 (heuristic baseline) and layer 6 (component recompute)
   remain in `xpts.py`, outside the engine, called by the pipeline.
2. **Strict contracts at the boundary** (`minutes_contract.py`).
   `PlayerMinutesState` and `MinuteOverrideState` are Pydantic models that
   reject invalid rows loudly, with CSV line numbers, *before* a run starts.
   `resolve_minutes_run_inputs()` is the single dispatch point where a run's
   minutes inputs are resolved; it returns a frozen `MinutesRunInputs` value.
3. **I/O lives only in adapters.** Three real adapters feed the same seam:
   - **weekly CSVs** — legacy defaults and auto-discovery filenames survive
     as named constants resolved at the boundary (never inside the engine or
     pipeline), honouring `use_player_minutes_input_file=False` as "skip the
     defaults entirely";
   - **CLI paths** — `--manual-minutes` / `--minute-overrides` (repeatable,
     later files win), plumbed through `cli.py` and `legacy_export.py`;
   - **Admin Panel JSON** — `manual_minutes_states` / `minute_override_states`
     on `run_live_projection`, accepting contract states or raw dicts
     (coerced by the contract), with `minutes_model_bundle` (pre-loaded) and
     `write_snapshot=False` for a zero-file-I/O run.
   The state route and the path route are mutually exclusive per input;
   supplying both is an error, not a merge.

## Consequences

- **All future minutes logic must remain pure.** New precedence layers,
  matching rules, or minute-distribution changes go in `minutes_engine.py`
  (or modules it composes) and must not read files, hit the network, or
  mutate global state. `tests/test_minutes_engine.py` enforces this with a
  purity guard (`pd.read_csv` tripwired); `tests/test_pipeline_api.py`
  tripwires the full pipeline path. A change that needs new data must extend
  the contracts and adapters, not reach around them.
- **The web server integration is fixed by design:** load the model bundle
  once at startup, JSON-coerce request payloads through the contracts, call
  `run_live_projection(..., manual_minutes_states=..., minutes_model_bundle=...,
  write_snapshot=False)`. No disk I/O on the request path.
- **Bad weekly edits fail loudly before the run** (line-numbered contract
  errors) instead of being silently coerced mid-run. This is intentional and
  should not be "fixed" back to silent coercion.
- **Precedence semantics are contract:** later manual layers beat earlier
  ones; overrides pin `expected_minutes` only. The table-driven ladder in
  `tests/test_minutes_engine.py` is the executable specification.
- **Legacy filenames are compatibility, not architecture.** The gw37–38
  fallback and override auto-discovery names exist as constants at the
  boundary for the current weekly workflow; retiring them is a boundary-only
  change once the Admin Panel route is live.
- Future architecture reviews should not re-propose moving minutes I/O into
  the engine "for convenience," merging the state/path routes, or having the
  pipeline resolve paths itself — those shapes were deliberately removed.
