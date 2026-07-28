"use client";

/**
 * MinutesReviewTable -- the parsed weekly CSV, reviewable before a run.
 *
 * TanStack Table over ReviewRow (line + contract-mapped values + preflight
 * issues). Error rows are visually distinct (left accent + tinted background)
 * and carry their issues inline, so the admin can fix the CSV by line number
 * without leaving the screen. Sortable; sticky header; scrolls inside a
 * fixed-height card (the weekly template is ~700 rows).
 */

import { Fragment, useMemo, useState } from "react";
import {
  type ColumnDef,
  type SortingState,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { AlertCircle, CheckCircle2 } from "lucide-react";

import { cn } from "@/lib/utils";
import type { ReviewRow } from "@/lib/validations/minutes";

// ---------------------------------------------------------------- helpers

function text(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

function num(value: unknown, digits = 2): string {
  const parsed = typeof value === "number" ? value : Number(value);
  if (value === null || value === undefined || value === "" || Number.isNaN(parsed)) {
    return "—";
  }
  return parsed.toFixed(digits);
}

/** Numeric-aware sort over the raw candidate values. */
function byValue(key: string) {
  return (a: { original: ReviewRow }, b: { original: ReviewRow }): number => {
    const left = Number(a.original.values[key]);
    const right = Number(b.original.values[key]);
    if (Number.isNaN(left) && Number.isNaN(right)) return 0;
    if (Number.isNaN(left)) return -1;
    if (Number.isNaN(right)) return 1;
    return left - right;
  };
}

// ---------------------------------------------------------------- columns

const columns: ColumnDef<ReviewRow>[] = [
  {
    id: "line",
    header: "Line",
    accessorFn: (row) => row.line,
    cell: ({ row }) => (
      <span className="font-mono text-xs text-muted-foreground">{row.original.line}</span>
    ),
  },
  {
    id: "status",
    header: "Status",
    accessorFn: (row) => row.issues.length,
    cell: ({ row }) =>
      row.original.issues.length === 0 ? (
        <span className="inline-flex items-center gap-1 text-emerald-600">
          <CheckCircle2 className="h-3.5 w-3.5" />
          <span className="text-xs">ok</span>
        </span>
      ) : (
        <span className="inline-flex items-center gap-1 text-red-600">
          <AlertCircle className="h-3.5 w-3.5" />
          <span className="text-xs font-medium">
            {row.original.issues.length} issue{row.original.issues.length === 1 ? "" : "s"}
          </span>
        </span>
      ),
  },
  {
    id: "player",
    header: "Player",
    accessorFn: (row) => text(row.values.player ?? row.values.player_key),
    cell: ({ row }) => (
      <span className="font-medium">
        {text(row.original.values.player ?? row.original.values.player_key)}
      </span>
    ),
  },
  {
    id: "gameweek",
    header: "GW",
    accessorFn: (row) => row.values.gameweek,
    sortingFn: byValue("gameweek"),
    cell: ({ row }) => text(row.original.values.gameweek),
  },
  {
    id: "position",
    header: "Pos",
    accessorFn: (row) => text(row.values.position),
  },
  {
    id: "team",
    header: "Team",
    accessorFn: (row) => text(row.values.team),
  },
  {
    id: "start_probability",
    header: "Start",
    accessorFn: (row) => row.values.start_probability,
    sortingFn: byValue("start_probability"),
    cell: ({ row }) => (
      <span className="font-mono text-xs">{num(row.original.values.start_probability)}</span>
    ),
  },
  {
    id: "likely_minutes",
    header: "Mins",
    accessorFn: (row) => row.values.likely_minutes,
    sortingFn: byValue("likely_minutes"),
    cell: ({ row }) => (
      <span className="font-mono text-xs">{num(row.original.values.likely_minutes, 1)}</span>
    ),
  },
  {
    id: "chance_of_playing",
    header: "Chance",
    accessorFn: (row) => row.values.chance_of_playing,
    sortingFn: byValue("chance_of_playing"),
    cell: ({ row }) => (
      <span className="font-mono text-xs">{num(row.original.values.chance_of_playing, 0)}</span>
    ),
  },
];

// -------------------------------------------------------------- component

export default function MinutesReviewTable({ rows }: { rows: ReviewRow[] }) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const errorCount = useMemo(
    () => rows.filter((row) => row.issues.length > 0).length,
    [rows],
  );

  const table = useReactTable({
    data: rows,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-card">
      <div className="flex items-center justify-between border-b border-border px-4 py-2">
        <span className="text-sm font-medium">Manual minutes review</span>
        <span className="text-xs text-muted-foreground">
          {rows.length} rows
          {errorCount > 0 && (
            <span className="ml-2 font-medium text-red-600">{errorCount} with issues</span>
          )}
        </span>
      </div>

      <div className="max-h-80 overflow-auto">
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
            {table.getRowModel().rows.map((row) => (
              <Fragment key={row.id}>
                <tr
                  className={cn(
                    "border-t border-border/60 transition-colors",
                    row.original.issues.length > 0
                      ? "border-l-2 border-l-red-500 bg-red-50/70 hover:bg-red-50"
                      : "hover:bg-muted/50",
                  )}
                >
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id} className="whitespace-nowrap px-3 py-1.5">
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
                {row.original.issues.length > 0 && (
                  <tr className="border-l-2 border-l-red-500 bg-red-50/40">
                    <td colSpan={columns.length} className="px-3 pb-2 pt-0 text-xs text-red-700">
                      {row.original.issues.join(" · ")}
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && (
          <p className="p-6 text-center text-sm text-muted-foreground">
            No rows parsed yet.
          </p>
        )}
      </div>
    </div>
  );
}
