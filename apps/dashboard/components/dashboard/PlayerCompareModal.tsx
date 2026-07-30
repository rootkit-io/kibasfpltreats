"use client";

/**
 * PlayerCompareModal -- side-by-side Monte Carlo comparison for 2-4 players.
 *
 * Rendered as a bottom sheet rather than a centred dialog: the selection
 * happens in a long list, and a sheet keeps the list visible while comparing.
 *
 * Metric rows highlight the leader per row (higher is better for everything
 * shown EXCEPT volatility, where lower is better) -- the direction is declared
 * per metric rather than assumed.
 */

import { useMemo, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Check, Search, X } from "lucide-react";

import { cn } from "@/lib/utils";
import { BracketBar } from "@/components/dashboard/BracketDistribution";
import type { EnrichedSimulation } from "@/lib/api/simulations";

const MAX_SELECTION = 4;

interface Metric {
  label: string;
  pick: (row: EnrichedSimulation) => number | null | undefined;
  digits?: number;
  percent?: boolean;
  /** "high" -> larger is better; "low" -> smaller is better. */
  better: "high" | "low";
}

const METRICS: Metric[] = [
  { label: "Mean points", pick: (r) => r.mean_pts, better: "high" },
  { label: "Floor (P10)", pick: (r) => r.floor_p10, better: "high" },
  { label: "Upside (P90)", pick: (r) => r.upside_p90, better: "high" },
  { label: "Volatility (σ)", pick: (r) => r.std_pts, better: "low" },
  { label: "P(1+ return)", pick: (r) => r.p1_return, percent: true, better: "high" },
  { label: "P(2+ returns)", pick: (r) => r.p2_return, percent: true, better: "high" },
  { label: "P(15+ pts)", pick: (r) => r.bracket_15_plus, percent: true, better: "high" },
];

function format(value: number | null | undefined, metric: Metric): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return metric.percent ? `${(value * 100).toFixed(0)}%` : value.toFixed(metric.digits ?? 2);
}

/**
 * Spring rather than a duration curve: the panel is summoned by a click, and
 * a light overshoot reads as the surface *responding* to that click. Damping
 * is high enough that it settles without visible bounce.
 */
const PANEL_SPRING = { type: "spring", stiffness: 420, damping: 34, mass: 0.9 } as const;

