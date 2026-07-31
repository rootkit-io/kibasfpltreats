"use client";

/**
 * ProjectionsTable -- multi-gameweek range view over the published run.
 *
 * Two modes, switched in the toolbar and mirrored into the URL:
 *   range  -- one row per player, metrics folded across the GW window
 *   single -- raw player-gameweek rows for one GW (drilldown)
 *
 * Every control (mode, window, position, sort, query) round-trips through the
 * query string, so a filtered view is a shareable link.
 *
 * Performance: aggregation is memoised per (rows, window) and the body is
 * virtualised with the spacer-row technique, so the DOM holds ~30 <tr> whether
 * the window yields 832 rows or 3,328.
 *
 * Monte Carlo columns are deliberately absent -- `published_player_week`
 * carries no simulation columns, so they would render as em-dashes forever.
 */

import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { motion, useReducedMotion } from "framer-motion";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ArrowDown, ArrowUp, Download, Link2, Search, Star, X } from "lucide-react";

import { cn } from "@/lib/utils";
import { TeamKit } from "@/components/ui/TeamKit";
import type { ProjectionRow } from "@/lib/validations/projections";
import { HEAT_CLASS, heatFor } from "@/lib/heat";
import { useWatchlist } from "@/lib/watchlist";
import { copyCurrentUrl, downloadCsv, toCsv } from "@/lib/export";
import {
  POSITIONS,
  aggregateRange,
  availableGameweeks,
  filterToWindow,
  matchesPosition,
  matchesQuery,
  type PositionFilter,
  type RangeRow,
  type ViewMode,
} from "@/lib/api/dashboard";

const ROW_HEIGHT = 38;
const OVERSCAN = 12;

type SortKey =
  | "web_name" | "team_short" | "position" | "price" | "selected_by_pct"
  | "fixtures" | "expected_minutes" | "xg" | "xa" | "xpts" | "xpts_per_gw"
  | "p_return" | "p_haul";

interface Column {
  key: SortKey;
  label: string;
  title?: string;
  numeric?: boolean;
  digits?: number;
  percent?: boolean;
  emphasis?: boolean;
  /** Hidden below this breakpoint to keep tablets usable. */
  hideUnder?: "sm" | "md" | "lg";
  rangeOnly?: boolean;
}

const COLUMNS: Column[] = [
  { key: "web_name", label: "Player" },
  { key: "team_short", label: "Team", hideUnder: "sm" },
  { key: "position", label: "Pos" },
  { key: "price", label: "£", numeric: true, digits: 1 },
  { key: "selected_by_pct", label: "Own", numeric: true, digits: 1, hideUnder: "lg" },
  { key: "fixtures", label: "Fx", numeric: true, digits: 0, hideUnder: "md" },
  { key: "expected_minutes", label: "xMins", numeric: true, digits: 0, hideUnder: "md" },
  { key: "xg", label: "xG", numeric: true, digits: 2, hideUnder: "lg" },
  { key: "xa", label: "xA", numeric: true, digits: 2, hideUnder: "lg" },
  { key: "xpts", label: "xPts", numeric: true, digits: 2, emphasis: true },
  {
    key: "xpts_per_gw", label: "xPts/GW", numeric: true, digits: 2,
    title: "Mean xPts per gameweek in the window", rangeOnly: true, hideUnder: "sm",
  },
  {
    key: "p_return", label: "P(RET)", numeric: true, percent: true,
    title: "P(at least one return across the window)", hideUnder: "md",
  },
  {
    key: "p_haul", label: "P(HAUL)", numeric: true, percent: true,
    title: "P(at least one haul across the window)", hideUnder: "lg",
  },
];

const HIDE_CLASS: Record<NonNullable<Column["hideUnder"]>, string> = {
  sm: "hidden sm:table-cell",
  md: "hidden md:table-cell",
  lg: "hidden lg:table-cell",
};

