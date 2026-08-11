import { describe, it, expect } from "vitest";
import {
  settleFreeTransferWeek,
  calculateFreeTransfersEnteringPlanningGw,
  hasUnlimitedInitialTransfers,
  clampFreeTransfers,
  FREE_TRANSFER_CAP,
} from "./freeTransfers";

// ── clampFreeTransfers ────────────────────────────────────────────────────────

describe("clampFreeTransfers", () => {
  it("clamps to 0 at the floor", () => expect(clampFreeTransfers(-5)).toBe(0));
  it("clamps to 5 at the ceiling", () => expect(clampFreeTransfers(9)).toBe(FREE_TRANSFER_CAP));
  it("passes through valid values", () => expect(clampFreeTransfers(2)).toBe(2));
  it("handles non-finite values: NaN → 0, Infinity → cap", () => {
    expect(clampFreeTransfers(NaN)).toBe(0);
    expect(clampFreeTransfers(Infinity)).toBe(FREE_TRANSFER_CAP);
  });
});

// ── hasUnlimitedInitialTransfers ──────────────────────────────────────────────

describe("hasUnlimitedInitialTransfers", () => {
  it("is unlimited for GW1 when currentGw is 0 (pre-season)", () =>
    expect(hasUnlimitedInitialTransfers(1, 0)).toBe(true));
  it("is NOT unlimited for GW2 onwards", () =>
    expect(hasUnlimitedInitialTransfers(2, 0)).toBe(false));
  it("is NOT unlimited for GW1 if currentGw is already 1", () =>
    expect(hasUnlimitedInitialTransfers(1, 1)).toBe(false));
});

// ── settleFreeTransferWeek ────────────────────────────────────────────────────

describe("settleFreeTransferWeek — normal week", () => {
  it("0 transfers used → 0 hits, full FT rolls forward + 1", () => {
    const r = settleFreeTransferWeek({ openingFt: 1, transfersMade: 0, chip: null, openingUnlimited: false });
    expect(r.hits).toBe(0);
    expect(r.chargeableTransfers).toBe(0);
    expect(r.nextFt).toBe(2);
  });

  it("1 transfer with 1 FT → 0 hits, 1 FT next week", () => {
    const r = settleFreeTransferWeek({ openingFt: 1, transfersMade: 1, chip: null, openingUnlimited: false });
    expect(r.hits).toBe(0);
    expect(r.chargeableTransfers).toBe(0);
    expect(r.nextFt).toBe(1);
  });

  it("2 transfers with 1 FT → 4 pts hit, 1 FT next week", () => {
    const r = settleFreeTransferWeek({ openingFt: 1, transfersMade: 2, chip: null, openingUnlimited: false });
    expect(r.hits).toBe(4);
    expect(r.chargeableTransfers).toBe(1);
    expect(r.nextFt).toBe(1);
  });

  it("3 transfers with 2 FT → 4 pts hit (1 chargeable)", () => {
    const r = settleFreeTransferWeek({ openingFt: 2, transfersMade: 3, chip: null, openingUnlimited: false });
    expect(r.hits).toBe(4);
    expect(r.chargeableTransfers).toBe(1);
    expect(r.nextFt).toBe(1);
  });

  it("FTs accumulate: 0 transfers with 4 FT → 5 next week (capped)", () => {
    const r = settleFreeTransferWeek({ openingFt: 4, transfersMade: 0, chip: null, openingUnlimited: false });
    expect(r.nextFt).toBe(FREE_TRANSFER_CAP);
  });

  it("FTs do not exceed cap of 5", () => {
    const r = settleFreeTransferWeek({ openingFt: 5, transfersMade: 0, chip: null, openingUnlimited: false });
    expect(r.nextFt).toBe(FREE_TRANSFER_CAP);
  });

  it("0 hits when no transfers made regardless of FT count", () => {
    const r = settleFreeTransferWeek({ openingFt: 3, transfersMade: 0, chip: null, openingUnlimited: false });
    expect(r.hits).toBe(0);
  });

  it("5 transfers with 1 FT → 4 chargeable → 16 pts hit", () => {
    const r = settleFreeTransferWeek({ openingFt: 1, transfersMade: 5, chip: null, openingUnlimited: false });
    expect(r.chargeableTransfers).toBe(4);
    expect(r.hits).toBe(16);
  });
});

describe("settleFreeTransferWeek — Wildcard", () => {
  it("WC: no hits regardless of transfer count", () => {
    const r = settleFreeTransferWeek({ openingFt: 1, transfersMade: 15, chip: "wc", openingUnlimited: false });
    expect(r.hits).toBe(0);
    expect(r.chargeableTransfers).toBe(0);
  });

  it("WC: FTs preserved exactly — no +1 earned (matches KFT2627 original)", () => {
    const r = settleFreeTransferWeek({ openingFt: 2, transfersMade: 5, chip: "wc", openingUnlimited: false });
    expect(r.nextFt).toBe(2); // preserved, not 3
  });

  it("WC with 1 FT: preserved at 1, not incremented to 2", () => {
    const r = settleFreeTransferWeek({ openingFt: 1, transfersMade: 11, chip: "wc", openingUnlimited: false });
    expect(r.nextFt).toBe(1);
  });

  it("WC: FTs still capped at 5", () => {
    const r = settleFreeTransferWeek({ openingFt: 5, transfersMade: 2, chip: "wc", openingUnlimited: false });
    expect(r.nextFt).toBe(FREE_TRANSFER_CAP);
  });

  it("WC and FH behave identically for nextFt (both preserve opening)", () => {
    const wc = settleFreeTransferWeek({ openingFt: 3, transfersMade: 7, chip: "wc", openingUnlimited: false });
    const fh = settleFreeTransferWeek({ openingFt: 3, transfersMade: 7, chip: "fh", openingUnlimited: false });
    expect(wc.nextFt).toBe(fh.nextFt);
    expect(wc.hits).toBe(fh.hits);
    expect(wc.chargeableTransfers).toBe(fh.chargeableTransfers);
  });
});

