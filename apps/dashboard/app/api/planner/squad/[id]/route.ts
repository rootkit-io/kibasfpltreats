/**
 * BFF: GET /api/planner/squad/[id]
 *
 * Aggregates everything the planner needs from the official FPL API into a
 * single PlannerBootstrap payload. All FPL requests run in parallel; the
 * locked-GW picks fetch is sequential only because it depends on knowing
 * the current GW first.
 *
 * Auth: Clerk session required (middleware.ts protects all non-api routes,
 * but we also check here so the API route itself is not an open proxy).
 *
 * Returned shape: PlannerBootstrap (lib/planner/types.ts)
 */

import { auth } from "@clerk/nextjs/server";
import { NextRequest, NextResponse } from "next/server";

import {
  calculateFreeTransfersEnteringPlanningGw,
} from "@/lib/planner/freeTransfers";
import type {
  ChipCode,
  FplBootstrap,
  FplPick,
  FplTransferRecord,
  PlannerBootstrap,
} from "@/lib/planner/types";

// ── Constants ─────────────────────────────────────────────────────────────────

const FPL = "https://fantasy.premierleague.com/api";
const UA = "KibasFPLTreats/1.0 (https://kibasfpltreats.com)";
const HEADERS = { "User-Agent": UA };
const MAX_ID = 100_000_000;
const TIMEOUT_MS = 12_000;

// ── Helpers ───────────────────────────────────────────────────────────────────

function err(msg: string, status: number) {
  return NextResponse.json({ error: msg }, { status });
}

