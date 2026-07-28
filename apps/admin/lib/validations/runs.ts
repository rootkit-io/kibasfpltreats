/**
 * Zod mirrors of the backend's run-history responses (Phase 10):
 * GET /api/v1/admin/projections/runs        -> { runs: RunSummary[] }
 * GET /api/v1/admin/projections/runs/{id}   -> RunSummary + { tables }
 *
 * Same ground rule as the minutes contracts: the SERVER shape is the source
 * of truth; these schemas validate at the fetch boundary so the UI never
 * renders blind casts of upstream JSON.
 */

import { z } from "zod";

export const runStatusSchema = z.enum(["draft", "published", "archived"]);
export type RunStatus = z.infer<typeof runStatusSchema>;

/** One row of the history list -- `_run_summary` on the backend. */
export const runSummarySchema = z
  .object({
    run_id: z.string().min(1),
    season: z.string(),
    gameweek: z.string().nullish(), // "1" or "1-38"; null when unscoped
    gw_start: z.number().int().nullish(),
    gw_end: z.number().int().nullish(),
    status: runStatusSchema,
    created_at: z.string(),
    published_at: z.string().nullish(),
    source: z.string().nullish(),
    n_sim: z.number().int().nullish(),
    include_mc: z.boolean().nullish(),
    minutes_model_loaded: z.boolean().nullish(),
    manual_minutes_layers: z.number().int().nullish(),
    override_count: z.number().int().nullish(),
    notes: z.string().nullish(),
  })
  .passthrough();

export type RunSummary = z.infer<typeof runSummarySchema>;

export const runListResponseSchema = z.object({
  runs: z.array(runSummarySchema),
});

/** Single-run detail: summary + the persisted preview tables (records
 * format, identical to the run endpoint's `tables`). */
export const runDetailResponseSchema = runSummarySchema.extend({
  tables: z.record(z.array(z.record(z.unknown()))),
});

export type RunDetail = z.infer<typeof runDetailResponseSchema>;
