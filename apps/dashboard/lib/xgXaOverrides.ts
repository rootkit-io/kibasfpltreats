"use client";

/**
 * Personal xG / xA overrides — localStorage, no server sync.
 *
 * WHAT IS STORED
 * Overrides are stored as per-gameweek RATES, not as window totals.
 * A user who thinks Saka will deliver 0.25 xG/GW sets 0.25, and the grid
 * shows that value multiplied by however many gameweeks are in the current
 * window. This means the override stays meaningful when the window changes.
 *
 * xPts is deliberately NOT recalculated. The model's xPts was built on its
 * own xG/xA estimates and a goal-scoring distribution; replicating that
 * client-side would need the full model. The cheap version edits the two
 * displayed stats only -- a visual annotation, not a re-run.
 *
 * SCOPE
 * Per player, per season. A key is `{playerId}` inside a localStorage entry
 * keyed to `kft:xgxa:{season}`. A new season starts clean.
 */

import { useCallback, useEffect, useState } from "react";

export interface XgXaRates {
  /** Per-gameweek xG override, or null to use the model value. */
  xg: number | null;
  /** Per-gameweek xA override, or null to use the model value. */
  xa: number | null;
}

const KEY_PREFIX = "kft:xgxa:";

function storageKey(season: string): string {
  return `${KEY_PREFIX}${season}`;
}

function readLocal(season: string): Record<number, XgXaRates> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(storageKey(season));
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return {};
    const clean: Record<number, XgXaRates> = {};
    for (const [key, value] of Object.entries(parsed)) {
      const pid = Number(key);
      if (!Number.isFinite(pid) || !value || typeof value !== "object") continue;
      const v = value as Record<string, unknown>;
      clean[pid] = {
        xg: typeof v.xg === "number" && v.xg >= 0 ? v.xg : null,
        xa: typeof v.xa === "number" && v.xa >= 0 ? v.xa : null,
      };
    }
    return clean;
  } catch {
    return {};
  }
}

function writeLocal(season: string, data: Record<number, XgXaRates>): void {
  try {
    window.localStorage.setItem(storageKey(season), JSON.stringify(data));
  } catch { /* quota / private mode */ }
}

export function useXgXaOverrides(season: string) {
  const [overrides, setOverrides] = useState<Record<number, XgXaRates>>({});
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    setOverrides(readLocal(season));
    setHydrated(true);
  }, [season]);

  /**
   * Set xG or xA per-GW rate. `null` clears the override and reverts to
   * the model value. Setting both to null removes the player entry entirely.
   */
  const set = useCallback(
    (playerId: number, field: "xg" | "xa", value: number | null) => {
      setOverrides((current) => {
        const existing = current[playerId] ?? { xg: null, xa: null };
        const next = { ...existing, [field]: value };
        const updated = { ...current };
        if (next.xg === null && next.xa === null) {
          delete updated[playerId];
        } else {
          updated[playerId] = next;
        }
        writeLocal(season, updated);
        return updated;
      });
    },
    [season],
  );

  const clear = useCallback(
    (playerId: number) => {
      setOverrides((current) => {
        const updated = { ...current };
        delete updated[playerId];
        writeLocal(season, updated);
        return updated;
      });
    },
    [season],
  );

  /**
   * Effective xG for a player in a window of `numGameweeks` GWs.
   * Returns the model value when no override is set.
   */
  const effectiveXg = useCallback(
    (playerId: number, modelXg: number | null, numGameweeks: number): number | null => {
      const rate = overrides[playerId]?.xg;
      if (rate !== undefined && rate !== null) return rate * numGameweeks;
      return modelXg;
    },
    [overrides],
  );

  const effectiveXa = useCallback(
    (playerId: number, modelXa: number | null, numGameweeks: number): number | null => {
      const rate = overrides[playerId]?.xa;
      if (rate !== undefined && rate !== null) return rate * numGameweeks;
      return modelXa;
    },
    [overrides],
  );

  return { overrides, set, clear, effectiveXg, effectiveXa, hydrated };
}
