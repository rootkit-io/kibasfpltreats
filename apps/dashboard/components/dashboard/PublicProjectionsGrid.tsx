"use client";

/**
 * PublicProjectionsGrid -- read-only public view of the latest published run.
 *
 * Differences from the admin ProjectionsGrid (intentional, not drift):
 * - no table tabs: the public surface exposes ONE dataset
 *   (published_player_week). Column *groups* toggle instead.
 * - no edit/override affordances; every cell is display-only.
 * - a gameweek chip filter, because the published run spans multiple GWs.
 *
 * Performance: same @tanstack/react-virtual spacer-row technique -- the DOM
 * holds ~30 <tr> regardless of row count, so 563+ rows scroll at 60fps.
 */

import { useMemo, useRef, useState } from "react";
import {
  type ColumnDef,
  type SortingState,
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Search, Sparkles } from "lucide-react";

import { cn } from "@/lib/utils";
import { TeamKit } from "@/components/ui/TeamKit";
import type { ProjectionRow } from "@/lib/validations/projections";

const ROW_HEIGHT = 40;

// ------------------------------------------------------------ column defs

function textColumn(
  id: string,
  header: string,
  pick: (row: ProjectionRow) => string | null | undefined,
  className?: string,
): ColumnDef<ProjectionRow> {
  return {
    id,
    header,
    accessorFn: (row) => pick(row) ?? "",
    cell: ({ row }) => (
      <span className={cn("block truncate", className)}>
        {pick(row.original) ?? "—"}
      </span>
    ),
  };
}

function numberColumn(
  id: string,
  header: string,
  pick: (row: ProjectionRow) => number | null | undefined,
  { digits = 2, percent = false, emphasis = false } = {},
): ColumnDef<ProjectionRow> {
  return {
    id,
    header,
    // Nulls sort last in both directions by sinking them to -Infinity.
    accessorFn: (row) => pick(row) ?? Number.NEGATIVE_INFINITY,
    cell: ({ row }) => {
      const value = pick(row.original);
      const text =
        value === null || value === undefined
          ? "—"
          : percent
            ? `${(value * 100).toFixed(0)}%`
            : value.toFixed(digits);
      return (
        <span
          className={cn(
            "block text-right font-mono text-xs tabular-nums",
            emphasis && "font-semibold text-foreground",
            !emphasis && "text-muted-foreground",
          )}
        >
          {text}
        </span>
      );
    },
  };
}

const IDENTITY_COLUMNS: ColumnDef<ProjectionRow>[] = [
  numberColumn("gameweek_id", "GW", (r) => r.gameweek_id, { digits: 0 }),
  {
    id: "web_name",
    header: "Player",
    accessorFn: (row) => row.web_name ?? "",
    cell: ({ row }) => (
      <span className="flex min-w-0 items-center gap-2 font-medium text-foreground">
        <TeamKit teamCode={row.original.team_short} size={18} />
        <span className="truncate">{row.original.web_name ?? "—"}</span>
      </span>
    ),
  },
  textColumn("team_short", "Team", (r) => r.team_short),
  textColumn("position", "Pos", (r) => r.position),
  numberColumn("price", "£", (r) => r.price, { digits: 1 }),
];

const PROJECTION_COLUMNS: ColumnDef<ProjectionRow>[] = [
  numberColumn("fixtures_in_week", "Fx", (r) => r.fixtures_in_week, { digits: 0 }),
  numberColumn("expected_minutes", "xMins", (r) => r.expected_minutes, { digits: 0 }),
  numberColumn("xg", "xG", (r) => r.xg),
  numberColumn("xa", "xA", (r) => r.xa),
  numberColumn("xpts", "xPts", (r) => r.xpts, { emphasis: true }),
  numberColumn("p_return", "P(ret)", (r) => r.p_return, { percent: true }),
  numberColumn("p_haul", "P(haul)", (r) => r.p_haul, { percent: true }),
];

const SIMULATION_COLUMNS: ColumnDef<ProjectionRow>[] = [
  numberColumn("mc_meanpts", "MC mean", (r) => r.mc_meanpts),
  numberColumn("mc_stdpts", "MC std", (r) => r.mc_stdpts),
  numberColumn("mc_floor", "Floor", (r) => r.mc_floor),
  numberColumn("mc_upside", "Upside", (r) => r.mc_upside),
  numberColumn("bracket_15_plus", "P(15+)", (r) => r.bracket_15_plus, { percent: true }),
];

type ViewMode = "projections" | "simulations";

// -------------------------------------------------------------- component

