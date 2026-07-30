"use client";

/**
 * FixtureTicker -- clubs x gameweeks difficulty grid.
 *
 * Ports the live site's interaction set: sort by a gameweek's difficulty,
 * hide gameweeks or clubs, override a cell's FDR, A-Z / easiest sorting,
 * General / Attack / Defense modes, and undo.
 *
 * Every mutation pushes onto an undo stack so a misclick during a planning
 * session is one keystroke away from being reverted.
 */

import { useMemo, useState } from "react";
import { RotateCcw, Undo2, X } from "lucide-react";

import { cn } from "@/lib/utils";
import {
  FDR_CLASS,
  bandDifficulty,
  runDifficulty,
  toTeamFixtures,
  type DifficultyMode,
  type FixtureRow,
} from "@/lib/api/fixtures";

type SortMode = "az" | "easiest" | "hardest" | { gameweek: number };

interface Snapshot {
  hiddenTeams: string[];
  hiddenGameweeks: number[];
  overrides: [string, number][];
  sort: SortMode;
}

const MODES: { key: DifficultyMode; label: string }[] = [
  { key: "general", label: "General" },
  { key: "attack", label: "Attack" },
  { key: "defense", label: "Defense" },
];

export default function FixtureTicker({ fixtures }: { fixtures: FixtureRow[] }) {
  const [mode, setMode] = useState<DifficultyMode>("general");
  const [hiddenTeams, setHiddenTeams] = useState<Set<string>>(new Set());
  const [hiddenGameweeks, setHiddenGameweeks] = useState<Set<number>>(new Set());
  const [overrides, setOverrides] = useState<Map<string, number>>(new Map());
  const [sort, setSort] = useState<SortMode>("az");
  const [undoStack, setUndoStack] = useState<Snapshot[]>([]);

  const teamFixtures = useMemo(() => toTeamFixtures(fixtures), [fixtures]);
  const bands = useMemo(() => bandDifficulty(teamFixtures, mode), [teamFixtures, mode]);

  const allGameweeks = useMemo(
    () => [...new Set(teamFixtures.map((f) => f.gameweek))].sort((a, b) => a - b),
    [teamFixtures],
  );
  const allTeams = useMemo(
    () => [...new Set(teamFixtures.map((f) => f.team))].sort((a, b) => a.localeCompare(b)),
    [teamFixtures],
  );

  const visibleGameweeks = allGameweeks.filter((g) => !hiddenGameweeks.has(g));

  /** Team -> gameweek -> fixtures (a DGW yields more than one). */
  const grid = useMemo(() => {
    const map = new Map<string, Map<number, typeof teamFixtures>>();
    for (const f of teamFixtures) {
      let byGw = map.get(f.team);
      if (!byGw) map.set(f.team, (byGw = new Map()));
      const cell = byGw.get(f.gameweek);
      if (cell) cell.push(f);
      else byGw.set(f.gameweek, [f]);
    }
    return map;
  }, [teamFixtures]);

  const snapshot = (): Snapshot => ({
    hiddenTeams: [...hiddenTeams],
    hiddenGameweeks: [...hiddenGameweeks],
    overrides: [...overrides],
    sort,
  });
  const push = () => setUndoStack((s) => [...s.slice(-19), snapshot()]);

  const undo = () => {
    const previous = undoStack[undoStack.length - 1];
    if (!previous) return;
    setHiddenTeams(new Set(previous.hiddenTeams));
    setHiddenGameweeks(new Set(previous.hiddenGameweeks));
    setOverrides(new Map(previous.overrides));
    setSort(previous.sort);
    setUndoStack((s) => s.slice(0, -1));
  };

  const reset = () => {
    push();
    setHiddenTeams(new Set());
    setHiddenGameweeks(new Set());
    setOverrides(new Map());
    setSort("az");
  };

  const teams = useMemo(() => {
    const shown = allTeams.filter((t) => !hiddenTeams.has(t));
    if (sort === "az") return shown;
    if (typeof sort === "object") {
      return [...shown].sort(
        (a, b) =>
          (overrides.get(`${a}:${sort.gameweek}`) ?? bands.get(`${a}:${sort.gameweek}`) ?? 9) -
          (overrides.get(`${b}:${sort.gameweek}`) ?? bands.get(`${b}:${sort.gameweek}`) ?? 9),
      );
    }
    const dir = sort === "easiest" ? 1 : -1;
    return [...shown].sort(
      (a, b) =>
        (runDifficulty(a, visibleGameweeks, bands, overrides) -
          runDifficulty(b, visibleGameweeks, bands, overrides)) * dir,
    );
  }, [allTeams, hiddenTeams, sort, bands, overrides, visibleGameweeks]);

  const cycleOverride = (team: string, gameweek: number, current: number) => {
    push();
    const next = new Map(overrides);
    const value = (current % 5) + 1; // 1..5 then wrap
    next.set(`${team}:${gameweek}`, value);
    setOverrides(next);
  };

  if (teamFixtures.length === 0) {
    return (
      <p className="border border-dashed border-border bg-card px-4 py-16 text-center text-xs text-muted-foreground">
        This published run carries no fixture-level forecasts, so the ticker has
        nothing to show.
      </p>
    );
  }

  return (
    <div className="border border-border bg-card">
      {/* ------------------------------------------------------- controls */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
        <div>
          <h3 className="text-sm font-semibold">Fixture Difficulty Ticker</h3>
          <p className="text-[11px] text-muted-foreground">
            Click a GW header to sort by it · click a cell to override its FDR ·
            click a club to hide it
          </p>
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-2">
          <div className="inline-flex rounded-lg border border-border p-0.5">
            {MODES.map((m) => (
              <button
                key={m.key}
                onClick={() => setMode(m.key)}
                className={cn(
                  "rounded-md px-2.5 py-1 text-xs font-medium transition",
                  mode === m.key
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {m.label}
              </button>
            ))}
          </div>

          <div className="inline-flex rounded-lg border border-border p-0.5">
            {([["az", "A–Z"], ["easiest", "Easiest"], ["hardest", "Hardest"]] as const).map(
              ([key, label]) => (
                <button
                  key={key}
                  onClick={() => { push(); setSort(key); }}
                  className={cn(
                    "rounded-md px-2.5 py-1 text-xs font-medium transition",
                    sort === key
                      ? "bg-foreground text-background"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {label}
                </button>
              ),
            )}
          </div>

          <button
            onClick={undo}
            disabled={undoStack.length === 0}
            className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground transition hover:text-foreground disabled:opacity-40"
          >
            <Undo2 className="h-3 w-3" /> Undo
          </button>
          <button
            onClick={reset}
            className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground transition hover:text-foreground"
          >
            <RotateCcw className="h-3 w-3" /> Reset
          </button>
        </div>
      </div>

      {/* hidden chips */}
      {(hiddenTeams.size > 0 || hiddenGameweeks.size > 0) && (
        <div className="flex flex-wrap items-center gap-1.5 border-b border-border px-4 py-2">
          <span className="text-[10px] uppercase tracking-wide text-muted-foreground">
            Hidden
          </span>
          {[...hiddenGameweeks].sort((a, b) => a - b).map((g) => (
            <button
              key={`gw${g}`}
              onClick={() => { push(); const n = new Set(hiddenGameweeks); n.delete(g); setHiddenGameweeks(n); }}
              className="rounded-md border border-border px-1.5 py-0.5 font-mono text-[10px] hover:border-primary"
            >
              GW{g} ×
            </button>
          ))}
          {[...hiddenTeams].sort().map((t) => (
            <button
              key={t}
              onClick={() => { push(); const n = new Set(hiddenTeams); n.delete(t); setHiddenTeams(n); }}
              className="rounded-md border border-border px-1.5 py-0.5 text-[10px] hover:border-primary"
            >
              {t} ×
            </button>
          ))}
          <button
            onClick={() => { push(); setHiddenTeams(new Set()); setHiddenGameweeks(new Set()); }}
            className="ml-1 text-[10px] text-primary underline-offset-2 hover:underline"
          >
            Show all
          </button>
        </div>
      )}

      {/* ------------------------------------------------------------ grid */}
      <div className="scroll-thin overflow-x-auto">
        <table className="w-full border-collapse text-xs">
          <thead className="glass-header sticky top-0 z-20">
            <tr>
              <th className="sticky left-0 z-10 bg-black/80 px-3 py-1.5 text-left font-medium text-muted-foreground backdrop-blur-md">
                Club
              </th>
              {visibleGameweeks.map((g) => (
                <th key={g} className="px-1 py-2 text-center font-medium">
                  <div className="inline-flex items-center gap-1">
                    <button
                      onClick={() => { push(); setSort({ gameweek: g }); }}
                      className={cn(
                        "rounded px-1 font-mono text-[11px] transition",
                        typeof sort === "object" && sort.gameweek === g
                          ? "text-primary"
                          : "text-muted-foreground hover:text-foreground",
                      )}
                      title={`Sort by GW${g} difficulty`}
                    >
                      GW{g}
                    </button>
                    <button
                      onClick={() => { push(); setHiddenGameweeks(new Set(hiddenGameweeks).add(g)); }}
                      className="text-muted-foreground/60 hover:text-foreground"
                      aria-label={`Hide GW${g}`}
                    >
                      <X className="h-2.5 w-2.5" />
                    </button>
                  </div>
                </th>
              ))}
              <th className="px-2 py-2 text-right font-medium text-muted-foreground">Run</th>
            </tr>
          </thead>
          <tbody>
            {teams.map((team) => (
              <tr key={team} className="border-t border-border/50">
                <td className="sticky left-0 z-10 bg-black/80 px-3 py-1 backdrop-blur-md">
                  <button
                    onClick={() => { push(); setHiddenTeams(new Set(hiddenTeams).add(team)); }}
                    className="truncate text-left font-medium hover:text-muted-foreground"
                    title={`Hide ${team}`}
                  >
                    {team}
                  </button>
                </td>
                {visibleGameweeks.map((g) => {
                  const cell = grid.get(team)?.get(g) ?? [];
                  const key = `${team}:${g}`;
                  const band = overrides.get(key) ?? bands.get(key);
                  if (cell.length === 0) {
                    return (
                      <td key={g} className="px-1 py-1">
                        <div className="rounded-md bg-muted/40 px-1 py-1 text-center font-mono text-[10px] text-muted-foreground">
                          —
                        </div>
                      </td>
                    );
                  }
                  return (
                    <td key={g} className="px-1 py-1">
                      <button
                        onClick={() => cycleOverride(team, g, band ?? 3)}
                        title={`${cell.map((c) => `${c.isHome ? "vs" : "@"} ${c.opponent}`).join(", ")} — FDR ${band ?? "?"}${overrides.has(key) ? " (overridden)" : ""}`}
                        className={cn(
                          "w-full rounded-md px-1 py-1 text-center font-mono text-[10px] transition hover:opacity-80",
                          FDR_CLASS[band ?? 3],
                          overrides.has(key) && "ring-1 ring-primary ring-offset-1 ring-offset-card",
                        )}
                      >
                        {cell
                          .map((c) => `${c.isHome ? "" : "@"}${abbrev(c.opponent)}`)
                          .join(" ")}
                      </button>
                    </td>
                  );
                })}
                <td className="px-2 text-right font-mono text-[11px] tabular-nums text-muted-foreground">
                  {runDifficulty(team, visibleGameweeks, bands, overrides)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex items-center gap-2 border-t border-border px-4 py-2 font-mono text-[10px] text-muted-foreground">
        <span>easy</span>
        {[1, 2, 3, 4, 5].map((b) => (
          <span key={b} className={cn("rounded px-1.5", FDR_CLASS[b])}>{b}</span>
        ))}
        <span>hard</span>
        <span className="ml-auto">{teams.length} clubs · {visibleGameweeks.length} GWs</span>
      </div>
    </div>
  );
}

/** "Nottingham Forest" -> "NFO"-ish: enough to read in a dense cell. */
function abbrev(name: string): string {
  const words = name.split(/\s+/).filter(Boolean);
  if (words.length === 1) return words[0].slice(0, 3).toUpperCase();
  return words.map((w) => w[0]).join("").slice(0, 3).toUpperCase();
}
