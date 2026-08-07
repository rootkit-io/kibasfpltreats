/**
 * Fixture ticker BFF.
 *
 * The browser only talks to this same-origin route. Clerk tokens and the
 * backend admin credential stay on the server; the latter is never exposed to
 * the client bundle or response.
 */

import { auth } from "@clerk/nextjs/server";
import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";

import { BACKEND_URL } from "@/lib/api/bff";

export const dynamic = "force-dynamic";

const UPSTREAM_TIMEOUT_MS = 30_000;
const seasonSchema = z.string().regex(/^\d{4}$/);
const gameweekSchema = z.coerce.number().int().min(1).max(38);
const patchSchema = z
  .object({
    fixture_id: z.number().int().positive(),
    target_team_id: z.number().int().positive(),
    fdr_override: z.union([z.literal(1), z.literal(2), z.literal(3), z.literal(4), z.literal(5)]).nullable(),
    opponent_team_id: z.number().int().positive().optional(),
  })
  .strict()
  .superRefine((payload, context) => {
    if (payload.opponent_team_id === payload.target_team_id) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["opponent_team_id"],
        message: "opponent_team_id must differ from target_team_id",
      });
    }
  });

type SessionClaims = Record<string, unknown> | null | undefined;

function normalized(value: unknown): string | null {
  return typeof value === "string" && value.trim()
    ? value.trim().toLowerCase()
    : null;
}

function claimEmail(claims: SessionClaims): string | null {
  if (!claims) return null;
  return normalized(claims.email) ?? normalized(claims.email_address);
}

function error(message: string, status: number): NextResponse {
  return NextResponse.json(
    { error: message },
    { status, headers: { "Cache-Control": "no-store" } },
  );
}

function responseFromUpstream(upstream: Response, body: unknown): NextResponse {
  const headers = new Headers({ "Cache-Control": "no-store" });
  const retryAfter = upstream.headers.get("Retry-After");
  if (retryAfter) headers.set("Retry-After", retryAfter);
  return NextResponse.json(body, { status: upstream.status, headers });
}

async function fetchBackend(
  path: string,
  init: RequestInit,
): Promise<Response | NextResponse> {
  try {
    return await fetch(`${BACKEND_URL}${path}`, {
      ...init,
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });
  } catch (caught) {
    const timedOut = caught instanceof Error && caught.name === "TimeoutError";
    return error(
      timedOut ? "fixture backend timed out" : "fixture backend unreachable",
      timedOut ? 504 : 502,
    );
  }
}

async function sessionToken(): Promise<string | NextResponse> {
  const { userId, getToken } = await auth();
  if (!userId) return error("unauthorized", 401);

  const token = await getToken();
  return token ?? error("unauthorized", 401);
}

function requestedReadQuery(request: NextRequest):
  | URLSearchParams
  | NextResponse {
  const allowed = new Set(["season", "gameweek"]);
  for (const [key] of request.nextUrl.searchParams) {
    if (!allowed.has(key) || request.nextUrl.searchParams.getAll(key).length !== 1) {
      return error("invalid fixture query", 400);
    }
  }

  const query = new URLSearchParams();
  const season = request.nextUrl.searchParams.get("season");
  const gameweek = request.nextUrl.searchParams.get("gameweek");
  if (season !== null && !seasonSchema.safeParse(season).success) {
    return error("invalid season", 400);
  }
  if (gameweek !== null && !gameweekSchema.safeParse(gameweek).success) {
    return error("invalid gameweek", 400);
  }
  if (season !== null) query.set("season", season);
  if (gameweek !== null) query.set("gameweek", gameweek);
  return query;
}

function requestedSeason(request: NextRequest): string | NextResponse | null {
  const values = request.nextUrl.searchParams.getAll("season");
  if (values.length > 1) return error("invalid season", 400);
  if (values.length === 0) return null;
  return seasonSchema.safeParse(values[0]).success
    ? values[0]
    : error("invalid season", 400);
}

async function resolveCurrentSeason(token: string): Promise<string | NextResponse> {
  const upstream = await fetchBackend("/api/v1/public/fixtures", {
    method: "GET",
    headers: { Authorization: `Bearer ${token}` },
  });
  if (upstream instanceof NextResponse) return upstream;

  const body: unknown = await upstream.json().catch(() => null);
  if (body === null) return error("invalid response from fixture backend", 502);
  if (!upstream.ok) return responseFromUpstream(upstream, body);
  if (typeof body !== "object" || body === null || !("season" in body)) {
    return error("fixture backend did not return a current season", 502);
  }
  const season = seasonSchema.safeParse(body.season);
  return season.success
    ? season.data
    : error("fixture backend did not return a current season", 502);
}

