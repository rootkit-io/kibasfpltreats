/**
 * Core types for the KFT Transfer Planner.
 *
 * All prices are in FPL's internal unit: tenths of £m.
 * £7.5m is stored as 75. This matches the official API and avoids
 * floating-point drift when adding/subtracting across transfers.
 */

// ── FPL element types ─────────────────────────────────────────────────────────

export type ElementType = 1 | 2 | 3 | 4; // GK | DEF | MID | FWD

export const ELEMENT_TYPE_LABEL: Record<ElementType, string> = {
  1: "GK",
  2: "DEF",
  3: "MID",
  4: "FWD",
};

// ── Chip codes ────────────────────────────────────────────────────────────────

export type ChipCode = "wc" | "fh" | "bb" | "tc";

export const CHIP_DISPLAY: Record<ChipCode, string> = {
  wc: "Wildcard",
  fh: "Free Hit",
  bb: "Bench Boost",
  tc: "Triple Captain",
};

// ── Squad picks ───────────────────────────────────────────────────────────────

/**
 * One player in the squad.
 *
 * `position` is the squad slot (1–15): slots 1–11 are starters (ordered by
 * the lineup plan), slots 12–15 are the bench (12 = first-choice bench GK
 * by convention, 13–15 = ordered outfield bench).
 */
export interface PlannerPick {
  element: number;       // FPL player ID
  position: number;      // 1–15
  multiplier: number;    // 1 = normal, 2 = captain, 3 = TC
  isViceCaptain: boolean;
  purchasePrice: number; // price paid (tenths of £m)
  sellingPrice: number;  // calculated sell price (tenths of £m)
}

// ── Transfer records ──────────────────────────────────────────────────────────

/**
 * One planned transfer.
 *
 * `planOrder` determines the sequence within a GW (earlier planOrder applied
 * first). Same-GW chains A→B then B→C are collapsed to A→C.
 * `warnings` are non-blocking advisory messages (over-budget, >3 from club).
 */
export interface TransferRecord {
  uid: string;
  gw: number;
  outId: number;
  inId: number;
  outPrice: number;    // sale proceeds (tenths of £m)
  inPrice: number;     // purchase cost (tenths of £m)
  purchasePrice: number; // stored for future selling price calculation
  planOrder: number;
  warnings: string[];
}

// ── GW state derivation ───────────────────────────────────────────────────────

/**
 * The result of walking the planner state forward to a specific GW.
 * This is the single source of truth for the pitch, bank bar and transfer list.
 */
export interface DerivedGwState {
  squad: PlannerPick[];
  bank: number;           // unspent budget (tenths of £m)
  ft: number;             // free transfers entering this GW (before use)
  openingFt: number;      // same as ft (alias used by hit calculation)
  transfersMade: number;  // planned transfers in this GW
  chargeableTransfers: number; // transfers beyond the free allowance
  nextFt: number;         // free transfers entering the NEXT GW
  hits: number;           // point deductions (chargeableTransfers * 4)
  chip: ChipCode | null;  // chip planned for this GW
  unlimited: boolean;     // true when transfers are free/unlimited
  ftOverride: boolean;    // true when FT was set by an explicit override
}

// ── FT settlement ─────────────────────────────────────────────────────────────

/** Inputs to settleFreeTransferWeek. */
export interface FtWeekInput {
  openingFt: number;
  transfersMade: number;
  chip: ChipCode | null;
  openingUnlimited: boolean;
}

/** Result of settleFreeTransferWeek. */
export interface FtWeekResult {
  hits: number;
  chargeableTransfers: number;
  nextFt: number;
}

// ── FPL API shapes (minimal — only fields used by the planner) ────────────────

export interface FplPlayer {
  id: number;
  web_name: string;
  first_name: string;
  second_name: string;
  element_type: ElementType;
  team: number;           // team ID
  now_cost: number;       // current price (tenths of £m)
  status: string;         // "a" | "d" | "i" | "n" | "s" | "u"
  chance_of_playing_next_round: number | null;
  selected_by_percent: string;
  form: string;
  ep_next: string | null;
  points_per_game: string;
  total_points: number;
  event_points: number | null;
}

export interface FplTeam {
  id: number;
  name: string;
  short_name: string;
  code: number;
}

export interface FplEvent {
  id: number;
  name: string;
  deadline_time: string; // ISO 8601
  finished: boolean;
  is_current: boolean;
  is_next: boolean;
}

export interface FplBootstrap {
  elements: FplPlayer[];
  teams: FplTeam[];
  events: FplEvent[];
  total_players: number;
}

export interface FplPick {
  element: number;
  position: number;
  multiplier: number;
  is_captain: boolean;
  is_vice_captain: boolean;
  selling_price?: number;
  purchase_price?: number;
}

export interface FplTransferRecord {
  element_in: number;
  element_out: number;
  element_in_cost: number;
  element_out_cost: number;
  event: number;
  time: string;
}

export interface FplChipPlay {
  name: string;
  event: number;
}

// ── Planner bootstrap (response from /api/planner/squad/[id]) ────────────────

/**
 * The single aggregate payload the BFF returns after fetching all FPL
 * endpoints in parallel. The client converts this into initial PlannerState.
 */
export interface PlannerBootstrap {
  bootstrap: FplBootstrap;
  picks: FplPick[];
  currentGw: number;
  planningStartGw: number;
  gwDeadlines: Record<number, number>; // GW number → unix ms
  bank: number;                        // tenths of £m
  freeTransfers: number;
  chipHistory: Record<ChipCode, number[]>;
  currentActiveChip: ChipCode | null;
  activeFreeHitGw: number | null;
  preFreeHitPicks: FplPick[] | null;
  transfers: FplTransferRecord[];      // for purchase price reconstruction
}

// ── xPts row (compact, from /api/planner/xpts) ───────────────────────────────

export interface XptsRow {
  player_id: number | null;
  player_key: string;
  web_name: string;
  team: string;
  position: string;
  gw: number;
  xpts: number | null;
  expected_minutes: number | null;
  price: number | null;  // tenths of £m
}

// ── Fixture data ──────────────────────────────────────────────────────────────

export interface FixtureCell {
  d: number;        // FDR 1–5
  o: string;        // opponent short code (uppercase = home, lowercase = away)
  h: boolean;       // true if this team is playing at home
  fixture_id: number;
}

export type FixtureData = Record<string, Record<number, FixtureCell[]>>;
// FixtureData[teamName][gw] = array of fixtures (usually 1, 2 for DGW, 0 for BGW)

// ── Saved plan slot ───────────────────────────────────────────────────────────

export interface PlanSlot {
  schemaVersion: 2;
  season: string;          // e.g. "2627"
  manifestVersion: string; // from data-manifest.json
  savedAt: number;         // unix ms
  label: string;
  managerId: number | null;
  managerName: string | null;
  planGw: number;
  origSquad: PlannerPick[];
  origBank: number;
  origFreeTransfers: number;
  transfers: TransferRecord[];
  lineupPlan: Record<string, Record<string, number>>;
  captainPlan: Record<string, number>;
  viceCaptainPlan: Record<string, number>;
  chipPlan: Record<string, ChipCode>;
  ftOverrides: Record<string, number>;
}