async function fplFetch<T>(path: string): Promise<T> {
  const r = await fetch(`${FPL}${path}`, {
    headers: HEADERS,
    cache: "no-store",
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  if (!r.ok) throw new Error(`FPL ${r.status} ${path}`);
  return r.json() as Promise<T>;
}

/**
 * Map FPL's chip name strings to our 4-code union.
 * FPL uses several spellings across history / live endpoints.
 */
const CHIP_MAP: Record<string, ChipCode> = {
  wildcard: "wc",
  freehit: "fh",
  free_hit: "fh",
  bboost: "bb",
  bench_boost: "bb",
  "3xc": "tc",
  triplecaptain: "tc",
  triple_captain: "tc",
  // 2026/27 new chip
  assistant_manager: "tc", // maps to TC slot if it behaves like TC
};

function mapChipName(raw: string): ChipCode | null {
  return CHIP_MAP[String(raw ?? "").toLowerCase()] ?? null;
}

/**
 * Derive currentGw from bootstrap events.
 * Returns the id of the most recently started (or just-finished) event,
 * falling back to 0 (pre-season) if no events have started yet.
 */
function deriveCurrentGw(
  events: FplBootstrap["events"],
): number {
  const current = events.find((e) => e.is_current);
  if (current) return current.id;
  const next = events.find((e) => e.is_next);
  if (next) return Math.max(0, next.id - 1);
  return 0;
}

/**
 * Derive planningStartGw: the next GW that hasn't been locked yet.
 * If currentGw is 0 (pre-season), that's GW1.
 */
function derivePlanningStartGw(
  events: FplBootstrap["events"],
  currentGw: number,
): number {
  const next = events.find((e) => e.is_next);
  if (next) return next.id;
  return Math.max(1, currentGw + 1);
}

/**
 * Build a GW → deadline-ms map from bootstrap events.
 * Only includes future events with a deadline_time.
 */
function buildGwDeadlines(
  events: FplBootstrap["events"],
): Record<number, number> {
  const map: Record<number, number> = {};
  for (const ev of events) {
    if (ev.deadline_time) {
      map[ev.id] = new Date(ev.deadline_time).getTime();
    }
  }
  return map;
}

/**
 * Reconstruct purchase and selling prices for each pick.
 *
 * Precedence (matches KFT2627):
 * 1. Most recent transfer-in cost for this player
 * 2. pick.purchase_price from the FPL API (available from GW2 onward)
 * 3. Current price (now_cost from bootstrap)
 */
function reconstructPrices(
  picks: FplPick[],
  transfers: FplTransferRecord[],
  playerMap: Map<number, { now_cost: number }>,
): FplPick[] {
  // Build a map: element → most recent purchase price from transfers
  const purchaseFromTransfers = new Map<number, number>();
  // transfers come newest-first from the API; iterate once to capture most recent
  for (const t of transfers) {
    if (!purchaseFromTransfers.has(t.element_in)) {
      purchaseFromTransfers.set(t.element_in, t.element_in_cost);
    }
  }

  return picks.map((pick) => {
    const nowCost = playerMap.get(pick.element)?.now_cost ?? pick.selling_price ?? 0;
    const purchase =
      purchaseFromTransfers.get(pick.element) ??
      pick.purchase_price ??
      nowCost;

    // FPL selling price formula: if current < purchase → sell at current;
    // otherwise purchase + floor((current - purchase) / 2)
    const selling = nowCost < purchase
      ? nowCost
      : purchase + Math.floor((nowCost - purchase) / 2);

    return {
      ...pick,
      purchase_price: purchase,
      selling_price: selling,
    };
  });
}

// ── Route handler ─────────────────────────────────────────────────────────────

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const { userId } = await auth();
  if (!userId) return err("unauthorized", 401);

  const { id } = await params;
  const managerId = parseInt(id, 10);
  if (!Number.isFinite(managerId) || managerId < 1 || managerId > MAX_ID) {
    return err("invalid manager id", 400);
  }

  try {
    // ── Phase 1: parallel fetches that don't depend on each other ────────────
    const [bootstrap, historyData, transfers] = await Promise.all([
      fplFetch<FplBootstrap>("/bootstrap-static/"),
      fplFetch<{
        current: Array<{ event: number; event_transfers: number }>;
        chips: Array<{ name: string; event: number }>;
      }>(`/entry/${managerId}/history/`),
      fplFetch<FplTransferRecord[]>(`/entry/${managerId}/transfers/`),
    ]);

    const currentGw = deriveCurrentGw(bootstrap.events);
    const planningStartGw = derivePlanningStartGw(bootstrap.events, currentGw);

    // ── Phase 2: fetch locked picks (depends on currentGw) ───────────────────
    let picks: FplPick[] = [];
    let bankFromApi = 1000; // £100.0m default (pre-season, full budget)
    let currentActiveChip: ChipCode | null = null;
    let activeFreeHitGw: number | null = null;
    let preFreeHitPicks: FplPick[] | null = null;

    if (currentGw > 0) {
      const picksData = await fplFetch<{
        picks: FplPick[];
        entry_history: {
          bank: number;
          event_transfers: number;
          event_transfers_cost: number;
        };
        active_chip: string | null;
      }>(`/entry/${managerId}/event/${currentGw}/picks/`);

      picks = picksData.picks ?? [];
      bankFromApi = picksData.entry_history?.bank ?? 0;

      const rawChip = picksData.active_chip;
      currentActiveChip = rawChip ? mapChipName(rawChip) : null;

      // If active chip is Free Hit, the current API squad is temporary.
      // We need the pre-FH squad so planning continues from the permanent squad.
      if (currentActiveChip === "fh" && currentGw > 1) {
        activeFreeHitGw = currentGw;
        try {
          const preFhData = await fplFetch<{ picks: FplPick[] }>(
            `/entry/${managerId}/event/${currentGw - 1}/picks/`,
          );
          preFreeHitPicks = preFhData.picks ?? null;
        } catch {
          // Non-fatal: planner will warn but can still function with current picks
          preFreeHitPicks = null;
        }
      }
    }

    // ── Chip history ─────────────────────────────────────────────────────────
    const chipHistory: Record<ChipCode, number[]> = { wc: [], fh: [], bb: [], tc: [] };
    for (const chip of historyData.chips ?? []) {
      const code = mapChipName(chip.name);
      if (code) chipHistory[code].push(chip.event);
    }

    // ── Free transfer reconstruction ─────────────────────────────────────────
    // Build txByGw from official transfer history (transfers newest-first)
    const txByGw: Record<number, number> = {};
    for (const gw of historyData.current ?? []) {
      txByGw[gw.event] = gw.event_transfers;
    }

    const freeTransfers = calculateFreeTransfersEnteringPlanningGw(
      currentGw,
      txByGw,
      chipHistory,
      currentActiveChip,
    );

    // ── Reconstruct purchase / selling prices ─────────────────────────────────
    const playerMap = new Map(
      bootstrap.elements.map((p) => [p.id, { now_cost: p.now_cost }]),
    );

    // transfers come newest-first from the FPL API — keep that order for
    // reconstructPrices() which captures the most recent purchase price first
    const enrichedPicks = reconstructPrices(picks, transfers, playerMap);
    const enrichedPreFhPicks = preFreeHitPicks
      ? reconstructPrices(preFreeHitPicks, transfers, playerMap)
      : null;

    // ── Assemble response ─────────────────────────────────────────────────────
    const payload: PlannerBootstrap = {
      bootstrap,
      picks: enrichedPicks,
      currentGw,
      planningStartGw,
      gwDeadlines: buildGwDeadlines(bootstrap.events),
      bank: bankFromApi,
      freeTransfers,
      chipHistory,
      currentActiveChip,
      activeFreeHitGw,
      preFreeHitPicks: enrichedPreFhPicks,
      transfers, // raw, for the profile transfer history panel
    };

    return NextResponse.json(payload, {
      headers: {
        // Short cache: squad state can change between requests (transfers, deadlines)
        "Cache-Control": "private, max-age=30, stale-while-revalidate=60",
      },
    });
  } catch (e) {
    const timedOut = e instanceof Error && e.name === "TimeoutError";
    return err(
      timedOut ? "FPL API timed out" : "FPL API unavailable",
      timedOut ? 504 : 502,
    );
  }
}
