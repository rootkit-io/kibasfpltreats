/**
 * Autofill — async beam-search squad builder.
 *
 * Constructs the best legal 15-player FPL squad from the player pool,
 * constrained by:
 *   - Exactly 2 GK, 5 DEF, 5 MID, 3 FWD
 *   - Total cost ≤ £100.0m (1000 in tenths)
 *   - Max 3 players from any one club
 *   - Players with status "u" excluded
 *
 * Algorithm: beam search over position slots with width 2400.
 * Yields to the browser every 6000 candidate checks via scheduler.yield()
 * or setTimeout(0), so the page stays responsive.
 *
 * Scores each player once over up to 5 GWs using the xPts index.
 * Returns the best squad or null if none found within budget.
 */

import type { FplPlayer } from "@/lib/planner/types";
import type { XptsIndex } from "@/lib/planner/xpts";
import { getXptsFromIndex } from "@/lib/planner/xpts";

// ── Types ─────────────────────────────────────────────────────────────────────

export interface AutofillOptions {
  players: FplPlayer[];
  xptsIndex: XptsIndex;
  gameweeks: number[];       // up to 5 GWs to score over
  budget: number;            // total budget in tenths of £m (default 1000)
  onProgress?: (step: string, pct: number) => void;
  signal?: AbortSignal;
}

export interface AutofillResult {
  squad: FplPlayer[];        // 15 players ordered GK,GK,DEF×5,MID×5,FWD×3
  totalCost: number;
}

// ── Scoring ───────────────────────────────────────────────────────────────────

/** Pre-compute a score for each player once (sum of xPts over available GWs). */
function buildScoreMap(
  players: FplPlayer[],
  xptsIndex: XptsIndex,
  gameweeks: number[],
): Map<number, number> {
  const gws = gameweeks.slice(0, 5);
  const map = new Map<number, number>();
  for (const p of players) {
    let total = 0;
    for (const gw of gws) {
      total += getXptsFromIndex(xptsIndex, p.id, gw) ?? 0;
    }
    map.set(p.id, total);
  }
  return map;
}

// ── Position slot order ───────────────────────────────────────────────────────

const SLOT_TYPES = [1, 1, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 4, 4, 4] as const;
// GK GK DEF DEF DEF DEF DEF MID MID MID MID MID FWD FWD FWD

// ── Beam state ────────────────────────────────────────────────────────────────

interface BeamState {
  players: FplPlayer[];
  cost: number;
  score: number;
  clubCounts: Record<number, number>;
}

const BEAM_WIDTH = 2400;
const YIELD_INTERVAL = 6_000;

async function yieldToBrowser(): Promise<void> {
  // Use the Scheduler API when available (Chrome 115+), fall back to setTimeout
  const sched = typeof globalThis !== "undefined"
    ? (globalThis as Record<string, unknown>).scheduler as { yield?: () => Promise<void> } | undefined
    : undefined;
  if (sched?.yield) {
    await sched.yield();
  } else {
    await new Promise<void>((r) => setTimeout(r, 0));
  }
}

// ── Main ──────────────────────────────────────────────────────────────────────

export async function autofill(options: AutofillOptions): Promise<AutofillResult | null> {
  const {
    players,
    xptsIndex,
    gameweeks,
    budget = 1000,
    onProgress,
    signal,
  } = options;

  onProgress?.("Scoring players…", 0);

  // Filter: correct status, positive cost
  const eligible = players.filter(
    (p) => p.status !== "u" && p.now_cost > 0,
  );

  const scoreMap = buildScoreMap(eligible, xptsIndex, gameweeks);

  // Build candidate pools per position type
  // Pool = top 50 by score + cheapest 18, deduplicated
  function poolForType(type: number): FplPlayer[] {
    const typed = eligible.filter((p) => p.element_type === type);
    const byScore = [...typed].sort((a, b) => (scoreMap.get(b.id) ?? 0) - (scoreMap.get(a.id) ?? 0));
    const byCost = [...typed].sort((a, b) => a.now_cost - b.now_cost);
    const top50 = new Set(byScore.slice(0, 50).map((p) => p.id));
    const cheapest18 = new Set(byCost.slice(0, 18).map((p) => p.id));
    const ids = new Set([...top50, ...cheapest18]);
    return typed.filter((p) => ids.has(p.id));
  }

  // Pre-compute minimum future cost: sum of cheapest remaining player per unfilled slot
  // This allows early pruning of over-budget beams.
  const pools: Record<number, FplPlayer[]> = {
    1: poolForType(1),
    2: poolForType(2),
    3: poolForType(3),
    4: poolForType(4),
  };

  // Min cost for each remaining slot after index `slotIdx`
  const minCostFromSlot: number[] = new Array(SLOT_TYPES.length + 1).fill(0);
  for (let i = SLOT_TYPES.length - 1; i >= 0; i--) {
    const type = SLOT_TYPES[i];
    const cheapest = Math.min(...pools[type].map((p) => p.now_cost));
    minCostFromSlot[i] = cheapest + minCostFromSlot[i + 1];
  }

  onProgress?.("Building squad…", 5);

  let beam: BeamState[] = [{ players: [], cost: 0, score: 0, clubCounts: {} }];
  let checkCount = 0;

  for (let slotIdx = 0; slotIdx < SLOT_TYPES.length; slotIdx++) {
    if (signal?.aborted) return null;

    const type = SLOT_TYPES[slotIdx];
    const pool = pools[type];
    const next: BeamState[] = [];
    const remainingMinCost = minCostFromSlot[slotIdx + 1];

    for (const state of beam) {
      for (const player of pool) {
        checkCount++;

        // Already in squad
        if (state.players.some((p) => p.id === player.id)) continue;

        // Club limit
        const clubCount = state.clubCounts[player.team] ?? 0;
        if (clubCount >= 3) continue;

        // Budget prune
        const newCost = state.cost + player.now_cost;
        if (newCost + remainingMinCost > budget) continue;

        next.push({
          players: [...state.players, player],
          cost: newCost,
          score: state.score + (scoreMap.get(player.id) ?? 0),
          clubCounts: { ...state.clubCounts, [player.team]: clubCount + 1 },
        });

        if (checkCount % YIELD_INTERVAL === 0) {
          await yieldToBrowser();
          if (signal?.aborted) return null;
        }
      }
    }

    if (next.length === 0) return null; // No valid squad found

    // Sort by score desc, keep best BEAM_WIDTH
    next.sort((a, b) => b.score - a.score);
    beam = next.slice(0, BEAM_WIDTH);

    const pct = 5 + Math.round(((slotIdx + 1) / SLOT_TYPES.length) * 90);
    onProgress?.(`Slot ${slotIdx + 1}/15…`, pct);
    await yieldToBrowser();
    if (signal?.aborted) return null;
  }

  onProgress?.("Finalising…", 97);

  // Pick the highest-scoring complete squad within budget
  const best = beam.find((s) => s.cost <= budget);
  if (!best) return null;

  onProgress?.("Done", 100);

  return {
    squad: best.players,
    totalCost: best.cost,
  };
}
