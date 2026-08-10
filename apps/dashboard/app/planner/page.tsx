/**
 * /planner — Transfer Planner page.
 *
 * Server component: auth guard + parallel fetch of projections and fixtures.
 * Both datasets are passed to PlannerShell so the client starts with real
 * xPts and FDR data rather than waiting for a second round-trip.
 */

import { Suspense } from "react";
import { auth } from "@clerk/nextjs/server";
import { UserButton } from "@clerk/nextjs";

import PlannerShell from "@/components/planner/PlannerShell";
import { BrandLogo } from "@/components/ui/brand-logo";
import { BACKEND_URL } from "@/lib/api/bff";
import {
  parseLatestProjections,
  type ProjectionRow,
} from "@/lib/validations/projections";
import {
  buildFixtureData,
  buildXptsIndex,
  type XptsIndex,
} from "@/lib/planner/xpts";
import type { FixtureData } from "@/lib/planner/types";
import type { FdrFixture } from "@/lib/api/fixtures";
import { fdrFixtureSchema } from "@/lib/api/fixtures";
import { z } from "zod";

export const dynamic = "force-dynamic";

// ── Server-side data fetchers ─────────────────────────────────────────────────

async function fetchProjectionRows(token: string | null): Promise<ProjectionRow[]> {
  try {
    const headers: Record<string, string> = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const r = await fetch(`${BACKEND_URL}/api/v1/public/projections/latest`, {
      cache: "no-store",
      headers,
      signal: AbortSignal.timeout(20_000),
    });
    if (!r.ok) return [];
    return parseLatestProjections(await r.json()).rows;
  } catch {
    return [];
  }
}

const fixturesResponseSchema = z.object({
  fixtures: z.array(fdrFixtureSchema),
}).passthrough();

async function fetchFdrFixtures(token: string | null): Promise<FdrFixture[]> {
  try {
    const headers: Record<string, string> = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const r = await fetch(`${BACKEND_URL}/api/v1/public/fixtures`, {
      cache: "no-store",
      headers,
      signal: AbortSignal.timeout(20_000),
    });
    if (!r.ok) return [];
    const body = fixturesResponseSchema.safeParse(await r.json());
    return body.success ? body.data.fixtures : [];
  } catch {
    return [];
  }
}

// ── Page component ────────────────────────────────────────────────────────────

export default async function PlannerPage({
  searchParams,
}: {
  searchParams: Promise<{ id?: string }>;
}) {
  await auth.protect();
  const { getToken } = await auth();
  const token = await getToken();

  const { id } = await searchParams;
  const managerId = id ? parseInt(id, 10) : null;
  const validId =
    managerId !== null && Number.isFinite(managerId) && managerId > 0
      ? managerId
      : null;

  // Parallel: both datasets are independent of each other and of manager load
  const [projectionRows, fdrFixtures] = await Promise.all([
    fetchProjectionRows(token),
    fetchFdrFixtures(token),
  ]);

  // Build indexes on the server — zero cost on the client
  const xptsIndex = buildXptsIndex(projectionRows);
  const fixtureData = buildFixtureData(fdrFixtures);

  return (
    <main className="min-h-screen bg-background text-foreground">
      <header className="flex items-center justify-between gap-4 border-b border-border bg-card px-6 py-3">
        <div className="flex items-center gap-2">
          <BrandLogo size="sm" className="text-[#FF5F1F]" />
          <span className="text-sm font-semibold text-muted-foreground">
            Kiba&apos;s FPL Treats
          </span>
        </div>
        <div className="flex items-center gap-3">
          <a href="/" className="text-xs text-muted-foreground transition hover:text-foreground">
            Analytics
          </a>
          <a href="/profile" className="text-xs text-muted-foreground transition hover:text-foreground">
            Profile
          </a>
          <UserButton />
        </div>
      </header>

      <Suspense fallback={<PlannerLoadingSkeleton />}>
        <PlannerShell
          initialManagerId={validId}
          xptsIndex={xptsIndex}
          fixtureData={fixtureData}
        />
      </Suspense>
    </main>
  );
}

// ── Loading skeleton ──────────────────────────────────────────────────────────

function PlannerLoadingSkeleton() {
  return (
    <div className="space-y-0">
      <div className="flex items-center gap-3 border-b border-border bg-card px-4 py-2.5">
        <div className="h-4 w-20 animate-pulse rounded bg-muted" />
        <div className="h-4 w-px bg-border" />
        <div className="h-4 w-32 animate-pulse rounded bg-muted" />
        <div className="ml-auto h-7 w-24 animate-pulse rounded bg-muted" />
      </div>
      <div className="flex gap-4 border-b border-border bg-card px-4 py-2">
        <div className="h-4 w-24 animate-pulse rounded bg-muted" />
        <div className="h-4 w-12 animate-pulse rounded bg-muted" />
        <div className="h-4 w-16 animate-pulse rounded bg-muted" />
      </div>
      <div className="grid grid-cols-1 gap-0 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="px-4 py-6 space-y-4">
          <div className="h-3 w-24 animate-pulse rounded bg-muted" />
          <div className="relative overflow-hidden rounded" style={{ minHeight: 360, background: "#1a3a1a" }}>
            {Array.from({ length: 4 }).map((_, row) => (
              <div key={row} className="flex justify-center gap-3 py-4">
                {Array.from({ length: [1, 4, 4, 2][row] }).map((_, i) => (
                  <div key={i} className="h-20 w-16 animate-pulse rounded bg-white/10" />
                ))}
              </div>
            ))}
          </div>
        </div>
        <div className="border-l border-border px-4 py-4 space-y-3">
          <div className="flex gap-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="h-8 flex-1 animate-pulse rounded bg-muted" />
            ))}
          </div>
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="h-14 animate-pulse rounded border border-border bg-muted/50" />
          ))}
        </div>
      </div>
    </div>
  );
}
