"use client";

/**
 * ProjectionsGrid -- the run response tables (weekly / player_fixture /
 * monte_carlo) in a virtualized TanStack Table.
 *
 * Type safety: response rows are validated through a Zod row schema at the
 * boundary (numbers arrive as number|null from pandas' to_json; unknown
 * extra columns pass through). No `any`, no blind casts.
 *
 * Performance: the backend returns ~700 weekly rows and several thousand
 * player_fixture rows -- rows are DOM-virtualized with @tanstack/react-virtual
 * (fixed row height, spacer-row technique), so the DOM holds ~30 rows
 * regardless of data size.
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
import { Search } from "lucide-react";
import { z } from "zod";

import { cn } from "@/lib/utils";
import type { PreviewTables } from "@/components/admin/WeeklyRunWizard";

// ------------------------------------------------------- row schema/types

/** pandas to_json emits null for NaN; ids may arrive as floats. */
const cell = z.number().nullish();

const projectionRowSchema = z
  .object({
    player_id: cell,
    event: cell,
    web_name: z.string().nullish(),
    position: z.string().nullish(),
    minutes_model_source: z.string().nullish(),
    fixtures: cell,
    expected_minutes: cell,
    xG: cell,
    xA: cell,
    xPts: cell,
    P_return: cell,
    P_haul: cell,
    MC_MeanPts: cell,
    MC_StdPts: cell,
    MC_Floor: cell,
    MC_Upside: cell,
    Bracket_15_plus: cell,
  })
  .passthrough();

export type ProjectionRow = z.infer<typeof projectionRowSchema>;

// ------------------------------------------------------------ column sets

function textColumn(id: string, header: string, pick: (row: ProjectionRow) => string | null | undefined): ColumnDef<ProjectionRow> {
  return {
    id,
    header,
    accessorFn: (row) => pick(row) ?? "",
    cell: ({ row }) => <span>{pick(row.original) ?? "—"}</span>,
  };
}

function numberColumn(
  id: string,
  header: string,
  pick: (row: ProjectionRow) => number | null | undefined,
  digits = 2,
): ColumnDef<ProjectionRow> {
  return {
    id,
    header,
    accessorFn: (row) => pick(row) ?? Number.NEGATIVE_INFINITY,
    cell: ({ row }) => {
      const value = pick(row.original);
      return (
        <span className="block text-right font-mono text-xs">
          {value === null || value === undefined ? "—" : value.toFixed(digits)}
        </span>
      );
    },
  };
}

const playerColumns: ColumnDef<ProjectionRow>[] = [
  numberColumn("event", "GW", (r) => r.event, 0),
  textColumn("web_name", "Player", (r) => r.web_name),
  textColumn("position", "Pos", (r) => r.position),
];

const COLUMN_SETS: Record<string, ColumnDef<ProjectionRow>[]> = {
  weekly: [
    ...playerColumns,
    numberColumn("fixtures", "Fx", (r) => r.fixtures, 0),
    numberColumn("expected_minutes", "xMins", (r) => r.expected_minutes, 1),
    numberColumn("xG", "xG", (r) => r.xG),
    numberColumn("xA", "xA", (r) => r.xA),
    numberColumn("xPts", "xPts", (r) => r.xPts),
    numberColumn("P_return", "P(ret)", (r) => r.P_return),
    numberColumn("P_haul", "P(haul)", (r) => r.P_haul),
  ],
  player_fixture: [
    ...playerColumns,
    numberColumn("expected_minutes", "xMins", (r) => r.expected_minutes, 1),
    numberColumn("xG", "xG", (r) => r.xG),
    numberColumn("xA", "xA", (r) => r.xA),
    numberColumn("xPts", "xPts", (r) => r.xPts),
    textColumn("minutes_model_source", "Minutes source", (r) => r.minutes_model_source),
  ],
  monte_carlo: [
    ...playerColumns,
    numberColumn("MC_MeanPts", "MC mean", (r) => r.MC_MeanPts),
    numberColumn("MC_StdPts", "MC std", (r) => r.MC_StdPts),
    numberColumn("MC_Floor", "Floor", (r) => r.MC_Floor),
    numberColumn("MC_Upside", "Upside", (r) => r.MC_Upside),
    numberColumn("Bracket_15_plus", "P(15+)", (r) => r.Bracket_15_plus),
  ],
};

const TAB_ORDER = ["weekly", "player_fixture", "monte_carlo"] as const;

// -------------------------------------------------------------- component

