"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent,
} from "react";
import { AlertCircle, RefreshCw, X } from "lucide-react";

import { cn } from "@/lib/utils";
import {
  getFixtures,
  patchFixtureFDR,
  type FdrFixture,
  type FdrValue,
  type FixtureRow,
} from "@/lib/api/fixtures";

const GAMEWEEKS = Array.from({ length: 38 }, (_, index) => index + 1);
const FDR_VALUES: FdrValue[] = [1, 2, 3, 4, 5];

type TeamCell = {
  fixture: FdrFixture;
  opponentId: number;
  opponent: string;
  isHome: boolean;
  fdr: FdrValue | null;
};

type TeamRow = {
  id: number;
  shortName: string;
  fixtures: Map<number, TeamCell[]>;
};

type SelectedFixture = TeamCell & {
  teamId: number;
  teamName: string;
  position: { top: number; left: number };
};

function fdrClass(fdr: FdrValue | null): string {
  if (fdr === 1 || fdr === 2) return "bg-emerald-950/60 text-emerald-300";
  if (fdr === 4 || fdr === 5) return "bg-rose-950/60 text-rose-300";
  return "bg-zinc-900/60 text-zinc-300";
}

function buildMatrix(fixtures: FdrFixture[]): TeamRow[] {
  const teams = new Map<number, TeamRow>();

  const add = (
    teamId: number,
    teamName: string | null,
    gameweek: number | null,
    cell: TeamCell,
  ) => {
    if (gameweek === null) return;
    const row = teams.get(teamId) ?? {
      id: teamId,
      shortName: teamName?.toUpperCase() || `TEAM ${teamId}`,
      fixtures: new Map<number, TeamCell[]>(),
    };
    const fixturesForGameweek = row.fixtures.get(gameweek) ?? [];
    fixturesForGameweek.push(cell);
    row.fixtures.set(gameweek, fixturesForGameweek);
    teams.set(teamId, row);
  };

  for (const fixture of fixtures) {
    add(fixture.team_h_id, fixture.team_h_short_name, fixture.gameweek, {
      fixture,
      opponentId: fixture.team_a_id,
      opponent: fixture.team_a_short_name?.toUpperCase() || `TEAM ${fixture.team_a_id}`,
      isHome: true,
      fdr: fixture.team_h_fdr,
    });
    add(fixture.team_a_id, fixture.team_a_short_name, fixture.gameweek, {
      fixture,
      opponentId: fixture.team_h_id,
      opponent: fixture.team_h_short_name?.toUpperCase() || `TEAM ${fixture.team_h_id}`,
      isHome: false,
      fdr: fixture.team_a_fdr,
    });
  }

  return [...teams.values()].sort((left, right) =>
    left.shortName.localeCompare(right.shortName),
  );
}

function updateFixtureFdr(
  fixtures: FdrFixture[],
  selected: SelectedFixture,
  value: FdrValue | null,
  applyToOpponent: boolean,
): FdrFixture[] {
  return fixtures.map((fixture) => {
    const isSelectedFixture = fixture.fixture_id === selected.fixture.fixture_id;
    const isPairFixture =
      (fixture.team_h_id === selected.teamId && fixture.team_a_id === selected.opponentId) ||
      (fixture.team_h_id === selected.opponentId && fixture.team_a_id === selected.teamId);
    if (!isSelectedFixture && (!applyToOpponent || !isPairFixture)) return fixture;

    if (fixture.team_h_id === selected.teamId) {
      return { ...fixture, team_h_fdr: value };
    }
    if (fixture.team_a_id === selected.teamId) {
      return { ...fixture, team_a_fdr: value };
    }
    return fixture;
  });
}

function tickerError(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "Could not update this fixture. Try again.";
}

