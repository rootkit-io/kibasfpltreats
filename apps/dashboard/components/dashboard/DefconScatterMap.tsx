"use client";

/**
 * DefconScatterMap — defensive contributions per 90 plotted against expected
 * minutes.
 *
 * Single SVG chart. Mirrors the KFT2627 defconScatterSection layout and uses
 * the same scatter helpers as XgiScatterMap (inline here so neither module
 * imports the other).
 *
 * defcon90 is only meaningful for GK and DEF; MID and FWD dots will cluster
 * near zero by design.
 */

import { useId, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { cn } from "@/lib/utils";
import { TeamKit } from "@/components/ui/TeamKit";
import type { ProjectionRow } from "@/lib/validations/projections";

// ── Position colours (shared with XgiScatterMap) ─────────────────────────────
const POSITION_COLOR: Record<string, string> = {
  GK: "#D97706",
  DEF: "#059669",
  MID: "#2563EB",
  FWD: "#DC2626",
};
const POSITIONS = ["GK", "DEF", "MID", "FWD"] as const;

// ── SVG canvas ────────────────────────────────────────────────────────────────
const W = 900;
const H = 400;
const M = { t: 16, r: 20, b: 52, l: 62 } as const;
const CW = W - M.l - M.r;
const CH = H - M.t - M.b;

// ── Axis helpers ──────────────────────────────────────────────────────────────

function axisMax(values: number[]): number {
  const finite = values.filter(Number.isFinite);
  if (!finite.length) return 1;
  const max = Math.max(...finite);
  if (max <= 0) return 1;
  const padded = max * 1.1;
  const step =
    padded <= 1 ? 0.1
    : padded <= 5 ? 0.5
    : padded <= 20 ? 2
    : padded <= 100 ? 10
    : 50;
  return Math.round(Math.max(step, Math.ceil(padded / step) * step) * 1000) / 1000;
}

function axisTicks(max: number, steps = 4): number[] {
  const n = Math.max(2, steps);
  return Array.from({ length: n + 1 }, (_, i) =>
    Math.round((max * i / n) * 100) / 100,
  );
}

// ── Types ─────────────────────────────────────────────────────────────────────

interface DotRow {
  key: string;
  player_id: number;
  name: string;
  team: string | null;
  position: string | null;
  minutes: number;
  defcon90: number;
  xpts: number | null;
  price: number | null;
}

type SortField = "defcon90" | "minutes" | "xpts";

interface LocalHover {
  key: string;
  cx: number;
  cy: number;
  row: DotRow;
}

// ── Data normalisation ────────────────────────────────────────────────────────

function buildDotRows(rows: ProjectionRow[], gw: number): DotRow[] {
  return rows.flatMap((r) => {
    if (r.gameweek_id !== gw || typeof r.player_id !== "number") return [];
    const minutes =
      typeof r.expected_minutes === "number" && Number.isFinite(r.expected_minutes)
        ? r.expected_minutes : null;
    const defcon90 =
      typeof r.defcon90 === "number" && Number.isFinite(r.defcon90) ? r.defcon90 : null;
    // Keep rows that have minutes even without defcon90 (will sit at y=0 edge),
    // but drop rows missing minutes entirely since x-axis has no value.
    if (minutes === null || defcon90 === null) return [];
    return [{
      key: `${r.player_id}:${r.gameweek_id}`,
      player_id: r.player_id,
      name: r.web_name ?? "—",
      team: r.team_short ?? null,
      position: (r.position ?? "").toUpperCase() || null,
      minutes,
      defcon90,
      xpts: typeof r.xpts === "number" && Number.isFinite(r.xpts) ? r.xpts : null,
      price: typeof r.price === "number" && Number.isFinite(r.price) ? r.price : null,
    }];
  });
}

// ── Filter ────────────────────────────────────────────────────────────────────

interface Filters {
  positions: Set<string>;
  search: string;
  team: string;
  priceMin: number;
  priceMax: number;
}

function applyFilters(rows: DotRow[], f: Filters): DotRow[] {
  const q = f.search.trim().toLowerCase();
  return rows.filter((r) => {
    if (r.position && !f.positions.has(r.position)) return false;
    if (f.team && r.team !== f.team) return false;
    if (q && !r.name.toLowerCase().includes(q) && !(r.team ?? "").toLowerCase().includes(q))
      return false;
    if (r.price !== null) {
      if (r.price < f.priceMin || r.price > f.priceMax) return false;
    }
    return true;
  });
}

// ── Chart ─────────────────────────────────────────────────────────────────────

function ScatterChart({
  rows,
  xMax,
  yMax,
  hovered,
  onHover,
}: {
  rows: DotRow[];
  xMax: number;
  yMax: number;
  hovered: string | null;
  onHover: (key: string | null) => void;
}) {
  const [tip, setTip] = useState<LocalHover | null>(null);

  const toX = (v: number) => M.l + (Math.min(Math.max(v, 0), xMax) / xMax) * CW;
  const toY = (v: number) => M.t + CH - (Math.min(Math.max(v, 0), yMax) / yMax) * CH;

  const yTicks = axisTicks(yMax);
  const xTicks = axisTicks(xMax);

  const handleEnter = (r: DotRow, cx: number, cy: number) => {
    onHover(r.key);
    setTip({ key: r.key, cx, cy, row: r });
  };
  const handleLeave = () => { onHover(null); setTip(null); };

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        role="img"
        aria-label="Defensive contributions per 90 against expected minutes"
        style={{ display: "block" }}
        onMouseLeave={handleLeave}
      >
        {yTicks.map((t) => {
          const y = toY(t);
          return (
            <g key={`y${t}`}>
              <line x1={M.l} x2={M.l + CW} y1={y} y2={y} stroke="rgba(127,127,127,.12)" />
              <text x={M.l - 8} y={y + 4} textAnchor="end" fill="var(--muted-foreground,#888)" fontSize={11}>{t}</text>
            </g>
          );
        })}
        {xTicks.map((t) => {
          const x = toX(t);
          return (
            <g key={`x${t}`}>
              <line x1={x} x2={x} y1={M.t} y2={M.t + CH} stroke="rgba(127,127,127,.08)" />
              <text x={x} y={H - 30} textAnchor="middle" fill="var(--muted-foreground,#888)" fontSize={11}>{t}</text>
            </g>
          );
        })}
        <text x={M.l + CW / 2} y={H - 8} textAnchor="middle" fill="var(--muted-foreground,#888)" fontSize={12}>
          Expected minutes
        </text>
        <text
          x={16} y={M.t + CH / 2}
          transform={`rotate(-90 16 ${M.t + CH / 2})`}
          textAnchor="middle" fill="var(--muted-foreground,#888)" fontSize={12}
        >
          Defensive contributions per 90
        </text>

        {tip && (
          <g className="pointer-events-none">
            <line x1={M.l} x2={tip.cx} y1={tip.cy} y2={tip.cy} stroke="rgba(127,127,127,.35)" strokeDasharray="3 3" strokeWidth={1} />
            <line x1={tip.cx} x2={tip.cx} y1={tip.cy} y2={M.t + CH} stroke="rgba(127,127,127,.35)" strokeDasharray="3 3" strokeWidth={1} />
          </g>
        )}

        {rows.map((r) => {
          const cx = toX(r.minutes);
          const cy = toY(r.defcon90);
          const active = hovered === r.key;
          const color = POSITION_COLOR[r.position ?? ""] ?? "#7A8799";
          return (
            <circle
              key={r.key}
              cx={cx} cy={cy}
              r={active ? 7 : 5}
              fill={color}
              fillOpacity={active ? 1 : 0.82}
              stroke={active ? "white" : "none"}
              strokeWidth={active ? 1.5 : 0}
              className="cursor-pointer"
              onMouseEnter={() => handleEnter(r, cx, cy)}
              tabIndex={0}
              role="button"
              aria-label={`${r.name} (${r.team ?? "?"}, ${r.position ?? "?"}) · Mins ${r.minutes.toFixed(0)} · Defcon/90 ${r.defcon90.toFixed(2)}`}
              onFocus={() => handleEnter(r, cx, cy)}
              onBlur={handleLeave}
            />
          );
        })}
      </svg>

      {tip && (
        <div
          className="pointer-events-none absolute z-10 min-w-[160px] border border-border bg-black/85 p-2.5 backdrop-blur-md"
          style={{
            left: `${(tip.cx / W) * 100}%`,
            top: `${(tip.cy / H) * 100}%`,
            transform: "translate(12px, -50%)",
          }}
        >
          <p className="flex items-center gap-1.5 text-xs font-semibold text-white">
            <span
              className="h-2 w-2 shrink-0 rounded-full"
              style={{ backgroundColor: POSITION_COLOR[tip.row.position ?? ""] ?? "#7A8799" }}
            />
            {tip.row.name}
          </p>
          <p className="mt-0.5 text-[10px] text-zinc-400">
            {tip.row.team ?? "—"} · {tip.row.position ?? "—"}
            {tip.row.price !== null && ` · £${tip.row.price.toFixed(1)}`}
          </p>
          <dl className="mt-1.5 grid grid-cols-2 gap-x-3 gap-y-0.5 font-mono text-[10px]">
            <dt className="text-zinc-400">Defcon/90</dt><dd className="text-right text-white">{tip.row.defcon90.toFixed(2)}</dd>
            {tip.row.xpts !== null && <><dt className="text-zinc-400">xPts</dt><dd className="text-right text-white">{tip.row.xpts.toFixed(2)}</dd></>}
            <dt className="text-zinc-400">Mins</dt><dd className="text-right text-white">{tip.row.minutes.toFixed(0)}</dd>
          </dl>
        </div>
      )}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function DefconScatterMap({
  rows,
  gameweeks,
}: {
  rows: ProjectionRow[];
  gameweeks: number[];
}) {
  const uid = useId();
  const [gwIndex, setGwIndex] = useState(0);
  const [positions, setPositions] = useState<Set<string>>(new Set(POSITIONS));
  const [search, setSearch] = useState("");
  const [team, setTeam] = useState("");
  const [priceMin, setPriceMin] = useState(3.0);
  const [priceMax, setPriceMax] = useState(16.0);
  const [hovered, setHovered] = useState<string | null>(null);
  const [sort, setSort] = useState<SortField>("defcon90");

  const safeIndex = Math.min(gwIndex, Math.max(0, gameweeks.length - 1));
  const gw = gameweeks[safeIndex] ?? null;

  const allRows = useMemo(
    () => (gw === null ? [] : buildDotRows(rows, gw)),
    [rows, gw],
  );

  const allTeams = useMemo(
    () => [...new Set(allRows.map((r) => r.team).filter(Boolean) as string[])].sort(),
    [allRows],
  );

  const filtered = useMemo(
    () => applyFilters(allRows, { positions, search, team, priceMin, priceMax }),
    [allRows, positions, search, team, priceMin, priceMax],
  );

  const xMax = useMemo(() => axisMax(filtered.map((r) => r.minutes)), [filtered]);
  const yMax = useMemo(() => axisMax(filtered.map((r) => r.defcon90)), [filtered]);

  const topRows = useMemo(() => {
    return [...filtered]
      .sort((a, b) => {
        const av = (a[sort] ?? -Infinity) as number;
        const bv = (b[sort] ?? -Infinity) as number;
        return bv - av;
      })
      .slice(0, 10);
  }, [filtered, sort]);

  const togglePosition = (pos: string) => {
    setPositions((prev) => {
      const next = new Set(prev);
      if (next.has(pos) && next.size > 1) next.delete(pos);
      else next.add(pos);
      return next;
    });
  };

  if (gameweeks.length === 0) {
    return (
      <p className="border border-dashed border-border bg-card px-4 py-16 text-center text-xs text-muted-foreground">
        No gameweeks available.
      </p>
    );
  }

  const hasData = allRows.length > 0;

  return (
    <div className="flex flex-col gap-4">

      {/* ── Controls ── */}
      <div className="border border-border bg-card">
        <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2.5">
          <button
            onClick={() => setGwIndex((i) => Math.max(0, i - 1))}
            disabled={safeIndex === 0}
            aria-label="Previous gameweek"
            className="inline-flex h-7 w-7 items-center justify-center rounded border border-border text-muted-foreground transition hover:text-foreground disabled:opacity-40"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <span className="min-w-[3rem] text-center font-mono text-sm font-semibold">GW{gw ?? "—"}</span>
          <button
            onClick={() => setGwIndex((i) => Math.min(gameweeks.length - 1, i + 1))}
            disabled={safeIndex === gameweeks.length - 1}
            aria-label="Next gameweek"
            className="inline-flex h-7 w-7 items-center justify-center rounded border border-border text-muted-foreground transition hover:text-foreground disabled:opacity-40"
          >
            <ChevronRight className="h-4 w-4" />
          </button>

          <div className="mx-1 h-4 w-px bg-border" />

          <div className="inline-flex rounded-md border border-border p-0.5" role="group" aria-label="Filter by position">
            {POSITIONS.map((p) => {
              const active = positions.has(p);
              return (
                <button
                  key={p}
                  onClick={() => togglePosition(p)}
                  aria-pressed={active}
                  className={cn("rounded px-2.5 py-1 text-xs font-semibold transition",
                    active ? "text-white" : "text-muted-foreground hover:text-foreground")}
                  style={active ? { backgroundColor: POSITION_COLOR[p] } : {}}
                >
                  {p}
                </button>
              );
            })}
          </div>

          <span className="ml-auto font-mono text-[11px] text-muted-foreground" aria-live="polite">
            {filtered.length} player{filtered.length !== 1 ? "s" : ""} in view
            {!hasData && " · defcon90 not in this run"}
          </span>
        </div>

        <div className="flex flex-wrap items-center gap-2 px-4 py-2">
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search player or team…"
            aria-label="Search player or team"
            className="h-7 w-44 rounded border border-border bg-background px-2 text-xs outline-none focus:border-primary"
          />
          <div className="mx-1 h-4 w-px bg-border" />
          <label htmlFor={`${uid}-team`} className="text-xs text-muted-foreground">Team</label>
          <select
            id={`${uid}-team`}
            value={team}
            onChange={(e) => setTeam(e.target.value)}
            className="h-7 rounded border border-border bg-background px-2 text-xs outline-none focus:border-primary"
          >
            <option value="">All teams</option>
            {allTeams.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <div className="mx-1 h-4 w-px bg-border" />
          <span className="text-xs text-muted-foreground">Price</span>
          <input type="number" value={priceMin}
            onChange={(e) => setPriceMin(Number(e.target.value) || 0)}
            min={3.0} max={16.0} step={0.5} aria-label="Minimum price"
            className="h-7 w-14 rounded border border-border bg-background px-2 text-xs outline-none focus:border-primary"
          />
          <span className="text-xs text-muted-foreground">to</span>
          <input type="number" value={priceMax}
            onChange={(e) => setPriceMax(Number(e.target.value) || 99)}
            min={3.0} max={30.0} step={0.5} aria-label="Maximum price"
            className="h-7 w-14 rounded border border-border bg-background px-2 text-xs outline-none focus:border-primary"
          />
        </div>
      </div>

      {/* ── Chart ── */}
      <div className="border border-border bg-card">
        <div className="overflow-x-auto px-3 pb-1 pt-3">
          {filtered.length === 0 ? (
            <p className="py-12 text-center text-xs text-muted-foreground">
              {hasData
                ? "No players match these filters."
                : "Defensive contributions (defcon90) are not included in this published run."}
            </p>
          ) : (
            <ScatterChart rows={filtered} xMax={xMax} yMax={yMax} hovered={hovered} onHover={setHovered} />
          )}
        </div>
        <div className="flex flex-wrap gap-4 border-t border-border/50 px-4 py-2" aria-hidden>
          {POSITIONS.map((p) => (
            <span key={p} className="inline-flex items-center gap-1.5 text-xs font-semibold text-muted-foreground">
              <i className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: POSITION_COLOR[p] }} />
              {p}
            </span>
          ))}
        </div>
      </div>

      {/* ── Companion table ── */}
      {filtered.length > 0 && (
        <div className="overflow-x-auto border border-border bg-card">
          <div className="flex items-center justify-between border-b border-border px-4 py-2">
            <p className="text-[11px] text-muted-foreground">Top 10 · same filters as chart</p>
            <div className="flex items-center gap-1.5">
              <span className="text-[11px] text-muted-foreground">Sort</span>
              <select
                value={sort}
                onChange={(e) => setSort(e.target.value as SortField)}
                className="h-6 rounded border border-border bg-background px-1.5 text-[11px] outline-none focus:border-primary"
                aria-label="Sort the player list"
              >
                <option value="defcon90">Defcon / 90</option>
                <option value="minutes">Minutes</option>
                <option value="xpts">xPts</option>
              </select>
            </div>
          </div>

          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border text-left text-[11px] font-semibold text-muted-foreground">
                <th className="px-3 py-2">Player</th>
                <th className="px-3 py-2">Team</th>
                <th className="px-3 py-2 text-right">Defcon/90</th>
                <th className="px-3 py-2 text-right">xPts</th>
                <th className="px-3 py-2 text-right">Mins</th>
                <th className="px-3 py-2 text-right">£</th>
              </tr>
            </thead>
            <tbody>
              {topRows.map((r) => (
                <tr
                  key={r.key}
                  className={cn("border-b border-border/50 transition-colors", hovered === r.key && "bg-muted/50")}
                  onMouseEnter={() => setHovered(r.key)}
                  onMouseLeave={() => setHovered(null)}
                >
                  <td className="px-3 py-1.5">
                    <span className="inline-flex items-center gap-1.5">
                      <TeamKit teamCode={r.team} size={16} />
                      <span className="inline-block h-1.5 w-1.5 shrink-0 rounded-full" style={{ backgroundColor: POSITION_COLOR[r.position ?? ""] ?? "#7A8799" }} aria-hidden />
                      {r.name}
                    </span>
                  </td>
                  <td className="px-3 py-1.5 text-muted-foreground">{r.team ?? "—"}</td>
                  <td className={cn("px-3 py-1.5 text-right font-mono tabular-nums font-semibold", sort === "defcon90" && "text-primary")}>{r.defcon90.toFixed(2)}</td>
                  <td className={cn("px-3 py-1.5 text-right font-mono tabular-nums", sort === "xpts" && "text-primary")}>{r.xpts !== null ? r.xpts.toFixed(2) : "—"}</td>
                  <td className={cn("px-3 py-1.5 text-right font-mono tabular-nums text-muted-foreground", sort === "minutes" && "text-primary")}>{r.minutes.toFixed(0)}</td>
                  <td className="px-3 py-1.5 text-right font-mono tabular-nums text-muted-foreground">{r.price !== null ? r.price.toFixed(1) : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