export default function PlayerCompareModal({ rows }: { rows: EnrichedSimulation[] }) {
  const reduceMotion = useReducedMotion();
  const [selected, setSelected] = useState<number[]>([]);
  const [query, setQuery] = useState("");

  const candidates = useMemo(() => {
    const q = query.trim().toLowerCase();
    const base = q
      ? rows.filter(
          (r) =>
            (r.web_name ?? "").toLowerCase().includes(q) ||
            (r.team_short ?? "").toLowerCase().includes(q),
        )
      : [...rows].sort((a, b) => (b.mean_pts ?? 0) - (a.mean_pts ?? 0));
    return base.slice(0, 60);
  }, [rows, query]);

  const chosen = useMemo(
    () => selected.map((id) => rows.find((r) => r.player_id === id)).filter(Boolean) as EnrichedSimulation[],
    [selected, rows],
  );

  const toggle = (playerId: number) =>
    setSelected((prev) =>
      prev.includes(playerId)
        ? prev.filter((id) => id !== playerId)
        : prev.length >= MAX_SELECTION
          ? prev
          : [...prev, playerId],
    );

  /** Index of the winning column for a metric, or -1 when undecidable. */
  const leaderIndex = (metric: Metric): number => {
    let best = -1;
    let bestValue: number | null = null;
    chosen.forEach((row, i) => {
      const value = metric.pick(row);
      if (typeof value !== "number" || !Number.isFinite(value)) return;
      if (
        bestValue === null ||
        (metric.better === "high" ? value > bestValue : value < bestValue)
      ) {
        bestValue = value;
        best = i;
      }
    });
    return best;
  };

  return (
    <div className="grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
      {/* ------------------------------------------------------- picker */}
      <div className="border border-border bg-card">
        <div className="border-b border-border p-3">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Find a player…"
              className="w-full rounded-md border border-border bg-background py-1.5 pl-8 pr-2 text-xs outline-none focus:border-primary"
            />
          </div>
          <p className="mt-2 font-mono text-[10px] text-muted-foreground">
            {selected.length}/{MAX_SELECTION} selected
          </p>
        </div>
        <ul className="scroll-thin max-h-[420px] overflow-auto">
          {candidates.map((row) => {
            const isSelected = selected.includes(row.player_id);
            const atLimit = !isSelected && selected.length >= MAX_SELECTION;
            return (
              <li key={`${row.player_id}:${row.gameweek_id}`}>
                <button
                  onClick={() => toggle(row.player_id)}
                  disabled={atLimit}
                  className={cn(
                    "flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition",
                    isSelected ? "bg-primary/10 text-foreground" : "hover:bg-muted/50",
                    atLimit && "cursor-not-allowed opacity-40",
                  )}
                >
                  <span
                    className={cn(
                      "flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded border",
                      isSelected ? "border-primary bg-primary text-primary-foreground" : "border-border",
                    )}
                  >
                    {isSelected && <Check className="h-2.5 w-2.5" />}
                  </span>
                  <span className="truncate font-medium">{row.web_name ?? "—"}</span>
                  <span className="ml-auto shrink-0 font-mono text-[10px] text-muted-foreground">
                    {typeof row.mean_pts === "number" ? row.mean_pts.toFixed(1) : "—"}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>

      {/* ---------------------------------------------------- comparison */}
      <motion.div
        layout={!reduceMotion}
        transition={PANEL_SPRING}
        className="border border-border bg-card p-4"
      >
        {chosen.length < 2 ? (
          <p className="py-16 text-center text-xs text-muted-foreground">
            Select at least two players to compare their simulated outcomes.
          </p>
        ) : (
          <>
            <div
              className="grid gap-3"
              style={{ gridTemplateColumns: `140px repeat(${chosen.length}, minmax(0,1fr))` }}
            >
              <div />
              <AnimatePresence initial={false} mode="popLayout">
              {chosen.map((row) => (
                <motion.div
                  key={row.player_id}
                  layout={!reduceMotion}
                  initial={reduceMotion ? false : { opacity: 0, y: -8, scale: 0.97 }}
                  animate={{ opacity: 1, y: 0, scale: 1 }}
                  exit={reduceMotion ? undefined : { opacity: 0, scale: 0.97 }}
                  transition={PANEL_SPRING}
                  className="min-w-0"
                >
                  <div className="flex items-start justify-between gap-1">
                    <p className="truncate text-xs font-semibold">{row.web_name ?? "—"}</p>
                    <button
                      onClick={() => toggle(row.player_id)}
                      className="text-muted-foreground hover:text-foreground"
                      aria-label={`Remove ${row.web_name ?? "player"}`}
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </div>
                  <p className="truncate text-[10px] text-muted-foreground">
                    {row.team_short ?? "—"} · {row.position ?? "—"}
                  </p>
                </motion.div>
              ))}
              </AnimatePresence>
            </div>

            <div className="mt-3 space-y-1.5">
              {METRICS.map((metric) => {
                const leader = leaderIndex(metric);
                return (
                  <div
                    key={metric.label}
                    className="grid items-center gap-3 border-t border-border/50 py-1.5"
                    style={{ gridTemplateColumns: `140px repeat(${chosen.length}, minmax(0,1fr))` }}
                  >
                    <span className="text-[11px] text-muted-foreground">{metric.label}</span>
                    {chosen.map((row, i) => (
                      <span
                        key={row.player_id}
                        className={cn(
                          "text-right font-mono text-xs tabular-nums",
                          i === leader ? "font-semibold text-positive" : "text-muted-foreground",
                        )}
                      >
                        {format(metric.pick(row), metric)}
                      </span>
                    ))}
                  </div>
                );
              })}
            </div>

            <div className="mt-5 space-y-3">
              <p className="text-[11px] font-medium text-muted-foreground">
                Outcome distribution
              </p>
              {chosen.map((row) => (
                <div key={row.player_id}>
                  <p className="mb-1 text-[10px] text-muted-foreground">{row.web_name ?? "—"}</p>
                  <BracketBar row={row} showLabels />
                </div>
              ))}
            </div>
          </>
        )}
      </motion.div>
    </div>
  );
}
