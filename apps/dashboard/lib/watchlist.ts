"use client";

/**
 * Watchlist -- client-only player bookmarking.
 *
 * Deliberately localStorage rather than a backend table: there is no user
 * table or watchlist table in the schema, and adding one would mean a
 * migration plus a write endpoint on a surface that is currently read-only.
 * Per-device persistence covers the actual use case (come back tomorrow and
 * your shortlist is still there) with zero backend surface.
 *
 * Keyed by FPL player_id, which is season-scoped -- the key includes the
 * season so a new season starts from a clean list rather than resurrecting
 * ids that now belong to different players.
 */

import { useCallback, useEffect, useState } from "react";

const KEY_PREFIX = "kft:watchlist:";

function storageKey(season: string): string {
  return `${KEY_PREFIX}${season}`;
}

function read(season: string): Set<number> {
  if (typeof window === "undefined") return new Set();
  try {
    const raw = window.localStorage.getItem(storageKey(season));
    if (!raw) return new Set();
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return new Set();
    return new Set(parsed.filter((v): v is number => typeof v === "number"));
  } catch {
    // Corrupt or unavailable storage (private mode, quota) must not break the
    // page -- an empty watchlist is a safe degradation.
    return new Set();
  }
}

export function useWatchlist(season: string) {
  // Starts empty on both server and first client render so hydration matches;
  // the stored value lands in the effect below.
  const [ids, setIds] = useState<Set<number>>(() => new Set());
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    setIds(read(season));
    setHydrated(true);
  }, [season]);

  const persist = useCallback(
    (next: Set<number>) => {
      setIds(next);
      try {
        window.localStorage.setItem(storageKey(season), JSON.stringify([...next]));
      } catch {
        /* quota or private mode: keep the in-memory state, drop persistence */
      }
    },
    [season],
  );

  const toggle = useCallback(
    (playerId: number) => {
      const next = new Set(ids);
      if (next.has(playerId)) next.delete(playerId);
      else next.add(playerId);
      persist(next);
    },
    [ids, persist],
  );

  const clear = useCallback(() => persist(new Set()), [persist]);

  return { ids, toggle, clear, hydrated, size: ids.size };
}
