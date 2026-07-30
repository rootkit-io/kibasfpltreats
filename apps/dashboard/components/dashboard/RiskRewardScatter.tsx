"use client";

/**
 * RiskRewardScatter -- floor vs upside (or mean vs volatility) for one GW.
 *
 * Hand-rolled SVG rather than a charting dependency: this is a single scatter
 * with ~830 points, and recharts would add ~100kB gzipped to a route whose
 * whole JS budget is currently ~165kB.
 *
 * Reading the plot:
 *   x = floor_p10  (10th percentile -- the bad-week outcome)
 *   y = upside_p90 (90th percentile -- the ceiling)
 *   top-right    = high floor AND high ceiling (premium)
 *   top-left     = low floor, high ceiling (explosive differential)
 *   bottom-right = high floor, low ceiling (safe, dull)
 * The diagonal marks floor == upside, i.e. zero spread.
 */

import { useMemo, useState } from "react";

import { cn } from "@/lib/utils";
import type { EnrichedSimulation } from "@/lib/api/simulations";

type Axes = "floor_upside" | "mean_volatility";

const POSITION_COLOR: Record<string, string> = {
  GK: "#38bdf8",
  DEF: "#34d399",
  MID: "#fbbf24",
  FWD: "#fb7185",
};

const PAD = { top: 16, right: 16, bottom: 36, left: 44 };
const W = 720;
const H = 420;

interface Point {
  row: EnrichedSimulation;
  x: number;
  y: number;
}

