"use client";

import { useMemo, useState } from "react";
import { Search, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { TeamKit } from "@/components/ui/TeamKit";
import type { ProjectionRow } from "@/lib/validations/projections";

const POSITIONS = ["All", "GK", "DEF", "MID", "FWD"] as const;
type PositionTab = (typeof POSITIONS)[number];

const TOP_N = 50;
const SORT_OPTIONS = ["xgi", "xg", "xa", "xpts"] as const;
type SortField = (typeof SORT_OPTIONS)[number];

interface RadarRow {
  player_id: number;
  name: string;
  team: string | null;
  position: string | null;
  xg: number;
  xa: number;
  xpts: number;
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
  const [sort, setSort] = useState<SortField>("xgi");
  const [search, setSearch] = useState("");
  const [hiddenTeams, setHiddenTeams] = useState<Set<string>>(new Set());
  const [showXg, setShowXg] = useState(true);
  const [showXa, setShowXa] = useState(true);

  // All unique teams for the filter
  const allTeams = useMemo(() => {
    const teams = new Set<string>();
    rows.forEach((r) => { if (r.team_short) teams.add(r.team_short); });
    return [...teams].sort();
  }, [rows]);

  const ranked = useMemo<RadarRow[]>(() => {
    const q = search.trim().toLowerCase();
    const scoped = rows.filter((r) => {
      if (gameweek !== null && r.gameweek_id !== gameweek) return false;
      if (position !== "All" && (r.position ?? "").toUpperCase() !== position) return false;
      if (r.team_short && hiddenTeams.has(r.team_short)) return false;
      if (q && !(r.web_name ?? "").toLowerCase().includes(q) && !(r.team_short ?? "").toLowerCase().includes(q)) return false;
      return true;
    });

    const mapped = scoped.flatMap((r) => {
      const xg = typeof r.xg === "number" && Number.isFinite(r.xg) ? r.xg : 0;
      const xa = typeof r.xa === "number" && Number.isFinite(r.xa) ? r.xa : 0;
      const xpts = typeof r.xpts === "number" && Number.isFinite(r.xpts) ? r.xpts : 0;
      if (xg + xa <= 0 || typeof r.player_id !== "number") return [];
      return [{ player_id: r.player_id, name: r.web_name ?? "—", team: r.team_short ?? null,
                position: r.position ?? null, xg, xa, xpts, xgi: xg + xa }];
    });

    return mapped.sort((a, b) => b[sort] - a[sort]).slice(0, TOP_N);
  }, [rows, gameweek, position, search, hiddenTeams, sort]);

  const maxVal = ranked.length > 0 ? Math.max(...ranked.map(r =>
    sort === "xpts" ? r.xpts : (showXg && showXa ? r.xgi : showXg ? r.xg : r.xa)
  )) : 1;

  return (
    <div className="border border-border bg-card">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
        <div>
          <h3 className="text-sm font-semibold">Goal Involvement Radar</h3>
          <p className="text-[11px] text-muted-foreground">
            Projected xG + xA — top {TOP_N}, GW {gameweek ?? "—"}.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {/* Search */}
          <div className="relative">
            <Search className="absolute left-2 top-1/2 h-3 w-3 -translate-y-1/2 text-muted-foreground" aria-hidden />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search player…"
              className="h-7 w-40 rounded-md border border-border bg-background pl-6 pr-2 text-xs outline-none focus:border-primary"
            />
            {search && (
              <button onClick={() => setSearch("")} className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground">
                <X className="h-3 w-3" />
              </button>
            )}
          </div>

          {/* Sort */}
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as SortField)}
            className="h-7 rounded-md border border-border bg-background px-2 text-xs outline-none focus:border-primary"
          >
            <option value="xgi">Sort: xGI</option>
            <option value="xg">Sort: xG</option>
            <option value="xa">Sort: xA</option>
            <option value="xpts">Sort: xPts</option>
          </select>

          {/* Position filter */}
          <div className="inline-flex rounded-md border border-border p-0.5">
            {POSITIONS.map((p) => (
              <button key={p} onClick={() => setPosition(p)}
                className={cn("rounded px-2 py-0.5 text-xs font-medium transition",
                  position === p ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")}>
                {p}
              </button>
            ))}
          </div>

          {/* GW */}
          {gameweeks.length > 0 && (
            <select value={gameweek ?? gameweeks[0]}
              onChange={(e) => onGameweekChange(Number(e.target.value))}
              className="h-7 rounded-md border border-border bg-background px-2 font-mono text-xs outline-none focus:border-primary"
              aria-label="Gameweek">
              {gameweeks.map((g) => <option key={g} value={g}>GW{g}</option>)}
            </select>
          )}
        </div>
      </div>

      {/* Column toggles + team filter */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/50 px-4 py-1.5 text-[10px]">
        <div className="flex items-center gap-3 text-muted-foreground">
          <label className="flex cursor-pointer items-center gap-1">
            <input type="checkbox" checked={showXg} onChange={(e) => setShowXg(e.target.checked)} className="h-3 w-3" />
            <span className="inline-block h-2 w-2 bg-positive" /> xG
          </label>
          <label className="flex cursor-pointer items-center gap-1">
            <input type="checkbox" checked={showXa} onChange={(e) => setShowXa(e.target.checked)} className="h-3 w-3" />
            <span className="inline-block h-2 w-2 bg-sky-400" /> xA
          </label>
        </div>
        {/* Hidden team chips */}
        {hiddenTeams.size > 0 && (
          <div className="flex flex-wrap items-center gap-1">
            {[...hiddenTeams].map((t) => (
              <button key={t} onClick={() => setHiddenTeams((s) => { const n = new Set(s); n.delete(t); return n; })}
                className="inline-flex items-center gap-0.5 rounded border border-border bg-muted px-1.5 py-0.5 text-muted-foreground hover:text-foreground">
                {t} <X className="h-2.5 w-2.5" />
              </button>
            ))}
            <button onClick={() => setHiddenTeams(new Set())} className="text-[10px] text-muted-foreground underline hover:text-foreground">
              Show all
            </button>
          </div>
        )}
        {/* Team filter */}
        {allTeams.length > 0 && hiddenTeams.size === 0 && (
          <div className="flex flex-wrap gap-1">
            {allTeams.map((t) => (
              <button key={t} onClick={() => setHiddenTeams((s) => new Set([...s, t]))}
                title={`Hide ${t}`}
                className="rounded border border-border px-1 py-0.5 text-[10px] text-muted-foreground hover:bg-muted hover:text-foreground">
                {t}
              </button>
            ))}
          </div>
        )}
      </div>

      {ranked.length === 0 ? (
        <p className="px-4 py-12 text-center text-xs text-muted-foreground">
          No goal-involvement data for this selection.
        </p>
      ) : (
        <ul className="space-y-1 p-4">
          {ranked.map((row, i) => {
            const barTotal = sort === "xpts" ? row.xpts
              : (showXg && showXa ? row.xgi : showXg ? row.xg : row.xa);
            const pct = (v: number) => `${(v / maxVal) * 100}%`;
            return (
              <li key={row.player_id}
                className="grid grid-cols-[20px_minmax(0,1fr)_1fr_52px] items-center gap-2">
                <span className="text-right font-mono text-[10px] text-muted-foreground">{i + 1}</span>
                <button
                  onClick={() => row.team && setHiddenTeams((s) => new Set([...s, row.team!]))}
                  title={`Hide ${row.team ?? row.name}`}
                  className="flex min-w-0 items-center gap-1.5 text-xs font-medium hover:opacity-70">
                  <TeamKit teamCode={row.team} size={16} />
                  <span className="truncate">{row.name}</span>
                </button>
                <div className="flex h-3.5 overflow-hidden rounded-sm bg-muted"
                  title={`xG ${row.xg.toFixed(2)} · xA ${row.xa.toFixed(2)} · xPts ${row.xpts.toFixed(2)}`}>
                  {sort === "xpts" ? (
                    <div className="bg-amber-400" style={{ width: pct(row.xpts) }} />
                  ) : (
                    <>
                      {showXg && <div className="bg-positive" style={{ width: pct(row.xg) }} />}
                      {showXa && <div className="bg-sky-400" style={{ width: pct(row.xa) }} />}
                    </>
                  )}
                </div>
                <span className="text-right font-mono text-xs tabular-nums text-foreground">
                  {sort === "xpts" ? row.xpts.toFixed(2)
                    : (showXg && showXa ? row.xgi : showXg ? row.xg : row.xa).toFixed(2)}
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