export default function PublicProjectionsGrid({
  rows,
  hasSimulations,
}: {
  rows: ProjectionRow[];
  hasSimulations: boolean;
}) {
  const [view, setView] = useState<ViewMode>("projections");
  const [gameweek, setGameweek] = useState<number | null>(null);
  const [sorting, setSorting] = useState<SortingState>([
    { id: "xpts", desc: true },
  ]);
  const [globalFilter, setGlobalFilter] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  /** GWs present in the published run, for the chip filter. */
  const gameweeks = useMemo(() => {
    const seen = new Set<number>();
    for (const row of rows) {
      if (typeof row.gameweek_id === "number") seen.add(row.gameweek_id);
    }
    return [...seen].sort((a, b) => a - b);
  }, [rows]);

  const visibleRows = useMemo(
    () =>
      gameweek === null
        ? rows
        : rows.filter((row) => row.gameweek_id === gameweek),
    [rows, gameweek],
  );

  const columns = useMemo(
    () => [
      ...IDENTITY_COLUMNS,
      ...(view === "simulations" && hasSimulations
        ? SIMULATION_COLUMNS
        : PROJECTION_COLUMNS),
    ],
    [view, hasSimulations],
  );

  const table = useReactTable({
    data: visibleRows,
    columns,
    state: { sorting, globalFilter },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    globalFilterFn: (row, _columnId, filterValue: string) => {
      const needle = String(filterValue).toLowerCase();
      return (
        (row.original.web_name ?? "").toLowerCase().includes(needle) ||
        (row.original.team_short ?? "").toLowerCase().includes(needle)
      );
    },
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  });

  const tableRows = table.getRowModel().rows;
  const virtualizer = useVirtualizer({
    count: tableRows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 12,
  });
  const virtualRows = virtualizer.getVirtualItems();
  const paddingTop = virtualRows.length > 0 ? virtualRows[0].start : 0;
  const paddingBottom =
    virtualRows.length > 0
      ? virtualizer.getTotalSize() - virtualRows[virtualRows.length - 1].end
      : 0;

  return (
    <section className="overflow-hidden rounded-xl border border-border bg-card shadow-sm">
      {/* ------------------------------------------------------- toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border bg-muted/30 px-4 py-3">
        <div className="flex flex-wrap items-center gap-2">
          {/* column-group switch */}
          <div className="flex rounded-lg border border-border bg-background p-0.5">
            {(["projections", "simulations"] as const).map((mode) => {
              const disabled = mode === "simulations" && !hasSimulations;
              return (
                <button
                  key={mode}
                  type="button"
                  onClick={() => setView(mode)}
                  disabled={disabled}
                  title={disabled ? "This run was executed without Monte Carlo" : undefined}
                  className={cn(
                    "rounded-md px-3 py-1 text-xs font-medium capitalize transition-colors",
                    view === mode && !disabled
                      ? "bg-foreground text-background"
                      : "text-muted-foreground hover:text-foreground",
                    disabled && "cursor-not-allowed opacity-40 hover:text-muted-foreground",
                  )}
                >
                  {mode}
                </button>
              );
            })}
          </div>

          {/* gameweek chips */}
          {gameweeks.length > 1 && (
            <div className="flex flex-wrap items-center gap-1">
              <button
                type="button"
                onClick={() => setGameweek(null)}
                className={cn(
                  "rounded-md px-2 py-1 text-xs font-medium",
                  gameweek === null
                    ? "bg-foreground text-background"
                    : "text-muted-foreground hover:bg-muted",
                )}
              >
                All
              </button>
              {gameweeks.map((gw) => (
                <button
                  key={gw}
                  type="button"
                  onClick={() => setGameweek(gw)}
                  className={cn(
                    "rounded-md px-2 py-1 text-xs font-mono",
                    gameweek === gw
                      ? "bg-foreground text-background"
                      : "text-muted-foreground hover:bg-muted",
                  )}
                >
                  GW{gw}
                </button>
              ))}
            </div>
          )}
        </div>

        <label className="flex items-center gap-2 rounded-lg border border-border bg-background px-2.5 py-1.5">
          <Search className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
          <input
            value={globalFilter}
            onChange={(event) => setGlobalFilter(event.target.value)}
            placeholder="Search player or team…"
            aria-label="Search player or team"
            className="w-44 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
        </label>
      </div>

      {/* -------------------------------------------- virtualized table */}
      <div ref={scrollRef} className="max-h-[34rem] overflow-auto">
        <table className="w-full border-collapse text-sm">
          <thead className="sticky top-0 z-10 bg-card/95 backdrop-blur">
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id} className="border-b border-border">
                {headerGroup.headers.map((header) => {
                  const sorted = header.column.getIsSorted();
                  return (
                    <th
                      key={header.id}
                      onClick={header.column.getToggleSortingHandler()}
                      aria-sort={
                        sorted === "asc"
                          ? "ascending"
                          : sorted === "desc"
                            ? "descending"
                            : "none"
                      }
                      className="cursor-pointer select-none whitespace-nowrap px-3 py-2.5 text-left text-[11px] font-semibold uppercase tracking-wider text-muted-foreground transition-colors hover:text-foreground"
                    >
                      {flexRender(header.column.columnDef.header, header.getContext())}
                      <span className="ml-1 text-foreground">
                        {sorted === "asc" ? "↑" : sorted === "desc" ? "↓" : ""}
                      </span>
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {paddingTop > 0 && (
              <tr>
                <td colSpan={columns.length} style={{ height: paddingTop }} />
              </tr>
            )}
            {virtualRows.map((virtualRow) => {
              const row = tableRows[virtualRow.index];
              return (
                <tr
                  key={row.id}
                  className="border-b border-border/50 transition-colors hover:bg-muted/40"
                  style={{ height: ROW_HEIGHT }}
                >
                  {row.getVisibleCells().map((cellCtx) => (
                    <td key={cellCtx.id} className="whitespace-nowrap px-3 py-2">
                      {flexRender(cellCtx.column.columnDef.cell, cellCtx.getContext())}
                    </td>
                  ))}
                </tr>
              );
            })}
            {paddingBottom > 0 && (
              <tr>
                <td colSpan={columns.length} style={{ height: paddingBottom }} />
              </tr>
            )}
          </tbody>
        </table>
        {tableRows.length === 0 && (
          <p className="p-10 text-center text-sm text-muted-foreground">
            No players match this filter.
          </p>
        )}
      </div>

      {/* -------------------------------------------------------- footer */}
      <div className="flex items-center justify-between gap-2 border-t border-border bg-muted/30 px-4 py-2 text-xs text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <Sparkles className="h-3 w-3" aria-hidden />
          {gameweek === null ? "All gameweeks" : `Gameweek ${gameweek}`}
        </span>
        <span className="font-mono tabular-nums">
          {tableRows.length.toLocaleString()} rows · {virtualRows.length} in DOM
        </span>
      </div>
    </section>
  );
}
