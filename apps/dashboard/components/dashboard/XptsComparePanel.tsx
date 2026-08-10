"use client";

/**
 * XptsComparePanel — multi-line xPts chart comparing up to 10 players.
 *
 * Ports the KFT2627 xpts-compare.js logic into React. Works from
 * ProjectionRow[] so no simulation data is required. Lines break across
 * missing gameweek values rather than interpolating.
 *
 * Players are selected from a search-filtered picker. The chart shows
 * projected xPts per gameweek for each selected player. A companion
 * table lists totals and per-GW averages.
 */

import { useId, useMemo, useState } from "react";
import { Check, Search, X } from "lucide-react";

import { cn } from "@/lib/utils";
import { TeamKit } from "@/components/ui/TeamKit";
import type { ProjectionRow } from "@/lib/validations/projections";

// ── Palette (matches KFT2627/assets/xpts-compare.js) ─────────────────────────
const PALETTE = [
  "#FF5F1F", "#2563EB", "#059669", "#DC2626", "#7C3AED",
  "#D97706", "#0891B2", "#DB2777", "#65A30D", "#4F46E5",
] as const;

const MAX_PLAYERS = 10;

// ── SVG canvas ────────────────────────────────────────────────────────────────
const W = 820;
const H = 300;
const M = { t: 16, r: 24, b: 40, l: 48 } as const;
const CW = W - M.l - M.r;
const CH = H - M.t - M.b;

// ── Types ─────────────────────────────────────────────────────────────────────

interface PlayerMeta {
  player_id: number;
  name: string;
  team: string | null;
  position: string | null;
  /** Total xPts across all available gameweeks (for picker sort). */
  totalXpts: number;
}

interface Series {
  player_id: number;
  name: string;
  team: string | null;
  color: string;
  /** One entry per gameweek in `gameweeks`, value null when data is missing. */
  points: { gw: number; xpts: number | null }[];
  total: number | null;
  average: number | null;
}

interface LocalHover {
  player_id: number;
  gw: number;
  xpts: number;
  cx: number;
  cy: number;
  color: string;
  name: string;
  team: string | null;
}

// ── Axis helpers ──────────────────────────────────────────────────────────────

function axisMax(series: Series[]): number {
  const values: number[] = [];
  for (const s of series) {
    for (const p of s.points) {
      if (p.xpts !== null) values.push(p.xpts);
    }
  }
  if (!values.length) return 4;
  const max = Math.max(...values);
  const padded = max * 1.12;
  const step = padded <= 8 ? 1 : padded <= 20 ? 2 : 5;
  return Math.max(4, Math.ceil(padded / step) * step);
}

function yTicks(max: number): number[] {
  const step = max <= 8 ? 1 : max <= 20 ? 2 : 5;
  const out: number[] = [];
  for (let v = 0; v <= max; v += step) out.push(v);
  return out;
}

// ── Build player meta ─────────────────────────────────────────────────────────

function buildPlayerMeta(rows: ProjectionRow[]): PlayerMeta[] {
  const map = new Map<number, PlayerMeta & { xptsSum: number }>();
  for (const r of rows) {
    if (typeof r.player_id !== "number") continue;
    const existing = map.get(r.player_id);
    const xpts = typeof r.xpts === "number" && Number.isFinite(r.xpts) ? r.xpts : 0;
    if (existing) {
      existing.xptsSum += xpts;
      existing.totalXpts = existing.xptsSum;
    } else {
      map.set(r.player_id, {
        player_id: r.player_id,
        name: r.web_name ?? "—",
        team: r.team_short ?? null,
        position: (r.position ?? "").toUpperCase() || null,
        totalXpts: xpts,
        xptsSum: xpts,
      });
    }
  }
  return [...map.values()]
    .map(({ xptsSum: _, ...rest }) => rest)
    .sort((a, b) => b.totalXpts - a.totalXpts);
}

// ── Build series ──────────────────────────────────────────────────────────────

