"use client";

/**
 * FormationPicker — inline button group for the 8 legal FPL formations.
 *
 * Selecting a formation triggers a formation change via callback. The
 * active formation is highlighted. Layout is compact so it sits cleanly
 * in the pitch toolbar row.
 */

import { cn } from "@/lib/utils";

// ── Legal formations ──────────────────────────────────────────────────────────

/** All eight formations the planner supports, expressed as [DEF, MID, FWD]. */
export const FORMATIONS = [
  [3, 4, 3],
  [3, 5, 2],
  [4, 3, 3],
  [4, 4, 2],
  [4, 5, 1],
  [5, 2, 3],
  [5, 3, 2],
  [5, 4, 1],
] as const;

export type Formation = readonly [number, number, number];

export function formationLabel(f: Formation): string {
  return `${f[0]}-${f[1]}-${f[2]}`;
}

/** Detect the current formation from the squad's starter position slots. */
export function detectFormation(
  starters: Array<{ position: number; elementType: number }>,
): Formation {
  // elementType: 1=GK, 2=DEF, 3=MID, 4=FWD
  const def = starters.filter((p) => p.elementType === 2).length;
  const mid = starters.filter((p) => p.elementType === 3).length;
  const fwd = starters.filter((p) => p.elementType === 4).length;
  const match = FORMATIONS.find(
    ([d, m, f]) => d === def && m === mid && f === fwd,
  );
  return match ?? [4, 4, 2];
}

/**
 * Assign starter position slots for a formation.
 * Returns a map: element → position (1–11).
 * Position 1 = GK, 2–N+1 = DEF, then MID, then FWD.
 */
export function slotsForFormation(
  squad: Array<{ element: number; elementType: number; isBench: boolean }>,
  formation: Formation,
): Record<number, number> {
  const [dCount, mCount, fCount] = formation;
  const gk = squad.filter((p) => !p.isBench && p.elementType === 1);
  const def = squad.filter((p) => !p.isBench && p.elementType === 2).slice(0, dCount);
  const mid = squad.filter((p) => !p.isBench && p.elementType === 3).slice(0, mCount);
  const fwd = squad.filter((p) => !p.isBench && p.elementType === 4).slice(0, fCount);

  const ordered = [...gk, ...def, ...mid, ...fwd];
  const slots: Record<number, number> = {};
  ordered.forEach((p, i) => { slots[p.element] = i + 1; });
  return slots;
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function FormationPicker({
  current,
  onChange,
}: {
  current: Formation;
  onChange: (f: Formation) => void;
}) {
  return (
    <div
      className="inline-flex flex-wrap gap-0.5 rounded border border-border bg-muted/30 p-0.5"
      role="group"
      aria-label="Choose formation"
    >
      {FORMATIONS.map((f) => {
        const label = formationLabel(f);
        const active =
          f[0] === current[0] && f[1] === current[1] && f[2] === current[2];
        return (
          <button
            key={label}
            type="button"
            onClick={() => onChange(f)}
            aria-pressed={active}
            className={cn(
              "rounded px-2 py-0.5 font-mono text-[10px] font-semibold transition",
              active
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
          >
            {label}
          </button>
        );
      })}
    </div>
  );
}
