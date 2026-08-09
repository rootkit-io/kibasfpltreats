import { auth } from "@clerk/nextjs/server";
import { NextRequest, NextResponse } from "next/server";

const FPL = "https://fantasy.premierleague.com/api";
const UA = "KibasFPLTreats/1.0 (https://kibasfpltreats.com)";
const MAX_PAGES = 25;

function err(msg: string, status: number) {
  return NextResponse.json({ error: msg }, { status });
}

export async function GET(
  _req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { userId } = await auth();
  if (!userId) return err("unauthorized", 401);

  const { id } = await params;
  const leagueId = parseInt(id, 10);
  if (!Number.isFinite(leagueId) || leagueId < 1) return err("invalid league id", 400);

  try {
    let page = 1;
    let all: unknown[] = [];
    let hasNext = true;

    while (hasNext && page <= MAX_PAGES) {
      const data = await fetch(
        `${FPL}/leagues-classic/${leagueId}/standings/?page_standings=${page}`,
        { headers: { "User-Agent": UA }, cache: "no-store", signal: AbortSignal.timeout(10_000) },
      ).then((r) => r.json());

      const results = (data?.standings?.results ?? []) as unknown[];
      all = all.concat(results);
      hasNext = Boolean(data?.standings?.has_next);
      page++;
    }

    return NextResponse.json(
      { standings: all },
      { headers: { "Cache-Control": "public, max-age=300, stale-while-revalidate=1800" } },
    );
  } catch (e) {
    const to = e instanceof Error && e.name === "TimeoutError";
    return err(to ? "FPL API timed out" : "FPL API unavailable", to ? 504 : 502);
  }
}