function buildSeries(
  selected: number[],
  rows: ProjectionRow[],
  gameweeks: number[],
): Series[] {
  const lookup = new Map<string, number>();
  for (const r of rows) {
    if (typeof r.player_id !== "number" || typeof r.gameweek_id !== "number") continue;
    if (typeof r.xpts === "number" && Number.isFinite(r.xpts)) {
      lookup.set(`${r.player_id}:${r.gameweek_id}`, r.xpts);
    }
  }
  const meta = new Map<number, { name: string; team: string | null }>();
  for (const r of rows) {
    if (typeof r.player_id === "number" && !meta.has(r.player_id)) {
      meta.set(r.player_id, { name: r.web_name ?? "—", team: r.team_short ?? null });
    }
  }

  return selected.map((pid, i) => {
    const m = meta.get(pid);
    const points = gameweeks.map((gw) => ({
      gw,
      xpts: lookup.get(`${pid}:${gw}`) ?? null,
    }));
    const known = points.filter((p): p is { gw: number; xpts: number } => p.xpts !== null);
    const total = known.length ? Math.round(known.reduce((s, p) => s + p.xpts, 0) * 100) / 100 : null;
    const average = known.length ? Math.round((total! / known.length) * 100) / 100 : null;
    return {
      player_id: pid,
      name: m?.name ?? "—",
      team: m?.team ?? null,
      color: PALETTE[i % PALETTE.length],
      points,
      total,
      average,
    };
  });
}

// ── SVG chart ─────────────────────────────────────────────────────────────────

