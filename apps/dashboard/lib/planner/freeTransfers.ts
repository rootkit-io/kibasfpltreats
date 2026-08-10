/**
 * Free transfer accounting — pure functions, no side effects.
 *
 * Ported from KFT2627/planner.html with TypeScript types added.
 * These are the most correctness-critical functions in the planner:
 * every bank / hits / FT display derives from them.
 *
 * Key rules (2026/27):
 * - Free transfers accumulate: 1 per GW, banked up to 5.
 * - Extra transfers beyond the free allowance cost 4 pts each (hits).
 * - Wildcard/Free Hit: transfers are free, FTs are preserved.
 * - Pre-GW1 / unlimited: all transfers free, FTs unaffected.
 * - An explicit ftOverride replaces the rolling calculation entirely.
 */

import type { ChipCode, FtWeekInput, FtWeekResult } from "./types";

export const FREE_TRANSFER_CAP = 5;

// ── Helpers ───────────────────────────────────────────────────────────────────

export function clampFreeTransfers(value: number): number {
  const n = Number(value);
  // Non-finite but valid numbers (Infinity, -Infinity) clamp to the range.
  // Only NaN falls back to 0 before clamping.
  const safe = Number.isNaN(n) ? 0 : n;
  return Math.min(FREE_TRANSFER_CAP, Math.max(0, Math.round(safe)));
}

/**
 * Whether the initial squad selection for this GW is unlimited.
 * Pre-GW1 (gw <= 1 and currentGw === 0) counts as unlimited.
 * This mirrors hasUnlimitedInitialTransfers() in the KFT2627 planner.
 */
export function hasUnlimitedInitialTransfers(gw: number, currentGw: number): boolean {
  return gw <= 1 && currentGw === 0;
}

// ── Core settlement ───────────────────────────────────────────────────────────

/**
 * Settle one GW's free transfer accounting.
 *
 * Given the opening FTs, how many transfers were made, and whether a chip
 * was active, returns: how many were chargeable, how many hits that is,
 * and how many FTs roll into the next GW.
 *
 * Wildcard and Free Hit make all transfers free. Free Hit also has no
 * effect on the rolling FT balance. Bench Boost and Triple Captain leave
 * transfer accounting unchanged.
 */
export function settleFreeTransferWeek(input: FtWeekInput): FtWeekResult {
  const opening = clampFreeTransfers(input.openingFt);
  const made = Math.max(0, Math.round(Number(input.transfersMade) || 0));
  const { chip, openingUnlimited } = input;

  const chipUnlimited = chip === "wc" || chip === "fh";
  const unlimited = openingUnlimited || chipUnlimited;

  if (unlimited) {
    // Free Hit: FTs are unaffected — they carry to next GW unchanged.
    // Wildcard: same; FTs preserved.
    return {
      hits: 0,
      chargeableTransfers: 0,
      nextFt: chip === "fh"
        ? clampFreeTransfers(opening)          // FH: preserve exact count
        : clampFreeTransfers(opening + 1),     // WC: still earns +1 next GW
    };
  }

  const chargeable = Math.max(0, made - opening);
  const hits = chargeable * 4;

  // Roll forward: (opening - used) + 1 next GW, capped at FREE_TRANSFER_CAP.
  const used = Math.min(made, opening);
  const nextFt = clampFreeTransfers(opening - used + 1);

  return { hits, chargeableTransfers: chargeable, nextFt };
}

// ── Multi-GW reconstruction ───────────────────────────────────────────────────

/**
 * Walk completed GWs to calculate how many free transfers are available
 * entering the first planning GW.
 *
 * Key insight from KFT2627: GW1 is always treated as unlimited (pre-deadline
 * squad changes). Every manager therefore enters GW2 with exactly 1 FT.
 * The walk starts at GW2, not GW1.
 *
 * @param currentGw   Last locked/completed GW (0 = pre-season)
 * @param txByGw      Map of GW -> number of transfers made that GW
 * @param chipHistory Map of chip code -> list of GWs used
 * @param currentActiveChip  Chip currently active (may not be in history yet)
 */
export function calculateFreeTransfersEnteringPlanningGw(
  currentGw: number,
  txByGw: Record<number, number>,
  chipHistory: Record<ChipCode, number[]>,
  currentActiveChip: ChipCode | null,
): number {
  const completedGw = Math.max(0, Math.round(Number(currentGw) || 0));

  // Pre-season or GW1 just completed: every manager enters GW2 with 1 FT.
  // GW1 squad changes are unlimited so they never affect the rolling balance.
  if (completedGw < 2) return 1;

  // Start with 1 FT entering GW2 (the first GW where normal accounting applies).
  let ft = 1;

  for (let gw = 2; gw <= completedGw; gw++) {
    const isWC =
      (chipHistory.wc ?? []).includes(gw) ||
      (gw === completedGw && currentActiveChip === "wc");
    const isFH =
      (chipHistory.fh ?? []).includes(gw) ||
      (gw === completedGw && currentActiveChip === "fh");

    const chip: ChipCode | null = isWC ? "wc" : isFH ? "fh" : null;
    const made = Number(txByGw[gw] ?? 0);

    const result = settleFreeTransferWeek({
      openingFt: ft,
      transfersMade: made,
      chip,
      openingUnlimited: false, // GW1 unlimited never applies from GW2 onward
    });

    ft = result.nextFt;
  }

  return ft;
}
