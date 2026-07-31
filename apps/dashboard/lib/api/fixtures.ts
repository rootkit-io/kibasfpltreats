/**
 * Fixture API contracts.
 *
 * `FixtureRow` / `parseLatestFixtures` remain for the published-run ticker
 * until its server loader is moved to the dedicated FDR BFF route. The FDR
 * widget below consumes the strict `FdrFixture` contract instead.
 */

import { z } from "zod";

const nullableNumber = z.number().nullable();

// ---------------------------------------------------------------- legacy published-fixture contract

export const fixtureRowSchema = z
  .object({
    fixture_id: z.number(),
    gameweek_id: z.number().nullish(),
    kickoff_time: z.string().nullish(),
    home_team: z.string().nullish(),
    away_team: z.string().nullish(),
    home_goals_lambda: nullableNumber.optional(),
    away_goals_lambda: nullableNumber.optional(),
    home_cs_prob: nullableNumber.optional(),
    away_cs_prob: nullableNumber.optional(),
    projection_source: z.string().nullish(),
  })
  .passthrough();

export type FixtureRow = z.infer<typeof fixtureRowSchema>;

const latestFixturesSchema = z.object({
  run: z.object({ run_id: z.string() }).passthrough(),
  count: z.number(),
  fixtures: z.array(z.unknown()),
});

export function parseLatestFixtures(payload: unknown): FixtureRow[] {
  const envelope = latestFixturesSchema.parse(payload);
  return envelope.fixtures.flatMap((row) => {
    const parsed = fixtureRowSchema.safeParse(row);
    return parsed.success ? [parsed.data] : [];
  });
}

// --------------------------------------------------------------------- FDR contract

export const fdrValueSchema = z.union([
  z.literal(1),
  z.literal(2),
  z.literal(3),
  z.literal(4),
  z.literal(5),
]);

export type FdrValue = z.infer<typeof fdrValueSchema>;

export const fdrFixtureSchema = z
  .object({
    fixture_id: z.number().int().positive(),
    gameweek: z.number().int().min(1).max(38).nullable(),
    kickoff_time: z.string().nullable(),
    finished: z.boolean(),
    team_h_id: z.number().int().positive(),
    team_h_short_name: z.string().nullable(),
    team_a_id: z.number().int().positive(),
    team_a_short_name: z.string().nullable(),
    team_h_fdr: fdrValueSchema.nullable(),
    team_a_fdr: fdrValueSchema.nullable(),
  })
  .strict();

export type FdrFixture = z.infer<typeof fdrFixtureSchema>;

const fixturesResponseSchema = z
  .object({
    season: z.string().nullable(),
    gameweek: z.number().int().min(1).max(38).nullable(),
    count: z.number().int().nonnegative(),
    fixtures: z.array(fdrFixtureSchema),
  })
  .strict();

export type FixturesResponse = z.infer<typeof fixturesResponseSchema>;

export interface GetFixturesOptions {
  season?: string;
  gameweek?: number;
  signal?: AbortSignal;
}

export interface PatchFixtureFDRInput {
  fixture_id: number;
  target_team_id: number;
  fdr_override: FdrValue | null;
  opponent_team_id?: number;
}

export interface PatchFixtureFDRResult {
  updated: number;
  updated_fixture_ids: number[];
}

export class FixturesApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "FixturesApiError";
  }
}

function fixturesUrl({ season, gameweek }: GetFixturesOptions): string {
  const params = new URLSearchParams();
  if (season) params.set("season", season);
  if (gameweek !== undefined) params.set("gameweek", String(gameweek));
  const query = params.toString();
  return query ? `/api/projections/fixtures?${query}` : "/api/projections/fixtures";
}

async function responseMessage(response: Response): Promise<string> {
  const body: unknown = await response.json().catch(() => null);
  if (
    body !== null &&
    typeof body === "object" &&
    "error" in body &&
    typeof body.error === "string"
  ) {
    return body.error;
  }
  return `Fixture request failed (${response.status})`;
}

/** Fetch the effective FDR values exposed by the dashboard BFF. */
export async function getFixtures(
  options: GetFixturesOptions = {},
): Promise<FixturesResponse> {
  const response = await fetch(fixturesUrl(options), {
    method: "GET",
    cache: "no-store",
    signal: options.signal,
  });
  if (!response.ok) {
    throw new FixturesApiError(await responseMessage(response), response.status);
  }
  return fixturesResponseSchema.parse(await response.json());
}

/** Persist an FDR override through the dashboard BFF. */
export async function patchFixtureFDR(
  input: PatchFixtureFDRInput,
): Promise<PatchFixtureFDRResult> {
  const response = await fetch("/api/projections/fixtures", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    throw new FixturesApiError(await responseMessage(response), response.status);
  }

  return z
    .object({
      updated: z.number().int().nonnegative(),
      updated_fixture_ids: z.array(z.number().int().positive()),
    })
    .passthrough()
    .parse(await response.json());
}