export default function FixtureTicker({ fixtures: _legacyFixtures }: { fixtures: FixtureRow[] }) {
  const [fixtures, setFixtures] = useState<FdrFixture[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<SelectedFixture | null>(null);
  const [selectedFdr, setSelectedFdr] = useState<FdrValue | null>(3);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState<"one" | "pair" | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const dialogRef = useRef<HTMLDivElement | null>(null);

  const loadFixtures = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setLoadError(null);
    try {
      const response = await getFixtures({ signal });
      setFixtures(response.fixtures);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setLoadError(tickerError(error));
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void loadFixtures(controller.signal);
    return () => controller.abort();
  }, [loadFixtures]);

  useEffect(() => {
    if (!selected) return;
    const focus = window.requestAnimationFrame(() => dialogRef.current?.focus());
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setSelected(null);
        triggerRef.current?.focus();
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.cancelAnimationFrame(focus);
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [selected]);

  const matrix = useMemo(() => buildMatrix(fixtures), [fixtures]);

  const openEditor = (cell: TeamCell, row: TeamRow, event: MouseEvent<HTMLButtonElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    triggerRef.current = event.currentTarget;
    setSelectedFdr(cell.fdr ?? 3);
    setSaveError(null);
    setSelected({
      ...cell,
      teamId: row.id,
      teamName: row.shortName,
      position: {
        top: Math.min(window.innerHeight - 276, Math.max(16, bounds.bottom + 8)),
        left: Math.min(window.innerWidth - 304, Math.max(16, bounds.left)),
      },
    });
  };

  const closeEditor = () => {
    setSelected(null);
    setSaveError(null);
    triggerRef.current?.focus();
  };

  const saveOverride = async (scope: "one" | "pair") => {
    if (!selected) return;
    setSaving(scope);
    setSaveError(null);
    try {
      await patchFixtureFDR({
        fixture_id: selected.fixture.fixture_id,
        target_team_id: selected.teamId,
        fdr_override: selectedFdr,
        ...(scope === "pair" ? { opponent_team_id: selected.opponentId } : {}),
      });
      setFixtures((current) =>
        updateFixtureFdr(current, selected, selectedFdr, scope === "pair"),
      );
      closeEditor();
    } catch (error) {
      setSaveError(tickerError(error));
    } finally {
      setSaving(null);
    }
  };

  if (loading) {
    return <TickerSkeleton />;
  }

  if (loadError) {
    return (
      <section className="flex flex-col items-start gap-3 border border-rose-900 bg-rose-950/20 p-4">
        <div className="flex items-center gap-2 text-rose-300">
          <AlertCircle className="h-4 w-4" aria-hidden />
          <p className="text-sm font-medium">Couldn&apos;t load fixture difficulty.</p>
        </div>
        <p className="text-xs text-zinc-400">{loadError}</p>
        <button
          type="button"
          onClick={() => void loadFixtures()}
          className="inline-flex min-h-10 items-center gap-2 border border-zinc-700 px-3 text-xs font-medium text-zinc-100 transition-colors hover:border-zinc-500 hover:bg-zinc-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-200 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950"
        >
          <RefreshCw className="h-3.5 w-3.5" aria-hidden />
          Retry
        </button>
      </section>
    );
  }

  if (matrix.length === 0) {
    return (
      <section className="flex flex-col items-start gap-2 border border-zinc-800 bg-zinc-950 p-6">
        <p className="text-sm font-medium text-zinc-100">No fixtures loaded yet.</p>
        <p className="text-xs text-zinc-400">
          Import the current FPL fixture list, then return here to set difficulty overrides.
        </p>
      </section>
    );
  }

  return (
    <section className="border border-zinc-800 bg-zinc-950 text-zinc-100">
      <header className="flex flex-wrap items-end justify-between gap-3 border-b border-zinc-800 px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold tracking-wide">Fixture Difficulty Ticker</h2>
          <p className="mt-1 text-xs text-zinc-400">
            Home opponents are uppercase. Away opponents are muted lowercase. Click a fixture to edit its FDR.
          </p>
        </div>
        <span className="font-mono text-[11px] text-zinc-400">
          {matrix.length} teams × {GAMEWEEKS.length} gameweeks
        </span>
      </header>

      <div className="group/ticker scroll-thin max-h-[calc(100vh-12rem)] overflow-auto">
        <table className="min-w-[2480px] border-collapse text-xs">
          <thead>
            <tr>
              <th className="sticky left-0 top-0 z-30 min-w-28 border-b border-r border-zinc-800 bg-zinc-950 px-3 py-2 text-left text-[11px] font-medium uppercase tracking-[0.12em] text-zinc-400">
                Team
              </th>
              {GAMEWEEKS.map((gameweek) => (
                <th
                  key={gameweek}
                  scope="col"
                  className="sticky top-0 z-20 w-16 min-w-16 border-b border-r border-zinc-800 bg-zinc-950 px-1 py-2 text-center font-mono text-[11px] font-medium text-zinc-400 group-hover/ticker:opacity-55"
                >
                  {gameweek}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.map((row) => (
              <tr key={row.id} className="group/row">
                <th
                  scope="row"
                  className="sticky left-0 z-20 min-w-28 border-b border-r border-zinc-800 bg-zinc-950 px-3 py-2 text-left font-mono text-xs font-semibold text-zinc-100 transition-opacity duration-150 motion-reduce:transition-none group-hover/ticker:opacity-35 group-hover/row:!opacity-100"
                >
                  {row.shortName}
                </th>
                {GAMEWEEKS.map((gameweek) => {
                  const cells = row.fixtures.get(gameweek) ?? [];
                  return (
                    <td
                      key={gameweek}
                      className="w-16 min-w-16 border-b border-r border-zinc-800 p-0 align-stretch transition-opacity duration-150 motion-reduce:transition-none group-hover/ticker:opacity-35 group-hover/row:!opacity-100"
                    >
                      {cells.length === 0 ? (
                        <div className="flex min-h-14 items-center justify-center font-mono text-xs text-zinc-700">—</div>
                      ) : (
                        <div className="flex min-h-14 flex-col divide-y divide-zinc-800">
                          {cells.map((cell) => (
                            <button
                              key={cell.fixture.fixture_id}
                              type="button"
                              onClick={(event) => openEditor(cell, row, event)}
                              aria-label={`${row.shortName} ${cell.isHome ? "home against" : "away at"} ${cell.opponent}, FDR ${cell.fdr ?? "unavailable"}`}
                              className={cn(
                                "relative flex min-h-14 w-full flex-1 flex-col items-center justify-center px-1 py-1 text-center transition-[filter,opacity] duration-150 motion-reduce:transition-none hover:brightness-125 focus-visible:z-10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-100 focus-visible:ring-inset",
                                fdrClass(cell.fdr),
                              )}
                            >
                              <span className={cn("text-[11px] font-semibold tracking-wide", !cell.isHome && "text-zinc-400")}>
                                {cell.isHome ? cell.opponent.toUpperCase() : cell.opponent.toLowerCase()}
                              </span>
                              <span className="mt-0.5 font-mono text-xs tabular-nums">
                                {cell.fdr ?? "—"}
                              </span>
                            </button>
                          ))}
                        </div>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {selected && (
        <div
          ref={dialogRef}
          role="dialog"
          aria-labelledby="fixture-editor-title"
          tabIndex={-1}
          className="fixed z-50 w-72 border border-zinc-700 bg-zinc-950 p-4 shadow-2xl shadow-black/50 outline-none"
          style={selected.position}
        >
          <div className="flex items-start justify-between gap-3">
            <div>
              <p id="fixture-editor-title" className="text-sm font-semibold text-zinc-100">
                {selected.teamName} {selected.isHome ? "vs" : "@"} {selected.opponent}
              </p>
              <p className="mt-1 font-mono text-[11px] text-zinc-400">
                GW {selected.fixture.gameweek ?? "—"} · fixture {selected.fixture.fixture_id}
              </p>
            </div>
            <button
              type="button"
              onClick={closeEditor}
              className="inline-flex min-h-10 min-w-10 items-center justify-center border border-zinc-800 text-zinc-400 transition-colors hover:border-zinc-600 hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-200 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950"
              aria-label="Close fixture editor"
            >
              <X className="h-4 w-4" aria-hidden />
            </button>
          </div>

          <fieldset className="mt-4">
            <legend className="text-xs font-medium text-zinc-300">Difficulty rating</legend>
            <div className="mt-2 grid grid-cols-5 gap-1" role="radiogroup" aria-label="Fixture difficulty rating">
              {FDR_VALUES.map((value) => (
                <button
                  key={value}
                  type="button"
                  role="radio"
                  aria-checked={selectedFdr === value}
                  onClick={() => setSelectedFdr(value)}
                  className={cn(
                    "min-h-10 border border-zinc-700 font-mono text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-100 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950",
                    selectedFdr === value
                      ? "border-zinc-100 bg-zinc-100 text-zinc-950"
                      : "bg-zinc-950 text-zinc-300 hover:border-zinc-500",
                  )}
                >
                  {value}
                </button>
              ))}
            </div>
            <button
              type="button"
              onClick={() => setSelectedFdr(null)}
              className="mt-2 min-h-10 text-xs text-zinc-400 underline underline-offset-4 transition-colors hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-100 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950"
            >
              Clear override
            </button>
          </fieldset>

          {saveError && (
            <p role="alert" className="mt-3 border-l-2 border-rose-500 pl-2 text-xs text-rose-300">
              {saveError}
            </p>
          )}

          <div className="mt-4 grid gap-2">
            <button
              type="button"
              onClick={() => void saveOverride("one")}
              disabled={saving !== null}
              aria-busy={saving === "one"}
              className="min-h-10 border border-zinc-100 bg-zinc-100 px-3 text-xs font-medium text-zinc-950 transition-colors hover:bg-zinc-300 disabled:cursor-wait disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-100 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950"
            >
              {saving === "one" ? "Saving…" : "Apply only here"}
            </button>
            <button
              type="button"
              onClick={() => void saveOverride("pair")}
              disabled={saving !== null}
              aria-busy={saving === "pair"}
              className="min-h-10 border border-zinc-700 px-3 text-xs font-medium text-zinc-200 transition-colors hover:border-zinc-500 hover:bg-zinc-900 disabled:cursor-wait disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-100 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950"
            >
              {saving === "pair" ? "Saving…" : `Apply to all matches vs ${selected.opponent}`}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

function TickerSkeleton() {
  return (
    <section className="border border-zinc-800 bg-zinc-950 p-4" aria-busy="true" aria-label="Loading fixture difficulty">
      <div className="h-4 w-48 animate-pulse bg-zinc-800 motion-reduce:animate-none" />
      <div className="mt-4 grid grid-cols-7 gap-px border border-zinc-800 bg-zinc-800 sm:grid-cols-10">
        {Array.from({ length: 70 }, (_, index) => (
          <div key={index} className="h-14 animate-pulse bg-zinc-950 motion-reduce:animate-none" />
        ))}
      </div>
    </section>
  );
}
