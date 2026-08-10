/**
 * Planner reducer — PlannerState + action union.
 *
 * All mutations go through this reducer. No component ever mutates state
 * directly. The derive.ts pure function reads a snapshot of this state to
 * produce DerivedGwState for the pitch and bank bar.
 *
 * Undo: every action that mutates planning data (transfers, chips, lineup,
 * captain) first pushes a snapshot onto state.history (capped at 20).
 */

import type {
  PlannerPick,
  TransferRecord,
  ChipCode,
  PlannerBootstrap,
  FplPlayer,
  FplTeam,
} from "./types";
import {
  applyChipAssign,
  removeChipPlan,
} from "./chipRules";
import { calculateFreeTransfersEnteringPlanningGw } from "./freeTransfers";

// ── Snapshot (for undo) ───────────────────────────────────────────────────────

export interface PlannerSnapshot {
  label: string;
  transfers: TransferRecord[];
  lineupPlan: Record<string, Record<string, number>>;
  captainPlan: Record<string, number>;
  viceCaptainPlan: Record<string, number>;
  chipPlan: Record<string, ChipCode>;
  ftOverrides: Record<string, number>;
}

// ── State ─────────────────────────────────────────────────────────────────────

export interface PlannerState {
  // ── Source data (loaded once, never mutated by plan actions)
  playerMap: Map<number, FplPlayer>;
  teamMap: Map<number, FplTeam>;

  // ── Manager identity
  managerId: number | null;
  managerName: string | null;
  teamName: string | null;
  currentGw: number;
  planningStartGw: number;
  gwDeadlines: Record<number, number>;

  // ── Squad baseline
  origSquad: PlannerPick[];
  origBank: number;
  origFreeTransfers: number;

  // ── Planning data (all go through undo)
  planGw: number;
  transfers: TransferRecord[];
  lineupPlan: Record<string, Record<string, number>>;
  captainPlan: Record<string, number>;
  viceCaptainPlan: Record<string, number>;
  chipPlan: Record<string, ChipCode>;
  chipHistory: Record<ChipCode, number[]>;
  currentActiveChip: ChipCode | null;
  ftOverrides: Record<string, number>;
  activeFreeHitGw: number | null;
  preFreeHitSquad: PlannerPick[] | null;

  // ── UI state (not undoable)
  selectedCard: number | null;
  subMode: number | null;         // element ID initiating a sub
  pendingTransferOut: number | null;
  activeModal: "transfer" | "captain" | null;

  // ── Loading / error
  loadStatus: "idle" | "loading" | "ready" | "error";
  loadError: string | null;

  // ── Undo stack (capped at 20)
  history: PlannerSnapshot[];

  // ── Preferences
  hideXpts: boolean;

  // ── Manual mode
  manualMode: boolean;
}

// ── Actions ───────────────────────────────────────────────────────────────────

export type PlannerAction =
  | { type: "LOAD_START" }
  | { type: "LOAD_SUCCESS"; payload: PlannerBootstrap }
  | { type: "LOAD_ERROR"; error: string }
  | { type: "SET_PLAN_GW"; gw: number }
  | { type: "ADD_TRANSFER"; record: TransferRecord }
  | { type: "REMOVE_TRANSFER"; uid: string }
  | { type: "ASSIGN_CHIP"; chip: ChipCode; gw: number }
  | { type: "REMOVE_CHIP"; gw: number }
  | { type: "SET_CAPTAIN"; element: number; gw: number }
  | { type: "SET_VICE_CAPTAIN"; element: number; gw: number }
  | { type: "CONFIRM_SUB"; fromElement: number; toElement: number; fromPos: number; toPos: number; gw: number }
  | { type: "SET_FT_OVERRIDE"; gw: number; ft: number }
  | { type: "UNDO" }
  | { type: "RESET_PLAN" }
  | { type: "LOAD_PLAN_SLOT"; slot: import("./types").PlanSlot }
  | { type: "SELECT_CARD"; element: number | null }
  | { type: "ENTER_SUB_MODE"; element: number }
  | { type: "EXIT_SUB_MODE" }
  | { type: "SET_PENDING_TRANSFER_OUT"; element: number | null }
  | { type: "OPEN_MODAL"; modal: "transfer" | "captain" }
  | { type: "CLOSE_MODAL" }
  | { type: "TOGGLE_HIDE_XPTS" };

