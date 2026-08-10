"use client";

/**
 * PlanSlots — 4 named local save/load slots for the planner.
 *
 * Slots persist to localStorage under a versioned key that includes the
 * season so cross-season corruption is impossible. Each slot shows:
 * manager name, saved timestamp, GW, transfer count.
 *
 * Schema version 2 matches KFT2627's kft_planner_paths_v2 format.
 */

import { useEffect, useState } from "react";
import { Save, FolderOpen, Trash2, Pencil } from "lucide-react";

import { cn } from "@/lib/utils";
import type { PlanSlot } from "@/lib/planner/types";
import type { PlannerState, PlannerAction } from "@/lib/planner/state";

// ── Storage helpers ───────────────────────────────────────────────────────────

const SCHEMA_VERSION = 2 as const;
const SEASON = "2627";
const STORAGE_KEY = `kft_planner_paths_v${SCHEMA_VERSION}_${SEASON}`;
const MAX_SLOTS = 4;

function loadSlots(): (PlanSlot | null)[] {
  try {
    if (typeof window === "undefined") return Array(MAX_SLOTS).fill(null);
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return Array(MAX_SLOTS).fill(null);
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return Array(MAX_SLOTS).fill(null);
    // Validate schema version on each slot
    return parsed.map((s: unknown) => {
      if (s === null || s === undefined) return null;
      if (typeof s !== "object") return null;
      const slot = s as Partial<PlanSlot>;
      if (slot.schemaVersion !== SCHEMA_VERSION) return null;
      if (slot.season !== SEASON) return null;
      return slot as PlanSlot;
    }).slice(0, MAX_SLOTS).concat(Array(MAX_SLOTS).fill(null)).slice(0, MAX_SLOTS);
  } catch {
    return Array(MAX_SLOTS).fill(null);
  }
}

function saveSlots(slots: (PlanSlot | null)[]) {
  try {
    if (typeof window === "undefined") return;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(slots));
  } catch { /* storage full or unavailable */ }
}

function stateToSlot(state: PlannerState, label: string): PlanSlot {
  return {
    schemaVersion: SCHEMA_VERSION,
    season: SEASON,
    manifestVersion: "current",
    savedAt: Date.now(),
    label,
    managerId: state.managerId,
    managerName: state.managerName,
    planGw: state.planGw,
    origSquad: state.origSquad.map((p) => ({ ...p })),
    origBank: state.origBank,
    origFreeTransfers: state.origFreeTransfers,
    transfers: state.transfers.map((t) => ({ ...t })),
    lineupPlan: JSON.parse(JSON.stringify(state.lineupPlan)),
    captainPlan: { ...state.captainPlan },
    viceCaptainPlan: { ...state.viceCaptainPlan },
    chipPlan: { ...state.chipPlan },
    ftOverrides: { ...state.ftOverrides },
  };
}

function fmtDate(ms: number): string {
  return new Date(ms).toLocaleString("en-GB", {
    day: "numeric", month: "short",
    hour: "2-digit", minute: "2-digit",
  });
}

// ── Component ─────────────────────────────────────────────────────────────────

