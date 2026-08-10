import { describe, it, expect } from "vitest";
import { derivePlanStateForGw, type DeriverInput } from "./derive";
import type { PlannerPick, TransferRecord } from "./types";

// ── Fixtures ──────────────────────────────────────────────────────────────────

function pick(element: number, position: number, purchasePrice = 75): PlannerPick {
  return {
    element,
    position,
    multiplier: position <= 11 ? 1 : 0,
    isViceCaptain: false,
    purchasePrice,
    sellingPrice: purchasePrice,
  };
}

/** Build a minimal 15-player squad: 1 GK, 4 DEF, 4 MID, 2 FWD starters + bench */
function baseSquad(): PlannerPick[] {
  return [
    pick(1, 1),   // GK starter
    pick(2, 2), pick(3, 3), pick(4, 4), pick(5, 5),  // DEF starters
    pick(6, 6), pick(7, 7), pick(8, 8), pick(9, 9),  // MID starters
    pick(10, 10), pick(11, 11),                        // FWD starters
    pick(12, 12), pick(13, 13), pick(14, 14), pick(15, 15), // bench
  ];
}

function baseInput(overrides: Partial<DeriverInput> = {}): DeriverInput {
  return {
    currentGw: 5,
    origSquad: baseSquad(),
    bank: 10,         // £1.0m in tenths
    freeTransfers: 1,
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
    ...overrides,
  };
}

function transfer(
  gw: number,
  outId: number,
  inId: number,
  outPrice = 75,
  inPrice = 75,
  planOrder = 1,
): TransferRecord {
  return {
    uid: `${gw}-${outId}-${inId}`,
    gw,
    outId,
    inId,
    outPrice,
    inPrice,
    purchasePrice: inPrice,
    planOrder,
    warnings: [],
  };
}

// ── Empty squad guard ─────────────────────────────────────────────────────────

describe("derivePlanStateForGw — empty squad", () => {
  it("returns empty squad when origSquad is empty", () => {
    const state = derivePlanStateForGw(6, baseInput({ origSquad: [] }));
    expect(state.squad).toHaveLength(0);
    expect(state.bank).toBe(10);
    expect(state.ft).toBe(1);
  });

  it("returns safe defaults when gw is 0", () => {
    const state = derivePlanStateForGw(0, baseInput());
    expect(state.squad).toHaveLength(0);
  });
});

// ── No transfers ──────────────────────────────────────────────────────────────

describe("derivePlanStateForGw — no transfers", () => {
  it("returns the original squad when no transfers planned", () => {
    const state = derivePlanStateForGw(6, baseInput());
    expect(state.squad).toHaveLength(15);
    expect(state.squad.map((p) => p.element)).toEqual(
      baseSquad().map((p) => p.element),
    );
  });

  it("0 hits when no transfers made", () => {
    const state = derivePlanStateForGw(6, baseInput());
    expect(state.hits).toBe(0);
    expect(state.chargeableTransfers).toBe(0);
  });

  it("bank unchanged when no transfers", () => {
    const state = derivePlanStateForGw(6, baseInput({ bank: 50 }));
    expect(state.bank).toBe(50);
  });
});

// ── Single transfer ───────────────────────────────────────────────────────────

describe("derivePlanStateForGw — single transfer", () => {
  it("replaces the out player with the in player", () => {
    const input = baseInput({
      transfers: [transfer(6, 10, 99)],
    });
    const state = derivePlanStateForGw(6, input);
    const elements = state.squad.map((p) => p.element);
    expect(elements).not.toContain(10);
    expect(elements).toContain(99);
  });

  it("0 hits when 1 transfer with 1 FT", () => {
    const state = derivePlanStateForGw(6, baseInput({ transfers: [transfer(6, 10, 99)] }));
    expect(state.hits).toBe(0);
  });

  it("4 pts hit when 2 transfers with 1 FT", () => {
    const input = baseInput({
      transfers: [transfer(6, 10, 99, 75, 75, 1), transfer(6, 11, 88, 75, 75, 2)],
    });
    const state = derivePlanStateForGw(6, input);
    expect(state.hits).toBe(4);
    expect(state.chargeableTransfers).toBe(1);
  });

  it("bank increases when selling price > buy price", () => {
    const input = baseInput({
      bank: 0,
      transfers: [transfer(6, 10, 99, 100, 80)], // sell 100, buy 80 → +20
    });
    const state = derivePlanStateForGw(6, input);
    expect(state.bank).toBe(20);
  });

  it("bank decreases when buy price > selling price", () => {
    const input = baseInput({
      bank: 50,
      transfers: [transfer(6, 10, 99, 80, 100)], // sell 80, buy 100 → -20
    });
    const state = derivePlanStateForGw(6, input);
    expect(state.bank).toBe(30);
  });
});

// ── Multi-GW propagation ──────────────────────────────────────────────────────

