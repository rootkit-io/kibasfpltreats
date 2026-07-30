/**
 * Fetch-boundary types and helpers for the Monte Carlo read surface.
 *
 * Column names are the DATABASE's, not the model export's. The repository
 * renames on write, so the wire carries `mean_pts` / `floor_p10` /
 * `upside_p90` / `bracket_3_6` -- not `MC_MeanPts` / `MC_Floor` /
 * `Bracket_3_to_6`. Mixing the two vocabularies is the easiest way to end up
 * with a grid of em-dashes, so the types below track the DB spelling exactly.
 *
 * Everything is nullish: a published run may have been executed with
 * `include_mc = false`, or ingested from CSVs with no simulation grain, in
 * which case the endpoint returns an empty list rather than an error.
 */

import { z } from "zod";

const num = z.number().nullish();

export const simulationRowSchema = z
  .object({
    player_id: z.number(),
    gameweek_id: z.number(),
    web_name: z.string().nullish(),
    team_short: z.string().nullish(),
    n_sim: num,

    // distribution shape
    mean_pts: num,
    std_pts: num,
    min_pts: num,
    max_pts: num,
    floor_p10: num,
    p25: num,
    p75: num,
    upside_p90: num,

    // return probabilities
    p1_return: num,
    p2_return: num,
    p_return: num,
    p_haul: num,

    // outcome brackets (mutually exclusive, sum to ~1)
    bracket_le_2: num,
    bracket_3_6: num,
    bracket_7_9: num,
    bracket_10_14: num,
    bracket_15_plus: num,
  })
  .passthrough();

export type SimulationRow = z.infer<typeof simulationRowSchema>;

export const latestSimulationsSchema = z.object({
  run: z.object({ run_id: z.string() }).passthrough(),
  gameweek: z.number().nullish(),
  count: z.number(),
  simulations: z.array(z.unknown()),
});

export interface LatestSimulations {
  gameweek: number | null;
  rows: SimulationRow[];
}

/**
 * Parse an upstream payload. Row parsing is lossy-tolerant (one bad row can
 * never blank the view); a bad envelope throws, because that means the
 * contract changed.
 */
export function parseLatestSimulations(payload: unknown): LatestSimulations {
  const envelope = latestSimulationsSchema.parse(payload);
  const rows = envelope.simulations.flatMap((row) => {
    const parsed = simulationRowSchema.safeParse(row);
    return parsed.success ? [parsed.data] : [];
  });
  return { gameweek: envelope.gameweek ?? null, rows };
}

/** Simulation row plus the position/price the sims view does not carry. */
export interface EnrichedSimulation extends SimulationRow {
  position: string | null;
  price: number | null;
}

/**
 * Attach position and price from the projections payload.
 *
 * `published_player_week_simulations` joins players and teams but NOT
 * position -- it only reaches `player_gameweek_projections` to resolve
 * team_id. Rather than widen the view (a migration), the dashboard merges the
 * two payloads it already has on the (player_id, gameweek_id) grain.
 */
export function enrichWithPositions(
  simulations: SimulationRow[],
  projections: { player_id?: number | null; gameweek_id?: number | null; position?: string | null; price?: number | null }[],
): EnrichedSimulation[] {
  const lookup = new Map<string, { position: string | null; price: number | null }>();
  for (const row of projections) {
    if (typeof row.player_id !== "number" || typeof row.gameweek_id !== "number") continue;
    lookup.set(`${row.player_id}:${row.gameweek_id}`, {
      position: row.position ?? null,
      price: row.price ?? null,
    });
  }
  return simulations.map((sim) => {
    const extra = lookup.get(`${sim.player_id}:${sim.gameweek_id}`);
    return { ...sim, position: extra?.position ?? null, price: extra?.price ?? null };
  });
}

/**
 * Bracket palette: a single cold -> hot ramp so the bar reads as one
 * continuous distribution rather than five unrelated categories. The top
 * bracket lands on the `positive` token, tying "best outcome" to the same
 * green used for positive metrics everywhere else.
 *
 * NOTE these class names live in a .ts data file, so `lib/**` must stay in the
 * Tailwind `content` globs or none of them are compiled.
 */
export const BRACKETS = [
  { key: "bracket_le_2", label: "0–2", className: "bg-zinc-700" },
  { key: "bracket_3_6", label: "3–6", className: "bg-sky-700" },
  { key: "bracket_7_9", label: "7–9", className: "bg-teal-600" },
  { key: "bracket_10_14", label: "10–14", className: "bg-emerald-500" },
  { key: "bracket_15_plus", label: "15+", className: "bg-positive" },
] as const;

export type BracketKey = (typeof BRACKETS)[number]["key"];

/** True when at least one bracket carries a usable probability. */
export function hasBrackets(row: SimulationRow): boolean {
  return BRACKETS.some((b) => {
    const v = row[b.key];
    return typeof v === "number" && Number.isFinite(v);
  });
}

/** Distinct gameweeks present, ascending. */
export function simulationGameweeks(rows: SimulationRow[]): number[] {
  return [...new Set(rows.map((r) => r.gameweek_id))].sort((a, b) => a - b);
}