// ── Initial state ─────────────────────────────────────────────────────────────

export function initialPlannerState(): PlannerState {
  return {
    playerMap: new Map(),
    teamMap: new Map(),
    managerId: null,
    managerName: null,
    teamName: null,
    currentGw: 0,
    planningStartGw: 1,
    gwDeadlines: {},
    origSquad: [],
    origBank: 1000,
    origFreeTransfers: 1,
    planGw: 1,
    transfers: [],
    lineupPlan: {},
    captainPlan: {},
    viceCaptainPlan: {},
    chipPlan: {},
    chipHistory: { wc: [], fh: [], bb: [], tc: [] },
    currentActiveChip: null,
    ftOverrides: {},
    activeFreeHitGw: null,
    preFreeHitSquad: null,
    selectedCard: null,
    subMode: null,
    pendingTransferOut: null,
    activeModal: null,
    loadStatus: "idle",
    loadError: null,
    history: [],
    hideXpts: false,
    manualMode: false,
  };
}

// ── Snapshot helpers ──────────────────────────────────────────────────────────

function takeSnapshot(state: PlannerState, label: string): PlannerSnapshot {
  return {
    label,
    transfers: state.transfers.map((t) => ({ ...t })),
    lineupPlan: JSON.parse(JSON.stringify(state.lineupPlan)),
    captainPlan: { ...state.captainPlan },
    viceCaptainPlan: { ...state.viceCaptainPlan },
    chipPlan: { ...state.chipPlan },
    ftOverrides: { ...state.ftOverrides },
  };
}

function pushHistory(state: PlannerState, label: string): PlannerSnapshot[] {
  return [...state.history, takeSnapshot(state, label)].slice(-20);
}

function restoreSnapshot(
  state: PlannerState,
  snap: PlannerSnapshot,
): Partial<PlannerState> {
  return {
    transfers: snap.transfers.map((t) => ({ ...t })),
    lineupPlan: JSON.parse(JSON.stringify(snap.lineupPlan)),
    captainPlan: { ...snap.captainPlan },
    viceCaptainPlan: { ...snap.viceCaptainPlan },
    chipPlan: { ...snap.chipPlan },
    ftOverrides: { ...snap.ftOverrides },
  };
}

// ── Bootstrap → initial squad picks ──────────────────────────────────────────

function bootstrapToPicks(payload: PlannerBootstrap): PlannerPick[] {
  return payload.picks.map((p) => ({
    element: p.element,
    position: p.position,
    multiplier: p.is_captain ? 2 : 1,
    isViceCaptain: p.is_vice_captain,
    purchasePrice: p.purchase_price ?? p.selling_price ?? 0,
    sellingPrice: p.selling_price ?? p.purchase_price ?? 0,
  }));
}

// ── Next planOrder for transfers ──────────────────────────────────────────────

function nextPlanOrder(transfers: TransferRecord[], gw: number): number {
  const gwTrs = transfers.filter((t) => t.gw === gw);
  return gwTrs.length === 0
    ? 1
    : Math.max(...gwTrs.map((t) => t.planOrder)) + 1;
}

// ── Reducer ───────────────────────────────────────────────────────────────────

