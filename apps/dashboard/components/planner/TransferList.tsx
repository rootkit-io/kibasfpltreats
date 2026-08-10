"use client";

/**
 * TransferList — ordered list of planned transfers for the displayed GW.
 *
 * Shows OUT → IN with prices, hit count, and a remove button.
 * Syncs with the derived bank/FT/hits from the reducer.
 */

import { X } from "lucide-react";

import { cn } from "@/lib/utils";
import { KitShirt } from "@/components/ui/KitShirt";
import type { PlannerState, PlannerAction } from "@/lib/planner/state";
import type { DerivedGwState } from "@/lib/planner/types";

function pence(tenths: number): string {
  return `£${(tenths / 10).toFixed(1)}m`;
}

export default function TransferList({
  state,
  derived,
  dispatch,
}: {
  state: PlannerState;
  derived: DerivedGwState;
  dispatch: React.Dispatch<PlannerAction>;
}) {
  const { playerMap, teamMap, planGw, transfers } = state;
  const gwTransfers = transfers
    .filter((t) => t.gw === planGw)
    .sort((a, b) => a.planOrder - b.planOrder);

  if (gwTransfers.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 py-12 text-center">
        <p className="text-sm text-muted-foreground">No transfers planned for GW{planGw}.</p>
        <p className="text-xs text-muted-foreground/60">
          Tap a player on the pitch then choose "Transfer out".
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-0">
      {/* Summary bar */}
      <div className="flex items-center gap-4 border-b border-border bg-muted/20 px-4 py-2 text-xs">
        <span className="font-mono tabular-nums">
          {gwTransfers.length} transfer{gwTransfers.length !== 1 ? "s" : ""}
        </span>
        <span className={cn("font-mono font-semibold tabular-nums", derived.hits > 0 ? "text-rose-400" : "text-emerald-400")}>
          {derived.hits > 0 ? `−${derived.hits} pts` : "No hits"}
        </span>
        <span className="font-mono tabular-nums text-muted-foreground">
          Bank after: {pence(derived.bank)}
        </span>
      </div>

      {/* Transfer rows */}
      <ul className="divide-y divide-border/50">
        {gwTransfers.map((t) => {
          const outPlayer = playerMap.get(t.outId);
          const inPlayer = playerMap.get(t.inId);
          const outTeam = teamMap.get(outPlayer?.team ?? 0);
          const inTeam = teamMap.get(inPlayer?.team ?? 0);
          const delta = t.inPrice - t.outPrice;

          return (
            <li key={t.uid} className="flex items-center gap-3 px-4 py-3">
              {/* OUT */}
              <div className="flex min-w-0 flex-1 items-center gap-2">
                <KitShirt teamCode={outTeam?.short_name} size={20} idSuffix={`out-${t.outId}`} />
                <div className="min-w-0">
                  <p className="truncate text-xs font-medium text-rose-400">
                    {outPlayer?.web_name ?? `#${t.outId}`}
                  </p>
                  <p className="font-mono text-[10px] text-muted-foreground">
                    {pence(t.outPrice)}
                  </p>
                </div>
              </div>

              {/* Arrow + delta */}
              <div className="flex shrink-0 flex-col items-center">
                <span className="text-muted-foreground">→</span>
                <span className={cn(
                  "font-mono text-[9px] tabular-nums",
                  delta > 0 ? "text-rose-400" : delta < 0 ? "text-emerald-400" : "text-muted-foreground/50",
                )}>
                  {delta > 0 ? `+${pence(delta)}` : delta < 0 ? pence(delta) : "even"}
                </span>
              </div>

              {/* IN */}
              <div className="flex min-w-0 flex-1 items-center gap-2">
                <KitShirt teamCode={inTeam?.short_name} size={20} idSuffix={`in-${t.inId}`} />
                <div className="min-w-0">
                  <p className="truncate text-xs font-medium text-emerald-400">
                    {inPlayer?.web_name ?? `#${t.inId}`}
                  </p>
                  <p className="font-mono text-[10px] text-muted-foreground">
                    {pence(t.inPrice)}
                  </p>
                </div>
              </div>

              {/* Warnings */}
              {t.warnings.length > 0 && (
                <span className="shrink-0 text-[10px] font-semibold text-amber-400" title={t.warnings.join(", ")}>
                  ⚠
                </span>
              )}

              {/* Remove */}
              <button
                onClick={() => dispatch({ type: "REMOVE_TRANSFER", uid: t.uid })}
                aria-label={`Remove transfer: ${outPlayer?.web_name ?? t.outId} → ${inPlayer?.web_name ?? t.inId}`}
                className="shrink-0 text-muted-foreground transition hover:text-rose-400"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
