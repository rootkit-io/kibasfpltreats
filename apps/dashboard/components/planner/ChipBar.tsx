"use client";

/**
 * ChipBar — WC / FH / BB / TC chip controls for the displayed GW.
 *
 * Each chip shows: name, status (available / planned / used / blocked),
 * and the GW it was or will be used. Clicking an available chip assigns
 * it; clicking a planned chip removes the plan. Used chips are read-only.
 */

import { cn } from "@/lib/utils";
import {
  CHIPS,
  chipHalf,
  isChipUsedInHalf,
  isChipInHistory,
  validateChipAssign,
} from "@/lib/planner/chipRules";
import type { ChipCode } from "@/lib/planner/types";
import { CHIP_DISPLAY } from "@/lib/planner/types";
import type { PlannerState, PlannerAction } from "@/lib/planner/state";

type ChipStatus = "available" | "planned" | "used" | "blocked";

function getChipStatus(
  chip: ChipCode,
  gw: number,
  state: PlannerState,
): ChipStatus {
  const { chipHistory, chipPlan, currentGw } = state;

  // Already in official history for this half
  if (isChipInHistory(chip, chipHistory)) {
    const half = chipHalf(gw);
    if (isChipUsedInHalf(chip, half, chipHistory, {})) return "used";
  }

  // Planned for this exact GW
  const planned = chipPlan[String(gw)] as ChipCode | undefined;
  if (planned === chip) return "planned";

  // Validate whether it can be assigned
  const result = validateChipAssign(chip, gw, chipHistory, chipPlan as Record<number, ChipCode>, currentGw);
  if (!result.ok) return "blocked";

  return "available";
}

function chipGwLabel(
  chip: ChipCode,
  gw: number,
  state: PlannerState,
): string | null {
  const history = state.chipHistory[chip] ?? [];
  if (history.length > 0) return `GW${history[history.length - 1]}`;
  const planned = state.chipPlan[String(gw)] as ChipCode | undefined;
  if (planned === chip) return `GW${gw} ✓`;
  return null;
}

const STATUS_STYLES: Record<ChipStatus, string> = {
  available: "border-border text-muted-foreground hover:border-primary/60 hover:text-foreground cursor-pointer",
  planned:   "border-primary bg-primary/15 text-primary cursor-pointer",
  used:      "border-border/40 bg-muted/30 text-muted-foreground/50 cursor-not-allowed",
  blocked:   "border-border/40 text-muted-foreground/40 cursor-not-allowed",
};

export default function ChipBar({
  state,
  dispatch,
}: {
  state: PlannerState;
  dispatch: React.Dispatch<PlannerAction>;
}) {
  const { planGw } = state;

  function handleChip(chip: ChipCode) {
    const status = getChipStatus(chip, planGw, state);
    if (status === "used" || status === "blocked") return;
    if (status === "planned") {
      dispatch({ type: "REMOVE_CHIP", gw: planGw });
    } else {
      dispatch({ type: "ASSIGN_CHIP", chip, gw: planGw });
    }
  }

  return (
    <div
      className="flex flex-wrap items-center gap-2 px-4 py-3"
      role="group"
      aria-label="Chip controls"
    >
      <span className="mr-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        Chips
      </span>
      {CHIPS.map((chip) => {
        const status = getChipStatus(chip, planGw, state);
        const label = chipGwLabel(chip, planGw, state);
        const half = chipHalf(planGw);
        const usedThisHalf = isChipUsedInHalf(chip, half, state.chipHistory, {});
        const plannedElsewhere =
          status === "blocked" &&
          !usedThisHalf &&
          Object.entries(state.chipPlan).some(
            ([gwStr, c]) => c === chip && Number(gwStr) !== planGw,
          );

        return (
          <button
            key={chip}
            type="button"
            onClick={() => handleChip(chip)}
            disabled={status === "used" || status === "blocked"}
            title={
              status === "used"
                ? `${CHIP_DISPLAY[chip]} already used`
                : status === "blocked"
                  ? plannedElsewhere
                    ? `${CHIP_DISPLAY[chip]} planned for another GW this half`
                    : `${CHIP_DISPLAY[chip]} not available for GW${planGw}`
                  : status === "planned"
                    ? `Remove ${CHIP_DISPLAY[chip]} from GW${planGw}`
                    : `Use ${CHIP_DISPLAY[chip]} in GW${planGw}`
            }
            aria-pressed={status === "planned"}
            className={cn(
              "inline-flex flex-col items-center rounded border px-3 py-1.5 transition",
              STATUS_STYLES[status],
            )}
          >
            <span className="text-xs font-bold uppercase">{chip.toUpperCase()}</span>
            <span className="text-[9px] font-medium">
              {status === "used"
                ? `Used ${label ?? ""}`
                : status === "planned"
                  ? "Planned ✓"
                  : status === "blocked"
                    ? "Unavailable"
                    : `H${half} available`}
            </span>
          </button>
        );
      })}
    </div>
  );
}