export default function ProjectionsGrid({ tables }: { tables: PreviewTables }) {
  const availableTabs: string[] = TAB_ORDER.filter(
    (name) => (tables[name] ?? []).length > 0,
  );
  const [activeTab, setActiveTab] = useState<string>(availableTabs[0] ?? "weekly");
  const [sorting, setSorting] = useState<SortingState>([{ id: "xPts", desc: true }]);
  const [globalFilter, setGlobalFilter] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  const tab = availableTabs.includes(activeTab) ? activeTab : (availableTabs[0] ?? "weekly");

  const rows: ProjectionRow[] = useMemo(() => {
    const raw = tables[tab] ?? [];
    return raw.flatMap((row) => {
      const parsed = projectionRowSchema.safeParse(row);
      return parsed.success ? [parsed.data] : [];
    });
  }, [tables, tab]);

  const table = useReactTable({
    data: rows,
    columns: COLUMN_SETS[tab] ?? COLUMN_SETS.weekly,
    state: { sorting, globalFilter },
    onSortingChange: setSorting,
    onGlobalFilterChange: setGlobalFilter,
    globalFilterFn: (row, _columnId, filterValue: string) =>
      (row.original.web_name ?? "")
        .toLowerCase()
        .includes(String(filterValue).toLowerCase()),
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  });

  const tableRows = table.getRowModel().rows;
  const virtualizer = useVirtualizer({
    count: tableRows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 36,
    overscan: 12,
  });
  const virtualRows = virtualizer.getVirtualItems();
  const paddingTop = virtualRows.length > 0 ? virtualRows[0].start : 0;
  const paddingBottom =
    virtualRows.length > 0
      ? virtualizer.getTotalSize() - virtualRows[virtualRows.length - 1].end
      : 0;

  const columnCount = (COLUMN_SETS[tab] ?? COLUMN_SETS.weekly).length;

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-card">
      {/* ------------------------------------------------ tabs + filter */}
      <div className="flex items-center justify-between gap-4 border-b border-border px-4 py-2">
        <div className="flex gap-1">
          {availableTabs.map((name) => (
            <button
              key={name}
              onClick={() => setActiveTab(name)}
              className={cn(
                "rounded-md px-3 py-1 text-sm",
                name === tab
                  ? "bg-foreground font-medium text-background"
                  : "text-muted-foreground hover:bg-muted",
              )}
            >
              {name} ({(tables[name] ?? []).length})
            </button>
          ))}
        </div>
        <label className="flex items-center gap-2 rounded-md border border-border px-2 py-1">
          <Search className="h-3.5 w-3.5 text-muted-foreground" />
          <input
            value={globalFilter}
            onChange={(event) => setGlobalFilter(event.target.value)}
            placeholder="Filter players…"
            className="w-40 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
        </label>
      </div>

      {/* --------------------------------------------- virtualized table */}
      <div ref={scrollRef} className="max-h-[28rem] overflow-auto">
        <table className="w-full border-collapse text-sm">
          <thead className="sticky top-0 z-10 bg-muted/95 backdrop-blur">
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id}>
                {headerGroup.headers.map((header) => (
                  <th
                    key={header.id}
                    onClick={header.column.getToggleSortingHandler()}
                    className="cursor-pointer select-none whitespace-nowrap px-3 py-2 text-left text-xs font-medium uppercase tracking-wide text-muted-foreground hover:text-foreground"
                  >
                    {flexRender(header.column.columnDef.header, header.getContext())}
                    {{ asc: " ↑", desc: " ↓" }[header.column.getIsSorted() as string] ?? ""}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {paddingTop > 0 && (
              <tr>
                <td colSpan={columnCount} style={{ height: paddingTop }} />
              </tr>
            )}
            {virtualRows.map((virtualRow) => {
              const row = tableRows[virtualRow.index];
              return (
                <tr
                  key={row.id}
                  className="border-t border-border/60 hover:bg-muted/50"
                  style={{ height: 36 }}
                >
                  {row.getVisibleCells().map((cellCtx) => (
                    <td key={cellCtx.id} className="whitespace-nowrap px-3 py-1.5">
                      {flexRender(cellCtx.column.columnDef.cell, cellCtx.getContext())}
                    </td>
                  ))}
                </tr>
              );
            })}
            {paddingBottom > 0 && (
              <tr>
                <td colSpan={columnCount} style={{ height: paddingBottom }} />
              </tr>
            )}
          </tbody>
        </table>
        {tableRows.length === 0 && (
          <p className="p-6 text-center text-sm text-muted-foreground">No rows.</p>
        )}
      </div>

      <div className="border-t border-border px-4 py-1.5 text-right text-xs text-muted-foreground">
        {tableRows.length} rows · {virtualRows.length} in DOM (virtualized)
      </div>
    </div>
  );
}
