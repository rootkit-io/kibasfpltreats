/**
 * BFF proxy core: every browser call to the FPL backend rides through here.
 *
 * Security posture (mirrors the backend's fail-closed auth, ADR-0001..0004):
 * - `ADMIN_API_TOKEN` lives ONLY in server env; it is injected here and
 *   never serialized into any client bundle or response.
 * - Unconfigured token => 503 (fail closed), matching FastAPI's behaviour.
 * - Upstream 4xx bodies (especially the contract 400s with `loc` paths) are
 *   passed through VERBATIM -- the UI maps them back to CSV line numbers.
 */

import { NextResponse } from "next/server";

const BACKEND_URL = process.env.FPL_BACKEND_URL ?? "http://127.0.0.1:8000";

/** Sync API by decision: a full run (MC + draft save) is one long request. */
const UPSTREAM_TIMEOUT_MS = 280_000;

/** Reads (run history, single-run re-hydration) are bounded queries. */
const READ_TIMEOUT_MS = 30_000;

async function forwardToBackend(
  path: string,
  init: { method: "GET" | "POST"; payload?: unknown; timeoutMs: number },
): Promise<NextResponse> {
  const token = process.env.ADMIN_API_TOKEN;
  if (!token) {
    return NextResponse.json(
      { error: "admin token not configured on the server" },
      { status: 503 },
    );
  }

  try {
    const upstream = await fetch(`${BACKEND_URL}${path}`, {
      method: init.method,
      headers: {
        ...(init.payload === undefined
          ? {}
          : { "Content-Type": "application/json" }),
        "X-Admin-Token": token,
      },
      body:
        init.payload === undefined ? undefined : JSON.stringify(init.payload),
      cache: "no-store",
      signal: AbortSignal.timeout(init.timeoutMs),
    });

    const body = await upstream.json().catch(() => null);
    if (body === null) {
      return NextResponse.json(
        { error: "invalid response from projection backend" },
        { status: 502 },
      );
    }
    // Pass status AND body through: 200 previews, 400 contract errors,
    // 401/503 auth states, 404/409 publish conflicts all surface as-is.
    return NextResponse.json(body, { status: upstream.status });
  } catch (error) {
    const timedOut = error instanceof Error && error.name === "TimeoutError";
    return NextResponse.json(
      {
        error: timedOut
          ? "projection run exceeded the request timeout"
          : "projection backend unreachable",
      },
      { status: timedOut ? 504 : 502 },
    );
  }
}

/** POST proxy: run + publish mutations (long timeout, JSON payload). */
export async function proxyToBackend(
  path: string,
  payload: unknown,
): Promise<NextResponse> {
  return forwardToBackend(path, {
    method: "POST",
    payload,
    timeoutMs: UPSTREAM_TIMEOUT_MS,
  });
}

/** GET proxy: run-history reads (short timeout, no body). */
export async function proxyGetFromBackend(path: string): Promise<NextResponse> {
  return forwardToBackend(path, { method: "GET", timeoutMs: READ_TIMEOUT_MS });
}
