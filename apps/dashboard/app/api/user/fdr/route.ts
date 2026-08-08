/**
 * BFF proxy for per-user FDR overrides.
 *
 * GET  /api/user/fdr?season=2627   -> { season, entries: [{fixture_id, team_id, fdr}] }
 * PUT  /api/user/fdr               -> { fixture_id, team_id, fdr }
 * DELETE /api/user/fdr             -> 204
 *
 * Every request is Clerk-authed. The Clerk session token is forwarded to the
 * backend as a Bearer header -- the backend's verify_clerk_token dependency
 * validates it against Clerk's JWKS and returns the sub claim.
 *
 * No admin rights required: difficulty is a personal read.
 */
import { auth } from "@clerk/nextjs/server";
import { type NextRequest, NextResponse } from "next/server";

const BACKEND = process.env.FPL_BACKEND_URL ?? "http://localhost:8000";

function error(message: string, status: number): NextResponse {
  return NextResponse.json({ error: message }, { status });
}

async function sessionToken(): Promise<string | NextResponse> {
  const { getToken, userId } = await auth();
  if (!userId) return error("unauthorized", 401);
  const token = await getToken();
  if (!token) return error("unauthorized", 401);
  return token;
}

export async function GET(request: NextRequest): Promise<NextResponse> {
  const token = await sessionToken();
  if (token instanceof NextResponse) return token;

  const season = request.nextUrl.searchParams.get("season") ?? "";
  const upstream = await fetch(
    `${BACKEND}/api/v1/user/fdr?season=${encodeURIComponent(season)}`,
    { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" },
  ).catch(() => null);

  if (!upstream) return error("backend unavailable", 502);
  const body: unknown = await upstream.json().catch(() => null);
  return NextResponse.json(body ?? {}, { status: upstream.status });
}

export async function PUT(request: NextRequest): Promise<NextResponse> {
  const token = await sessionToken();
  if (token instanceof NextResponse) return token;

  const body: unknown = await request.json().catch(() => null);
  if (!body) return error("invalid payload", 400);

  const upstream = await fetch(`${BACKEND}/api/v1/user/fdr`, {
    method: "PUT",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  }).catch(() => null);

  if (!upstream) return error("backend unavailable", 502);
  const resp: unknown = await upstream.json().catch(() => null);
  return NextResponse.json(resp ?? {}, { status: upstream.status });
}

export async function DELETE(request: NextRequest): Promise<NextResponse> {
  const token = await sessionToken();
  if (token instanceof NextResponse) return token;

  const body: unknown = await request.json().catch(() => null);
  if (!body) return error("invalid payload", 400);

  const upstream = await fetch(`${BACKEND}/api/v1/user/fdr`, {
    method: "DELETE",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  }).catch(() => null);

  if (!upstream) return error("backend unavailable", 502);
  if (upstream.status === 204) return new NextResponse(null, { status: 204 });
  const resp: unknown = await upstream.json().catch(() => null);
  return NextResponse.json(resp ?? {}, { status: upstream.status });
}
