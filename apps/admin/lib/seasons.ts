/**
 * Season codes (Phase 11). The backend scopes every dimension and fact row
 * by this short code (FPL recycles ids every year -- see RunMetadata).
 * Newest first; the default is the current season.
 */

export const SEASONS = ["2627", "2526", "2425", "2324"] as const;

export type Season = (typeof SEASONS)[number];

export const DEFAULT_SEASON: Season = "2627";

/** "2627" -> "2026/27" for display. */
export function seasonLabel(code: string): string {
  if (!/^\d{4}$/.test(code)) return code;
  return `20${code.slice(0, 2)}/${code.slice(2)}`;
}
