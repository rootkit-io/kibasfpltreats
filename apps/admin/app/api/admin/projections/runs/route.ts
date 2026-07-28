/**
 * BFF route: GET /api/admin/projections/runs[?limit=N]
 * -> FastAPI GET /api/v1/admin/projections/runs
 *
 * Run history for the HistorySidebar: metadata-only summaries, most recent
 * first. X-Admin-Token is injected server-side (never in the browser).
 */

import { NextRequest, NextResponse } from "next/server";
import { proxyGetFromBackend } from "@/lib/api/bff";

export const dynamic = "force-dynamic"; // admin traffic is never cached
export const maxDuration = 60;

export async function GET(request: NextRequest) {
  const limit = request.nextUrl.searchParams.get("limit");
  // Validate before it touches an upstream URL; backend clamps to 1..100.
  if (limit !== null && !/^\d{1,3}$/.test(limit)) {
    return NextResponse.json({ error: "invalid limit" }, { status: 400 });
  }
  const query = limit === null ? "" : `?limit=${limit}`;
  return proxyGetFromBackend(`/api/v1/admin/projections/runs${query}`);
}
