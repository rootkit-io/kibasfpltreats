import { auth } from "@clerk/nextjs/server";
import { NextRequest, NextResponse } from "next/server";

const FPL = "https://fantasy.premierleague.com/api";
const UA = "KibasFPLTreats/1.0 (https://kibasfpltreats.com)";

function err(msg: string, status: number) {
  return NextResponse.json({ error: msg }, { status });
}

async function fpl(path: string) {
  const r = await fetch(`${FPL}${path}`, {
    headers: { "User-Agent": UA },
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
  if (!Number.isFinite(managerId) || managerId < 1) return err("invalid id", 400);

  try {
    const entry = await fpl(`/entry/${managerId}/`);
    const gw = typeof entry.current_event === "number" ? entry.current_event : null;

    const payload: Record<string, unknown> = {
      fetchedAt: new Date().toISOString(),
      gw,
      summary: {
        summary_overall_points: entry.summary_overall_points ?? null,
        summary_overall_rank: entry.summary_overall_rank ?? null,
        summary_event_points: entry.summary_event_points ?? null,
        summary_event_rank: entry.summary_event_rank ?? null,
        current_event: entry.current_event ?? null,
      },
      leagues: ((entry.leagues?.classic ?? []) as Array<Record<string, unknown>>).map((l) => ({
        id: l.id,
        name: l.name,
        league_type: l.league_type,
        entry_rank: l.entry_rank ?? null,
        entry_last_rank: l.entry_last_rank ?? null,
      })),
      picks: null,
      liveElements: null,
    };

    if (gw) {
      const [picksData, liveData] = await Promise.all([
        fpl(`/entry/${managerId}/event/${gw}/picks/`).catch(() => null),
        fpl(`/event/${gw}/live/`).catch(() => null),
      ]);
      if (picksData?.picks) {
        payload.picks = (picksData.picks as Array<Record<string, unknown>>).map((p) => ({
          element: p.element,
          multiplier: p.multiplier,
          position: p.position,
        }));
      }
      if (liveData?.elements) {
        const pts: Record<number, number> = {};
        (liveData.elements as Array<Record<string, unknown>>).forEach((el) => {
          if (el.id && el.stats) pts[el.id as number] = (el.stats as Record<string, unknown>).total_points as number ?? 0;
        });
        payload.liveElements = pts;
      }
    }

    return NextResponse.json(payload, {
      headers: { "Cache-Control": "public, max-age=45, stale-while-revalidate=120" },
    });
  } catch (e) {
    const to = e instanceof Error && e.name === "TimeoutError";
    return err(to ? "FPL API timed out" : "FPL API unavailable", to ? 504 : 502);
  }
}