export default function RiskRewardScatter({ rows }: { rows: EnrichedSimulation[] }) {
  const [axes, setAxes] = useState<Axes>("floor_upside");
  const [hover, setHover] = useState<Point | null>(null);
  const [positions, setPositions] = useState<Set<string>>(new Set());

  const config =
    axes === "floor_upside"
      ? { xKey: "floor_p10" as const, yKey: "upside_p90" as const, xLabel: "Floor (P10)", yLabel: "Upside (P90)" }
      : { xKey: "std_pts" as const, yKey: "mean_pts" as const, xLabel: "Volatility (σ)", yLabel: "Mean points" };

  const points = useMemo<Point[]>(() => {
    const out: Point[] = [];
    for (const row of rows) {
      const x = row[config.xKey];
      const y = row[config.yKey];
      if (typeof x !== "number" || typeof y !== "number") continue;
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
      if (positions.size > 0 && !positions.has((row.position ?? "").toUpperCase())) continue;
      out.push({ row, x, y });
    }
    return out;
  }, [rows, config.xKey, config.yKey, positions]);

  const bounds = useMemo(() => {
    if (points.length === 0) return { minX: 0, maxX: 1, minY: 0, maxY: 1 };
    const xs = points.map((p) => p.x);
    const ys = points.map((p) => p.y);
    const minX = Math.min(...xs, 0);
    const maxX = Math.max(...xs) || 1;
    const minY = Math.min(...ys, 0);
    const maxY = Math.max(...ys) || 1;
    return { minX, maxX: maxX * 1.04, minY, maxY: maxY * 1.04 };
  }, [points]);

  const sx = (x: number) =>
    PAD.left + ((x - bounds.minX) / (bounds.maxX - bounds.minX || 1)) * (W - PAD.left - PAD.right);
  const sy = (y: number) =>
    H - PAD.bottom - ((y - bounds.minY) / (bounds.maxY - bounds.minY || 1)) * (H - PAD.top - PAD.bottom);

  const ticks = (lo: number, hi: number) => {
    const step = niceStep((hi - lo) / 5);
    const out: number[] = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) out.push(Number(v.toFixed(6)));
    return out;
  };

  const togglePosition = (p: string) =>
    setPositions((prev) => {
      const next = new Set(prev);
      if (next.has(p)) next.delete(p);
      else next.add(p);
      return next;
    });

  if (rows.length === 0) {
    return (
      <p className="border border-dashed border-border bg-card px-4 py-16 text-center text-xs text-muted-foreground">
        This published run carries no simulation data.
      </p>
    );
  }

  return (
    <div className="border border-border bg-card">
      {/* -------------------------------------------------------- controls */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
        <div className="inline-flex rounded-lg border border-border p-0.5">
          {(
            [
              ["floor_upside", "Floor vs Upside"],
              ["mean_volatility", "Mean vs Volatility"],
            ] as [Axes, string][]
          ).map(([value, label]) => (
            <button
              key={value}
              onClick={() => setAxes(value)}
              className={cn(
                "rounded-md px-3 py-1 text-xs font-medium transition",
                axes === value
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="ml-auto inline-flex flex-wrap gap-1">
          {Object.keys(POSITION_COLOR).map((p) => {
            const active = positions.size === 0 || positions.has(p);
            return (
              <button
                key={p}
                onClick={() => togglePosition(p)}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs transition",
                  active ? "border-border text-foreground" : "border-transparent text-muted-foreground/50",
                )}
              >
                <span
                  className="h-2 w-2 rounded-full"
                  style={{ backgroundColor: POSITION_COLOR[p], opacity: active ? 1 : 0.3 }}
                />
                {p}
              </button>
            );
          })}
        </div>
      </div>

      {/* ------------------------------------------------------------ plot */}
      <div className="relative">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="w-full"
          role="img"
          aria-label={`${config.yLabel} against ${config.xLabel}`}
          onMouseLeave={() => setHover(null)}
        >
          {/* Plot field: a defined coordinate space for the dots to sit in.
              A 20px minor mesh in near-black, clipped to the field, then the
              labelled major gridlines drawn over it. */}
          <defs>
            <pattern id="rr-mesh" width="20" height="20" patternUnits="userSpaceOnUse">
              <path
                d="M20 0 L0 0 0 20"
                fill="none"
                className="stroke-zinc-900"
                strokeWidth="1"
              />
            </pattern>
          </defs>
          <rect
            x={PAD.left}
            y={PAD.top}
            width={W - PAD.left - PAD.right}
            height={H - PAD.top - PAD.bottom}
            fill="url(#rr-mesh)"
            className="stroke-zinc-900"
            strokeWidth="1"
          />

          {/* grid + axes */}
          {ticks(bounds.minY, bounds.maxY).map((t) => (
            <g key={`y${t}`}>
              <line x1={PAD.left} x2={W - PAD.right} y1={sy(t)} y2={sy(t)} className="stroke-border/30" strokeWidth="1" />
              <text x={PAD.left - 8} y={sy(t)} textAnchor="end" dominantBaseline="middle" className="fill-muted-foreground text-[9px]">
                {t}
              </text>
            </g>
          ))}
          {ticks(bounds.minX, bounds.maxX).map((t) => (
            <g key={`x${t}`}>
              <line y1={PAD.top} y2={H - PAD.bottom} x1={sx(t)} x2={sx(t)} className="stroke-border/20" strokeWidth="1" />
              <text x={sx(t)} y={H - PAD.bottom + 14} textAnchor="middle" className="fill-muted-foreground text-[9px]">
                {t}
              </text>
            </g>
          ))}

          {/* zero-spread diagonal (floor == upside) */}
          {axes === "floor_upside" && (
            <line
              x1={sx(Math.max(bounds.minX, bounds.minY))}
              y1={sy(Math.max(bounds.minX, bounds.minY))}
              x2={sx(Math.min(bounds.maxX, bounds.maxY))}
              y2={sy(Math.min(bounds.maxX, bounds.maxY))}
              className="stroke-muted-foreground/30"
              strokeDasharray="4 4"
              strokeWidth="1"
            />
          )}

          {/* points */}
          {points.map((p) => {
            const active = hover?.row.player_id === p.row.player_id;
            return (
              <circle
                key={`${p.row.player_id}:${p.row.gameweek_id}`}
                cx={sx(p.x)}
                cy={sy(p.y)}
                r={active ? 5 : 3}
                fill={POSITION_COLOR[(p.row.position ?? "").toUpperCase()] ?? "#a1a1aa"}
                fillOpacity={active ? 1 : 0.62}
                stroke={active ? "currentColor" : "none"}
                strokeWidth="1"
                className="cursor-pointer text-foreground"
                onMouseEnter={() => setHover(p)}
              />
            );
          })}

          {/* crosshair to both axes: makes the hovered dot's coordinates
              readable without hunting across the field */}
          {hover && (
            <g className="pointer-events-none">
              <line
                x1={PAD.left}
                x2={sx(hover.x)}
                y1={sy(hover.y)}
                y2={sy(hover.y)}
                className="stroke-zinc-700"
                strokeDasharray="2 3"
                strokeWidth="1"
              />
              <line
                x1={sx(hover.x)}
                x2={sx(hover.x)}
                y1={sy(hover.y)}
                y2={H - PAD.bottom}
                className="stroke-zinc-700"
                strokeDasharray="2 3"
                strokeWidth="1"
              />
            </g>
          )}

          <text x={W / 2} y={H - 4} textAnchor="middle" className="fill-muted-foreground text-[10px]">
            {config.xLabel}
          </text>
          <text
            x={-(H / 2)}
            y={12}
            transform="rotate(-90)"
            textAnchor="middle"
            className="fill-muted-foreground text-[10px]"
          >
            {config.yLabel}
          </text>
        </svg>

        {/* tooltip */}
        {hover && (
          <div
            // No transition: the tooltip must snap to the cursor's dot, not
            // glide between them -- a lagging tooltip reads as broken at this
            // point density.
            className="pointer-events-none absolute z-10 min-w-[168px] border border-border bg-black/80 p-2.5 backdrop-blur-md"
            style={{
              left: `${(sx(hover.x) / W) * 100}%`,
              top: `${(sy(hover.y) / H) * 100}%`,
              transform: "translate(12px, -50%)",
            }}
          >
            <p className="flex items-center gap-1.5 text-xs font-semibold">
              <span
                className="h-1.5 w-1.5 shrink-0"
                style={{
                  backgroundColor:
                    POSITION_COLOR[(hover.row.position ?? "").toUpperCase()] ?? "#a1a1aa",
                }}
              />
              {hover.row.web_name ?? "—"}
            </p>
            <p className="text-[10px] text-muted-foreground">
              {hover.row.team_short ?? "—"} · {hover.row.position ?? "—"}
              {typeof hover.row.price === "number" && ` · £${hover.row.price.toFixed(1)}`}
            </p>
            <dl className="mt-1.5 grid grid-cols-2 gap-x-3 gap-y-0.5 font-mono text-[10px]">
              <Metric label="Mean" value={hover.row.mean_pts} />
              <Metric label="σ" value={hover.row.std_pts} />
              <Metric label="Floor" value={hover.row.floor_p10} />
              <Metric label="Upside" value={hover.row.upside_p90} />
            </dl>
          </div>
        )}
      </div>

      <p className="border-t border-border px-4 py-2 font-mono text-[10px] text-muted-foreground">
        {points.length.toLocaleString()} players plotted
        {axes === "floor_upside" && " · dashed line = zero spread (floor equals upside)"}
      </p>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number | null | undefined }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="text-right text-foreground">
        {typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : "—"}
      </dd>
    </>
  );
}

/** 1 / 2 / 5 x 10^n step so axis labels stay round. */
function niceStep(raw: number): number {
  if (!Number.isFinite(raw) || raw <= 0) return 1;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  if (norm <= 1) return mag;
  if (norm <= 2) return 2 * mag;
  if (norm <= 5) return 5 * mag;
  return 10 * mag;
}
