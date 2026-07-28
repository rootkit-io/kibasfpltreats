/**
 * Zod mirrors of the backend minutes contracts (fpl_xpts/minutes_contract.py):
 * PlayerMinutesState and MinuteOverrideState, plus the weekly-CSV column
 * mapping and a line-numbered pre-flight validator for PapaParse output.
 *
 * Ground rules:
 * - The SERVER contract is the source of truth. These schemas exist for
 *   fast local feedback; a payload that passes here can still 400 upstream,
 *   and the UI must render those errors too (loc -> CSV line = row + 2).
 * - Probabilities mirror the server's acceptance exactly: 0..1, or percent
 *   values in (1, 100] which the server normalises by dividing by 100.
 *   We validate the range; we do NOT normalise -- that is the contract's job.
 */

import { z } from "zod";

// ---------------------------------------------------------------- helpers

/** CSV cells arrive as strings; "" means "not provided". */
const blankToUndefined = (value: unknown) =>
  typeof value === "string" && value.trim() === "" ? undefined : value;

const probability = z.coerce
  .number()
  .min(0, "probability must be >= 0")
  .max(100, "probability must be within 0..1 (or 0..100 as percent)");

const minutes0to90 = z.coerce
  .number()
  .min(0, "minutes must be within 0..90")
  .max(90, "minutes must be within 0..90");

const optionalInt = z.preprocess(
  blankToUndefined,
  z.coerce.number().int().optional(),
);

const optionalString = z.preprocess(
  blankToUndefined,
  z.string().trim().optional(),
);

// --------------------------------------------------------- contract mirrors

/** Mirror of fpl_xpts.minutes_contract.PlayerMinutesState. */
export const playerMinutesStateSchema = z
  .object({
    gameweek: optionalInt,
    player_id: optionalInt,
    player_key: optionalString,
    player: optionalString,
    team: optionalString,
    position: optionalString,
    likely_minutes: minutes0to90,
    start_probability: probability,
    chance_of_playing: z.preprocess(blankToUndefined, probability.optional()),
  })
  .refine((row) => row.player_id !== undefined || !!row.player_key, {
    message: "row needs player_id or player_key",
    path: ["player_id"],
  });

export type PlayerMinutesState = z.infer<typeof playerMinutesStateSchema>;

/** Mirror of fpl_xpts.minutes_contract.MinuteOverrideState. */
export const minuteOverrideStateSchema = z
  .object({
    gameweek: z.coerce.number().int(),
    fixture_in_week: z.preprocess(
      blankToUndefined,
      z.coerce.number().int().min(1, "fixture_in_week must be >= 1").default(1),
    ),
    player_id: optionalInt,
    player_key: optionalString,
    minutes: minutes0to90,
  })
  .refine((row) => row.player_id !== undefined || !!row.player_key, {
    message: "row needs player_id or player_key",
    path: ["player_id"],
  });

export type MinuteOverrideState = z.infer<typeof minuteOverrideStateSchema>;

/** Request body for POST /api/admin/projections/run (BFF -> FastAPI). */
export const projectionRunRequestSchema = z.object({
  manual_minutes: z.array(playerMinutesStateSchema).default([]),
  overrides: z.array(minuteOverrideStateSchema).default([]),
  include_mc: z.boolean().default(false),
  save_as_draft: z.boolean().default(false),
  season: z
    .string()
    .regex(/^\d{4}$/, "season is the short code, e.g. 2627")
    .optional(),
  notes: z.string().max(500).optional(),
});

export type ProjectionRunRequest = z.infer<typeof projectionRunRequestSchema>;

// -------------------------------------------------- weekly CSV -> contract

/**
 * The weekly CSV header (written by the backend's template writer):
 * GW,player_id,player_key,player,team,Pos,start,mins,api_start,api_mins,
 * appearances,total_minutes,chance_of_playing
 *
 * Mapping to contract fields (extra columns are ignored, as on the server):
 *   GW -> gameweek · mins -> likely_minutes · start -> start_probability
 *   Pos -> position · chance_of_playing -> chance_of_playing
 */
export const CSV_TO_CONTRACT: Record<string, keyof PlayerMinutesState> = {
  GW: "gameweek",
  player_id: "player_id",
  player_key: "player_key",
  player: "player",
  team: "team",
  Pos: "position",
  start: "start_probability",
  mins: "likely_minutes",
  chance_of_playing: "chance_of_playing",
};

export interface CsvRowError {
  /** 1-based CSV line number, header included -- same convention as the
   * backend's ManualMinutesError messages. */
  line: number;
  issues: string[];
}

/** One CSV line as the review table renders it: contract-mapped values plus
 * any preflight issues attached to that line (empty = valid row). */
export interface ReviewRow {
  line: number;
  values: Record<string, unknown>;
  issues: string[];
}