export default function PlanSlots({
  state,
  dispatch,
}: {
  state: PlannerState;
  dispatch: React.Dispatch<PlannerAction>;
}) {
  const [slots, setSlots] = useState<(PlanSlot | null)[]>(() => loadSlots());
  const [editingIdx, setEditingIdx] = useState<number | null>(null);
  const [editLabel, setEditLabel] = useState("");

  // Keep slots in sync with localStorage on mount
  useEffect(() => {
    setSlots(loadSlots());
  }, []);

  function save(idx: number) {
    const label = slots[idx]?.label ?? `Slot ${idx + 1}`;
    const slot = stateToSlot(state, label);
    const next = [...slots];
    next[idx] = slot;
    setSlots(next);
    saveSlots(next);
  }

  function load(idx: number) {
    const slot = slots[idx];
    if (!slot) return;
    dispatch({ type: "LOAD_PLAN_SLOT", slot });
  }

  function remove(idx: number) {
    const next = [...slots];
    next[idx] = null;
    setSlots(next);
    saveSlots(next);
  }

  function startRename(idx: number) {
    setEditingIdx(idx);
    setEditLabel(slots[idx]?.label ?? `Slot ${idx + 1}`);
  }

  function commitRename(idx: number) {
    if (!slots[idx]) return;
    const next = [...slots];
    next[idx] = { ...next[idx]!, label: editLabel.trim() || `Slot ${idx + 1}` };
    setSlots(next);
    saveSlots(next);
    setEditingIdx(null);
  }

  const hasPlan = state.origSquad.length > 0;

  return (
    <div className="flex flex-col gap-0">
      <p className="border-b border-border px-4 py-2.5 text-[11px] text-muted-foreground">
        4 local save slots — stored in your browser, never on a server.
      </p>

      {Array.from({ length: MAX_SLOTS }, (_, i) => {
        const slot = slots[i];
        const isEditing = editingIdx === i;

        return (
          <div
            key={i}
            className="flex items-start gap-3 border-b border-border/50 px-4 py-3"
          >
            {/* Slot number */}
            <span className="mt-0.5 shrink-0 font-mono text-xs text-muted-foreground/60">
              {i + 1}
            </span>

            {/* Slot content */}
            <div className="min-w-0 flex-1">
              {slot ? (
                <>
                  {isEditing ? (
                    <form
                      onSubmit={(e) => { e.preventDefault(); commitRename(i); }}
                      className="flex gap-1.5"
                    >
                      <input
                        value={editLabel}
                        onChange={(e) => setEditLabel(e.target.value)}
                        autoFocus
                        maxLength={40}
                        className="h-6 flex-1 rounded border border-primary bg-background px-2 text-xs outline-none"
                      />
                      <button type="submit"
                        className="rounded bg-primary px-2 py-0.5 text-[10px] font-semibold text-primary-foreground">
                        Save
                      </button>
                      <button type="button" onClick={() => setEditingIdx(null)}
                        className="text-[10px] text-muted-foreground hover:text-foreground">
                        Cancel
                      </button>
                    </form>
                  ) : (
                    <div className="flex items-center gap-1.5">
                      <p className="truncate text-xs font-semibold">{slot.label}</p>
                      <button
                        onClick={() => startRename(i)}
                        className="text-muted-foreground/50 hover:text-muted-foreground"
                        aria-label="Rename slot"
                      >
                        <Pencil className="h-2.5 w-2.5" />
                      </button>
                    </div>
                  )}
                  <p className="mt-0.5 font-mono text-[10px] text-muted-foreground">
                    GW{slot.planGw}
                    {slot.transfers.length > 0 && ` · ${slot.transfers.length} transfer${slot.transfers.length !== 1 ? "s" : ""}`}
                    {slot.managerName && ` · ${slot.managerName}`}
                  </p>
                  <p className="font-mono text-[9px] text-muted-foreground/50">
                    {fmtDate(slot.savedAt)}
                  </p>
                </>
              ) : (
                <p className="text-xs text-muted-foreground/50">Empty</p>
              )}
            </div>

            {/* Actions */}
            <div className="flex shrink-0 items-center gap-1">
              {slot && (
                <button
                  onClick={() => load(i)}
                  title="Load this plan"
                  className="inline-flex h-6 w-6 items-center justify-center rounded border border-border text-muted-foreground transition hover:border-primary/50 hover:text-primary"
                >
                  <FolderOpen className="h-3 w-3" />
                </button>
              )}
              <button
                onClick={() => save(i)}
                disabled={!hasPlan}
                title={hasPlan ? "Save current plan here" : "Load a squad first"}
                className="inline-flex h-6 w-6 items-center justify-center rounded border border-border text-muted-foreground transition hover:border-emerald-500/50 hover:text-emerald-400 disabled:opacity-30"
              >
                <Save className="h-3 w-3" />
              </button>
              {slot && (
                <button
                  onClick={() => {
                    if (confirm(`Delete "${slot.label}"?`)) remove(i);
                  }}
                  title="Delete slot"
                  className="inline-flex h-6 w-6 items-center justify-center rounded border border-border text-muted-foreground transition hover:border-rose-500/50 hover:text-rose-400"
                >
                  <Trash2 className="h-3 w-3" />
                </button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