function Sparkline({
  points,
  draw,
}: {
  points: { gameweek: number; xpts: number | null }[];
  /** Gated by the parent's intro window -- see `animateIntro`. Without this
      the line would redraw on every scroll frame as rows are recycled. */
  draw: boolean;
}) {
  const values = points.map((p) => p.xpts ?? 0);
  if (values.length < 2) return null;
  const max = Math.max(...values, 0.0001);
  const min = Math.min(...values, 0);
  const span = max - min || 1;
  const w = 46;
  const h = 14;
  const d = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * w;
      const y = h - ((v - min) / span) * h;
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  /**
   * Trend must be EARNED. `last >= first` graded a flat all-zero line as
   * "rising" and painted it bright green, making a player with no projected
   * points the loudest mark on the row -- the exact opposite of the heat
   * hierarchy. Flat and all-zero series now recede instead.
   */
  const first = values[0];
  const last = values[values.length - 1];
  const allZero = values.every((v) => v === 0);
  const trend: "up" | "down" | "flat" =
    allZero || last === first ? "flat" : last > first ? "up" : "down";
  return (
    <svg width={w} height={h} className="overflow-visible" aria-hidden>
      <path
        d={d}
        fill="none"
        strokeWidth="1.25"
        // pathLength normalises the geometry to 1, so the CSS dash animation
        // works for any trajectory without measuring the path in JS.
        pathLength={1}
        className={cn(
          trend === "up"
            ? "stroke-positive"
            : trend === "down"
              ? "stroke-zinc-500"
              : "stroke-zinc-700",
          draw && "spark-draw",
        )}
      />
    </svg>
  );
}

