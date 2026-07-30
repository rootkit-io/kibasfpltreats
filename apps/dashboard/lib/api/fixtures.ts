/**
 * Fixture difficulty: wire types and FDR derivation.
 *
 * The backend returns the model's raw per-fixture quantities; difficulty
 * banding happens here so the ticker's three modes stay consistent from one
 * payload:
 *
 *   attack  -- how many goals THIS team is expected to score (higher = easier)
 *   defense -- this team's clean-sheet probability      (higher = easier)
 *   general -- the mean of the two normalised scores
 *
 * Banding is RANK-based across the whole payload rather than fixed
 * thresholds: goal expectations vary by season and model revision, so fixed
 * cutoffs would drift. Quintiles keep the 1-5 scale meaningful whatever the
 * absolute numbers look like.
 */

import { z } from "zod";

const num = z.number().nullish();

export const fixtureRowSchema = z
  .object({
    fixture_id: z.number(),
    gameweek_id: z.number().nullish(),
    kickoff_time: z.string().nullish(),
    home_team: z.string().nullish(),
    away_team: z.string().nullish(),
    home_goals_lambda: num,
    away_goals_lambda: num,
    home_cs_prob: num,
    away_cs_prob: num,
    projection_source: z.string().nullish(),
  })
  .passthrough();

export type FixtureRow = z.infer<typeof fixtureRowSchema>;

export const latestFixturesSchema = z.object({
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

export type DifficultyMode = "general" | "attack" | "defense";

/** One team's fixture within one gameweek. */
export interface TeamFixture {
  team: string;
  opponent: string;
  gameweek: number;
  isHome: boolean;
  /** Raw mode scores; higher = easier. Null when the model omitted them. */
  attackScore: number | null;
  defenseScore: number | null;
}

/** Explode each fixture into its two team-perspective rows. */
export function toTeamFixtures(rows: FixtureRow[]): TeamFixture[] {
  const out: TeamFixture[] = [];
  for (const row of rows) {
    const gw = row.gameweek_id;
    if (typeof gw !== "number" || !row.home_team || !row.away_team) continue;
    out.push({
      team: row.home_team,
      opponent: row.away_team,
      gameweek: gw,
      isHome: true,
      attackScore: numeric(row.home_goals_lambda),
      defenseScore: numeric(row.home_cs_prob),
    });
    out.push({
      team: row.away_team,
      opponent: row.home_team,
      gameweek: gw,
      isHome: false,
      attackScore: numeric(row.away_goals_lambda),
      defenseScore: numeric(row.away_cs_prob),
    });
  }
  return out;
}

function numeric(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** Min-max normalise to [0,1]; a flat series maps to 0.5. */
function normalise(values: (number | null)[]): (number | null)[] {
  const present = values.filter((v): v is number => v !== null);
  if (present.length === 0) return values.map(() => null);
  const lo = Math.min(...present);
  const hi = Math.max(...present);
  const span = hi - lo;
  return values.map((v) => (v === null ? null : span === 0 ? 0.5 : (v - lo) / span));
}

/**
 * Score each team-fixture for a mode, then band into 1 (easiest) - 5
 * (hardest) by quintile of the resulting distribution.
 */
export function bandDifficulty(
  fixtures: TeamFixture[],
  mode: DifficultyMode,
): Map<string, number> {
  const attack = normalise(fixtures.map((f) => f.attackScore));
  const defense = normalise(fixtures.map((f) => f.defenseScore));

  const scores = fixtures.map((_, i) => {
    if (mode === "attack") return attack[i];
    if (mode === "defense") return defense[i];
    const parts = [attack[i], defense[i]].filter((v): v is number => v !== null);
    return parts.length ? parts.reduce((a, b) => a + b, 0) / parts.length : null;
  });

  // Rank present scores; higher score (easier) -> lower FDR band.
  const indexed = scores
    .map((score, index) => ({ score, index }))
    .filter((e): e is { score: number; index: number } => e.score !== null)
    .sort((a, b) => b.score - a.score);

  const bands = new Map<string, number>();
  indexed.forEach((entry, rank) => {
    const band = Math.min(5, Math.floor((rank / indexed.length) * 5) + 1);
    bands.set(fixtureKey(fixtures[entry.index]), band);
  });
  return bands;
}

export function fixtureKey(fixture: TeamFixture): string {
  return `${fixture.team}:${fixture.gameweek}`;
}

/** Tailwind classes per FDR band, easy (1) -> hard (5). */
export const FDR_CLASS: Record<number, string> = {
  1: "bg-emerald-500/85 text-emerald-950",
  2: "bg-lime-400/80 text-lime-950",
  3: "bg-zinc-500/50 text-zinc-100",
  4: "bg-orange-500/80 text-orange-950",
  5: "bg-rose-600/85 text-rose-50",
};

/** Sum of bands for a team across the visible gameweeks; lower = easier run. */
export function runDifficulty(
  team: string,
  gameweeks: number[],
  bands: Map<string, number>,
  overrides: Map<string, number>,
): number {
  let total = 0;
  for (const gw of gameweeks) {
    const key = `${team}:${gw}`;
    total += overrides.get(key) ?? bands.get(key) ?? 3;
  }
  return total;
}