/** Build review rows from raw parsed CSV rows + line-numbered errors. */
export function buildReviewRows(
  rows: Record<string, unknown>[],
  errors: CsvRowError[],
): ReviewRow[] {
  const issuesByLine = new Map<number, string[]>();
  for (const error of errors) {
    const existing = issuesByLine.get(error.line) ?? [];
    issuesByLine.set(error.line, [...existing, ...error.issues]);
  }
  return rows.map((raw, index) => ({
    line: index + 2,
    values: csvRowToCandidate(raw),
    issues: issuesByLine.get(index + 2) ?? [],
  }));
}

export interface PreflightResult {
  states: PlayerMinutesState[];
  errors: CsvRowError[];
}

/** Map one raw PapaParse row (header: true) onto contract field names. */
export function csvRowToCandidate(row: Record<string, unknown>): Record<string, unknown> {
  const candidate: Record<string, unknown> = {};
  for (const [csvColumn, field] of Object.entries(CSV_TO_CONTRACT)) {
    if (csvColumn in row) candidate[field] = row[csvColumn];
  }
  return candidate;
}

/**
 * Pre-validate parsed CSV rows against the contract mirror.
 * All-or-nothing like the server: any row error blocks submission, every
 * error carries its CSV line number for grid highlighting.
 */
export function preflightManualMinutes(
  rows: Record<string, unknown>[],
): PreflightResult {
  const states: PlayerMinutesState[] = [];
  const errors: CsvRowError[] = [];
  rows.forEach((row, index) => {
    const parsed = playerMinutesStateSchema.safeParse(csvRowToCandidate(row));
    if (parsed.success) {
      states.push(parsed.data);
    } else {
      errors.push({
        line: index + 2, // 1-based + header row
        issues: parsed.error.issues.map(
          (issue) => `${issue.path.join(".") || "row"}: ${issue.message}`,
        ),
      });
    }
  });
  return { states, errors };
}

// --------------------------------------- overrides CSV -> contract (Phase 10)

/**
 * The minute-overrides CSV (backend contract: fpl_xpts.minutes_contract
 * .load_minute_overrides_csv). Required columns: GW, mins; identity via
 * player_id or player_key; optional fixture_in_week (defaults to 1).
 *
 * Mapping to MinuteOverrideState fields (extra columns are ignored, as on
 * the server): GW -> gameweek · mins -> minutes.
 */
export const OVERRIDES_CSV_TO_CONTRACT: Record<string, keyof MinuteOverrideState> = {
  GW: "gameweek",
  fixture_in_week: "fixture_in_week",
  player_id: "player_id",
  player_key: "player_key",
  mins: "minutes",
};

/** Columns the server hard-requires in an overrides CSV header. */
const OVERRIDES_REQUIRED_COLUMNS = ["GW", "mins"] as const;
const OVERRIDES_IDENTITY_COLUMNS = ["player_id", "player_key"] as const;

export interface OverridesPreflightResult {
  states: MinuteOverrideState[];
  errors: CsvRowError[];
}

/** Map one raw PapaParse row (header: true) onto override field names. */
export function overrideCsvRowToCandidate(
  row: Record<string, unknown>,
): Record<string, unknown> {
  const candidate: Record<string, unknown> = {};
  for (const [csvColumn, field] of Object.entries(OVERRIDES_CSV_TO_CONTRACT)) {
    if (csvColumn in row) candidate[field] = row[csvColumn];
  }
  return candidate;
}

/**
 * Pre-validate parsed minute_overrides.csv rows against the contract mirror.
 * Mirrors the server exactly: header-level column checks first (line 1),
 * then all-or-nothing row validation with 1-based CSV line numbers.
 */
export function preflightMinuteOverrides(
  rows: Record<string, unknown>[],
  fields?: string[],
): OverridesPreflightResult {
  // Header checks mirror the backend's missing-column / identity errors.
  if (fields !== undefined) {
    const headerIssues: string[] = [];
    for (const column of OVERRIDES_REQUIRED_COLUMNS) {
      if (!fields.includes(column)) {
        headerIssues.push(`missing required column: ${column}`);
      }
    }
    if (!OVERRIDES_IDENTITY_COLUMNS.some((column) => fields.includes(column))) {
      headerIssues.push(
        `must contain one of: ${OVERRIDES_IDENTITY_COLUMNS.join(", ")}`,
      );
    }
    if (headerIssues.length > 0) {
      return { states: [], errors: [{ line: 1, issues: headerIssues }] };
    }
  }

  const states: MinuteOverrideState[] = [];
  const errors: CsvRowError[] = [];
  rows.forEach((row, index) => {
    const parsed = minuteOverrideStateSchema.safeParse(
      overrideCsvRowToCandidate(row),
    );
    if (parsed.success) {
      states.push(parsed.data);
    } else {
      errors.push({
        line: index + 2, // 1-based + header row
        issues: parsed.error.issues.map(
          (issue) => `${issue.path.join(".") || "row"}: ${issue.message}`,
        ),
      });
    }
  });
  return { states, errors };
}
