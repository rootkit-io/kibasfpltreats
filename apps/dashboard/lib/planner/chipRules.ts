/**
 * Chip rules — pure validation, no side effects.
 *
 * 2026/27 rules:
 * - WC, FH, BB, TC each available ONCE per season half (GW1-19 / GW20-38).
 * - Only one chip may be used per GW.
 * - Free Hit cannot be played in GW1.
 * - Free Hit cannot be played in consecutive GWs.
 * - Assigning a chip removes another planned use of the same chip in that half.
 */

import type { ChipCode } from "./types";

export const CHIPS: ChipCode[] = ["wc", "fh", "bb", "tc"];

// ── Half-season split ─────────────────────────────────────────────────────────

/** Returns 1 (GW1-19) or 2 (GW20-38). */
export function chipHalf(gw: number): 1 | 2 {
  return Number(gw) <= 19 ? 1 : 2;
}

// ── Usage queries ─────────────────────────────────────────────────────────────

/** All GWs a chip has been used (history + plan). */
export function chipAllUsedGws(
  chip: ChipCode,
  chipHistory: Record<ChipCode, number[]>,
  chipPlan: Record<number, ChipCode>,
): number[] {
  const history = chipHistory[chip] ?? [];
  const planned = Object.entries(chipPlan)
    .filter(([, c]) => c === chip)
    .map(([gw]) => Number(gw));
  return [...history, ...planned];
}

/** Whether the chip's allocation for a given half is already consumed. */
export function isChipUsedInHalf(
  chip: ChipCode,
  half: 1 | 2,
  chipHistory: Record<ChipCode, number[]>,
  chipPlan: Record<number, ChipCode>,
): boolean {
  return chipAllUsedGws(chip, chipHistory, chipPlan).some(
    (gw) => chipHalf(gw) === half,
  );
}

/** Whether the chip is in official history (already played, cannot unplan). */
export function isChipInHistory(
  chip: ChipCode,
  chipHistory: Record<ChipCode, number[]>,
): boolean {
  return (chipHistory[chip] ?? []).length > 0;
}

// ── Validation ────────────────────────────────────────────────────────────────

export type ChipAssignResult =
  | { ok: true }
  | { ok: false; reason: string };

/**
 * Validate whether a chip can be assigned to `targetGw`.
 *
 * Does not mutate anything — returns ok/reason so the caller decides
 * how to surface the error.
 */
export function validateChipAssign(
  chip: ChipCode,
  targetGw: number,
  chipHistory: Record<ChipCode, number[]>,
  chipPlan: Record<number, ChipCode>,
  currentGw: number,
): ChipAssignResult {
  const gw = Number(targetGw);
  const half = chipHalf(gw);

  // Cannot plan a chip for a completed GW.
  if (gw <= currentGw) {
    return { ok: false, reason: `GW${gw} is already completed.` };
  }

  // Free Hit and Wildcard cannot be played in GW1 (initial transfers are unlimited).
  if ((chip === "fh" || chip === "wc") && gw === 1) {
    return { ok: false, reason: `${chip === "wc" ? "Wildcard" : "Free Hit"} cannot be used in GW1 — initial transfers are unlimited.` };
  }

  // Only one chip per GW.
  const existingChip = chipPlan[gw];
  if (existingChip && existingChip !== chip) {
    return {
      ok: false,
      reason: `${existingChip.toUpperCase()} is already planned for GW${gw}.`,
    };
  }

  // Already in official history for this half.
  if (isChipUsedInHalf(chip, half, chipHistory, {})) {
    // Official history only (not plan) — can still plan if only planned use.
    const historyOnly = (chipHistory[chip] ?? []).some(
      (hwg) => chipHalf(hwg) === half,
    );
    if (historyOnly) {
      return {
        ok: false,
        reason: `${chip.toUpperCase()} has already been used in the ${half === 1 ? "first" : "second"} half.`,
      };
    }
  }

  // Free Hit: cannot play in consecutive GWs.
  if (chip === "fh") {
    const allFhGws = chipAllUsedGws("fh", chipHistory, chipPlan);
    if (allFhGws.includes(gw - 1) || allFhGws.includes(gw + 1)) {
      return { ok: false, reason: "Free Hit cannot be used in consecutive gameweeks." };
    }
  }

  return { ok: true };
}

/**
 * Apply a chip assignment to the plan, removing any conflicting planned
 * use of the same chip in the same half (not official history).
 *
 * Returns a NEW chipPlan object (immutable update).
 */
export function applyChipAssign(
  chip: ChipCode,
  targetGw: number,
  chipPlan: Record<number, ChipCode>,
  chipHistory: Record<ChipCode, number[]>,
): Record<number, ChipCode> {
  const half = chipHalf(targetGw);
  const next = { ...chipPlan };

  // Remove any other planned (not historical) use of this chip in the same half.
  for (const [gwStr, c] of Object.entries(next)) {
    const gw = Number(gwStr);
    if (c === chip && chipHalf(gw) === half && gw !== targetGw) {
      const inHistory = (chipHistory[chip] ?? []).includes(gw);
      if (!inHistory) delete next[gw];
    }
  }

  next[targetGw] = chip;
  return next;
}

/** Remove a planned chip from a GW (no-op if it's in official history). */
export function removeChipPlan(
  targetGw: number,
  chipPlan: Record<number, ChipCode>,
  chipHistory: Record<ChipCode, number[]>,
): Record<number, ChipCode> {
  const chip = chipPlan[targetGw];
  if (!chip) return chipPlan;
  const inHistory = (chipHistory[chip] ?? []).includes(targetGw);
  if (inHistory) return chipPlan; // Cannot unplan an official chip use.
  const next = { ...chipPlan };
  delete next[targetGw];
  return next;
}
