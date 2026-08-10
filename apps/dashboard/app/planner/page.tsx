/**
 * /planner — Transfer Planner page.
 *
 * Server component: auth guard + optional ?id= pre-load.
 * Passes the manager ID (if present in the URL) to PlannerShell so the
 * client can auto-load without showing the entry form first.
 */

import { Suspense } from "react";
import { auth } from "@clerk/nextjs/server";
import { UserButton } from "@clerk/nextjs";
import { redirect } from "next/navigation";

import PlannerShell from "@/components/planner/PlannerShell";
import { BrandLogo } from "@/components/ui/brand-logo";

export const dynamic = "force-dynamic";

export default async function PlannerPage({
  searchParams,
}: {
  searchParams: Promise<{ id?: string }>;
}) {
  await auth.protect();

  const { id } = await searchParams;
  const managerId = id ? parseInt(id, 10) : null;
  const validId =
    managerId !== null && Number.isFinite(managerId) && managerId > 0
      ? managerId
      : null;

  return (
    <main className="min-h-screen bg-background text-foreground">
      {/* Global header — same pattern as the dashboard page */}
      <header className="flex items-center justify-between gap-4 border-b border-border bg-card px-6 py-3">
        <div className="flex items-center gap-2">
          <BrandLogo size="sm" className="text-[#FF5F1F]" />
          <span className="text-sm font-semibold text-muted-foreground">
            Kiba&apos;s FPL Treats
          </span>
        </div>
        <div className="flex items-center gap-3">
          <a
            href="/"
            className="text-xs text-muted-foreground transition hover:text-foreground"
          >
            Analytics
          </a>
          <a
            href="/profile"
            className="text-xs text-muted-foreground transition hover:text-foreground"
          >
            Profile
          </a>
          <UserButton />
        </div>
      </header>

      <Suspense fallback={<PlannerLoadingSkeleton />}>
        <PlannerShell initialManagerId={validId} />
      </Suspense>
    </main>
  );
}

function PlannerLoadingSkeleton() {
  return (
    <div className="space-y-0">
      {/* Nav bar skeleton */}
      <div className="flex items-center gap-3 border-b border-border bg-card px-4 py-2.5">
        <div className="h-4 w-20 animate-pulse rounded bg-muted" />
        <div className="h-4 w-px bg-border" />
        <div className="h-4 w-32 animate-pulse rounded bg-muted" />
        <div className="ml-auto h-7 w-24 animate-pulse rounded bg-muted" />
      </div>
      {/* Bank bar skeleton */}
      <div className="flex gap-4 border-b border-border bg-card px-4 py-2">
        <div className="h-4 w-24 animate-pulse rounded bg-muted" />
        <div className="h-4 w-12 animate-pulse rounded bg-muted" />
        <div className="h-4 w-16 animate-pulse rounded bg-muted" />
      </div>
      {/* Pitch skeleton */}
      <div className="px-4 py-6 space-y-3">
        <div className="h-3 w-20 animate-pulse rounded bg-muted" />
        <div className="grid grid-cols-11 gap-2">
          {Array.from({ length: 11 }).map((_, i) => (
            <div key={i} className="h-16 animate-pulse rounded border border-border bg-muted" />
          ))}
        </div>
        <div className="h-3 w-12 animate-pulse rounded bg-muted" />
        <div className="flex gap-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-10 w-28 animate-pulse rounded border border-border bg-muted" />
          ))}
        </div>
      </div>
    </div>
  );
}
