/**
 * Planner xPts resolver and fixture data builder.
 *
 * Builds fast lookup indexes from the published projection rows so that
 * getXpts(elementId, gw) and buildFixtureData() are O(1) after setup.
 *
 * Why not use the weekly CSV? The published projection API already carries
 * xpts, expected_minutes, xg, xa per player per GW with official player_id.
 * The CSV is only needed for the scenario editor (Phase 6). Using the API
 * avoids a 9MB browser download for the baseline display.
 */

import type { ProjectionRow } from "@/lib/validations/projections";
import type { FdrFixture } from "@/lib/api/fixtures";
import type { FixtureData, FixtureCell } from "@/lib/planner/types";

// ── xPts index ────────────────────────────────────────────────────────────────

export interface XptsIndex {
  /** Keyed by `${player_id}:${gw}`. */
  byPlayerGw: Map<string, number>;
}

/**
 * Build an O(1) xPts lookup from projection rows.
 * Rows with null player_id or null xpts are silently dropped.
 */
export function buildXptsIndex(rows: ProjectionRow[]): XptsIndex {
  const byPlayerGw = new Map<string, number>();
  for (const row of rows) {
    if (typeof row.player_id !== "number" || typeof row.gameweek_id !== "number") continue;
    if (typeof row.xpts !== "number" || !Number.isFinite(row.xpts)) continue;
    byPlayerGw.set(`${row.player_id}:${row.gameweek_id}`, row.xpts);
  }
  return { byPlayerGw };
}

/**
 * Look up projected xPts for a player in a given GW.
 * Returns null when no projection exists.
 */
export function getXptsFromIndex(
  index: XptsIndex,
  playerId: number,
  gw: number,
): number | null {
  return index.byPlayerGw.get(`${playerId}:${gw}`) ?? null;
}

// ── Fixture data builder ──────────────────────────────────────────────────────

/**
 * Convert the FDR fixture list into the FixtureData shape used by the pitch.
 *
 * FixtureData[teamShortName][gw] = array of FixtureCell
 *
 * Each fixture produces two entries: one for the home team and one for away.
 * The opponent code is uppercase for home teams, lowercase for away
 * (matching the KFT2627 tickerDisplayKey convention).
 */
export function buildFixtureData(fixtures: FdrFixture[]): FixtureData {
  const data: FixtureData = {};

  function addEntry(
    teamKey: string,
    gw: number,
    cell: FixtureCell,
  ) {
    if (!teamKey || !gw) return;
    if (!data[teamKey]) data[teamKey] = {};
    if (!data[teamKey][gw]) data[teamKey][gw] = [];
    data[teamKey][gw].push(cell);
  }

  for (const fix of fixtures) {
    const gw = fix.gameweek;
    if (!gw) continue;

    const homeKey = fix.team_h_short_name ?? fix.team_h_name;
    const awayKey = fix.team_a_short_name ?? fix.team_a_name;
    if (!homeKey || !awayKey) continue;

    const homeFdr = fix.team_h_fdr ?? 3;
    const awayFdr = fix.team_a_fdr ?? 3;

    // Home team entry: opponent displayed uppercase
    addEntry(homeKey, gw, {
      d: homeFdr,
      o: awayKey.toUpperCase(),
      h: true,
      fixture_id: fix.fixture_id,
    });

    // Away team entry: opponent displayed lowercase
    addEntry(awayKey, gw, {
      d: awayFdr,
      o: homeKey.toLowerCase(),
      h: false,
      fixture_id: fix.fixture_id,
    });
  }

  return data;
}

// ── Gameweeks present in projection rows ──────────────────────────────────────

export function extractGameweeks(rows: ProjectionRow[]): number[] {
  const seen = new Set<number>();
  for (const row of rows) {
    if (typeof row.gameweek_id === "number") seen.add(row.gameweek_id);
  }
  return [...seen].sort((a, b) => a - b);
}