describe("settleFreeTransferWeek — Free Hit", () => {
  it("FH: no hits", () => {
    const r = settleFreeTransferWeek({ openingFt: 1, transfersMade: 15, chip: "fh", openingUnlimited: false });
    expect(r.hits).toBe(0);
    expect(r.chargeableTransfers).toBe(0);
  });

  it("FH: FTs are preserved (not +1)", () => {
    const r = settleFreeTransferWeek({ openingFt: 2, transfersMade: 10, chip: "fh", openingUnlimited: false });
    expect(r.nextFt).toBe(2); // no +1, preserved
  });

  it("FH preserving 5 FTs keeps them at 5", () => {
    const r = settleFreeTransferWeek({ openingFt: 5, transfersMade: 5, chip: "fh", openingUnlimited: false });
    expect(r.nextFt).toBe(FREE_TRANSFER_CAP);
  });
});

describe("settleFreeTransferWeek — Bench Boost / Triple Captain", () => {
  it("BB: normal FT accounting unchanged", () => {
    const r = settleFreeTransferWeek({ openingFt: 1, transfersMade: 2, chip: "bb", openingUnlimited: false });
    expect(r.hits).toBe(4);
    expect(r.nextFt).toBe(1);
  });

  it("TC: normal FT accounting unchanged", () => {
    const r = settleFreeTransferWeek({ openingFt: 1, transfersMade: 0, chip: "tc", openingUnlimited: false });
    expect(r.hits).toBe(0);
    expect(r.nextFt).toBe(2);
  });
});

describe("settleFreeTransferWeek — pre-season unlimited", () => {
  it("unlimited: no hits even with many transfers", () => {
    const r = settleFreeTransferWeek({ openingFt: 1, transfersMade: 15, chip: null, openingUnlimited: true });
    expect(r.hits).toBe(0);
    expect(r.chargeableTransfers).toBe(0);
  });
});

// ── calculateFreeTransfersEnteringPlanningGw ──────────────────────────────────

describe("calculateFreeTransfersEnteringPlanningGw", () => {
  const emptyHistory = { wc: [], fh: [], bb: [], tc: [] };

  it("pre-season (currentGw=0) → 1 FT", () => {
    expect(calculateFreeTransfersEnteringPlanningGw(0, {}, emptyHistory, null)).toBe(1);
  });

  it("GW1 completed, any transfers → always 1 FT for GW2 (GW1 unlimited)", () => {
    expect(calculateFreeTransfersEnteringPlanningGw(1, { 1: 0 }, emptyHistory, null)).toBe(1);
    expect(calculateFreeTransfersEnteringPlanningGw(1, { 1: 1 }, emptyHistory, null)).toBe(1);
    expect(calculateFreeTransfersEnteringPlanningGw(1, { 1: 5 }, emptyHistory, null)).toBe(1);
  });

  it("GW2 completed, 0 transfers → 2 FT for GW3", () => {
    expect(calculateFreeTransfersEnteringPlanningGw(2, { 2: 0 }, emptyHistory, null)).toBe(2);
  });

  it("GW2 completed, 1 transfer used → 1 FT for GW3", () => {
    expect(calculateFreeTransfersEnteringPlanningGw(2, { 2: 1 }, emptyHistory, null)).toBe(1);
  });

  it("GW2+GW3 both 0 transfers → 3 FT for GW4", () => {
    expect(calculateFreeTransfersEnteringPlanningGw(3, { 2: 0, 3: 0 }, emptyHistory, null)).toBe(3);
  });

  it("accumulates correctly over GW2-GW6 with no transfers (capped at 5)", () => {
    const txByGw = { 2: 0, 3: 0, 4: 0, 5: 0, 6: 0 };
    expect(calculateFreeTransfersEnteringPlanningGw(6, txByGw, emptyHistory, null)).toBe(FREE_TRANSFER_CAP);
  });

  it("WC in GW3: FTs preserved exactly — no +1 (matches KFT2627 original)", () => {
    const txByGw = { 2: 0, 3: 5 }; // 5 transfers on WC week
    const history = { ...emptyHistory, wc: [3] };
    // enters GW2 with 1 FT, GW2: 0 tx → 2 FT, GW3 (WC): 2 preserved (no +1) = 2
    expect(calculateFreeTransfersEnteringPlanningGw(3, txByGw, history, null)).toBe(2);
  });

  it("FH in GW3: FTs preserved exactly (no +1)", () => {
    const txByGw = { 2: 0, 3: 8 };
    const history = { ...emptyHistory, fh: [3] };
    // enters GW2 with 1, GW2: 0 tx → 2, GW3 (FH): 2 preserved (no +1) = 2
    expect(calculateFreeTransfersEnteringPlanningGw(3, txByGw, history, null)).toBe(2);
  });

  it("hit taken in GW2 resets FT to 1 (used free slot, then +1)", () => {
    const txByGw = { 2: 2 }; // 1 FT available, 2 transfers = 1 hit, nextFt = 1
    expect(calculateFreeTransfersEnteringPlanningGw(2, txByGw, emptyHistory, null)).toBe(1);
  });

  it("currentActiveChip WC counts for the current locked GW — FTs preserved (no +1)", () => {
    const txByGw = { 2: 10 };
    // Active WC in GW2: enters GW2 with 1 FT, WC preserves it → 1
    const ft = calculateFreeTransfersEnteringPlanningGw(2, txByGw, emptyHistory, "wc");
    expect(ft).toBe(1);
  });
});
