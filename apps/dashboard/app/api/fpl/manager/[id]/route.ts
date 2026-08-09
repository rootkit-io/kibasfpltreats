/**
 * FPL manager proxy — fetches entry + history + transfers in parallel and
 * returns them as a single aggregate. Clerk auth required.
 */
import { auth } from "@clerk/nextjs/server";
import { NextRequest, NextResponse } from "next/server";

const FPL = "https://fantasy.premierleague.com/api";
const UA = "KibasFPLTreats/1.0 (https://kibasfpltreats.com)";
const HEADERS = { "User-Agent": UA };
const MAX_ID = 100_000_000;

function err(msg: string, status: number) {
  return NextResponse.json({ error: msg }, { status });
}

async function fpl(path: string) {
  const r = await fetch(`${FPL}${path}`, {
    headers: HEADERS,
    cache: "no-store",
    signal: AbortSignal.timeout(10_000),
  });
  if (!r.ok) throw new Error(`FPL ${r.status}`);
  return r.json();
}

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { userId } = await auth();
  if (!userId) return err("unauthorized", 401);

  const { id } = await params;
  const managerId = parseInt(id, 10);
  if (!Number.isFinite(managerId) || managerId < 1 || managerId > MAX_ID)
    return err("invalid manager id", 400);

  try {
    const [profile, history, transfers, bootstrap] = await Promise.all([
      fpl(`/entry/${managerId}/`),
      fpl(`/entry/${managerId}/history/`),
      fpl(`/entry/${managerId}/transfers/`),
      fpl("/bootstrap-static/"),
    ]);

    const playerMap = new Map<number, string>(
      ((bootstrap.elements ?? []) as Array<{ id: number; web_name: string }>).map(
        (p) => [p.id, p.web_name],
      ),
    );

    const enrichedTransfers = ((transfers ?? []) as Array<Record<string, unknown>>).map(
      (t) => ({
        ...t,
        element_in_name: typeof t.element_in === "number" ? (playerMap.get(t.element_in) ?? null) : null,
        element_out_name: typeof t.element_out === "number" ? (playerMap.get(t.element_out) ?? null) : null,
      }),
    );

    return NextResponse.json(
      {
        profile,
        history,
        transfers: enrichedTransfers,
        total_players: bootstrap.total_players ?? null,
      },
      {
        headers: { "Cache-Control": "public, max-age=60, stale-while-revalidate=300" },
      },
    );
  } catch (e) {
    const timedOut = e instanceof Error && e.name === "TimeoutError";
    return err(timedOut ? "FPL API timed out" : "FPL API unavailable", timedOut ? 504 : 502);
  }
}