function CompareChart({
  series,
  gameweeks,
  hovered,
  onHover,
}: {
  series: Series[];
  gameweeks: number[];
  hovered: LocalHover | null;
  onHover: (h: LocalHover | null) => void;
}) {
  const max = useMemo(() => axisMax(series), [series]);
  const ticks = useMemo(() => yTicks(max), [max]);

  const span = Math.max(gameweeks.length - 1, 1);
  const toX = (i: number) =>
    gameweeks.length === 1 ? M.l + CW / 2 : M.l + (CW * i) / span;
  const toY = (v: number) =>
    M.t + CH - (Math.min(Math.max(v, 0), max) / max) * CH;

  // Build SVG paths per series — segments break at null values
  const paths = useMemo(() =>
    series.map((s) => {
      const segments: string[] = [];
      let seg: string[] = [];
      s.points.forEach((p, i) => {
        if (p.xpts === null) {
          if (seg.length) { segments.push(seg.join(" ")); seg = []; }
          return;
        }
        seg.push(`${seg.length === 0 ? "M" : "L"}${toX(i).toFixed(1)},${toY(p.xpts).toFixed(1)}`);
      });
      if (seg.length) segments.push(seg.join(" "));
      return { key: s.player_id, color: s.color, segments };
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [series, max, gameweeks.length],
  );

  return (
    <div className="relative overflow-x-auto">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        style={{ minWidth: Math.max(300, gameweeks.length * 60) }}
        role="img"
        aria-label="xPts per gameweek comparison"
        onMouseLeave={() => onHover(null)}
      >
        {/* Y grid + labels */}
        {ticks.map((t) => {
          const y = toY(t);
          return (
            <g key={`y${t}`}>
              <line x1={M.l} x2={M.l + CW} y1={y} y2={y} stroke="rgba(127,127,127,.12)" />
              <text x={M.l - 6} y={y + 4} textAnchor="end" fill="var(--muted-foreground,#888)" fontSize={10}>{t}</text>
            </g>
          );
        })}

        {/* X grid + GW labels */}
        {gameweeks.map((gw, i) => {
          const x = toX(i);
          return (
            <g key={`x${gw}`}>
              <line x1={x} x2={x} y1={M.t} y2={M.t + CH} stroke="rgba(127,127,127,.07)" />
              <text x={x} y={H - 6} textAnchor="middle" fill="var(--muted-foreground,#888)" fontSize={10}>
                GW{gw}
              </text>
            </g>
          );
        })}

        {/* Lines */}
        {paths.map(({ key, color, segments }) =>
          segments.map((d, si) => (
            <path
              key={`${key}-${si}`}
              d={d}
              fill="none"
              stroke={color}
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              opacity="0.85"
            />
          )),
        )}

        {/* Dots + hover target */}
        {series.map((s) =>
          s.points.map((p, i) => {
            if (p.xpts === null) return null;
            const cx = toX(i);
            const cy = toY(p.xpts);
            const isHovered = hovered?.player_id === s.player_id && hovered?.gw === p.gw;
            return (
              <circle
                key={`${s.player_id}-${p.gw}`}
                cx={cx} cy={cy}
                r={isHovered ? 6 : 3.5}
                fill={s.color}
                stroke={isHovered ? "white" : s.color}
                strokeWidth={isHovered ? 1.5 : 0}
                className="cursor-pointer"
                onMouseEnter={() =>
                  onHover({ player_id: s.player_id, gw: p.gw, xpts: p.xpts!, cx, cy, color: s.color, name: s.name, team: s.team })
                }
              />
            );
          }),
        )}

        {/* Crosshair */}
        {hovered && (
          <g className="pointer-events-none">
            <line x1={M.l} x2={hovered.cx} y1={hovered.cy} y2={hovered.cy} stroke="rgba(127,127,127,.35)" strokeDasharray="3 3" strokeWidth={1} />
            <line x1={hovered.cx} x2={hovered.cx} y1={hovered.cy} y2={M.t + CH} stroke="rgba(127,127,127,.35)" strokeDasharray="3 3" strokeWidth={1} />
          </g>
        )}
      </svg>

      {/* Tooltip */}
      {hovered && (
        <div
          className="pointer-events-none absolute z-10 min-w-[140px] border border-border bg-black/85 p-2 backdrop-blur-md"
          style={{
            left: `${(hovered.cx / W) * 100}%`,
            top: `${(hovered.cy / H) * 100}%`,
            transform: "translate(10px, -50%)",
          }}
        >
          <p className="flex items-center gap-1.5 text-xs font-semibold text-white">
            <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: hovered.color }} />
            {hovered.name}
          </p>
          {hovered.team && <p className="text-[10px] text-zinc-400">{hovered.team}</p>}
          <dl className="mt-1 grid grid-cols-2 gap-x-3 font-mono text-[10px]">
            <dt className="text-zinc-400">GW{hovered.gw}</dt>
            <dd className="text-right text-white">{hovered.xpts.toFixed(2)}</dd>
          </dl>
        </div>
      )}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function XptsComparePanel({
  rows,
  gameweeks,
}: {
  rows: ProjectionRow[];
  gameweeks: number[];
}) {
  const uid = useId();
  const [selected, setSelected] = useState<number[]>([]);
  const [query, setQuery] = useState("");
  const [hovered, setHovered] = useState<LocalHover | null>(null);

  const allPlayers = useMemo(() => buildPlayerMeta(rows), [rows]);

  const candidates = useMemo(() => {
    const q = query.trim().toLowerCase();
    const filtered = q
      ? allPlayers.filter(
          (p) => p.name.toLowerCase().includes(q) || (p.team ?? "").toLowerCase().includes(q),
        )
      : allPlayers;
    return filtered.slice(0, 80);
  }, [allPlayers, query]);

  const series = useMemo(
    () => buildSeries(selected, rows, gameweeks),
    [selected, rows, gameweeks],
  );

  const toggle = (playerId: number) => {
    setSelected((prev) =>
      prev.includes(playerId)
        ? prev.filter((id) => id !== playerId)
        : prev.length >= MAX_PLAYERS
          ? prev
          : [...prev, playerId],
    );
  };

  const clear = () => setSelected([]);

  if (gameweeks.length === 0) {
    return (
      <p className="border border-dashed border-border bg-card px-4 py-16 text-center text-xs text-muted-foreground">
        No gameweeks available.
      </p>
    );
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)]">

      {/* ── Player picker ── */}
      <div className="border border-border bg-card">
        <div className="border-b border-border p-3">
          <label htmlFor={`${uid}-search`} className="sr-only">Search players</label>
          <div className="relative">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
            <input
              id={`${uid}-search`}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search player or team…"
              className="w-full rounded border border-border bg-background py-1.5 pl-8 pr-2 text-xs outline-none focus:border-primary"
            />
          </div>
          <div className="mt-2 flex items-center justify-between">
            <p className="font-mono text-[10px] text-muted-foreground">
              {selected.length}/{MAX_PLAYERS} selected
            </p>
            {selected.length > 0 && (
              <button onClick={clear} className="text-[10px] text-muted-foreground underline hover:text-foreground">
                Clear all
              </button>
            )}
          </div>
        </div>

        <ul className="max-h-[480px] overflow-auto">
          {candidates.map((player) => {
            const isSelected = selected.includes(player.player_id);
            const atLimit = !isSelected && selected.length >= MAX_PLAYERS;
            const colorIdx = selected.indexOf(player.player_id);
            return (
              <li key={player.player_id}>
                <button
                  onClick={() => toggle(player.player_id)}
                  disabled={atLimit}
                  className={cn(
                    "flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition",
                    isSelected ? "bg-primary/10 text-foreground" : "hover:bg-muted/50",
                    atLimit && "cursor-not-allowed opacity-40",
                  )}
                >
                  <span
                    className={cn(
                      "flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded border",
                      isSelected ? "border-primary bg-primary text-primary-foreground" : "border-border",
                    )}
                  >
                    {isSelected && <Check className="h-2.5 w-2.5" />}
                  </span>
                  {isSelected && colorIdx >= 0 && (
                    <span className="h-2 w-2 shrink-0 rounded-full" style={{ backgroundColor: PALETTE[colorIdx % PALETTE.length] }} />
                  )}
                  <span className="flex min-w-0 items-center gap-1.5 font-medium">
                    <TeamKit teamCode={player.team} size={16} />
                    <span className="truncate">{player.name}</span>
                  </span>
                  <span className="ml-auto shrink-0 font-mono text-[10px] text-muted-foreground tabular-nums">
                    {player.totalXpts.toFixed(1)}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>

      {/* ── Chart + table ── */}
      <div className="flex flex-col gap-4">
        <div className="border border-border bg-card">
          {/* Legend */}
          {series.length > 0 && (
            <div className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-2.5">
              {series.map((s) => (
                <span key={s.player_id} className="inline-flex items-center gap-1.5 text-xs font-medium">
                  <span className="h-3 w-3 rounded-sm" style={{ backgroundColor: s.color }} />
                  {s.name}
                  <button
                    onClick={() => toggle(s.player_id)}
                    className="text-muted-foreground hover:text-foreground"
                    aria-label={`Remove ${s.name}`}
                  >
                    <X className="h-3 w-3" />
                  </button>
                </span>
              ))}
            </div>
          )}

          <div className="px-3 py-3">
            {series.length === 0 ? (
              <p className="py-16 text-center text-xs text-muted-foreground">
                Select at least one player from the list to plot their xPts.
              </p>
            ) : (
              <CompareChart
                series={series}
                gameweeks={gameweeks}
                hovered={hovered}
                onHover={setHovered}
              />
            )}
          </div>
        </div>

        {/* Summary table */}
        {series.length > 0 && (
          <div className="overflow-x-auto border border-border bg-card">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-border text-left text-[11px] font-semibold text-muted-foreground">
                  <th className="px-3 py-2">Player</th>
                  {gameweeks.map((gw) => (
                    <th key={gw} className="px-2 py-2 text-right font-mono">GW{gw}</th>
                  ))}
                  <th className="px-3 py-2 text-right">Total</th>
                  <th className="px-3 py-2 text-right">Avg/GW</th>
                </tr>
              </thead>
              <tbody>
                {series.map((s) => (
                  <tr key={s.player_id} className="border-b border-border/50 transition-colors hover:bg-muted/30">
                    <td className="px-3 py-1.5">
                      <span className="inline-flex items-center gap-2">
                        <span className="h-2.5 w-2.5 shrink-0 rounded-sm" style={{ backgroundColor: s.color }} />
                        <TeamKit teamCode={s.team} size={14} />
                        <span className="font-medium">{s.name}</span>
                      </span>
                    </td>
                    {s.points.map(({ gw, xpts }) => (
                      <td key={gw} className={cn(
                        "px-2 py-1.5 text-right font-mono tabular-nums",
                        xpts !== null ? "text-foreground" : "text-muted-foreground/40",
                      )}>
                        {xpts !== null ? xpts.toFixed(2) : "—"}
                      </td>
                    ))}
                    <td className="px-3 py-1.5 text-right font-mono tabular-nums font-semibold">
                      {s.total !== null ? s.total.toFixed(2) : "—"}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono tabular-nums text-muted-foreground">
                      {s.average !== null ? s.average.toFixed(2) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
