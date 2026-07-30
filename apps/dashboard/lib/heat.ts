/**
 * Value-based typographic hierarchy ("heat").
 *
 * Presentation-only: maps a metric to a colour tier so strong numbers pull the
 * eye and noise recedes.
 *
 * WHY THRESHOLDS ARE PER-GAMEWEEK
 * -------------------------------
 * The grid's `xpts` is SUMMED across the selected window and `p_return` is
 * combined as 1 - PROD(1 - p). A single fixed cutoff therefore means two
 * completely different things depending on the window, measured against the
 * real published distribution (3,328 player-gameweeks):
 *
 *   xPts > 5.0, single gameweek ...... top  1.9% of rows  (nothing lights up)
 *   xPts > 5.0, 4-gameweek window .... top 27.4% of rows  (everything lights up)
 *
 * So each value is normalised back to a per-gameweek quantity before banding.
 * Sums divide by the gameweek count; probabilities invert the independent
 * combination via 1 - (1 - p)^(1/n). The tiers then mean the same thing in
 * single-GW and range mode, which is the whole point of the effect.
 *
 * Cutoffs are taken from that same distribution: p95 ~ 4.33 and p90 ~ 3.89
 * per gameweek, so 4.5 / 3.0 select roughly the top 4% and top 23%.
 */

export type HeatTier = "hot" | "warm" | "cool" | "idle";

/** Tailwind text colours, brightest to most recessive. */
export const HEAT_CLASS: Record<HeatTier, string> = {
  hot: "text-positive",
  warm: "text-emerald-400",
  cool: "text-emerald-800",
  idle: "text-zinc-700",
};

const XPTS_HOT_PER_GW = 4.5;
const XPTS_WARM_PER_GW = 3.0;
const PROB_HOT_PER_GW = 0.4;
const PROB_WARM_PER_GW = 0.2;

function band(value: number, hot: number, warm: number): HeatTier {
  if (value >= hot) return "hot";
  if (value >= warm) return "warm";
  if (value > 0) return "cool";
  return "idle";
}

/**
 * Tier for an additive metric (xPts) summed over `gameweeks` gameweeks.
 * `gameweeks <= 0` is treated as a single gameweek so the caller never has to
 * guard against an empty window.
 */
export function heatForSum(
  value: number | null | undefined,
  gameweeks: number,
): HeatTier {
  if (typeof value !== "number" || !Number.isFinite(value)) return "idle";
  const perGameweek = value / Math.max(gameweeks, 1);
  return band(perGameweek, XPTS_HOT_PER_GW, XPTS_WARM_PER_GW);
}

/**
 * Tier for a probability combined across `gameweeks` gameweeks as
 * 1 - PROD(1 - p). Inverting to the per-gameweek rate keeps a 4-week P(return)
 * from grading as elite purely because the window is long.
 */
export function heatForProbability(
  value: number | null | undefined,
  gameweeks: number,
): HeatTier {
  if (typeof value !== "number" || !Number.isFinite(value)) return "idle";
  if (value <= 0) return "idle";
  const n = Math.max(gameweeks, 1);
  // Clamp below 1 so a certainty does not produce Infinity through the root.
  const combined = Math.min(value, 0.999999);
  const perGameweek = 1 - Math.pow(1 - combined, 1 / n);
  return band(perGameweek, PROB_HOT_PER_GW, PROB_WARM_PER_GW);
}

/** Which columns get heat treatment, and how each is normalised. */
export const HEAT_COLUMNS: Record<string, "sum" | "probability"> = {
  xpts: "sum",
  xpts_per_gw: "sum",
  p_return: "probability",
  p_haul: "probability",
};

export function heatFor(
  columnKey: string,
  value: number | null | undefined,
  gameweeks: number,
): HeatTier | null {
  const kind = HEAT_COLUMNS[columnKey];
  if (!kind) return null;
  // `xpts_per_gw` is already a per-gameweek mean, so it bands directly.
  if (columnKey === "xpts_per_gw") return heatForSum(value, 1);
  return kind === "sum"
    ? heatForSum(value, gameweeks)
    : heatForProbability(value, gameweeks);
}
