/**
 * Dashboard BFF proxy core (Phase 12/14).
 *
 * Every browser call rides through this Next.js server layer:
 * - no CORS: browser only talks to its own origin;
 * - auth injection point: Bearer token attached server-side (Phase 13);
 * - 429 pass-through: rate-limit responses surface to the UI (Phase 14).
 */

import { NextResponse } from "next/server";

export const BACKEND_URL =
  process.env.FPL_BACKEND_URL ?? "http://127.0.0.1:8000";

const READ_TIMEOUT_MS = 30_000;

export async function proxyGetFromBackend(
  path: string,
  token?: string | null,
): Promise<NextResponse> {
  try {
    const headers: Record<string, string> = {};
    if (token) headers["Authorization"] = `Bearer ${token}`;

    const upstream = await fetch(`${BACKEND_URL}${path}`, {
      method: "GET",
      headers,
      cache: "no-store",
      signal: AbortSignal.timeout(READ_TIMEOUT_MS),
    });

    const body = await upstream.json().catch(() => null);
    if (body === null) {
      return NextResponse.json(
        { error: "invalid response from projection backend" },
        { status: 502 },
      );
    }
    // Pass status AND body through verbatim: 200, 404, 429, 503 all surface.
    return NextResponse.json(body, { status: upstream.status });
  } catch (error) {
    const timedOut = error instanceof Error && error.name === "TimeoutError";
    return NextResponse.json(
      {
        error: timedOut
          ? "projection backend timed out"
          : "projection backend unreachable",
      },
      { status: timedOut ? 504 : 502 },
    );
  }
}
