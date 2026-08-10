/**
 * GW state derivation — pure function, no side effects.
 *
 * derivePlanStateForGw(gw, state) is the single source of truth for every
 * UI surface: pitch, bank bar, FT count, hits, chip badge, transfer list.
 * It replaces KFT2627's derivePlanStateForGw() with full TypeScript types.
 *
 * Invariants (verified by tests):
 * - FT entering GW N+1 is exactly what GW N settled to, capped at 5.
 * - Wildcard: transfers permanent + free, FTs preserved.
 * - Free Hit: squad temporary, bank temporary, real squad/FT unchanged after.
 * - Pre-GW1 (currentGw=0): unlimited transfers, no hits.
 * - ftOverrides[gw] replaces the rolling FT calculation entirely for that GW.
 * - Same-GW chains (A→B, B→C) are applied in planOrder sequence.
 */

import type {
  PlannerPick,
  TransferRecord,
  ChipCode,
  DerivedGwState,
} from "./types";
import {
  settleFreeTransferWeek,
  hasUnlimitedInitialTransfers,
  clampFreeTransfers,
} from "./freeTransfers";

// ── Minimal state shape the deriver needs ─────────────────────────────────────
// Kept narrow so derive.ts stays a pure domain module with no React dependency.

export interface DeriverInput {
  currentGw: number;
  origSquad: PlannerPick[];
  bank: number;
  freeTransfers: number;
  transfers: TransferRecord[];
  lineupPlan: Record<string, Record<string, number>>;
  captainPlan: Record<string, number>;
  viceCaptainPlan: Record<string, number>;
  chipPlan: Record<string | number, ChipCode>;
  chipHistory: Record<ChipCode, number[]>;
  currentActiveChip: ChipCode | null;
  ftOverrides: Record<string | number, number>;
  activeFreeHitGw: number | null;
  preFreeHitSquad: PlannerPick[] | null;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function deepCloneSquad(squad: PlannerPick[]): PlannerPick[] {
  return squad.map((p) => ({ ...p }));
}

function getChipForGw(
  gw: number,
  input: DeriverInput,
): ChipCode | null {
  const planned = input.chipPlan[gw] ?? input.chipPlan[String(gw)] ?? null;
  // Also count a live Free Hit that the API reports as active.
  if (!planned && input.activeFreeHitGw === gw) return "fh";
  return planned;
}

function getFtOverride(
  gw: number,
  input: DeriverInput,
): number | null {
  const v = input.ftOverrides[gw] ?? input.ftOverrides[String(gw)];
  if (v === undefined || v === null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function getTransfersForGw(
  gw: number,
  input: DeriverInput,
): TransferRecord[] {
  return input.transfers
    .filter((t) => t.gw === gw)
    .sort((a, b) => a.planOrder - b.planOrder);
}

/**
 * Apply a single transfer record to a squad (mutates the squad in place).
 * Returns true if the OUT player was found and swapped.
 */
function applyTransferToSquad(
  squad: PlannerPick[],
  tr: TransferRecord,
): boolean {
  const idx = squad.findIndex((p) => p.element === tr.outId);
  if (idx < 0) return false;
  squad[idx] = {
    ...squad[idx],
    element: tr.inId,
    purchasePrice: tr.inPrice,
    sellingPrice: tr.inPrice, // future sale price starts at purchase price
    multiplier: 1,
    isViceCaptain: false,
  };
  return true;
}

/**
 * Apply a lineup plan to a squad (mutates in place).
 * The lineupPlan maps element ID → desired position slot.
 */
function applyLineupPlan(
  squad: PlannerPick[],
  gw: number,
  input: DeriverInput,
): void {
  const plan = input.lineupPlan[gw] ?? input.lineupPlan[String(gw)];
  if (!plan) return;
  for (const pick of squad) {
    const desired = plan[pick.element] ?? plan[String(pick.element)];
    if (typeof desired === "number") pick.position = desired;
  }
}

/**
 * Enforce captain/vice-captain state on a derived squad.
 * Reads captainPlan and viceCaptainPlan; falls back to the highest-position
 * starter (position 1 = best) if no plan exists.
 */
function enforceCaptainState(
  squad: PlannerPick[],
  gw: number,
  chip: ChipCode | null,
  input: DeriverInput,
): void {
  // Clear existing captain/VC flags and reset multipliers.
  for (const p of squad) {
    p.multiplier = p.position <= 11 ? 1 : 0;
    p.isViceCaptain = false;
  }

  const capPlan = input.captainPlan[gw] ?? input.captainPlan[String(gw)];
  const vcPlan = input.viceCaptainPlan[gw] ?? input.viceCaptainPlan[String(gw)];
  const starters = squad.filter((p) => p.position <= 11);

  // Resolve captain.
  let capPick =
    capPlan != null ? starters.find((p) => p.element === capPlan) : null;
  if (!capPick && starters.length > 0) {
    // Fallback: first starter (position 1 is the best; lower = higher priority).
    capPick = starters.reduce((best, p) =>
      p.position < best.position ? p : best,
    );
  }
  if (capPick) {
    capPick.multiplier = chip === "tc" ? 3 : 2;
  }

  // Resolve vice-captain (must differ from captain).
  let vcPick =
    vcPlan != null ? starters.find((p) => p.element === vcPlan) : null;
  if (!vcPick && starters.length > 1 && capPick) {
    vcPick = starters
      .filter((p) => p !== capPick)
      .reduce((best, p) => (p.position < best.position ? p : best));
  }
  if (vcPick && vcPick !== capPick) {
    vcPick.isViceCaptain = true;
  }
}

// ── Main derivation ───────────────────────────────────────────────────────────

/**
 * Derive the planner state for a target GW by walking from currentGw+1.
 *
 * Pure: takes the DeriverInput snapshot, returns DerivedGwState.
 * Never mutates the input.
 */
export function derivePlanStateForGw(
  targetGw: number,
  input: DeriverInput,
): DerivedGwState {
  const gw = Number(targetGw);
  const currentGw = Number(input.currentGw ?? 0);
  const initialFt = clampFreeTransfers(input.freeTransfers ?? 1);

  if (!gw || !input.origSquad?.length) {
    return {
      squad: [],
      bank: Number(input.bank ?? 0),
      ft: initialFt,
      openingFt: initialFt,
      transfersMade: 0,
      chargeableTransfers: 0,
      nextFt: initialFt,
      hits: 0,
      chip: null,
      unlimited: hasUnlimitedInitialTransfers(gw, currentGw),
      ftOverride: false,
    };
  }

  // ── Important: start at currentGw+1, not currentGw.
  // S.freeTransfers already represents FTs ENTERING the first planning GW.
  // Starting at currentGw would add an extra +1, double-counting.
  const startGw = Math.min(currentGw + 1, gw);

  // Track the live Free Hit base squad (pre-FH permanent squad).
  const liveFhGw = input.activeFreeHitGw ? Number(input.activeFreeHitGw) : null;
  const liveFhBase =
    liveFhGw && Array.isArray(input.preFreeHitSquad) && input.preFreeHitSquad.length
      ? deepCloneSquad(input.preFreeHitSquad)
      : null;

  let squad = deepCloneSquad(input.origSquad);
  let bank = Number(input.bank ?? 0);
  let ft = initialFt;
  let hitsForTarget = 0;
  let chipForTarget: ChipCode | null = null;

  for (let g = startGw; g <= gw; g++) {
    // Apply any explicit FT override for this GW.
    const ftOverrideValue = getFtOverride(g, input);
    if (ftOverrideValue !== null) ft = ftOverrideValue;

    const chip = getChipForGw(g, input);
    const trs = getTransfersForGw(g, input);
    const isTargetGw = g === gw;

    if (isTargetGw) chipForTarget = chip;

    if (chip === "fh") {
      if (isTargetGw) {
        // Free Hit: work on a temporary clone of the permanent squad.
        const temp = deepCloneSquad(liveFhBase ?? squad);
        let tempBank = bank;
        for (const tr of trs) {
          if (applyTransferToSquad(temp, tr)) {
            tempBank += tr.outPrice - tr.inPrice;
          }
        }
        applyLineupPlan(temp, g, input);
        enforceCaptainState(temp, g, chip, input);
        return {
          squad: temp,
          bank: tempBank,
          ft,
          openingFt: ft,
          transfersMade: trs.length,
          chargeableTransfers: 0,
          nextFt: clampFreeTransfers(ft), // FH preserves FTs
          hits: 0,
          chip,
          unlimited: true,
          ftOverride: ftOverrideValue !== null,
        };
      }
      // Past FH before the target GW: ignore its transfers, keep real state.
      continue;
    }

    // Normal / Wildcard GW: transfers are permanent.
    let applied = 0;
    for (const tr of trs) {
      if (applyTransferToSquad(squad, tr)) {
        bank += tr.outPrice - tr.inPrice;
        applied++;
      }
    }

    applyLineupPlan(squad, g, input);
    enforceCaptainState(squad, g, chip, input);

    const openingUnlimited = hasUnlimitedInitialTransfers(g, currentGw);
    const settlement = settleFreeTransferWeek({
      openingFt: ft,
      transfersMade: applied,
      chip,
      openingUnlimited,
    });

    if (isTargetGw) {
      hitsForTarget = settlement.hits;
    } else {
      ft = settlement.nextFt;
    }
  }

  // Re-derive target GW's settlement values using the final ft balance.
  const targetTrs = getTransfersForGw(gw, input);
  const targetChip = chipForTarget;
  const targetUnlimited = hasUnlimitedInitialTransfers(gw, currentGw);
  const targetSettlement = settleFreeTransferWeek({
    openingFt: ft,
    transfersMade: targetTrs.length,
    chip: targetChip,
    openingUnlimited: targetUnlimited,
  });

  enforceCaptainState(squad, gw, targetChip, input);

  return {
    squad,
    bank,
    ft,
    openingFt: ft,
    transfersMade: targetTrs.length,
    chargeableTransfers: targetSettlement.chargeableTransfers,
    nextFt: targetSettlement.nextFt,
    hits: hitsForTarget,
    chip: targetChip,
    unlimited: targetUnlimited || targetChip === "wc" || targetChip === "fh",
    ftOverride: getFtOverride(gw, input) !== null,
  };
}

// ── Convenience helpers used by the planner UI ────────────────────────────────

/** Total projected xPts for a derived GW state. */
export function calcDisplayedGwTotal(
  state: DerivedGwState,
  getXpts: (elementId: number, gw: number) => number | null,
  gw: number,
): number {
  const pool =
    state.chip === "bb"
      ? state.squad
      : state.squad.filter((p) => p.position <= 11);

  let total = 0;
  for (const p of pool) {
    const xp = getXpts(p.element, gw) ?? 0;
    total += xp * (p.multiplier || 1);
  }
  total -= state.hits;
  return Math.round(total * 10) / 10;
}
