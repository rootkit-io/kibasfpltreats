"use client";

/**
 * XgiRadar -- goal-involvement ranking, matching the live site's
 * "Goal Involvement Radar": projected xG + xA for a selected gameweek,
 * stacked so the split is readable at a glance.
 *
 * Stacked horizontal bars rather than a polar/spider chart: the quantity
 * being compared is one additive number (xGI = xG + xA) across many players,
 * and a ranked bar list reads far better at 20+ entries than a radar polygon.
 */

import { useMemo, useState } from "react";

import { cn } from "@/lib/utils";
import { TeamKit } from "@/components/ui/TeamKit";
import type { ProjectionRow } from "@/lib/validations/projections";

const POSITIONS = ["All", "DEF", "MID", "FWD"] as const;
type PositionTab = (typeof POSITIONS)[number];

const TOP_N = 25;

interface RadarRow {
  player_id: number;
  name: string;
  team: string | null;
  position: string | null;
  xg: number;
  xa: number;
  xgi: number;
}

export default function XgiRadar({
  rows,
  gameweeks,
  gameweek,
  onGameweekChange,
}: {
  rows: ProjectionRow[];
  gameweeks: number[];
  gameweek: number | null;
  onGameweekChange: (gw: number) => void;
}) {
  const [position, setPosition] = useState<PositionTab>("All");

  const ranked = useMemo<RadarRow[]>(() => {
    const scoped = rows.filter(
      (r) =>
        (gameweek === null || r.gameweek_id === gameweek) &&
        (position === "All" || (r.position ?? "").toUpperCase() === position),
    );
    const mapped = scoped.flatMap((r) => {
      const xg = typeof r.xg === "number" && Number.isFinite(r.xg) ? r.xg : 0;
      const xa = typeof r.xa === "number" && Number.isFinite(r.xa) ? r.xa : 0;
      if (xg + xa <= 0 || typeof r.player_id !== "number") return [];
      return [{
        player_id: r.player_id,
        name: r.web_name ?? "—",
        team: r.team_short ?? null,
        position: r.position ?? null,
        xg, xa, xgi: xg + xa,
      }];
    });
    return mapped.sort((a, b) => b.xgi - a.xgi).slice(0, TOP_N);
  }, [rows, gameweek, position]);

  const max = ranked.length > 0 ? ranked[0].xgi : 1;

  return (
    <div className="border border-border bg-card">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
        <div>
          <h3 className="text-sm font-semibold">Goal Involvement Radar</h3>
          <p className="text-[11px] text-muted-foreground">
            Projected xG + xA — top {TOP_N} for the selected gameweek.
          </p>
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          <div className="inline-flex rounded-lg border border-border p-0.5">
            {POSITIONS.map((p) => (
              <button
                key={p}
                onClick={() => setPosition(p)}
                className={cn(
                  "rounded-md px-2.5 py-1 text-xs font-medium transition",
                  position === p
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {p}
              </button>
            ))}
          </div>
          {gameweeks.length > 0 && (
            <select
              value={gameweek ?? gameweeks[0]}
              onChange={(e) => onGameweekChange(Number(e.target.value))}
              className="rounded-md border border-border bg-background px-2 py-1 font-mono text-xs outline-none focus:border-primary"
              aria-label="Gameweek"
            >
              {gameweeks.map((g) => (
                <option key={g} value={g}>GW{g}</option>
              ))}
            </select>
          )}
        </div>
      </div>

      {/* legend */}
      <div className="flex items-center gap-4 px-4 pt-3 font-mono text-[10px] text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2 w-2 bg-positive" /> xG — goal probability
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2 w-2 bg-sky-400" /> xA — assist probability
        </span>
      </div>

      {ranked.length === 0 ? (
        <p className="px-4 py-12 text-center text-xs text-muted-foreground">
          No goal-involvement data for this selection.
        </p>
      ) : (
        <ul className="space-y-1 p-4">
          {ranked.map((row, index) => (
            <li key={row.player_id} className="grid grid-cols-[18px_minmax(0,110px)_1fr_44px] items-center gap-2">
              <span className="text-right font-mono text-[10px] text-muted-foreground">
                {index + 1}
              </span>
              <span className="flex min-w-0 items-center gap-2 text-xs font-medium" title={`${row.name}${row.team ? ` · ${row.team}` : ""}`}>
                <TeamKit teamCode={row.team} size={18} />
                <span className="truncate">{row.name}</span>
              </span>
              <div
                className="flex h-3.5 overflow-hidden rounded-sm bg-muted"
                title={`xG ${row.xg.toFixed(2)} · xA ${row.xa.toFixed(2)}`}
              >
                <div
                  className="bg-positive"
                  style={{ width: `${(row.xg / max) * 100}%` }}
                />
                <div
                  className="bg-sky-400"
                  style={{ width: `${(row.xa / max) * 100}%` }}
                />
              </div>
              <span className="text-right font-mono text-xs tabular-nums text-foreground">
                {row.xgi.toFixed(2)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
