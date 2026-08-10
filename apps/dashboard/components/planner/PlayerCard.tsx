"use client";

/**
 * PlayerCard — one player on the pitch or bench.
 *
 * Three visual states:
 *   normal      — resting card
 *   selected    — tapped/clicked, action panel will open
 *   out-pending — marked for transfer out (dimmed, red border)
 *   sub-source  — initiating a substitution (amber border)
 *   sub-target  — valid swap candidate (green pulse)
 *
 * Shows: kit shirt, web_name, xPts (unless hidden), captain/VC badge,
 * position label, optional "Edited" badge.
 */

import { cn } from "@/lib/utils";
import { KitShirt } from "@/components/ui/KitShirt";
import type { FplPlayer, FplTeam } from "@/lib/planner/types";

// ── FDR colour map ────────────────────────────────────────────────────────────

const FDR_BG: Record<number, string> = {
  1: "bg-emerald-600",
  2: "bg-emerald-500",
  3: "bg-amber-500",
  4: "bg-rose-500",
  5: "bg-rose-700",
};

const FDR_TEXT: Record<number, string> = {
  1: "text-white",
  2: "text-white",
  3: "text-black",
  4: "text-white",
  5: "text-white",
};

export type CardState =
  | "normal"
  | "selected"
  | "out-pending"
  | "sub-source"
  | "sub-target";

export interface PlayerCardProps {
  element: number;
  player: FplPlayer | undefined;
  team: FplTeam | undefined;
  multiplier: number;       // 1 = normal, 2 = captain, 3 = TC
  isViceCaptain: boolean;
  isBench: boolean;
  xpts: number | null;
  hideXpts: boolean;
  cardState: CardState;
  /** Opponent code + FDR for the displayed GW, null = no fixture */
  fixture: { opp: string; fdr: number; home: boolean } | null;
  isEdited?: boolean;
  onClick: () => void;
}

export default function PlayerCard({
  element,
  player,
  team,
  multiplier,
  isViceCaptain,
  isBench,
  xpts,
  hideXpts,
  cardState,
  fixture,
  isEdited,
  onClick,
}: PlayerCardProps) {
  const name = player?.web_name ?? `#${element}`;
  const teamCode = team?.short_name ?? null;
  const isCaptain = multiplier >= 2;
  const isTC = multiplier === 3;

  return (
    <button
      onClick={onClick}
      type="button"
      aria-label={`${name}${isCaptain ? (isTC ? " — Triple Captain" : " — Captain") : isViceCaptain ? " — Vice Captain" : ""}${xpts !== null && !hideXpts ? ` — ${xpts.toFixed(1)} xPts` : ""}`}
      className={cn(
        // base
        "relative flex flex-col items-center gap-0.5 rounded px-1 pb-1.5 pt-1 text-center transition-all",
        "select-none focus-visible:outline-2 focus-visible:outline-primary",
        // sizing
        isBench ? "w-14" : "w-16",
        // state colours
        cardState === "normal" && "bg-card/90 hover:bg-card",
        cardState === "selected" && "bg-primary/15 ring-1 ring-primary",
        cardState === "out-pending" && "opacity-50 ring-1 ring-rose-500",
        cardState === "sub-source" && "ring-2 ring-amber-400",
        cardState === "sub-target" && "ring-2 ring-emerald-400 animate-pulse",
      )}
    >
      {/* Kit shirt */}
      <KitShirt
        teamCode={teamCode}
        size={isBench ? 24 : 28}
        idSuffix={String(element)}
      />

      {/* Player name */}
      <span
        className={cn(
          "w-full truncate font-medium leading-tight",
          isBench ? "text-[9px]" : "text-[10px]",
        )}
        title={name}
      >
        {name}
      </span>

      {/* xPts */}
      {!hideXpts && (
        <span
          className={cn(
            "font-mono font-semibold tabular-nums",
            isBench ? "text-[9px]" : "text-[10px]",
            xpts === null
              ? "text-muted-foreground/50"
              : xpts >= 7
                ? "text-emerald-400"
                : xpts >= 4
                  ? "text-foreground"
                  : "text-muted-foreground",
          )}
        >
          {xpts !== null ? xpts.toFixed(1) : "—"}
        </span>
      )}

      {/* Fixture pill */}
      {fixture && (
        <span
          className={cn(
            "rounded px-0.5 font-mono text-[8px] font-bold uppercase",
            FDR_BG[fixture.fdr] ?? "bg-zinc-600",
            FDR_TEXT[fixture.fdr] ?? "text-white",
          )}
        >
          {fixture.home ? fixture.opp.toUpperCase() : fixture.opp.toLowerCase()}
        </span>
      )}

      {/* Captain / VC badge */}
      {(isCaptain || isViceCaptain) && (
        <span
          className={cn(
            "absolute -right-1 -top-1 flex h-4 w-4 items-center justify-center rounded-full text-[8px] font-black leading-none",
            isTC
              ? "bg-purple-500 text-white"
              : isCaptain
                ? "bg-amber-400 text-black"
                : "bg-zinc-600 text-white",
          )}
          aria-hidden
        >
          {isTC ? "T" : isCaptain ? "C" : "V"}
        </span>
      )}

      {/* Edited badge */}
      {isEdited && (
        <span
          className="absolute -left-1 -top-1 h-1.5 w-1.5 rounded-full bg-sky-400"
          aria-label="Projection edited"
          title="Projection edited"
        />
      )}
    </button>
  );
}
