import { describe, it, expect } from "vitest";
import {
  chipHalf,
  validateChipAssign,
  applyChipAssign,
  removeChipPlan,
  isChipUsedInHalf,
} from "./chipRules";
import type { ChipCode } from "./types";

const empty = { wc: [], fh: [], bb: [], tc: [] };

// ── chipHalf ──────────────────────────────────────────────────────────────────

describe("chipHalf", () => {
  it("GW1 is first half", () => expect(chipHalf(1)).toBe(1));
  it("GW19 is first half", () => expect(chipHalf(19)).toBe(1));
  it("GW20 is second half", () => expect(chipHalf(20)).toBe(2));
  it("GW38 is second half", () => expect(chipHalf(38)).toBe(2));
});

// ── isChipUsedInHalf ──────────────────────────────────────────────────────────

describe("isChipUsedInHalf", () => {
  it("not used when history is empty and no plan", () => {
    expect(isChipUsedInHalf("wc", 1, empty, {})).toBe(false);
  });

  it("used when chip appears in history for that half", () => {
    const history = { ...empty, wc: [5] };
    expect(isChipUsedInHalf("wc", 1, history, {})).toBe(true);
  });

  it("not used in other half even if used in this one", () => {
    const history = { ...empty, wc: [5] };
    expect(isChipUsedInHalf("wc", 2, history, {})).toBe(false);
  });

  it("used when chip appears in chipPlan for that half", () => {
    expect(isChipUsedInHalf("wc", 1, empty, { 8: "wc" })).toBe(true);
  });
});

// ── validateChipAssign ────────────────────────────────────────────────────────

describe("validateChipAssign", () => {
  it("allows a valid chip assignment", () => {
    const r = validateChipAssign("wc", 6, empty, {}, 5);
    expect(r.ok).toBe(true);
  });

  it("rejects assigning chip to a completed GW", () => {
    const r = validateChipAssign("wc", 5, empty, {}, 5);
    expect(r.ok).toBe(false);
  });

  it("rejects FH in GW1", () => {
    const r = validateChipAssign("fh", 1, empty, {}, 0);
    expect(r.ok).toBe(false);
    expect((r as { ok: false; reason: string }).reason).toMatch(/GW1/);
  });

  it("rejects assigning chip when another chip is already planned for that GW", () => {
    const r = validateChipAssign("bb", 6, empty, { 6: "wc" }, 5);
    expect(r.ok).toBe(false);
  });

  it("allows re-planning the same chip type on the same GW", () => {
    const r = validateChipAssign("wc", 6, empty, { 6: "wc" }, 5);
    expect(r.ok).toBe(true);
  });

  it("rejects chip already used in official history for that half", () => {
    const history = { ...empty, wc: [3] };
    const r = validateChipAssign("wc", 8, history, {}, 5);
    expect(r.ok).toBe(false);
  });

  it("allows chip in second half even if used in first half", () => {
    const history = { ...empty, wc: [5] };
    const r = validateChipAssign("wc", 25, history, {}, 20);
    expect(r.ok).toBe(true);
  });

  it("rejects FH in consecutive GWs (history)", () => {
    const history = { ...empty, fh: [7] };
    const r = validateChipAssign("fh", 8, history, {}, 5);
    expect(r.ok).toBe(false);
  });

  it("rejects FH in consecutive GWs (planned)", () => {
    const r = validateChipAssign("fh", 9, empty, { 8: "fh" }, 5);
    expect(r.ok).toBe(false);
  });

  it("allows FH two GWs apart", () => {
    const r = validateChipAssign("fh", 10, empty, { 8: "fh" }, 5);
    expect(r.ok).toBe(true);
  });
});

// ── applyChipAssign ───────────────────────────────────────────────────────────

describe("applyChipAssign", () => {
  it("adds chip to plan", () => {
    const plan = applyChipAssign("wc", 6, {}, empty);
    expect(plan[6]).toBe("wc");
  });

  it("removes conflicting planned use of same chip in same half", () => {
    const plan = applyChipAssign("wc", 8, { 6: "wc" }, empty);
    expect(plan[6]).toBeUndefined();
    expect(plan[8]).toBe("wc");
  });

  it("does NOT remove planned use from the other half", () => {
    const plan = applyChipAssign("wc", 25, { 6: "wc" }, empty);
    expect(plan[6]).toBe("wc"); // first-half WC stays
    expect(plan[25]).toBe("wc");
  });

  it("does NOT remove a chip use that is in official history", () => {
    const history = { ...empty, wc: [6] };
    // Even though GW6 WC is in history, assigning WC at GW8 should not touch history.
    const plan = applyChipAssign("wc", 8, {}, history);
    expect(plan[8]).toBe("wc");
    // History is read-only — applyChipAssign only modifies the plan object.
  });

  it("returns a new object (does not mutate input)", () => {
    const original: Record<number, ChipCode> = { 10: "bb" };
    const plan = applyChipAssign("wc", 6, original, empty);
    expect(original[6]).toBeUndefined();
    expect(plan[6]).toBe("wc");
  });
});

// ── removeChipPlan ────────────────────────────────────────────────────────────

describe("removeChipPlan", () => {
  it("removes a planned chip", () => {
    const plan = removeChipPlan(6, { 6: "wc" }, empty);
    expect(plan[6]).toBeUndefined();
  });

  it("does not remove an official history chip", () => {
    const history = { ...empty, wc: [6] };
    const plan = removeChipPlan(6, { 6: "wc" }, history);
    expect(plan[6]).toBe("wc");
  });

  it("is a no-op when no chip is planned for that GW", () => {
    const original = { 7: "bb" as ChipCode };
    const plan = removeChipPlan(6, original, empty);
    expect(plan).toBe(original); // same reference (no change)
  });
});
