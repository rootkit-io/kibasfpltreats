/**
 * BFF route: GET /api/admin/projections/runs/{runId}
 * -> FastAPI GET /api/v1/admin/projections/runs/{run_id}
 *
 * One run's full state: header metadata plus the persisted result tables
 * (same `tables` shape as the run endpoint's preview), used by the
 * HistorySidebar to re-hydrate the ProjectionsGrid for a past run.
 * 404 (unknown run) and 503 (no database) pass through verbatim.
 */

import { NextRequest, NextResponse } from "next/server";
import { proxyGetFromBackend } from "@/lib/api/bff";

export const dynamic = "force-dynamic";
export const maxDuration = 60;

const RUN_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ runId: string }> },
) {
  const { runId } = await params;
  // Validate the path segment before it touches an upstream URL.
  if (!RUN_ID_PATTERN.test(runId)) {
    return NextResponse.json({ error: "invalid run id" }, { status: 400 });
  }
  return proxyGetFromBackend(`/api/v1/admin/projections/runs/${runId}`);
}
