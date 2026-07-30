"use client";

/**
 * BracketDistribution -- stacked probability bar over the five outcome
 * brackets from the Monte Carlo run.
 *
 * The brackets are mutually exclusive and sum to ~1, so a single stacked bar
 * is the honest encoding: segment width IS the probability. Segments below a
 * legibility threshold keep a minimum width, which is why the rendered widths
 * are normalised rather than used raw.
 */

import { BRACKETS, hasBrackets, type SimulationRow } from "@/lib/api/simulations";
import { cn } from "@/lib/utils";

function pct(value: number | null | undefined): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

export function BracketBar({
  row,
  height = "h-2.5",
  showLabels = false,
}: {
  row: SimulationRow;
  height?: string;
  showLabels?: boolean;
}) {
  if (!hasBrackets(row)) {
    return (
      <div className={cn("w-full bg-muted", height)} title="No simulation data" />
    );
  }
  const values = BRACKETS.map((b) => pct(row[b.key]));
  const total = values.reduce((a, b) => a + b, 0) || 1;

  return (
    <div className="w-full">
      <div className={cn("flex w-full overflow-hidden bg-muted", height)}>
        {BRACKETS.map((bracket, i) => {
          const share = values[i] / total;
          if (share <= 0) return null;
          return (
            <div
              key={bracket.key}
              className={cn(bracket.className, "transition-all")}
              style={{ width: `${share * 100}%` }}
              title={`${bracket.label} pts — ${(values[i] * 100).toFixed(1)}%`}
            />
          );
        })}
      </div>
      {showLabels && (
        <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1">
          {BRACKETS.map((bracket, i) => (
            <span
              key={bracket.key}
              className="inline-flex items-center gap-1 font-mono text-[10px] text-muted-foreground"
            >
              <span className={cn("h-2 w-2", bracket.className)} />
              {bracket.label}
              <span className="text-foreground">{(values[i] * 100).toFixed(0)}%</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export default function BracketDistribution({
  rows,
  limit = 20,
}: {
  rows: SimulationRow[];
  limit?: number;
}) {
  const ranked = [...rows]
    .filter(hasBrackets)
    .sort((a, b) => (b.mean_pts ?? 0) - (a.mean_pts ?? 0))
    .slice(0, limit);

  if (ranked.length === 0) {
    return (
      <p className="border border-dashed border-border bg-card px-4 py-12 text-center text-xs text-muted-foreground">
        This published run carries no simulation brackets.
      </p>
    );
  }

  return (
    <div className="border border-border bg-card">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
        <h3 className="text-sm font-semibold">Outcome distribution — top {ranked.length}</h3>
        <div className="flex flex-wrap gap-x-3">
          {BRACKETS.map((b) => (
            <span
              key={b.key}
              className="inline-flex items-center gap-1 font-mono text-[10px] text-muted-foreground"
            >
              <span className={cn("h-2 w-2", b.className)} />
              {b.label}
            </span>
          ))}
        </div>
      </div>
      <ul className="divide-y divide-border/50">
        {ranked.map((row) => (
          <li
            key={`${row.player_id}:${row.gameweek_id}`}
            className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-4 py-2 sm:grid-cols-[150px_minmax(0,1fr)_auto]"
          >
            <span className="truncate text-xs font-medium">{row.web_name ?? "—"}</span>
            <div className="col-span-2 sm:col-span-1">
              <BracketBar row={row} />
            </div>
            <span className="text-right font-mono text-xs tabular-nums text-muted-foreground">
              {typeof row.mean_pts === "number" ? row.mean_pts.toFixed(1) : "—"}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
