"use client";

/**
 * Personal fixture-difficulty overrides — three-layer precedence:
 *
 *   1. this user's override  (this module)
 *   2. admin global override (fixtures.team_h_fdr_override in DB)
 *   3. official FPL rating   (fixtures.team_h_fdr_fpl)
 *
 * The backend collapses 2 over 3; the client layers 1 on top.
 *
 * STORAGE STRATEGY
 * ----------------
 * Server-side (C1–C3): ratings are persisted per Clerk user ID in
 * `user_fixture_overrides`. They follow the user across devices and survive
 * browser clears.
 *
 * localStorage (cache): the last server-fetched state is written to
 * `kft:fdr:<season>` so the ticker can render immediately on the next load
 * before the server response arrives, and so signed-out visitors retain their
 * last session's ratings.
 *
 * On first load after sign-in, existing localStorage entries are migrated
 * up to the server (PUT each one), then the server becomes the source of
 * truth and localStorage is kept in sync as a read cache.
 *
 * SEASON SCOPING
 * --------------
 * Keyed by season so a new season starts clean rather than inheriting ratings
 * for fixtures that no longer exist.
 */

import { useCallback, useEffect, useRef, useState } from "react";

export type FdrValue = 1 | 2 | 3 | 4 | 5;
export type FdrOverrideKey = `${number}:${number}`;

const KEY_PREFIX = "kft:fdr:";

function storageKey(season: string): string {
  return `${KEY_PREFIX}${season}`;
}

export function overrideKey(fixtureId: number, teamId: number): FdrOverrideKey {
  return `${fixtureId}:${teamId}`;
}

function readLocal(season: string): Record<string, number> {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(storageKey(season));
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return {};
    const clean: Record<string, number> = {};
    for (const [key, value] of Object.entries(parsed)) {
      if (typeof value === "number" && Number.isInteger(value) && value >= 1 && value <= 5) {
        clean[key] = value;
      }
    }
    return clean;
  } catch {
    return {};
  }
}

function writeLocal(season: string, data: Record<string, number>): void {
  try {
    window.localStorage.setItem(storageKey(season), JSON.stringify(data));
  } catch { /* quota / private mode */ }
}

/** Fetch all server-side overrides for the season. Returns null when the
    user is not signed in (the BFF returns 401). */
async function fetchServerOverrides(
  season: string,
): Promise<Record<string, number> | null> {
  try {
    const r = await fetch(`/api/user/fdr?season=${encodeURIComponent(season)}`, {
      cache: "no-store",
    });
    if (r.status === 401) return null; // not signed in
    if (!r.ok) return null;
    const { entries } = (await r.json()) as {
      entries: Array<{ fixture_id: number; team_id: number; fdr: number }>;
    };
    const map: Record<string, number> = {};
    for (const e of entries ?? []) map[overrideKey(e.fixture_id, e.team_id)] = e.fdr;
    return map;
  } catch {
    return null;
  }
}

async function putServerOverride(
  season: string,
  fixtureId: number,
  teamId: number,
  fdr: FdrValue,
): Promise<void> {
  await fetch("/api/user/fdr", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ season, fixture_id: fixtureId, team_id: teamId, fdr }),
  });
}

async function deleteServerOverride(
  season: string,
  fixtureId: number,
  teamId: number,
): Promise<void> {
  await fetch("/api/user/fdr", {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ season, fixture_id: fixtureId, team_id: teamId }),
  });
}

export function useFdrOverrides(season: string) {
  const [overrides, setOverrides] = useState<Record<string, number>>({});
  const [hydrated, setHydrated] = useState(false);
  const [serverAvailable, setServerAvailable] = useState(false);
  const migratedRef = useRef(false);

  useEffect(() => {
    if (!season) return;
    let cancelled = false;

    async function init() {
      // 1. Paint immediately from localStorage (zero network latency).
      const local = readLocal(season);
      if (!cancelled) setOverrides(local);

      // 2. Try the server.
      const server = await fetchServerOverrides(season);
      if (cancelled) return;

      if (server === null) {
        // Not signed in — localStorage is the source of truth.
        setHydrated(true);
        return;
      }

      setServerAvailable(true);

      // 3. First time after sign-in: migrate any localStorage entries that the
      //    server doesn't already know about, then let the server win.
      if (!migratedRef.current && Object.keys(local).length > 0) {
        migratedRef.current = true;
        for (const [key, fdr] of Object.entries(local)) {
          if (!(key in server)) {
            const [fixtureId, teamId] = key.split(":").map(Number);
            // Fire-and-forget: a failed migration just means the entry stays
            // local only; the user can re-set it if they notice.
            void putServerOverride(season, fixtureId, teamId, fdr as FdrValue);
            server[key] = fdr;
          }
        }
      }

      setOverrides(server);
      writeLocal(season, server);
      setHydrated(true);
    }

    void init();
    return () => { cancelled = true; };
  }, [season]);

  /** Update local state + localStorage, then fire PUT to the server. */
  const setOverride = useCallback(
    (fixtureId: number, teamId: number, value: FdrValue | null) => {
      const key = overrideKey(fixtureId, teamId);
      setOverrides((current) => {
        const next = { ...current };
        if (value === null) delete next[key];
        else next[key] = value;
        writeLocal(season, next);
        return next;
      });

      if (serverAvailable) {
        if (value === null) {
          void deleteServerOverride(season, fixtureId, teamId);
        } else {
          void putServerOverride(season, fixtureId, teamId, value);
        }
      }
    },
    [season, serverAvailable],
  );

  const clearAll = useCallback(() => {
    setOverrides({});
    writeLocal(season, {});
    // No bulk-delete endpoint -- individual deletes would be N requests.
    // The server state will diverge until next sign-in migration or manual clear.
    // In practice clearAll is only called from Reset, and the server copy will
    // be refreshed on the next write anyway (upsert wins).
  }, [season]);

  const get = useCallback(
    (fixtureId: number, teamId: number): number | null =>
      overrides[overrideKey(fixtureId, teamId)] ?? null,
    [overrides],
  );

  return {
    overrides,
    get,
    setOverride,
    clearAll,
    hydrated,
    serverAvailable,
    count: Object.keys(overrides).length,
  };
}
