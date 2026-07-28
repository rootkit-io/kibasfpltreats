/**
 * BFF route: POST /api/admin/projections/run
 * -> FastAPI POST /api/v1/admin/projections/run
 *
 * Accepts the client's JSON payload (contract states parsed from the weekly
 * CSV -- never a file), injects X-Admin-Token server-side, and returns the
 * upstream response verbatim (200 preview+run_id, 400 field errors, etc.).
 */

import { NextRequest, NextResponse } from "next/server";
import { proxyToBackend } from "@/lib/api/bff";

export const dynamic = "force-dynamic"; // admin traffic is never cached
export const maxDuration = 300; // seconds; matches the sync-run decision

export async function POST(request: NextRequest) {
  let payload: unknown;
  try {
    payload = await request.json();
  } catch {
    return NextResponse.json(
      { error: "request body must be JSON" },
      { status: 400 },
    );
  }
  return proxyToBackend("/api/v1/admin/projections/run", payload);
}
