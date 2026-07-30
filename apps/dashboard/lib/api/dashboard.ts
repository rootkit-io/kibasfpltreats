/**
 * Dashboard data layer: gameweek-range selection and aggregation.
 *
 * The published endpoint returns ONE ROW PER PLAYER-GAMEWEEK across the whole
 * run horizon (~832 players x 4-6 GWs). Two views are derived from that:
 *
 *   "range"  -- one row per player, metrics folded across a GW window
 *   "single" -- the raw player-gameweek rows for one GW (drilldown)
 *
 * Aggregation is pure and lives here rather than in the component so it can be
 * unit-tested against real payload shapes.
 *
 * Deliberately excludes the Monte Carlo columns: `published_player_week` is
 * `player_gameweek_projections` joined to its dimensions, and that table holds
 * no simulation columns at all, so every `mc_` and `bracket_` field is always
 * SQL NULL on this surface.
 */

import type { ProjectionRow } from "@/lib/validations/projections";

export type ViewMode = "range" | "single";
export type PositionFilter = "ALL" | "GK" | "DEF" | "MID" | "FWD";

export const POSITIONS: PositionFilter[] = ["ALL", "GK", "DEF", "MID", "FWD"];

/** One player folded across a gameweek window. */
export interface RangeRow {
  player_id: number;
  web_name: string;
  team_short: string | null;
  position: string | null;
  /** Price is time-varying; we surface the value at the END of the window. */
  price: number | null;
  selected_by_pct: number | null;
  gameweeks: number[];
  fixtures: number;
  expected_minutes: number | null;
  xg: number | null;
  xa: number | null;
  xpts: number | null;
  /** Mean xPts per gameweek in the window -- comparable across window sizes. */
  xpts_per_gw: number | null;
  /** P(at least one return across the window) -- see combineIndependent. */
  p_return: number | null;
  p_haul: number | null;
  /** Per-GW xPts, ordered by gameweek, for inline sparklines. */
  trajectory: { gameweek: number; xpts: number | null }[];
}

/** Distinct gameweeks present in the payload, ascending. */
export function availableGameweeks(rows: ProjectionRow[]): number[] {
  const seen = new Set<number>();
  for (const row of rows) {
    if (typeof row.gameweek_id === "number") seen.add(row.gameweek_id);
  }
  return [...seen].sort((a, b) => a - b);
}

/** Sum that stays null when every contributing value is null. */
function sumOrNull(values: (number | null | undefined)[]): number | null {
  let total = 0;
  let seen = false;
  for (const value of values) {
    if (typeof value === "number" && Number.isFinite(value)) {
      total += value;
      seen = true;
    }
  }
  return seen ? total : null;
}

/**
 * Combine per-gameweek probabilities into "at least once across the window".
 *
 * Treats gameweeks as independent: P(>=1) = 1 - PROD(1 - p_i). Summing would
 * be wrong (it can exceed 1) and averaging would understate a multi-week
 * horizon, so neither is a defensible "range" number.
 */
export function combineIndependent(
  values: (number | null | undefined)[],
): number | null {
  let complement = 1;
  let seen = false;
  for (const value of values) {
    if (typeof value === "number" && Number.isFinite(value)) {
      complement *= 1 - Math.min(Math.max(value, 0), 1);
      seen = true;
    }
  }
  return seen ? 1 - complement : null;
}

/** Restrict to an inclusive gameweek window. */
export function filterToWindow(
  rows: ProjectionRow[],
  from: number,
  to: number,
): ProjectionRow[] {
  const lo = Math.min(from, to);
  const hi = Math.max(from, to);
  return rows.filter(
    (row) =>
      typeof row.gameweek_id === "number" &&
      row.gameweek_id >= lo &&
      row.gameweek_id <= hi,
  );
}

/**
 * Fold player-gameweek rows into one row per player.
 *
 * Additive metrics (xPts, xG, xA, minutes, fixtures) sum. Probabilities
 * combine via `combineIndependent`. Identity/price are taken from the LAST
 * gameweek in the window, because price and club can both change mid-horizon
 * and the latest value is the one a manager acts on.
 */
export function aggregateRange(rows: ProjectionRow[]): RangeRow[] {
  const byPlayer = new Map<number, ProjectionRow[]>();
  for (const row of rows) {
    if (typeof row.player_id !== "number") continue;
    const bucket = byPlayer.get(row.player_id);
    if (bucket) bucket.push(row);
    else byPlayer.set(row.player_id, [row]);
  }

  const out: RangeRow[] = [];
  for (const [playerId, group] of byPlayer) {
    const ordered = [...group].sort(
      (a, b) => (a.gameweek_id ?? 0) - (b.gameweek_id ?? 0),
    );
    const latest = ordered[ordered.length - 1];
    const gameweeks = ordered
      .map((r) => r.gameweek_id)
      .filter((gw): gw is number => typeof gw === "number");

    const xpts = sumOrNull(ordered.map((r) => r.xpts));
    out.push({
      player_id: playerId,
      web_name: latest.web_name ?? "—",
      team_short: latest.team_short ?? null,
      position: latest.position ?? null,
      price: latest.price ?? null,
      // `selected_by_pct` is the fact-table column name on this view.
      selected_by_pct:
        (latest as unknown as { selected_by_pct?: number | null })
          .selected_by_pct ?? null,
      gameweeks,
      fixtures: sumOrNull(ordered.map((r) => r.fixtures_in_week)) ?? 0,
      expected_minutes: sumOrNull(ordered.map((r) => r.expected_minutes)),
      xg: sumOrNull(ordered.map((r) => r.xg)),
      xa: sumOrNull(ordered.map((r) => r.xa)),
      xpts,
      xpts_per_gw:
        xpts !== null && gameweeks.length > 0 ? xpts / gameweeks.length : null,
      p_return: combineIndependent(ordered.map((r) => r.p_return)),
      p_haul: combineIndependent(ordered.map((r) => r.p_haul)),
      trajectory: ordered.map((r) => ({
        gameweek: r.gameweek_id ?? 0,
        xpts: r.xpts ?? null,
      })),
    });
  }
  return out;
}

/** Case-insensitive match across the fields a manager would type. */
export function matchesQuery(
  row: { web_name: string; team_short: string | null; position: string | null },
  query: string,
): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return (
    row.web_name.toLowerCase().includes(q) ||
    (row.team_short ?? "").toLowerCase().includes(q) ||
    (row.position ?? "").toLowerCase().includes(q)
  );
}

export function matchesPosition(
  row: { position: string | null },
  filter: PositionFilter,
): boolean {
  return filter === "ALL" || (row.position ?? "").toUpperCase() === filter;
}