export function plannerReducer(
  state: PlannerState,
  action: PlannerAction,
): PlannerState {
  switch (action.type) {

    // ── Loading ───────────────────────────────────────────────────────────────

    case "LOAD_START":
      return { ...initialPlannerState(), loadStatus: "loading" };

    case "LOAD_ERROR":
      return { ...state, loadStatus: "error", loadError: action.error };

    case "LOAD_SUCCESS": {
      const p = action.payload;

      const playerMap = new Map<number, FplPlayer>(
        p.bootstrap.elements.map((el) => [el.id, el]),
      );
      const teamMap = new Map<number, FplTeam>(
        p.bootstrap.teams.map((t) => [t.id, t]),
      );

      const origSquad = bootstrapToPicks(p);
      const preFhSquad = p.preFreeHitPicks
        ? p.preFreeHitPicks.map((pick) => ({
            element: pick.element,
            position: pick.position,
            multiplier: pick.is_captain ? 2 : 1,
            isViceCaptain: pick.is_vice_captain,
            purchasePrice: pick.purchase_price ?? pick.selling_price ?? 0,
            sellingPrice: pick.selling_price ?? pick.purchase_price ?? 0,
          }))
        : null;

      return {
        ...initialPlannerState(),
        playerMap,
        teamMap,
        managerId: null, // set by caller from URL
        managerName: null,
        teamName: null,
        currentGw: p.currentGw,
        planningStartGw: p.planningStartGw,
        gwDeadlines: p.gwDeadlines,
        origSquad,
        origBank: p.bank,
        origFreeTransfers: p.freeTransfers,
        planGw: p.planningStartGw,
        chipHistory: p.chipHistory,
        currentActiveChip: p.currentActiveChip,
        activeFreeHitGw: p.activeFreeHitGw,
        preFreeHitSquad: preFhSquad,
        loadStatus: "ready",
        loadError: null,
        history: [],
        hideXpts: state.hideXpts, // preserve user preference across reloads
      };
    }

    // ── Plan GW navigation ────────────────────────────────────────────────────

    case "SET_PLAN_GW":
      return {
        ...state,
        planGw: action.gw,
        selectedCard: null,
        subMode: null,
        pendingTransferOut: null,
        activeModal: null,
      };

    // ── Transfers ─────────────────────────────────────────────────────────────

    case "ADD_TRANSFER": {
      const record = {
        ...action.record,
        planOrder: nextPlanOrder(state.transfers, action.record.gw),
      };
      return {
        ...state,
        history: pushHistory(state, "Add transfer"),
        transfers: [...state.transfers, record],
        pendingTransferOut: null,
        activeModal: null,
        selectedCard: null,
      };
    }

    case "REMOVE_TRANSFER":
      return {
        ...state,
        history: pushHistory(state, "Remove transfer"),
        transfers: state.transfers.filter((t) => t.uid !== action.uid),
      };

    // ── Chips ─────────────────────────────────────────────────────────────────

    case "ASSIGN_CHIP":
      return {
        ...state,
        history: pushHistory(state, `Assign ${action.chip.toUpperCase()}`),
        chipPlan: applyChipAssign(
          action.chip,
          action.gw,
          state.chipPlan as Record<number, ChipCode>,
          state.chipHistory,
        ) as Record<string, ChipCode>,
      };

    case "REMOVE_CHIP":
      return {
        ...state,
        history: pushHistory(state, "Remove chip"),
        chipPlan: removeChipPlan(
          action.gw,
          state.chipPlan as Record<number, ChipCode>,
          state.chipHistory,
        ) as Record<string, ChipCode>,
      };

    // ── Captain / VC ──────────────────────────────────────────────────────────

    case "SET_CAPTAIN": {
      const gwKey = String(action.gw);
      const vcPlan = { ...state.viceCaptainPlan };
      if (vcPlan[gwKey] === action.element) delete vcPlan[gwKey];
      return {
        ...state,
        history: pushHistory(state, "Set captain"),
        captainPlan: { ...state.captainPlan, [gwKey]: action.element },
        viceCaptainPlan: vcPlan,
        selectedCard: null,
        activeModal: null,
      };
    }

    case "SET_VICE_CAPTAIN": {
      const gwKey = String(action.gw);
      const capPlan = { ...state.captainPlan };
      if (capPlan[gwKey] === action.element) delete capPlan[gwKey];
      return {
        ...state,
        history: pushHistory(state, "Set vice-captain"),
        viceCaptainPlan: { ...state.viceCaptainPlan, [gwKey]: action.element },
        captainPlan: capPlan,
        selectedCard: null,
        activeModal: null,
      };
    }

    // ── Lineup / substitutions ────────────────────────────────────────────────

    case "CONFIRM_SUB": {
      const gwKey = String(action.gw);
      const gwPlan = { ...(state.lineupPlan[gwKey] ?? {}) };
      // Swap the position slots of the two players in the lineupPlan.
      // fromPos and toPos come from the derived squad at dispatch time.
      const fromPos = action.fromPos;
      const toPos = action.toPos;
      gwPlan[String(action.fromElement)] = toPos;
      gwPlan[String(action.toElement)] = fromPos;
      return {
        ...state,
        history: pushHistory(state, "Substitution"),
        lineupPlan: { ...state.lineupPlan, [gwKey]: gwPlan },
        subMode: null,
        selectedCard: null,
      };
    }

    case "SET_FT_OVERRIDE": {
      const gwKey = String(action.gw);
      return {
        ...state,
        history: pushHistory(state, "FT override"),
        ftOverrides: { ...state.ftOverrides, [gwKey]: action.ft },
      };
    }

    // ── Undo ──────────────────────────────────────────────────────────────────

    case "UNDO": {
      if (state.history.length === 0) return state;
      const history = state.history.slice();
      const snap = history.pop()!;
      return {
        ...state,
        ...restoreSnapshot(state, snap),
        history,
        selectedCard: null,
        subMode: null,
        activeModal: null,
      };
    }

    // ── Reset ─────────────────────────────────────────────────────────────────

    case "RESET_PLAN":
      return {
        ...state,
        transfers: [],
        lineupPlan: {},
        captainPlan: {},
        viceCaptainPlan: {},
        chipPlan: {},
        ftOverrides: {},
        planGw: state.planningStartGw,
        history: [],
        selectedCard: null,
        subMode: null,
        pendingTransferOut: null,
        activeModal: null,
      };

    // ── Load plan slot ────────────────────────────────────────────────────────

    case "LOAD_PLAN_SLOT": {
      const s = action.slot;
      return {
        ...state,
        origSquad: s.origSquad,
        origBank: s.origBank,
        origFreeTransfers: s.origFreeTransfers,
        planGw: s.planGw,
        transfers: s.transfers,
        lineupPlan: s.lineupPlan,
        captainPlan: s.captainPlan,
        viceCaptainPlan: s.viceCaptainPlan,
        chipPlan: s.chipPlan,
        ftOverrides: s.ftOverrides,
        history: [],
        selectedCard: null,
        subMode: null,
        pendingTransferOut: null,
        activeModal: null,
      };
    }

    // ── UI state ──────────────────────────────────────────────────────────────

    case "SELECT_CARD":
      return {
        ...state,
        selectedCard: action.element,
        subMode: null,
        pendingTransferOut: null,
      };

    case "ENTER_SUB_MODE":
      return {
        ...state,
        subMode: action.element,
        selectedCard: null,
        activeModal: null,
      };

    case "EXIT_SUB_MODE":
      return { ...state, subMode: null };

    case "SET_PENDING_TRANSFER_OUT":
      return {
        ...state,
        pendingTransferOut: action.element,
        selectedCard: null,
        subMode: null,
      };

    case "OPEN_MODAL":
      return { ...state, activeModal: action.modal, selectedCard: null };

    case "CLOSE_MODAL":
      return { ...state, activeModal: null, pendingTransferOut: null };

    case "TOGGLE_HIDE_XPTS":
      return { ...state, hideXpts: !state.hideXpts };

    default:
      return state;
  }
}
