/**
 * BFF route: POST /api/admin/projections/runs/{runId}/publish
 * -> FastAPI POST /api/v1/admin/projections/runs/{run_id}/publish
 *
 * Publishing flips the public dashboard's published_* views, so this stays a
 * deliberate, confirmed, awaited mutation. 404 (unknown run) and 409
 * (archived) pass through for the PublishBar to render.
 */

import { NextRequest, NextResponse } from "next/server";
import { proxyToBackend } from "@/lib/api/bff";

export const dynamic = "force-dynamic";
export const maxDuration = 60;

const RUN_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function POST(
  _request: NextRequest,
  { params }: { params: Promise<{ runId: string }> },
) {
  const { runId } = await params;
  // Validate the path segment before it touches an upstream URL.
  if (!RUN_ID_PATTERN.test(runId)) {
    return NextResponse.json({ error: "invalid run id" }, { status: 400 });
  }
  return proxyToBackend(`/api/v1/admin/projections/runs/${runId}/publish`, undefined);
}
