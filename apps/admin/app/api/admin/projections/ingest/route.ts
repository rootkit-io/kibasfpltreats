/**
 * BFF route: POST /api/admin/projections/ingest
 * -> FastAPI POST /api/v1/admin/projections/ingest-csvs
 *
 * Forwards the two precomputed model exports (`weekly_player_week.csv` and
 * `mc_brackets_full_player_week.csv`) as multipart form data, injecting
 * X-Admin-Token server-side. Upstream status codes are returned verbatim so
 * the UI can distinguish preflight failure modes:
 *
 *   400 malformed CSV / missing columns / files from different runs
 *   409 season dimensions (players, teams, gameweeks) not loaded
 *   422 too many players failed identity resolution -- nothing was written
 */

import { NextRequest, NextResponse } from "next/server";
import { proxyMultipartToBackend } from "@/lib/api/bff";

export const dynamic = "force-dynamic"; // admin traffic is never cached
export const maxDuration = 300; // seconds; large CSVs + bulk insert

const WEEKLY_FIELD = "weekly_file";
const MC_FIELD = "mc_file";

export async function POST(request: NextRequest) {
  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    return NextResponse.json(
      { error: "request body must be multipart form data" },
      { status: 400 },
    );
  }

  // Fail fast on obviously incomplete submissions so we do not spend an
  // upload round-trip to learn the payload was missing a file.
  const missing = [WEEKLY_FIELD, MC_FIELD].filter(
    (field) => !(form.get(field) instanceof File),
  );
  if (missing.length > 0) {
    return NextResponse.json(
      {
        error: `missing required file field(s): ${missing.join(", ")}`,
        expected: {
          [WEEKLY_FIELD]: "weekly_player_week.csv",
          [MC_FIELD]: "mc_brackets_full_player_week.csv",
        },
      },
      { status: 400 },
    );
  }

  if (!String(form.get("season") ?? "").trim()) {
    return NextResponse.json(
      { error: "season is required (neither CSV carries it)" },
      { status: 400 },
    );
  }

  return proxyMultipartToBackend(
    "/api/v1/admin/projections/ingest-csvs",
    form,
  );
}
