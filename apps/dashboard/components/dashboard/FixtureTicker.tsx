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
import ClubMark from "@/components/dashboard/ClubMark";
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
  /** Full club name from `teams.name`; drives the club mark and the label. */
  fullName: string;
  fixtures: Map<number, TeamCell[]>;
};

type SelectedFixture = TeamCell & {
  teamId: number;
  teamName: string;
  position: { top: number; left: number };
};

type HoveredFixture = {
  fixtureId: number;
  targetTeamId: number;
  currentFdr: FdrValue | null;
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
    fullName: string | null | undefined,
    gameweek: number | null,
    cell: TeamCell,
  ) => {
    if (gameweek === null) return;
    const row = teams.get(teamId) ?? {
      id: teamId,
      shortName: teamName?.toUpperCase() || `TEAM ${teamId}`,
      fullName: fullName || teamName || `Team ${teamId}`,
      fixtures: new Map<number, TeamCell[]>(),
    };
    const fixturesForGameweek = row.fixtures.get(gameweek) ?? [];
    fixturesForGameweek.push(cell);
    row.fixtures.set(gameweek, fixturesForGameweek);
    teams.set(teamId, row);
  };

  for (const fixture of fixtures) {
    add(fixture.team_h_id, fixture.team_h_short_name, fixture.team_h_name, fixture.gameweek, {
      fixture,
      opponentId: fixture.team_a_id,
      opponent: fixture.team_a_short_name?.toUpperCase() || `TEAM ${fixture.team_a_id}`,
      isHome: true,
      fdr: fixture.team_h_fdr,
    });
    add(fixture.team_a_id, fixture.team_a_short_name, fixture.team_a_name, fixture.gameweek, {
      fixture,
      opponentId: fixture.team_h_id,
      opponent: fixture.team_h_short_name?.toUpperCase() || `TEAM ${fixture.team_h_id}`,
      isHome: false,
      fdr: fixture.team_a_fdr,
    });
  }

  return [...teams.values()].sort((left, right) =>
    left.fullName.localeCompare(right.fullName),
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

function updateSingleFixtureFdr(
  fixtures: FdrFixture[],
  fixtureId: number,
  targetTeamId: number,
  value: FdrValue | null,
): FdrFixture[] {
  return fixtures.map((fixture) => {
    if (fixture.fixture_id !== fixtureId) return fixture;
    if (fixture.team_h_id === targetTeamId) {
      return { ...fixture, team_h_fdr: value };
    }
    if (fixture.team_a_id === targetTeamId) {
      return { ...fixture, team_a_fdr: value };
    }
    return fixture;
  });
}

function currentFixtureFdr(
  fixtures: FdrFixture[],
  fixtureId: number,
  targetTeamId: number,
): FdrValue | null {
  const fixture = fixtures.find((candidate) => candidate.fixture_id === fixtureId);
  if (!fixture) return null;
  if (fixture.team_h_id === targetTeamId) return fixture.team_h_fdr;
  if (fixture.team_a_id === targetTeamId) return fixture.team_a_fdr;
  return null;
}

function isTypingTarget(element: Element | null): boolean {
  return (
    element instanceof HTMLInputElement ||
    element instanceof HTMLTextAreaElement ||
    element instanceof HTMLSelectElement ||
    (element instanceof HTMLElement && element.isContentEditable)
  );
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
  const [hoveredFixture, setHoveredFixture] = useState<HoveredFixture | null>(null);

  /**
   * Gameweek window. The grid is 38 columns wide, which is unreadable and
   * mostly empty when a run only covers part of the season -- narrowing it is
   * the single most useful control on the ticker.
   *
   * Clamped rather than validated: dragging "From" past "To" pushes the other
   * end along instead of rejecting the input.
   */
  const [fromGameweek, setFromGameweek] = useState(1);
  const [toGameweek, setToGameweek] = useState(38);

  /**
   * Hidden columns/rows and per-gameweek sorting.
   *
   * Two separate undo stacks rather than one shared history: hiding a
   * gameweek and hiding a club are different intents, and a single Undo makes
   * you guess which one it will reverse. The live ticker splits them the same
   * way ("Undo hidden GW" / "Restore team").
   */
  const [hiddenGameweeks, setHiddenGameweeks] = useState<number[]>([]);
  const [hiddenTeams, setHiddenTeams] = useState<number[]>([]);
  const [sortByGameweek, setSortByGameweek] = useState<
    { gameweek: number; direction: "ease" | "difficulty" } | null
  >(null);
  const [openGwMenu, setOpenGwMenu] = useState<number | null>(null);


  const hideGameweek = useCallback((gameweek: number) => {
    setHiddenGameweeks((current) =>
      current.includes(gameweek) ? current : [...current, gameweek],
    );
    setOpenGwMenu(null);
  }, []);

  const undoHiddenGameweek = useCallback(() => {
    setHiddenGameweeks((current) => current.slice(0, -1));
  }, []);

  const hideTeam = useCallback((teamId: number) => {
    setHiddenTeams((current) =>
      current.includes(teamId) ? current : [...current, teamId],
    );
  }, []);

  const restoreTeam = useCallback(() => {
    setHiddenTeams((current) => current.slice(0, -1));
  }, []);

  // A dropdown that only closes by re-clicking its trigger feels broken, and
  // an open menu inside a scroll container drifts away from its column.
  useEffect(() => {
    if (openGwMenu === null) return;
    const dismiss = (event: Event) => {
      if (event instanceof KeyboardEvent && event.key !== "Escape") return;
      setOpenGwMenu(null);
    };
    document.addEventListener("keydown", dismiss);
    document.addEventListener("pointerdown", dismiss);
    return () => {
      document.removeEventListener("keydown", dismiss);
      document.removeEventListener("pointerdown", dismiss);
    };
  }, [openGwMenu]);

  const resetView = useCallback(() => {
    setHiddenGameweeks([]);
    setHiddenTeams([]);
    setSortByGameweek(null);
    setFromGameweek(1);
    setToGameweek(38);
    setOpenGwMenu(null);
  }, []);


  const visibleGameweeks = useMemo(
    () =>
      GAMEWEEKS.filter(
        (gw) => gw >= fromGameweek && gw <= toGameweek && !hiddenGameweeks.includes(gw),
      ),
    [fromGameweek, toGameweek, hiddenGameweeks],
  );

  const setFrom = useCallback((next: number) => {
    setFromGameweek(next);
    setToGameweek((current) => (next > current ? next : current));
  }, []);

  const setTo = useCallback((next: number) => {
    setToGameweek(next);
    setFromGameweek((current) => (next < current ? next : current));
  }, []);
  const [toast, setToast] = useState<string | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const fixturesRef = useRef<FdrFixture[]>([]);
  const hoveredFixtureRef = useRef<HoveredFixture | null>(null);
  const selectedRef = useRef<SelectedFixture | null>(null);
  const mutationVersionRef = useRef(new Map<string, number>());
  const toastTimerRef = useRef<number | null>(null);
  const mountedRef = useRef(true);
  const paintHoveredFixtureRef = useRef<
    (fixture: HoveredFixture, value: FdrValue | null) => void
  >(() => undefined);

  const replaceFixtures = useCallback((update: (current: FdrFixture[]) => FdrFixture[]) => {
    const next = update(fixturesRef.current);
    fixturesRef.current = next;
    if (mountedRef.current) setFixtures(next);
  }, []);

  const dismissToast = useCallback(() => {
    if (toastTimerRef.current) window.clearTimeout(toastTimerRef.current);
    toastTimerRef.current = null;
    setToast(null);
  }, []);

  const showToast = useCallback((message: string) => {
    if (!mountedRef.current) return;
    if (toastTimerRef.current) window.clearTimeout(toastTimerRef.current);
    setToast(message);
    toastTimerRef.current = window.setTimeout(() => {
      if (mountedRef.current) setToast(null);
      toastTimerRef.current = null;
    }, 5_000);
  }, []);

  const closeEditor = useCallback(() => {
    if (!selectedRef.current) return;
    selectedRef.current = null;
    setSelected(null);
    setSaveError(null);
    triggerRef.current?.focus();
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      if (toastTimerRef.current) window.clearTimeout(toastTimerRef.current);
    };
  }, []);

  const loadFixtures = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setLoadError(null);
    try {
      const response = await getFixtures({ signal });
      replaceFixtures(() => response.fixtures);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setLoadError(tickerError(error));
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [replaceFixtures]);

  useEffect(() => {
    const controller = new AbortController();
    void loadFixtures(controller.signal);
    return () => controller.abort();
  }, [loadFixtures]);

  useEffect(() => {
    if (!selected) return;
    const focus = window.requestAnimationFrame(() => dialogRef.current?.focus());
    return () => {
      window.cancelAnimationFrame(focus);
    };
  }, [selected]);

  const matrix = useMemo(() => buildMatrix(fixtures), [fixtures]);

  const openEditor = (cell: TeamCell, row: TeamRow, event: MouseEvent<HTMLButtonElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    triggerRef.current = event.currentTarget;
    setSelectedFdr(cell.fdr ?? 3);
    setSaveError(null);
    const nextSelected = {
      ...cell,
      teamId: row.id,
      teamName: row.shortName,
      position: {
        top: Math.min(window.innerHeight - 276, Math.max(16, bounds.bottom + 8)),
        left: Math.min(window.innerWidth - 304, Math.max(16, bounds.left)),
      },
    };
    selectedRef.current = nextSelected;
    setSelected(nextSelected);
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
      replaceFixtures((current) =>
        updateFixtureFdr(current, selected, selectedFdr, scope === "pair"),
      );
      closeEditor();
    } catch (error) {
      setSaveError(tickerError(error));
    } finally {
      setSaving(null);
    }
  };

  const paintHoveredFixture = useCallback((fixture: HoveredFixture, value: FdrValue | null) => {
    const mutationKey = `${fixture.fixtureId}:${fixture.targetTeamId}`;
    const nextVersion = (mutationVersionRef.current.get(mutationKey) ?? 0) + 1;
    mutationVersionRef.current.set(mutationKey, nextVersion);
    const previousFdr = currentFixtureFdr(
      fixturesRef.current,
      fixture.fixtureId,
      fixture.targetTeamId,
    );

    replaceFixtures((current) =>
      updateSingleFixtureFdr(current, fixture.fixtureId, fixture.targetTeamId, value),
    );
    const nextHovered = { ...fixture, currentFdr: value };
    hoveredFixtureRef.current = nextHovered;
    setHoveredFixture(nextHovered);

    void patchFixtureFDR({
      fixture_id: fixture.fixtureId,
      target_team_id: fixture.targetTeamId,
      fdr_override: value,
    }).catch((error: unknown) => {
      if (
        !mountedRef.current ||
        mutationVersionRef.current.get(mutationKey) !== nextVersion
      ) {
        return;
      }
      replaceFixtures((current) =>
        updateSingleFixtureFdr(
          current,
          fixture.fixtureId,
          fixture.targetTeamId,
          previousFdr,
        ),
      );
      const revertedHovered = { ...fixture, currentFdr: previousFdr };
      if (
        hoveredFixtureRef.current?.fixtureId === fixture.fixtureId &&
        hoveredFixtureRef.current.targetTeamId === fixture.targetTeamId
      ) {
        hoveredFixtureRef.current = revertedHovered;
        setHoveredFixture(revertedHovered);
      }
      showToast("FDR update failed and was reverted.");
    });
  }, [replaceFixtures, showToast]);

  /**
   * Rows actually rendered: hidden clubs removed, then ordered.
   *
   * Sorting keys on ONE gameweek's difficulty, matching how the control is
   * invoked (from that column's header). A club with no fixture that week
   * sinks to the bottom in both directions -- a blank is not "easy", and
   * floating blanks to the top of an ease sort would be actively misleading.
   */
  const orderedRows = useMemo(() => {
    const visible = matrix.filter((row) => !hiddenTeams.includes(row.id));
    if (!sortByGameweek) return visible;

    const { gameweek, direction } = sortByGameweek;
    const ratingFor = (row: TeamRow): number | null => {
      const cells = row.fixtures.get(gameweek) ?? [];
      const ratings = cells
        .map((cell) => cell.fdr)
        .filter((value): value is FdrValue => value !== null);
      if (ratings.length === 0) return null;
      // A double gameweek is judged on its average difficulty.
      return ratings.reduce((sum, value) => sum + value, 0) / ratings.length;
    };

    return [...visible].sort((left, right) => {
      const a = ratingFor(left);
      const b = ratingFor(right);
      if (a === null && b === null) return left.fullName.localeCompare(right.fullName);
      if (a === null) return 1;
      if (b === null) return -1;
      if (a === b) return left.fullName.localeCompare(right.fullName);
      return direction === "ease" ? a - b : b - a;
    });
  }, [matrix, hiddenTeams, sortByGameweek]);

  paintHoveredFixtureRef.current = paintHoveredFixture;

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.repeat || isTypingTarget(document.activeElement)) return;
      if (event.key === "Escape") {
        closeEditor();
        return;
      }

      const hovered = hoveredFixtureRef.current;
      if (!hovered) return;
      const value = /^\d$/.test(event.key) && event.key >= "1" && event.key <= "5"
        ? Number(event.key) as FdrValue
        : event.key === "0" || event.key === "Backspace" || event.key === "Delete"
          ? null
          : undefined;
      if (value === undefined) return;

      event.preventDefault();
      paintHoveredFixtureRef.current(hovered, value);
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [closeEditor]);

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
        <div className="flex flex-wrap items-center gap-3">
          {/* Gameweek window. Selects rather than a slider: managers think in
              exact gameweek numbers ("GW5 to GW10"), not in ranges. */}
          <div className="flex items-center gap-1.5 border border-zinc-800 px-2.5 py-1.5">
            <span className="text-[10px] uppercase tracking-[0.12em] text-zinc-500">
              Range
            </span>
            <label className="sr-only" htmlFor="ticker-from-gw">From gameweek</label>
            <select
              id="ticker-from-gw"
              value={fromGameweek}
              onChange={(event) => setFrom(Number(event.target.value))}
              className="bg-zinc-950 px-1 py-0.5 font-mono text-[11px] text-zinc-100 outline-none focus-visible:ring-1 focus-visible:ring-zinc-100"
            >
              {GAMEWEEKS.map((gw) => (
                <option key={gw} value={gw}>GW{gw}</option>
              ))}
            </select>
            <span aria-hidden className="text-zinc-600">→</span>
            <label className="sr-only" htmlFor="ticker-to-gw">To gameweek</label>
            <select
              id="ticker-to-gw"
              value={toGameweek}
              onChange={(event) => setTo(Number(event.target.value))}
              className="bg-zinc-950 px-1 py-0.5 font-mono text-[11px] text-zinc-100 outline-none focus-visible:ring-1 focus-visible:ring-zinc-100"
            >
              {GAMEWEEKS.map((gw) => (
                <option key={gw} value={gw}>GW{gw}</option>
              ))}
            </select>
            {(fromGameweek !== 1 || toGameweek !== 38) && (
              <button
                type="button"
                onClick={() => { setFromGameweek(1); setToGameweek(38); }}
                className="ml-1 text-[10px] uppercase tracking-wide text-zinc-500 underline-offset-2 hover:text-zinc-100 hover:underline"
              >
                All
              </button>
            )}
          </div>

          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={undoHiddenGameweek}
              disabled={hiddenGameweeks.length === 0}
              className="border border-zinc-800 px-2 py-1.5 text-[11px] text-zinc-300 transition-colors hover:border-zinc-600 hover:text-zinc-100 disabled:cursor-not-allowed disabled:opacity-40"
            >
              Undo hidden GW
              {hiddenGameweeks.length > 0 && (
                <span className="ml-1 font-mono text-zinc-500">{hiddenGameweeks.length}</span>
              )}
            </button>
            <button
              type="button"
              onClick={restoreTeam}
              disabled={hiddenTeams.length === 0}
              className="border border-zinc-800 px-2 py-1.5 text-[11px] text-zinc-300 transition-colors hover:border-zinc-600 hover:text-zinc-100 disabled:cursor-not-allowed disabled:opacity-40"
            >
              Restore team
              {hiddenTeams.length > 0 && (
                <span className="ml-1 font-mono text-zinc-500">{hiddenTeams.length}</span>
              )}
            </button>
            <button
              type="button"
              onClick={() => setSortByGameweek(null)}
              disabled={sortByGameweek === null}
              className="border border-zinc-800 px-2 py-1.5 text-[11px] text-zinc-300 transition-colors hover:border-zinc-600 hover:text-zinc-100 disabled:cursor-not-allowed disabled:opacity-40"
            >
              Original order
            </button>
            <button
              type="button"
              onClick={resetView}
              className="border border-zinc-700 px-2 py-1.5 text-[11px] font-medium text-zinc-100 transition-colors hover:border-zinc-500 hover:bg-zinc-900"
            >
              Reset
            </button>
          </div>

          <div className="text-right font-mono text-[11px] text-zinc-400">
            <p>{orderedRows.length} teams × {visibleGameweeks.length} gameweeks</p>
            <p className="mt-1 text-zinc-500">[Hover + 1–5 to paint · 0/Del to clear]</p>
          </div>
        </div>
      </header>

      <div className="group/ticker scroll-thin max-h-[calc(100vh-12rem)] overflow-auto">
        <table
            className="border-collapse text-xs"
            style={{ minWidth: `${112 + visibleGameweeks.length * 64}px` }}
          >
          <thead>
            <tr>
              <th className="sticky left-0 top-0 z-30 w-44 min-w-44 border-b border-r border-zinc-800 bg-zinc-950 px-3 py-2 text-left text-[11px] font-medium uppercase tracking-[0.12em] text-zinc-400">
                Team
              </th>
              {visibleGameweeks.map((gameweek) => {
                const sorted = sortByGameweek?.gameweek === gameweek;
                return (
                  <th
                    key={gameweek}
                    scope="col"
                    className={cn(
                      "sticky top-0 w-16 min-w-16 border-b border-r border-zinc-800 bg-zinc-950 px-1 py-2 text-center font-mono text-[11px] font-medium text-zinc-400",
                      // While its menu is open the header must out-rank the
                      // sticky team column. Both are z-20 and the column comes
                      // later in the DOM, so on a tie the column painted OVER
                      // the menu -- the options were visible but unclickable.
                      openGwMenu === gameweek ? "z-50" : "z-20",
                      // The dimming is a focus aid for the grid; inherited by
                      // the menu it just made it look broken.
                      openGwMenu === gameweek ? null : "group-hover/ticker:opacity-55",
                    )}
                  >
                    <div className="relative">
                      <button
                        type="button"
                        aria-haspopup="menu"
                        aria-expanded={openGwMenu === gameweek}
                        onPointerDown={(event) => event.stopPropagation()}
                        onClick={() =>
                          setOpenGwMenu((current) => (current === gameweek ? null : gameweek))
                        }
                        className={cn(
                          "inline-flex w-full items-center justify-center gap-1 px-1 py-0.5 transition-colors hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-zinc-100",
                          sorted && "text-zinc-100",
                        )}
                        title={`GW${gameweek} options`}
                      >
                        {gameweek}
                        <span aria-hidden className="text-[8px] leading-none text-zinc-600">
                          {sorted ? (sortByGameweek?.direction === "ease" ? "▲" : "▼") : "•••"}
                        </span>
                      </button>

                      {openGwMenu === gameweek && (
                        <div
                          role="menu"
                          aria-label={`Gameweek ${gameweek} options`}
                          onPointerDown={(event) => event.stopPropagation()}
                          className="absolute left-1/2 top-full z-50 mt-1 w-52 -translate-x-1/2 border border-zinc-600 bg-[#0b0b0d] text-left opacity-100 shadow-xl shadow-black/80"
                        >
                          <p className="border-b border-zinc-800 px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.12em] text-zinc-500">
                            GW{gameweek}
                          </p>
                          <button
                            type="button"
                            role="menuitem"
                            onClick={() => {
                              setSortByGameweek({ gameweek, direction: "ease" });
                              setOpenGwMenu(null);
                            }}
                            className="block w-full px-3 py-2 text-left transition-colors hover:bg-zinc-900"
                          >
                            <span className="block text-xs font-medium text-zinc-100">Sort by ease</span>
                            <span className="block text-[10px] text-zinc-500">Easiest first</span>
                          </button>
                          <button
                            type="button"
                            role="menuitem"
                            onClick={() => {
                              setSortByGameweek({ gameweek, direction: "difficulty" });
                              setOpenGwMenu(null);
                            }}
                            className="block w-full px-3 py-2 text-left transition-colors hover:bg-zinc-900"
                          >
                            <span className="block text-xs font-medium text-zinc-100">Sort by difficulty</span>
                            <span className="block text-[10px] text-zinc-500">Hardest first</span>
                          </button>
                          <button
                            type="button"
                            role="menuitem"
                            onClick={() => hideGameweek(gameweek)}
                            className="block w-full border-t border-zinc-800 px-3 py-2 text-left transition-colors hover:bg-zinc-900"
                          >
                            <span className="block text-xs font-medium text-rose-300">Hide gameweek</span>
                            <span className="block text-[10px] text-zinc-500">
                              Remove GW{gameweek} from ticker
                            </span>
                          </button>
                        </div>
                      )}
                    </div>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {orderedRows.map((row) => (
              <tr key={row.id} className="group/row">
                <th
                  scope="row"
                  className="sticky left-0 z-20 w-44 min-w-44 border-b border-r border-zinc-800 bg-zinc-950 px-3 py-2 text-left font-mono text-xs font-semibold text-zinc-100 transition-opacity duration-150 motion-reduce:transition-none group-hover/ticker:opacity-35 group-hover/row:!opacity-100"
                >
                  <button
                    type="button"
                    onClick={() => hideTeam(row.id)}
                    title={`Hide ${row.fullName}`}
                    className="flex w-full items-center gap-2 text-left transition-opacity hover:opacity-60 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-zinc-100"
                  >
                    <ClubMark clubName={row.fullName} code={row.shortName} />
                    <span className="truncate">{row.fullName}</span>
                  </button>
                </th>
                {visibleGameweeks.map((gameweek) => {
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
                              onMouseEnter={() => {
                                const nextHovered = {
                                  fixtureId: cell.fixture.fixture_id,
                                  targetTeamId: row.id,
                                  currentFdr: cell.fdr,
                                };
                                hoveredFixtureRef.current = nextHovered;
                                setHoveredFixture(nextHovered);
                              }}
                              onMouseLeave={() => {
                                if (
                                  hoveredFixtureRef.current?.fixtureId === cell.fixture.fixture_id &&
                                  hoveredFixtureRef.current.targetTeamId === row.id
                                ) {
                                  hoveredFixtureRef.current = null;
                                  setHoveredFixture(null);
                                }
                              }}
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

      {toast && (
        <div
          role="status"
          className="fixed bottom-4 right-4 z-50 flex max-w-sm items-center gap-3 border border-rose-700 bg-zinc-950 px-3 py-2 text-xs text-zinc-100 shadow-lg shadow-black/40"
        >
          <span>{toast}</span>
          <button
            type="button"
            onClick={dismissToast}
            className="inline-flex min-h-10 min-w-10 shrink-0 items-center justify-center text-zinc-400 transition-colors hover:text-zinc-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-zinc-100 focus-visible:ring-offset-2 focus-visible:ring-offset-zinc-950"
            aria-label="Dismiss update notification"
          >
            <X className="h-4 w-4" aria-hidden />
          </button>
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
