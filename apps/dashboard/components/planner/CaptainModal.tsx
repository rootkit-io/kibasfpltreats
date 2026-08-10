"use client";

/**
 * CaptainModal — pick captain and vice-captain from the starting XI.
 *
 * Shows all 11 starters sorted by xPts (desc). Current captain gets
 * an amber ring; VC gets a zinc ring. Two-click flow: first click sets
 * captain, second click on a different player sets vice-captain.
 */

import { X } from "lucide-react";

import { cn } from "@/lib/utils";
import { KitShirt } from "@/components/ui/KitShirt";
import type { PlannerState, PlannerAction } from "@/lib/planner/state";
import type { DerivedGwState } from "@/lib/planner/types";

function pence(tenths: number): string {
  return `£${(tenths / 10).toFixed(1)}m`;
}

export default function CaptainModal({
  state,
  derived,
  dispatch,
  getXpts,
}: {
  state: PlannerState;
  derived: DerivedGwState;
  dispatch: React.Dispatch<PlannerAction>;
  getXpts: (element: number, gw: number) => number | null;
}) {
  const { playerMap, teamMap, planGw } = state;

  const starters = derived.squad
    .filter((p) => p.position <= 11)
    .map((p) => ({
      pick: p,
      player: playerMap.get(p.element),
      xpts: getXpts(p.element, planGw),
    }))
    .sort((a, b) => (b.xpts ?? 0) - (a.xpts ?? 0));

  const currentCap = derived.squad.find((p) => p.multiplier >= 2)?.element ?? null;
  const currentVc = derived.squad.find((p) => p.isViceCaptain)?.element ?? null;

  return (
    <div className="flex flex-col overflow-hidden" style={{ maxHeight: "calc(100vh - 160px)" }}>
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-4 py-3">
        <div>
          <h3 className="text-sm font-semibold">Captain & Vice-Captain</h3>
          <p className="text-[11px] text-muted-foreground">
            Tap to set captain · tap a different player to set vice-captain
          </p>
        </div>
        <button
          onClick={() => dispatch({ type: "CLOSE_MODAL" })}
          className="text-muted-foreground hover:text-foreground"
          aria-label="Close"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* Starters list */}
      <ul className="flex-1 divide-y divide-border/50 overflow-y-auto">
        {starters.map(({ pick, player, xpts }) => {
          const team = teamMap.get(player?.team ?? 0);
          const isCap = pick.element === currentCap;
          const isVc = pick.element === currentVc;
          const isTC = pick.multiplier === 3;

          return (
            <li
              key={pick.element}
              className={cn(
                "flex items-center gap-3 px-4 py-2.5 transition hover:bg-muted/30",
                isCap && "bg-amber-400/10",
                isVc && !isCap && "bg-zinc-700/20",
              )}
            >
              <KitShirt
                teamCode={team?.short_name}
                size={22}
                idSuffix={`cap-${pick.element}`}
              />
              <div className="min-w-0 flex-1">
                <p className={cn(
                  "truncate text-xs font-semibold",
                  isCap ? "text-amber-300" : isVc ? "text-zinc-300" : "text-foreground",
                )}>
                  {player?.web_name ?? `#${pick.element}`}
                </p>
                <p className="text-[10px] text-muted-foreground">
                  {team?.short_name ?? "—"}
                  {xpts !== null && (
                    <span className="ml-1 font-mono">{xpts.toFixed(1)} xPts</span>
                  )}
                </p>
              </div>

              {/* Badge */}
              {(isCap || isVc) && (
                <span className={cn(
                  "shrink-0 rounded px-1.5 py-0.5 text-[9px] font-black uppercase",
                  isCap
                    ? isTC ? "bg-purple-500 text-white" : "bg-amber-400 text-black"
                    : "bg-zinc-600 text-white",
                )}>
                  {isTC ? "TC" : isCap ? "C" : "V"}
                </span>
              )}

              {/* Action buttons */}
              <div className="flex shrink-0 gap-1.5">
                <button
                  onClick={() => dispatch({ type: "SET_CAPTAIN", element: pick.element, gw: planGw })}
                  disabled={isCap}
                  className={cn(
                    "rounded border px-2 py-1 text-[10px] font-semibold transition",
                    isCap
                      ? "border-amber-400/30 bg-amber-400/10 text-amber-300 cursor-default"
                      : "border-border text-muted-foreground hover:border-amber-400/50 hover:text-amber-300",
                  )}
                >
                  {isCap ? "Captain" : "Make C"}
                </button>
                <button
                  onClick={() => dispatch({ type: "SET_VICE_CAPTAIN", element: pick.element, gw: planGw })}
                  disabled={isVc || isCap}
                  className={cn(
                    "rounded border px-2 py-1 text-[10px] font-semibold transition",
                    isVc
                      ? "border-zinc-500/30 bg-zinc-600/10 text-zinc-300 cursor-default"
                      : isCap
                        ? "border-border/30 text-muted-foreground/30 cursor-not-allowed"
                        : "border-border text-muted-foreground hover:border-zinc-500/50 hover:text-zinc-300",
                  )}
                >
                  {isVc ? "VC" : "Make VC"}
                </button>
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