describe("derivePlanStateForGw — multi-GW FT propagation", () => {
  it("FT rolls forward: 0 transfers in GW6 → 2 FT entering GW7", () => {
    // No transfers planned for GW6, so FT goes 1 → 2.
    const state = derivePlanStateForGw(7, baseInput());
    expect(state.ft).toBe(2);
  });

  it("FT caps at 5 across many GWs with no transfers", () => {
    const state = derivePlanStateForGw(20, baseInput({ currentGw: 5, freeTransfers: 1 }));
    expect(state.ft).toBe(5);
  });

  it("transfer in intermediate GW reduces FT correctly for later GW", () => {
    // GW6: 2 transfers, 1 FT → 1 hit, 1 FT next. GW7 check.
    const input = baseInput({
      freeTransfers: 1,
      transfers: [
        transfer(6, 10, 99, 75, 75, 1),
        transfer(6, 11, 88, 75, 75, 2),
      ],
    });
    const state7 = derivePlanStateForGw(7, input);
    expect(state7.ft).toBe(1); // used both FT slots at GW6 → reset to 1
  });
});

// ── Wildcard ──────────────────────────────────────────────────────────────────

describe("derivePlanStateForGw — Wildcard", () => {
  it("WC: no hits even with 11 transfers", () => {
    const trs = Array.from({ length: 11 }, (_, i) =>
      transfer(6, i + 2, 100 + i, 75, 75, i + 1),
    );
    const input = baseInput({
      chipPlan: { 6: "wc" },
      transfers: trs,
    });
    const state = derivePlanStateForGw(6, input);
    expect(state.hits).toBe(0);
    expect(state.chip).toBe("wc");
  });

  it("WC: transfers are permanent", () => {
    const input = baseInput({
      chipPlan: { 6: "wc" },
      transfers: [transfer(6, 10, 99)],
    });
    const state6 = derivePlanStateForGw(6, input);
    const state7 = derivePlanStateForGw(7, input);
    // Player 99 should be in squad at GW7 too (permanent transfer)
    expect(state7.squad.map((p) => p.element)).toContain(99);
  });

  it("WC: FTs preserved + 1 next GW", () => {
    const input = baseInput({
      freeTransfers: 2,
      chipPlan: { 6: "wc" },
      transfers: [transfer(6, 10, 99)],
    });
    const state7 = derivePlanStateForGw(7, input);
    expect(state7.ft).toBe(3); // 2 + 1 (WC earns +1)
  });

  it("chip field is wc on the WC GW", () => {
    const state = derivePlanStateForGw(6, baseInput({ chipPlan: { 6: "wc" } }));
    expect(state.chip).toBe("wc");
  });

  it("chip field is null on a non-chip GW", () => {
    const state = derivePlanStateForGw(7, baseInput({ chipPlan: { 6: "wc" } }));
    expect(state.chip).toBeNull();
  });
});

// ── Free Hit ──────────────────────────────────────────────────────────────────

describe("derivePlanStateForGw — Free Hit", () => {
  it("FH: squad is temporary (real squad unchanged for next GW)", () => {
    const input = baseInput({
      chipPlan: { 6: "fh" },
      transfers: [transfer(6, 10, 99)],
    });
    const state6 = derivePlanStateForGw(6, input);
    const state7 = derivePlanStateForGw(7, input);
    // GW6: temporary squad has 99
    expect(state6.squad.map((p) => p.element)).toContain(99);
    // GW7: real squad is unchanged (10 still there, 99 gone)
    expect(state7.squad.map((p) => p.element)).not.toContain(99);
    expect(state7.squad.map((p) => p.element)).toContain(10);
  });

  it("FH: no hits", () => {
    const input = baseInput({
      chipPlan: { 6: "fh" },
      transfers: Array.from({ length: 10 }, (_, i) =>
        transfer(6, i + 2, 100 + i, 75, 75, i + 1),
      ),
    });
    const state = derivePlanStateForGw(6, input);
    expect(state.hits).toBe(0);
  });

  it("FH: FTs are preserved (no +1)", () => {
    const input = baseInput({
      freeTransfers: 3,
      chipPlan: { 6: "fh" },
      transfers: [transfer(6, 10, 99)],
    });
    const state7 = derivePlanStateForGw(7, input);
    expect(state7.ft).toBe(3); // preserved, not 3+1
  });

  it("FH: uses pre-FH squad as base when provided", () => {
    const preFh = baseSquad();
    preFh[0] = pick(999, 1); // pre-FH GK is 999
    const input = baseInput({
      chipPlan: { 6: "fh" },
      activeFreeHitGw: 6,
      preFreeHitSquad: preFh,
      transfers: [], // no transfers on the FH week itself
    });
    const state6 = derivePlanStateForGw(6, input);
    expect(state6.squad.map((p) => p.element)).toContain(999);
  });
});

// ── Bench Boost / Triple Captain ──────────────────────────────────────────────

