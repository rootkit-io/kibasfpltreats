"use client";

/**
 * Personal fixture-difficulty overrides.
 *
 * FDR has three layers, highest priority first:
 *
 *   1. this user's personal override   (here, per device)
 *   2. the admin's global override     (fixtures.team_h_fdr_override)
 *   3. the official FPL rating         (fixtures.team_h_fdr_fpl)
 *
 * The backend already collapses 2 over 3 via COALESCE, so the API hands us a
 * single effective rating and this layer sits on top of it.
 *
 * Deliberately localStorage, not a table. Difficulty is a personal read of a
 * fixture -- one manager rates Arsenal away a 5, another a 4 -- so it must not
 * be global. Storing it per user server-side would mean a new table, a write
 * endpoint and an auth story, for a preference that is worthless on someone
 * else's screen. The tradeoff is that it does not follow you between devices.
 *
 * Keyed by season so a new season starts clean rather than inheriting ratings
 * for fixtures that no longer exist.
 */

import { useCallback, useEffect, useState } from "react";

export type FdrOverrideKey = `${number}:${number}`;

const KEY_PREFIX = "kft:fdr:";

function storageKey(season: string): string {
  return `${KEY_PREFIX}${season}`;
}

/** `fixtureId:teamId` -- a fixture holds two ratings, one per side. */
export function overrideKey(fixtureId: number, teamId: number): FdrOverrideKey {
  return `${fixtureId}:${teamId}`;
}

function read(season: string): Record<string, number> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(storageKey(season));
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return {};
    const clean: Record<string, number> = {};
    for (const [key, value] of Object.entries(parsed)) {
      // Reject anything outside the 1-5 scale so corrupt storage cannot paint
      // a cell with a rating that has no colour.
      if (typeof value === "number" && Number.isInteger(value) && value >= 1 && value <= 5) {
        clean[key] = value;
      }
    }
    return clean;
  } catch {
    return {};
  }
}

export function useFdrOverrides(season: string) {
  // Starts empty on server and first client render so hydration matches.
  const [overrides, setOverrides] = useState<Record<string, number>>({});
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    setOverrides(read(season));
    setHydrated(true);
  }, [season]);

  const persist = useCallback(
    (next: Record<string, number>) => {
      setOverrides(next);
      try {
        window.localStorage.setItem(storageKey(season), JSON.stringify(next));
      } catch {
        /* quota or private mode: keep it in memory, drop persistence */
      }
    },
    [season],
  );

  /** `null` clears the personal override and falls back to the API value. */
  const setOverride = useCallback(
    (fixtureId: number, teamId: number, value: number | null) => {
      const key = overrideKey(fixtureId, teamId);
      const next = { ...overrides };
      if (value === null) delete next[key];
      else next[key] = value;
      persist(next);
    },
    [overrides, persist],
  );

  const clearAll = useCallback(() => persist({}), [persist]);

  const get = useCallback(
    (fixtureId: number, teamId: number): number | null =>
      overrides[overrideKey(fixtureId, teamId)] ?? null,
    [overrides],
  );

  return { overrides, get, setOverride, clearAll, hydrated, count: Object.keys(overrides).length };
}