export default function ProjectionsTable({
  rows,
  season = "current",
}: {
  rows: ProjectionRow[];
  season?: string;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  const gameweeks = useMemo(() => availableGameweeks(rows), [rows]);
  const firstGw = gameweeks[0] ?? 1;
  const lastGw = gameweeks[gameweeks.length - 1] ?? firstGw;

  // ------------------------------------------------------- URL-backed state
  const mode = (params.get("mode") === "single" ? "single" : "range") as ViewMode;
  const position = (POSITIONS.find((p) => p === params.get("pos")) ?? "ALL") as PositionFilter;
  const from = clamp(Number(params.get("from")) || firstGw, firstGw, lastGw);
  const to = clamp(Number(params.get("to")) || lastGw, firstGw, lastGw);
  const gw = clamp(Number(params.get("gw")) || firstGw, firstGw, lastGw);
  const sortKey = (params.get("sort") as SortKey) || "xpts";
  const sortDir = params.get("dir") === "asc" ? "asc" : "desc";

  // Search is local-then-synced so typing never blocks on a route update.
  const [query, setQuery] = useState(() => params.get("q") ?? "");
  const deferredQuery = useDeferredValue(query);
  const [copied, setCopied] = useState(false);

  /**
   * The mount cascade must fire ONCE. Rows are virtualised, so every scroll
   * frame remounts <tr>s at new indices -- without this gate each of those
   * would replay the entrance and the table would shimmer while scrolling.
   * After the intro window closes, `initial={false}` makes recycled rows
   * appear instantly.
   */
  const reduceMotion = useReducedMotion();
  const [introDone, setIntroDone] = useState(false);
  useEffect(() => {
    const id = setTimeout(() => setIntroDone(true), 700);
    return () => clearTimeout(id);
  }, []);
  const animateIntro = !introDone && !reduceMotion;

  const watchlist = useWatchlist(season);
  const watchedOnly = params.get("watch") === "1";

  const setParams = useCallback(
    (next: Record<string, string | null>) => {
      const sp = new URLSearchParams(params.toString());
      for (const [k, v] of Object.entries(next)) {
        if (v === null || v === "") sp.delete(k);
        else sp.set(k, v);
      }
      router.replace(`${pathname}?${sp.toString()}`, { scroll: false });
    },
    [params, pathname, router],
  );

  useEffect(() => {
    const id = setTimeout(() => {
      if ((params.get("q") ?? "") !== query) setParams({ q: query || null });
    }, 250);
    return () => clearTimeout(id);
  }, [query, params, setParams]);

  // ------------------------------------------------------------ data derive
  const windowed = useMemo(
    () => (mode === "range" ? filterToWindow(rows, from, to) : filterToWindow(rows, gw, gw)),
    [rows, mode, from, to, gw],
  );

  const tableRows = useMemo<RangeRow[]>(() => aggregateRange(windowed), [windowed]);

  const visible = useMemo(() => {
    const filtered = tableRows.filter(
      (r) =>
        matchesPosition(r, position) &&
        matchesQuery(r, deferredQuery) &&
        (!watchedOnly || watchlist.ids.has(r.player_id)),
    );
    const dir = sortDir === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      const av = a[sortKey as keyof RangeRow];
      const bv = b[sortKey as keyof RangeRow];
      if (typeof av === "string" || typeof bv === "string") {
        return String(av ?? "").localeCompare(String(bv ?? "")) * dir;
      }
      // Nulls always sink, regardless of direction.
      const an = typeof av === "number" ? av : Number.NEGATIVE_INFINITY;
      const bn = typeof bv === "number" ? bv : Number.NEGATIVE_INFINITY;
      return (an - bn) * dir;
    });
  }, [tableRows, position, deferredQuery, sortKey, sortDir, watchedOnly, watchlist.ids]);

  const columns = useMemo(
    () => COLUMNS.filter((c) => mode === "range" || !c.rangeOnly),
    [mode],
  );

  // ------------------------------------------------------------ virtualiser
  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: visible.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: OVERSCAN,
  });
  const items = virtualizer.getVirtualItems();
  const padTop = items.length ? items[0].start : 0;
  const padBottom = items.length
    ? virtualizer.getTotalSize() - items[items.length - 1].end
    : 0;

  const toggleSort = (key: SortKey) =>
    setParams(
      sortKey === key
        ? { dir: sortDir === "desc" ? "asc" : "desc" }
        : { sort: key, dir: "desc" },
    );

  const best = visible.length ? visible[0].xpts ?? 0 : 0;

  /** Exports exactly what is on screen: filtered, sorted, current columns. */
  const exportCsv = () => {
    const exportColumns = [
      { key: "web_name" as const, label: "Player" },
      { key: "team_short" as const, label: "Team" },
      { key: "position" as const, label: "Pos" },
      ...columns
        .filter((c) => c.numeric)
        .map((c) => ({ key: c.key as keyof RangeRow & string, label: c.label })),
    ];
    const scope = mode === "range" ? `GW${from}-${to}` : `GW${gw}`;
    downloadCsv(
      `kft-projections-${scope}-${position.toLowerCase()}.csv`,
      toCsv(visible, exportColumns),
    );
  };

  const share = async () => {
    if (await copyCurrentUrl()) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    }
  };

  return (
    <section className="flex flex-col gap-3">
      {/* ------------------------------------------------------- toolbar */}
      <div className="flex flex-col gap-3 border border-border bg-card p-3">
        <div className="flex flex-wrap items-center gap-2">
          {/* mode */}
          <div className="inline-flex rounded-lg border border-border p-0.5">
            {(["range", "single"] as ViewMode[]).map((m) => (
              <button
                key={m}
                onClick={() => setParams({ mode: m })}
                className={cn(
                  "rounded-md px-3 py-1 text-xs font-medium transition",
                  mode === m
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                {m === "range" ? "GW Range" : "Single GW"}
              </button>
            ))}
          </div>

          {/* window / drilldown selectors */}
          {mode === "range" ? (
            <div className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
              <span className="font-medium">GW</span>
              <GwSelect value={from} options={gameweeks} onChange={(v) => setParams({ from: String(Math.min(v, to)) })} />
              <span>→</span>
              <GwSelect value={to} options={gameweeks} onChange={(v) => setParams({ to: String(Math.max(v, from)) })} />
              <span className="ml-1 rounded bg-muted px-1.5 py-0.5 font-mono">
                {to - from + 1}&nbsp;GW
              </span>
            </div>
          ) : (
            <div className="inline-flex flex-wrap gap-1">
              {gameweeks.map((g) => (
                <button
                  key={g}
                  onClick={() => setParams({ gw: String(g) })}
                  className={cn(
                    "rounded-md border px-2 py-1 font-mono text-xs transition",
                    gw === g
                      ? "border-primary bg-primary/10 text-foreground"
                      : "border-border text-muted-foreground hover:text-foreground",
                  )}
                >
                  GW{g}
                </button>
              ))}
            </div>
          )}

          {/* search */}
          <div className="relative ml-auto min-w-[180px] flex-1 sm:max-w-xs">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search player, team, position…"
              className="w-full rounded-md border border-border bg-background py-1.5 pl-8 pr-7 text-xs outline-none focus:border-primary"
            />
            {query && (
              <button
                onClick={() => setQuery("")}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                aria-label="Clear search"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
        </div>

        {/* position tabs */}
        <div className="inline-flex flex-wrap gap-1">
          {POSITIONS.map((p) => (
            <button
              key={p}
              onClick={() => setParams({ pos: p === "ALL" ? null : p })}
              className={cn(
                "rounded-md px-2.5 py-1 text-xs font-medium transition",
                position === p
                  ? "bg-foreground text-background"
                  : "bg-muted text-muted-foreground hover:text-foreground",
              )}
            >
              {p}
            </button>
          ))}
          <button
            onClick={() => setParams({ watch: watchedOnly ? null : "1" })}
            className={cn(
              "inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium transition",
              watchedOnly
                ? "bg-amber-400 text-amber-950"
                : "bg-muted text-muted-foreground hover:text-foreground",
            )}
            title="Show only watchlisted players"
          >
            <Star className={cn("h-3 w-3", watchedOnly && "fill-current")} />
            {watchlist.hydrated ? watchlist.size : 0}
          </button>

          <span className="ml-auto flex items-center gap-2">
            <button
              onClick={exportCsv}
              className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground transition hover:text-foreground"
              title="Download the current view as CSV"
            >
              <Download className="h-3 w-3" /> CSV
            </button>
            <button
              onClick={share}
              className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground transition hover:text-foreground"
              title="Copy a link to this exact view"
            >
              <Link2 className="h-3 w-3" /> {copied ? "Copied" : "Share"}
            </button>
            <span className="font-mono text-xs text-muted-foreground">
              {visible.length.toLocaleString()} players
            </span>
          </span>
        </div>
      </div>

      {/* --------------------------------------------------------- table */}
      <div
        ref={scrollRef}
        className="scroll-thin relative max-h-[72vh] overflow-auto border border-border bg-card"
      >
        <table className="w-full border-collapse text-xs">
          <thead className="glass-header sticky top-0 z-20">
            <tr>
              <th className="w-8 px-2 py-2" aria-label="Watchlist" />
              {columns.map((c) => (
                <th
                  key={c.key}
                  title={c.title}
                  onClick={() => toggleSort(c.key)}
                  className={cn(
                    "cursor-pointer select-none whitespace-nowrap px-3 py-2 font-medium text-muted-foreground hover:text-foreground",
                    c.numeric ? "text-right" : "text-left",
                    c.hideUnder && HIDE_CLASS[c.hideUnder],
                  )}
                >
                  <span className="inline-flex items-center gap-1">
                    {c.numeric && sortIcon(sortKey === c.key, sortDir)}
                    {c.label}
                    {!c.numeric && sortIcon(sortKey === c.key, sortDir)}
                  </span>
                </th>
              ))}
              {mode === "range" && (
                <th className="hidden px-3 py-2 text-right font-medium text-muted-foreground xl:table-cell">
                  Trend
                </th>
              )}
            </tr>
          </thead>
          <tbody className="row-focus">
            {padTop > 0 && <tr style={{ height: padTop }} />}
            {items.map((v) => {
              const row = visible[v.index];
              const share = best > 0 ? (row.xpts ?? 0) / best : 0;
              return (
                <motion.tr
                  key={row.player_id}
                  // `false` skips the enter animation entirely for recycled
                  // rows; only the first paint cascades.
                  initial={animateIntro ? { opacity: 0, y: 6 } : false}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{
                    duration: 0.28,
                    ease: [0.16, 1, 0.3, 1],
                    // Cap the stagger so row 400 is not waiting 8 seconds.
                    delay: animateIntro ? Math.min(v.index, 22) * 0.016 : 0,
                  }}
                  style={{ height: ROW_HEIGHT }}
                  className="border-t border-border/60 transition-colors hover:bg-muted/50"
                >
                  <td className="px-2">
                    <button
                      onClick={() => watchlist.toggle(row.player_id)}
                      className="text-muted-foreground/40 transition hover:text-amber-400"
                      aria-label={`Toggle ${row.web_name} on watchlist`}
                    >
                      <Star
                        className={cn(
                          "h-3 w-3",
                          watchlist.ids.has(row.player_id) && "fill-amber-400 text-amber-400",
                        )}
                      />
                    </button>
                  </td>
                  {columns.map((c) => {
                    // Heat is normalised per gameweek, so the same tier means
                    // the same thing in single-GW and range mode.
                    const tier = heatFor(
                      c.key,
                      row[c.key as keyof RangeRow] as number | null,
                      row.gameweeks.length,
                    );
                    return (
                    <td
                      key={c.key}
                      className={cn(
                        "whitespace-nowrap px-3",
                        c.numeric
                          ? "text-right font-mono tabular-nums"
                          : "text-left",
                        c.hideUnder && HIDE_CLASS[c.hideUnder],
                        c.emphasis ? "font-semibold text-foreground" : "text-muted-foreground",
                        c.key === "web_name" && "font-medium text-foreground",
                        // twMerge lets this win over the base text colour.
                        tier && HEAT_CLASS[tier],
                      )}
                    >
                      {c.key === "web_name" ? (
                        <span className="inline-flex max-w-full items-center gap-2 align-middle">
                          <TeamKit teamCode={row.team_short} size={18} />
                          <span className="truncate">{row.web_name ?? "—"}</span>
                        </span>
                      ) : c.key === "xpts" ? (
                        <span className="relative inline-flex items-center justify-end gap-2">
                          <span
                            aria-hidden
                            className={cn(
                              "h-1",
                              tier === "hot"
                                ? "bg-positive"
                                : tier === "warm"
                                  ? "bg-emerald-500/70"
                                  : "bg-zinc-700",
                            )}
                            style={{ width: `${Math.max(share * 34, 2)}px` }}
                          />
                          {format(row[c.key as keyof RangeRow], c)}
                        </span>
                      ) : (
                        format(row[c.key as keyof RangeRow], c)
                      )}
                    </td>
                    );
                  })}
                  {mode === "range" && (
                    <td className="hidden px-3 text-right xl:table-cell">
                      <Sparkline points={row.trajectory} draw={animateIntro} />
                    </td>
                  )}
                </motion.tr>
              );
            })}
            {padBottom > 0 && <tr style={{ height: padBottom }} />}
          </tbody>
        </table>

        {visible.length === 0 && (
          <p className="px-4 py-12 text-center text-xs text-muted-foreground">
            No players match these filters.
          </p>
        )}
      </div>
    </section>
  );
}

// ------------------------------------------------------------------ helpers
function clamp(value: number, lo: number, hi: number): number {
  return Math.min(Math.max(value, lo), hi);
}

function sortIcon(active: boolean, dir: "asc" | "desc") {
  if (!active) return null;
  const Icon = dir === "asc" ? ArrowUp : ArrowDown;
  return <Icon className="h-3 w-3 text-primary" aria-hidden />;
}

function format(value: unknown, column: Column): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value || "—";
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  if (column.percent) return `${(value * 100).toFixed(0)}%`;
  return value.toFixed(column.digits ?? 2);
}

function GwSelect({
  value, options, onChange,
}: {
  value: number;
  options: number[];
  onChange: (value: number) => void;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(Number(e.target.value))}
      className="rounded-md border border-border bg-background px-1.5 py-1 font-mono text-xs outline-none focus:border-primary"
    >
      {options.map((o) => (
        <option key={o} value={o}>{o}</option>
      ))}
    </select>
  );
}