describe("derivePlanStateForGw — Bench Boost / Triple Captain", () => {
  it("BB chip is returned correctly", () => {
    const state = derivePlanStateForGw(6, baseInput({ chipPlan: { 6: "bb" } }));
    expect(state.chip).toBe("bb");
  });

  it("TC chip is returned correctly", () => {
    const state = derivePlanStateForGw(6, baseInput({ chipPlan: { 6: "tc" } }));
    expect(state.chip).toBe("tc");
  });

  it("TC: captain multiplier is 3", () => {
    const input = baseInput({
      chipPlan: { 6: "tc" },
      captainPlan: { 6: 1 }, // element 1 is captain
    });
    const state = derivePlanStateForGw(6, input);
    const cap = state.squad.find((p) => p.element === 1);
    expect(cap?.multiplier).toBe(3);
  });

  it("BB: normal FT accounting (hit for extra transfer)", () => {
    const input = baseInput({
      freeTransfers: 1,
      chipPlan: { 6: "bb" },
      transfers: [transfer(6, 10, 99, 75, 75, 1), transfer(6, 11, 88, 75, 75, 2)],
    });
    const state = derivePlanStateForGw(6, input);
    expect(state.hits).toBe(4);
  });
});

// ── Captain state ─────────────────────────────────────────────────────────────

describe("derivePlanStateForGw — captain state", () => {
  it("captain has multiplier 2", () => {
    const input = baseInput({ captainPlan: { 6: 1 } });
    const state = derivePlanStateForGw(6, input);
    const cap = state.squad.find((p) => p.element === 1);
    expect(cap?.multiplier).toBe(2);
  });

  it("vice captain has isViceCaptain=true", () => {
    const input = baseInput({ captainPlan: { 6: 1 }, viceCaptainPlan: { 6: 2 } });
    const state = derivePlanStateForGw(6, input);
    const vc = state.squad.find((p) => p.element === 2);
    expect(vc?.isViceCaptain).toBe(true);
  });

  it("captain and vice-captain are different players", () => {
    const input = baseInput({ captainPlan: { 6: 1 }, viceCaptainPlan: { 6: 1 } });
    const state = derivePlanStateForGw(6, input);
    // Same element cannot be both cap and VC — VC should fall back
    const capPick = state.squad.find((p) => p.element === 1);
    expect(capPick?.multiplier).toBe(2);
    expect(capPick?.isViceCaptain).toBe(false);
  });

  it("bench players have multiplier 0", () => {
    const state = derivePlanStateForGw(6, baseInput());
    const bench = state.squad.filter((p) => p.position > 11);
    expect(bench.every((p) => p.multiplier === 0)).toBe(true);
  });
});

// ── FT override ───────────────────────────────────────────────────────────────

describe("derivePlanStateForGw — FT override", () => {
  it("ftOverride replaces rolling FT calculation", () => {
    const input = baseInput({ ftOverrides: { 6: 3 } });
    const state = derivePlanStateForGw(6, input);
    expect(state.ft).toBe(3);
    expect(state.ftOverride).toBe(true);
  });

  it("no override → ftOverride is false", () => {
    const state = derivePlanStateForGw(6, baseInput());
    expect(state.ftOverride).toBe(false);
  });

  it("override works with string key (matches JS object coercion)", () => {
    const input = baseInput({ ftOverrides: { "6": 4 } });
    const state = derivePlanStateForGw(6, input);
    expect(state.ft).toBe(4);
  });
});

// ── Transfer chain collapse ───────────────────────────────────────────────────

describe("derivePlanStateForGw — same-GW chain application", () => {
  it("chain A→B then B→C: C ends up in squad, A is gone", () => {
    // Transfer A(10) out, B(99) in, then B(99) out, C(88) in.
    const input = baseInput({
      freeTransfers: 2,
      transfers: [
        transfer(6, 10, 99, 75, 75, 1),
        transfer(6, 99, 88, 75, 75, 2),
      ],
    });
    const state = derivePlanStateForGw(6, input);
    const elements = state.squad.map((p) => p.element);
    expect(elements).not.toContain(10);
    expect(elements).not.toContain(99); // intermediate player gone
    expect(elements).toContain(88);     // final player present
  });
});

// ── Pre-GW1 unlimited ─────────────────────────────────────────────────────────

describe("derivePlanStateForGw — pre-GW1 unlimited", () => {
  it("currentGw=0, targeting GW1: unlimited transfers, no hits", () => {
    const input = baseInput({
      currentGw: 0,
      freeTransfers: 1,
      transfers: Array.from({ length: 10 }, (_, i) =>
        transfer(1, i + 2, 100 + i, 75, 75, i + 1),
      ),
    });
    const state = derivePlanStateForGw(1, input);
    expect(state.hits).toBe(0);
    expect(state.unlimited).toBe(true);
  });
});

// ── nextFt propagation ────────────────────────────────────────────────────────

describe("derivePlanStateForGw — nextFt", () => {
  it("nextFt is the FT entering the GW after the target", () => {
    // 1 FT, 0 transfers in GW6 → nextFt should be 2
    const state = derivePlanStateForGw(6, baseInput());
    expect(state.nextFt).toBe(2);
  });

  it("nextFt after a hit week is 1 (used both free + hit)", () => {
    const input = baseInput({
      freeTransfers: 1,
      transfers: [transfer(6, 10, 99, 75, 75, 1), transfer(6, 11, 88, 75, 75, 2)],
    });
    const state = derivePlanStateForGw(6, input);
    expect(state.nextFt).toBe(1);
  });
});
