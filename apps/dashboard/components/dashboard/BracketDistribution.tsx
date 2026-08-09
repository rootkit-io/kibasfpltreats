"use client";

/**
 * BracketDistribution — Monte Carlo outcome stacked-bar chart.
 *
 * Upgraded from the basic sorted list to a full section matching KFT2627:
 * - GW selector when multiple gameweeks available
 * - Player search
 * - Sort by mean / haul probability / blank probability
 * - Position filter (All / GK / DEF / MID / FWD)
 * - Tooltip on hover
 */

import { useMemo, useState } from "react";
import { Search, X } from "lucide-react";
import { BRACKETS, hasBrackets, type SimulationRow } from "@/lib/api/simulations";
import { cn } from "@/lib/utils";
import { TeamKit } from "@/components/ui/TeamKit";

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
    return <div className={cn("w-full bg-muted", height)} title="No simulation data" />;
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
            <div key={bracket.key} className={cn(bracket.className, "transition-all")}
              style={{ width: `${share * 100}%` }}
              title={`${bracket.label} pts — ${(values[i] * 100).toFixed(1)}%`} />
          );
        })}
      </div>
      {showLabels && (
        <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1">
          {BRACKETS.map((bracket, i) => (
            <span key={bracket.key} className="inline-flex items-center gap-1 font-mono text-[10px] text-muted-foreground">
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

type SortField = "mean" | "haul" | "blank" | "xpts";
const POSITIONS = ["All", "GK", "DEF", "MID", "FWD"] as const;

export default function BracketDistribution({ rows }: { rows: SimulationRow[] }) {
  const gameweeks = useMemo(() => {
    const gws = new Set(rows.map((r) => r.gameweek_id));
    return [...gws].sort((a, b) => a - b);
  }, [rows]);

  const [gw, setGw] = useState<number | null>(gameweeks[0] ?? null);
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortField>("mean");
  const [position, setPosition] = useState<(typeof POSITIONS)[number]>("All");
  const [hoveredId, setHoveredId] = useState<number | null>(null);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return rows
      .filter(hasBrackets)
      .filter((r) => (gw === null || r.gameweek_id === gw))
      .filter((r) => position === "All" || (r as unknown as Record<string, unknown>).position === position)
      .filter((r) => !q || (r.web_name ?? "").toLowerCase().includes(q) || (r.team_short ?? "").toLowerCase().includes(q));
  }, [rows, gw, search, position]);

  const sorted = useMemo(() => {
    return [...filtered].sort((a, b) => {
      if (sort === "haul") return pct(b.p_haul) - pct(a.p_haul);
      if (sort === "blank") return pct(b.bracket_le_2) - pct(a.bracket_le_2);
      if (sort === "xpts") return pct((a as unknown as Record<string, unknown>).xpts as number) - pct((b as unknown as Record<string, unknown>).xpts as number);
      return pct(b.mean_pts) - pct(a.mean_pts);
    }).slice(0, 60);
  }, [filtered, sort]);

  if (rows.length === 0) {
    return (
      <p className="border border-dashed border-border bg-card px-4 py-12 text-center text-xs text-muted-foreground">
        This published run carries no simulation brackets.
      </p>
    );
  }

  return (
    <div className="border border-border bg-card">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
        <div>
          <h3 className="text-sm font-semibold">Points Range Forecast</h3>
          <p className="text-[11px] text-muted-foreground">
            Monte Carlo outcome distribution — {sorted.length} players shown
          </p>
        </div>
        <div className="flex flex-wrap gap-x-3">
          {BRACKETS.map((b) => (
            <span key={b.key} className="inline-flex items-center gap-1 font-mono text-[10px] text-muted-foreground">
              <span className={cn("h-2 w-2", b.className)} />
              {b.label}
            </span>
          ))}
        </div>
      </div>

      {/* Controls */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border/50 px-4 py-2">
        {/* GW select */}
        {gameweeks.length > 1 && (
          <select value={gw ?? ""} onChange={(e) => setGw(Number(e.target.value))}
            className="h-7 rounded border border-border bg-background px-2 font-mono text-xs outline-none focus:border-primary">
            {gameweeks.map((g) => <option key={g} value={g}>GW{g}</option>)}
          </select>
        )}

        {/* Search */}
        <div className="relative">
          <Search className="absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <input type="text" value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder="Search player or team…"
            className="h-7 w-44 rounded border border-border bg-background pl-6 pr-6 text-xs outline-none focus:border-primary" />
          {search && (
            <button onClick={() => setSearch("")} className="absolute right-1.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground">
              <X className="h-3 w-3" />
            </button>
          )}
        </div>

        {/* Sort */}
        <select value={sort} onChange={(e) => setSort(e.target.value as SortField)}
          className="h-7 rounded border border-border bg-background px-2 text-xs outline-none focus:border-primary">
          <option value="mean">Sort: Mean</option>
          <option value="haul">Sort: Haul%</option>
          <option value="blank">Sort: Blank%</option>
        </select>

        {/* Position filter */}
        <div className="inline-flex rounded border border-border p-0.5">
          {POSITIONS.map((p) => (
            <button key={p} onClick={() => setPosition(p)}
              className={cn("rounded px-2 py-0.5 text-xs font-medium transition",
                position === p ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")}>
              {p}
            </button>
          ))}
        </div>
      </div>

      {/* Rows */}
      {sorted.length === 0 ? (
        <p className="px-4 py-8 text-center text-xs text-muted-foreground">No simulation data for this selection.</p>
      ) : (
        <ul className="divide-y divide-border/50">
          {sorted.map((row) => {
            const isHovered = hoveredId === row.player_id;
            return (
              <li key={`${row.player_id}:${row.gameweek_id}`}
                onMouseEnter={() => setHoveredId(row.player_id)}
                onMouseLeave={() => setHoveredId(null)}
                className="grid grid-cols-[minmax(0,140px)_1fr_auto] items-center gap-3 px-4 py-2 transition hover:bg-muted/30 sm:grid-cols-[180px_1fr_60px]">
                {/* Name + kit */}
                <span className="flex min-w-0 items-center gap-2 text-xs font-medium">
                  <TeamKit teamCode={row.team_short} size={16} />
                  <span className="truncate">{row.web_name ?? "—"}</span>
                </span>

                {/* Distribution bar */}
                <div>
                  <BracketBar row={row} />
                  {isHovered && (
                    <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5">
                      {BRACKETS.map((b, i) => {
                        const val = pct(row[b.key]) * 100;
                        return (
                          <span key={b.key} className="inline-flex items-center gap-1 font-mono text-[9px] text-muted-foreground">
                            <span className={cn("h-1.5 w-1.5", b.className)} />
                            {b.label} <span className="text-foreground">{val.toFixed(0)}%</span>
                          </span>
                        );
                      })}
                    </div>
                  )}
                </div>

                {/* Mean */}
                <div className="text-right">
                  <div className="font-mono text-xs tabular-nums text-foreground">
                    {typeof row.mean_pts === "number" ? row.mean_pts.toFixed(1) : "—"}
                  </div>
                  {row.p_haul !== null && row.p_haul !== undefined && (
                    <div className="font-mono text-[10px] tabular-nums text-muted-foreground">
                      {(pct(row.p_haul) * 100).toFixed(0)}% haul
                    </div>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
