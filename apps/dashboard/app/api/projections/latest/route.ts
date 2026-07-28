/**
 * BFF route: GET /api/projections/latest[?gameweek=N]
 * -> FastAPI GET /api/v1/public/projections/latest
 *
 * Injects the Clerk session JWT as Authorization: Bearer (Phase 13).
 * Forwards 429 verbatim so the UI can render a rate-limit message (Phase 14).
 */

import { auth } from "@clerk/nextjs/server";
import { NextRequest, NextResponse } from "next/server";
import { proxyGetFromBackend } from "@/lib/api/bff";

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  const gameweek = request.nextUrl.searchParams.get("gameweek");
  if (gameweek !== null && !/^\d{1,2}$/.test(gameweek)) {
    return NextResponse.json({ error: "invalid gameweek" }, { status: 400 });
  }
  const { getToken } = await auth();
  const token = await getToken();
  const query = gameweek === null ? "" : `?gameweek=${gameweek}`;
  return proxyGetFromBackend(`/api/v1/public/projections/latest${query}`, token);
}
