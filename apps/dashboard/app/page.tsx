/**
 * Public dashboard home (Phase 13) -- async Server Component, Clerk-gated.
 *
 * Auth flow:
 *   1. clerkMiddleware() (middleware.ts) populates auth() for this request.
 *   2. (await auth()).protect() redirects unauthenticated users to Clerk's
 *      hosted sign-in page -- no manual redirect logic needed.
 *   3. getToken() returns the session JWT already in the request context.
 *   4. The JWT is forwarded as Authorization: Bearer to the backend fetch
 *      (server-side only -- never touches the browser).
 */

import { auth } from "@clerk/nextjs/server";
import { UserButton } from "@clerk/nextjs";
import { CalendarRange, Clock3, Cpu, Sparkles } from "lucide-react";

import PublicProjectionsGrid from "@/components/dashboard/PublicProjectionsGrid";
import { BACKEND_URL } from "@/lib/api/bff";
import {
  parseLatestProjections,
  seasonLabel,
  type LatestProjections,
} from "@/lib/validations/projections";

export const dynamic = "force-dynamic";

type FetchResult =
  | { status: "ok"; data: LatestProjections }
  | { status: "empty" }
  | { status: "rate_limited"; retryAfter: number }
  | { status: "unavailable" };

async function fetchLatest(token: string | null): Promise<FetchResult> {
  try {
    const headers: Record<string, string> = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;

    const response = await fetch(
      `${BACKEND_URL}/api/v1/public/projections/latest`,
      { cache: "no-store", headers, signal: AbortSignal.timeout(30_000) },
    );
    if (response.status === 404) return { status: "empty" };
    if (response.status === 429) {
      const retryAfter = parseInt(response.headers.get("Retry-After") ?? "60", 10);
      return { status: "rate_limited", retryAfter };
    }
    if (!response.ok) return { status: "unavailable" };
    return { status: "ok", data: parseLatestProjections(await response.json()) };
  } catch {
    return { status: "unavailable" };
  }
}

function gwRange(
  start: number | null | undefined,
  end: number | null | undefined,
): string {
  if (start == null && end == null) return "—";
  if (start != null && end != null)
    return start === end ? `GW ${start}` : `GW ${start}–${end}`;
  return `GW ${start ?? end}`;
}

export default async function DashboardPage() {
  // auth.protect() is a static method on the auth function (Clerk v7).
  // Redirects unauthenticated users to Clerk's hosted sign-in page.
  // auth() returns the session object with getToken() for the backend fetch.
  await auth.protect();
  const { getToken } = await auth();
  const token = await getToken();
  const result = await fetchLatest(token);

  return (
    <main className="mx-auto flex min-h-screen max-w-6xl flex-col gap-6 p-6">
      {/* ------------------------------------------------------------ hero */}
      <header className="flex items-start justify-between gap-4 pt-4">
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <Sparkles className="h-5 w-5 text-muted-foreground" aria-hidden />
            <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">
              Kiba&apos;s FPL Treats — Projections Dashboard
            </h1>
          </div>
          <p className="max-w-2xl text-sm text-muted-foreground">
            Expected minutes, expected points and Monte Carlo outcome brackets
            from the latest published projection run.
          </p>
        </div>
        {/* UserButton renders the signed-in user's avatar + sign-out menu. */}
        <UserButton />
      </header>

      {result.status === "ok" ? (
        <>
          {/* --------------------------------------------- run metadata */}
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-xl border border-border bg-card px-4 py-3 text-xs text-muted-foreground shadow-sm">
            <span className="font-medium text-foreground">
              Season {seasonLabel(result.data.run.season)}
            </span>
            <span className="inline-flex items-center gap-1.5">
              <CalendarRange className="h-3.5 w-3.5" aria-hidden />
              {gwRange(result.data.run.gw_start, result.data.run.gw_end)}
            </span>
            {result.data.run.include_mc && result.data.run.n_sim != null && (
              <span className="inline-flex items-center gap-1.5">
                <Cpu className="h-3.5 w-3.5" aria-hidden />
                {result.data.run.n_sim.toLocaleString()} Monte Carlo sims
              </span>
            )}
            {result.data.run.published_at && (
              <span className="inline-flex items-center gap-1.5">
                <Clock3 className="h-3.5 w-3.5" aria-hidden />
                Published{" "}
                {new Date(result.data.run.published_at).toLocaleString("en-GB", {
                  day: "numeric",
                  month: "short",
                  hour: "2-digit",
                  minute: "2-digit",
                  timeZone: "UTC",
                })}{" "}
                UTC
              </span>
            )}
          </div>

          <PublicProjectionsGrid
            rows={result.data.rows}
            hasSimulations={Boolean(result.data.run.include_mc)}
          />
        </>
      ) : result.status === "rate_limited" ? (
        <section className="flex flex-col items-center gap-3 rounded-xl border border-amber-200 bg-amber-50 px-6 py-16 text-center shadow-sm">
          <span className="rounded-full border border-amber-300 bg-amber-100 px-3 py-1 text-xs font-medium text-amber-800">
            Rate limited
          </span>
          <p className="max-w-md text-sm text-amber-900">
            You&apos;re fetching data too quickly. Please wait{" "}
            {result.retryAfter} second{result.retryAfter === 1 ? "" : "s"} before
            refreshing.
          </p>
        </section>
      ) : (
        <section className="flex flex-col items-center gap-3 rounded-xl border border-dashed border-border bg-card px-6 py-16 text-center shadow-sm">
          <span className="rounded-full border border-border bg-muted px-3 py-1 text-xs font-medium text-muted-foreground">
            {result.status === "empty"
              ? "No published run yet"
              : "Temporarily unavailable"}
          </span>
          <p className="max-w-md text-sm text-muted-foreground">
            {result.status === "empty"
              ? "Projections appear here the moment the first run is published from the Admin Panel."
              : "The projections service is unreachable right now. Refresh in a minute."}
          </p>
        </section>
      )}

      <footer className="pb-4 pt-2 text-center text-xs text-muted-foreground">
        Projections are estimates, not guarantees. Player prices from the
        official FPL API.
      </footer>
    </main>
  );
}