function hasAdminAccess(claims: SessionClaims, orgRole: string | null | undefined): boolean | NextResponse {
  const allowedEmail = normalized(process.env.ADMIN_EMAIL);
  const allowedRole = normalized(process.env.ADMIN_ROLE);
  if (!allowedEmail && !allowedRole) {
    return error("admin authorization is not configured", 503);
  }

  const emailAllowed =
    allowedEmail !== null && claimEmail(claims) === allowedEmail;
  const roleAllowed = allowedRole !== null && normalized(orgRole) === allowedRole;
  return emailAllowed || roleAllowed;
}

/**
 * CSRF same-origin check that survives a TLS-terminating reverse proxy.
 *
 * Comparing `Origin` to `nextUrl.origin` directly rejected EVERY legitimate
 * request in production. Caddy terminates TLS and forwards over plain HTTP,
 * so the app computes `nextUrl.origin` as `http://<host>` while the browser
 * sends `Origin: https://<host>`. The scheme differs, the strings differ, and
 * the request 403s before it ever reaches the auth check.
 *
 * Comparing HOSTS rather than full origins fixes it without weakening the
 * guard: the browser sets `Origin` itself, so a page on another site still
 * presents its own host and is still rejected. The proxy's `X-Forwarded-Host`
 * is accepted alongside `Host` for deployments that rewrite it.
 */
function isSameOrigin(request: NextRequest): boolean {
  const origin = request.headers.get("origin");
  // Browsers always send Origin on a cross-origin-capable method like PATCH,
  // so a missing header is treated as untrusted.
  if (!origin) return false;

  let originHost: string;
  try {
    originHost = new URL(origin).host;
  } catch {
    return false;
  }

  const allowed = new Set(
    [
      request.headers.get("x-forwarded-host"),
      request.headers.get("host"),
      request.nextUrl.host,
    ].filter((value): value is string => Boolean(value)),
  );
  return allowed.has(originHost);
}

export async function GET(request: NextRequest): Promise<NextResponse> {
  const token = await sessionToken();
  if (token instanceof NextResponse) return token;

  const query = requestedReadQuery(request);
  if (query instanceof NextResponse) return query;
  const queryString = query.toString();
  const upstream = await fetchBackend(
    `/api/v1/public/fixtures${queryString ? `?${queryString}` : ""}`,
    { method: "GET", headers: { Authorization: `Bearer ${token}` } },
  );
  if (upstream instanceof NextResponse) return upstream;

  const body: unknown = await upstream.json().catch(() => null);
  if (body === null) return error("invalid response from fixture backend", 502);
  return responseFromUpstream(upstream, body);
}

export async function PATCH(request: NextRequest): Promise<NextResponse> {
  if (!isSameOrigin(request)) return error("forbidden", 403);

  const { userId, orgRole, sessionClaims, getToken } = await auth();
  if (!userId) return error("unauthorized", 401);

  const authorized = hasAdminAccess(sessionClaims, orgRole);
  if (authorized instanceof NextResponse) return authorized;
  if (!authorized) return error("forbidden", 403);

  const adminToken = process.env.ADMIN_API_TOKEN;
  if (!adminToken) return error("admin token is not configured", 503);

  const parsed = patchSchema.safeParse(await request.json().catch(() => null));
  if (!parsed.success) return error("invalid fixture override payload", 400);

  const requested = requestedSeason(request);
  if (requested instanceof NextResponse) return requested;
  const clerkToken = await getToken();
  if (!clerkToken) return error("unauthorized", 401);
  const season = requested ?? (await resolveCurrentSeason(clerkToken));
  if (season instanceof NextResponse) return season;

  const { fixture_id, ...payload } = parsed.data;
  const query = new URLSearchParams({ season });
  // The FastAPI contract chooses scope through query parameters; keep that
  // implementation detail server-side while preserving the ticker's payload.
  if (payload.opponent_team_id === undefined) query.set("fixture_id", String(fixture_id));

  const upstream = await fetchBackend(`/api/v1/admin/fixtures/fdr?${query}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      "X-Admin-Token": adminToken,
    },
    body: JSON.stringify(payload),
  });
  if (upstream instanceof NextResponse) return upstream;

  const body: unknown = await upstream.json().catch(() => null);
  if (body === null) return error("invalid response from fixture backend", 502);
  return responseFromUpstream(upstream, body);
}
