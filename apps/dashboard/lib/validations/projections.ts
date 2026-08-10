/**
 * Fetch-boundary schemas for the public projections read path (Phase 12).
 *
 * DELIBERATE DUPLICATION: this mirrors the admin app's projection row
 * schema but is NOT shared with it. Two reasons, one structural and one
 * strategic:
 *
 *  1. The wire shapes genuinely differ. The admin grid parses pandas
 *     `to_json` output (`xPts`, `event`, `MC_MeanPts`); this parses rows
 *     from the `published_player_week` SQL view, where unquoted identifiers
 *     are folded to lower case (`xpts`, `gameweek_id`, `mc_meanpts`).
 *  2. Apps stay independently deployable until a shared `packages/` layer
 *     is justified by real reuse (see Phase 12 directive 2).
 *
 * Everything is `.nullish()` because SQL NULL is legitimate for unplayed
 * fixtures and for simulation columns when a run was executed with
 * `include_mc = false`.
 */

import { z } from "zod";

/** SQL NULL -> null; ids/metrics arrive as JSON numbers. */
const num = z.number().nullish();

export const projectionRowSchema = z
  .object({
    // identity (from the view's joins on players/teams/gameweeks)
    player_id: num,
    gameweek_id: num,
    web_name: z.string().nullish(),
    team_short: z.string().nullish(),
    team_name: z.string().nullish(),
    position: z.string().nullish(),
    price: num,

    // projection layer (view emits fact-table column names)
    fixtures_in_week: num,
    expected_minutes: num,
    xg: num,
    xa: num,
    xpts: num,
    defcon90: num,
    p_return: num,
    p_haul: num,

    // Monte Carlo layer (null when include_mc = false)
    mc_meanpts: num,
    mc_stdpts: num,
    mc_floor: num,
    mc_upside: num,
    bracket_15_plus: num,
  })
  .passthrough();

export type ProjectionRow = z.infer<typeof projectionRowSchema>;

/** Run header the public surface exposes (no admin provenance/notes). */
export const publishedRunSchema = z
  .object({
    run_id: z.string(),
    season: z.string(),
    gw_start: z.number().nullish(),
    gw_end: z.number().nullish(),
    n_sim: z.number().nullish(),
    include_mc: z.boolean().nullish(),
    published_at: z.string().nullish(),
  })
  .passthrough();

export type PublishedRun = z.infer<typeof publishedRunSchema>;

export const latestProjectionsSchema = z.object({
  run: publishedRunSchema,
  gameweek: z.number().nullish(),
  count: z.number(),
  player_week: z.array(z.unknown()),
});

export type LatestProjections = {
  run: PublishedRun;
  gameweek: number | null;
  rows: ProjectionRow[];
};

/**
 * Parse an upstream payload into grid-ready data.
 *
 * Row-level parsing is per-row and lossy-tolerant (`flatMap` drops
 * unparseable rows) so one malformed row can never blank the whole table --
 * same posture as the admin grid. A bad *envelope*, by contrast, throws:
 * that means the contract changed and the page should show an error.
 */
export function parseLatestProjections(payload: unknown): LatestProjections {
  const envelope = latestProjectionsSchema.parse(payload);
  const rows = envelope.player_week.flatMap((row) => {
    const parsed = projectionRowSchema.safeParse(row);
    return parsed.success ? [parsed.data] : [];
  });
  return {
    run: envelope.run,
    gameweek: envelope.gameweek ?? null,
    rows,
  };
}

/** Season code "2627" -> "2026/27" (dashboard-local copy, see above). */
export function seasonLabel(season: string): string {
  if (!/^\d{4}$/.test(season)) return season;
  return `20${season.slice(0, 2)}/${season.slice(2)}`;
}
