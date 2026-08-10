"use client";

/**
 * PlannerShell — root client component for the KFT Transfer Planner.
 *
 * Phase 2: data layer + state wired up. Shows a manager ID entry form,
 * loads the PlannerBootstrap from the BFF, and displays a live summary
 * of the derived GW state (bank, FT, hits, chip). The pitch and market
 * are Phase 3.
 */

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Loader2,
  RotateCcw,
  Undo2,
  EyeOff,
  Eye,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { plannerReducer, initialPlannerState } from "@/lib/planner/state";
import { derivePlanStateForGw } from "@/lib/planner/derive";
import { CHIP_DISPLAY } from "@/lib/planner/types";
import type { PlannerBootstrap } from "@/lib/planner/types";

// ── Constants ─────────────────────────────────────────────────────────────────

const STORAGE_KEY = "kft_planner_manager_id";
const MAX_GW = 38;

// ── Helpers ───────────────────────────────────────────────────────────────────

function pence(tenths: number): string {
  return `£${(tenths / 10).toFixed(1)}m`;
}

function gwList(planningStartGw: number): number[] {
  return Array.from(
    { length: MAX_GW - planningStartGw + 1 },
    (_, i) => planningStartGw + i,
  );
}

// ── DeadlineClock ─────────────────────────────────────────────────────────────

function DeadlineClock({ deadlineMs }: { deadlineMs: number | null }) {
  const [remaining, setRemaining] = useState<string | null>(null);
  const [urgency, setUrgency] = useState<"ok" | "warn" | "crit">("ok");

  useEffect(() => {
    if (!deadlineMs) return;
    const tick = () => {
      const diff = deadlineMs - Date.now();
      if (diff <= 0) { setRemaining("Deadline passed"); setUrgency("crit"); return; }
      const h = Math.floor(diff / 3_600_000);
      const m = Math.floor((diff % 3_600_000) / 60_000);
      const s = Math.floor((diff % 60_000) / 1_000);
      setRemaining(
        h > 0
          ? `${h}h ${m}m`
          : m > 0
            ? `${m}m ${s}s`
            : `${s}s`,
      );
      setUrgency(diff < 900_000 ? "crit" : diff < 21_600_000 ? "warn" : "ok");
    };
    tick();
    const id = setInterval(tick, 1_000);
    return () => clearInterval(id);
  }, [deadlineMs]);

  if (!remaining) return null;

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded border px-2 py-1 font-mono text-xs tabular-nums",
        urgency === "ok" && "border-emerald-500/30 bg-emerald-500/10 text-emerald-400",
        urgency === "warn" && "border-amber-400/40 bg-amber-400/10 text-amber-300",
        urgency === "crit" && "animate-pulse border-rose-500/50 bg-rose-500/15 text-rose-400",
      )}
    >
      ⏱ {remaining}
    </span>
  );
}

// ── BankBar ───────────────────────────────────────────────────────────────────

function BankBar({
  bank,
  ft,
  hits,
  chip,
  gw,
  ftOverride,
}: {
  bank: number;
  ft: number;
  hits: number;
  chip: string | null;
  gw: number;
  ftOverride: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-border bg-card px-4 py-2 text-xs">
      <span className="font-mono font-semibold tabular-nums text-foreground">
        Bank {pence(bank)}
      </span>

      <span className={cn(
        "font-mono tabular-nums",
        ft === 0 ? "text-rose-400" : ft >= 3 ? "text-emerald-400" : "text-foreground",
      )}>
        {ft} FT{ft !== 1 ? "s" : ""}
        {ftOverride && <sup className="ml-0.5 text-amber-400" title="Manually overridden">*</sup>}
      </span>

      {hits > 0 && (
        <span className="font-mono font-semibold tabular-nums text-rose-400">
          −{hits} pts hit{hits > 4 ? "s" : ""}
        </span>
      )}

      {chip && (
        <span className="rounded bg-primary/15 px-1.5 py-0.5 font-semibold text-primary">
          {CHIP_DISPLAY[chip as keyof typeof CHIP_DISPLAY] ?? chip.toUpperCase()}
        </span>
      )}

      <span className="ml-auto text-muted-foreground">GW{gw}</span>
    </div>
  );
}

// ── Manager entry form ────────────────────────────────────────────────────────

function ManagerForm({
  onLoad,
  loading,
  error,
}: {
  onLoad: (id: number) => void;
  loading: boolean;
  error: string | null;
}) {
  const [input, setInput] = useState("");

  return (
    <div className="mx-auto max-w-md space-y-4 px-4 py-12">
      <div>
        <h2 className="text-lg font-bold">Load your FPL squad</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Enter your FPL Manager ID to start planning transfers, chips and
          captaincy across future gameweeks.
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          Find your ID in the URL:{" "}
          <span className="font-mono text-muted-foreground/70">
            fantasy.premierleague.com/entry/<strong>123456</strong>/…
          </span>
        </p>
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          const n = parseInt(input.trim(), 10);
          if (Number.isFinite(n) && n > 0) onLoad(n);
        }}
        className="flex gap-2"
      >
        <input
          type="number"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Manager ID"
          min={1}
          className="h-10 flex-1 rounded border border-border bg-background px-3 font-mono text-sm outline-none focus:border-primary"
          aria-label="FPL Manager ID"
          disabled={loading}
        />
        <button
          type="submit"
          disabled={loading || !input.trim()}
          className="inline-flex h-10 items-center gap-2 rounded bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-60"
        >
          {loading && <Loader2 className="h-4 w-4 animate-spin" />}
          {loading ? "Loading…" : "Load Squad"}
        </button>
      </form>

      {error && (
        <p className="rounded border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-sm text-rose-400">
          {error}
        </p>
      )}
    </div>
  );
}

// ── Squad summary (Phase 2 placeholder for the pitch) ────────────────────────

function SquadSummary({
  state,
}: {
  state: ReturnType<typeof initialPlannerState>;
}) {
  const derived = derivePlanStateForGw(state.planGw, {
    currentGw: state.currentGw,
    origSquad: state.origSquad,
    bank: state.origBank,
    freeTransfers: state.origFreeTransfers,
    transfers: state.transfers,
    lineupPlan: state.lineupPlan,
    captainPlan: state.captainPlan,
    viceCaptainPlan: state.viceCaptainPlan,
    chipPlan: state.chipPlan,
    chipHistory: state.chipHistory,
    currentActiveChip: state.currentActiveChip,
    ftOverrides: state.ftOverrides,
    activeFreeHitGw: state.activeFreeHitGw,
    preFreeHitSquad: state.preFreeHitSquad,
  });

  const starters = derived.squad.filter((p) => p.position <= 11);
  const bench = derived.squad.filter((p) => p.position > 11);

  return (
    <div className="space-y-4 px-4 py-4">
      {/* Starters */}
      <div>
        <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          Starting XI
        </p>
        <div className="grid grid-cols-5 gap-2 sm:grid-cols-11">
          {[...starters]
            .sort((a, b) => a.position - b.position)
            .map((pick) => {
              const player = state.playerMap.get(pick.element);
              const isCap = pick.multiplier >= 2;
              const isVc = pick.isViceCaptain;
              return (
                <div
                  key={pick.element}
                  className={cn(
                    "flex flex-col items-center gap-0.5 rounded border px-1 py-2 text-center",
                    isCap
                      ? "border-amber-400/50 bg-amber-400/10"
                      : "border-border bg-card",
                  )}
                >
                  <span className="text-[10px] font-mono font-bold text-muted-foreground">
                    {["GK","DEF","DEF","DEF","DEF","MID","MID","MID","MID","FWD","FWD"][pick.position - 1] ?? "?"}
                  </span>
                  <span className="truncate text-[10px] font-medium" title={player?.web_name ?? String(pick.element)}>
                    {player?.web_name ?? `#${pick.element}`}
                  </span>
                  {(isCap || isVc) && (
                    <span className={cn(
                      "text-[9px] font-bold",
                      isCap ? "text-amber-400" : "text-muted-foreground",
                    )}>
                      {pick.multiplier === 3 ? "TC" : isCap ? "C" : "V"}
                    </span>
                  )}
                  <span className="font-mono text-[9px] text-muted-foreground">
                    {pence(pick.sellingPrice)}
                  </span>
                </div>
              );
            })}
        </div>
      </div>

      {/* Bench */}
      <div>
        <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
          Bench
        </p>
        <div className="flex flex-wrap gap-2">
          {[...bench]
            .sort((a, b) => a.position - b.position)
            .map((pick, i) => {
              const player = state.playerMap.get(pick.element);
              return (
                <div
                  key={pick.element}
                  className="flex items-center gap-2 rounded border border-border bg-card px-2 py-1.5"
                >
                  <span className="text-[10px] text-muted-foreground">{i + 1}</span>
                  <span className="text-xs font-medium">
                    {player?.web_name ?? `#${pick.element}`}
                  </span>
                  <span className="font-mono text-[10px] text-muted-foreground">
                    {pence(pick.sellingPrice)}
                  </span>
                </div>
              );
            })}
        </div>
      </div>

      {/* Transfers for this GW */}
      {state.transfers.filter((t) => t.gw === state.planGw).length > 0 && (
        <div>
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
            Planned transfers — GW{state.planGw}
          </p>
          <div className="space-y-1.5">
            {state.transfers
              .filter((t) => t.gw === state.planGw)
              .sort((a, b) => a.planOrder - b.planOrder)
              .map((t) => {
                const outPlayer = state.playerMap.get(t.outId);
                const inPlayer = state.playerMap.get(t.inId);
                return (
                  <div
                    key={t.uid}
                    className="flex items-center gap-2 text-xs"
                  >
                    <span className="inline-flex items-center gap-1 rounded bg-rose-500/10 px-1.5 py-0.5 text-rose-400">
                      OUT {outPlayer?.web_name ?? `#${t.outId}`}
                    </span>
                    <span className="text-muted-foreground">→</span>
                    <span className="inline-flex items-center gap-1 rounded bg-emerald-500/10 px-1.5 py-0.5 text-emerald-400">
                      IN {inPlayer?.web_name ?? `#${t.inId}`}
                    </span>
                  </div>
                );
              })}
          </div>
        </div>
      )}

      {derived.squad.length === 0 && (
        <p className="py-8 text-center text-sm text-muted-foreground">
          Squad not yet loaded for GW{state.planGw}.
        </p>
      )}
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export default function PlannerShell({
  initialManagerId,
}: {
  initialManagerId: number | null;
}) {
  const [state, dispatch] = useReducer(plannerReducer, initialPlannerState());
  const loadedIdRef = useRef<number | null>(null);

  // ── Load manager ────────────────────────────────────────────────────────────

  const loadManager = useCallback(async (id: number) => {
    if (loadedIdRef.current === id && state.loadStatus === "ready") return;
    loadedIdRef.current = id;
    dispatch({ type: "LOAD_START" });

    try {
      const r = await fetch(`/api/planner/squad/${id}`);
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body.error ?? `HTTP ${r.status}`);
      }
      const payload: PlannerBootstrap = await r.json();
      dispatch({ type: "LOAD_SUCCESS", payload });

      if (typeof window !== "undefined") {
        localStorage.setItem(STORAGE_KEY, String(id));
      }
    } catch (e) {
      dispatch({
        type: "LOAD_ERROR",
        error: e instanceof Error ? e.message : "Failed to load squad.",
      });
    }
  }, [state.loadStatus]);

  // Auto-load from prop or localStorage on mount
  useEffect(() => {
    const fromStorage =
      typeof window !== "undefined" ? localStorage.getItem(STORAGE_KEY) : null;
    const id = initialManagerId ?? (fromStorage ? parseInt(fromStorage, 10) : null);
    if (id && Number.isFinite(id) && id > 0) loadManager(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Derived state ───────────────────────────────────────────────────────────

  const derived =
    state.loadStatus === "ready"
      ? derivePlanStateForGw(state.planGw, {
          currentGw: state.currentGw,
          origSquad: state.origSquad,
          bank: state.origBank,
          freeTransfers: state.origFreeTransfers,
          transfers: state.transfers,
          lineupPlan: state.lineupPlan,
          captainPlan: state.captainPlan,
          viceCaptainPlan: state.viceCaptainPlan,
          chipPlan: state.chipPlan,
          chipHistory: state.chipHistory,
          currentActiveChip: state.currentActiveChip,
          ftOverrides: state.ftOverrides,
          activeFreeHitGw: state.activeFreeHitGw,
          preFreeHitSquad: state.preFreeHitSquad,
        })
      : null;

  const gws = gwList(state.planningStartGw);
  const gwIdx = gws.indexOf(state.planGw);
  const nextDeadlineMs = state.gwDeadlines[state.planGw] ?? null;

  // ── Render ──────────────────────────────────────────────────────────────────

  return (
    <div className="flex min-h-screen flex-col">

      {/* ── Top nav bar ── */}
      <nav className="sticky top-0 z-30 flex items-center gap-3 border-b border-border bg-card/95 px-4 py-2.5 backdrop-blur">
        <a
          href="/"
          className="text-xs text-muted-foreground transition hover:text-foreground"
        >
          ← Analytics
        </a>
        <span className="text-muted-foreground">/</span>
        <span className="text-sm font-semibold">Transfer Planner</span>

        {state.loadStatus === "ready" && (
          <>
            {/* GW navigation */}
            <div className="flex items-center gap-1">
              <button
                onClick={() => dispatch({ type: "SET_PLAN_GW", gw: gws[Math.max(0, gwIdx - 1)] })}
                disabled={gwIdx <= 0}
                aria-label="Previous gameweek"
                className="inline-flex h-7 w-7 items-center justify-center rounded border border-border text-muted-foreground transition hover:text-foreground disabled:opacity-40"
              >
                <ChevronLeft className="h-4 w-4" />
              </button>
              <span className="min-w-[3.5rem] text-center font-mono text-sm font-bold">
                GW{state.planGw}
              </span>
              <button
                onClick={() => dispatch({ type: "SET_PLAN_GW", gw: gws[Math.min(gws.length - 1, gwIdx + 1)] })}
                disabled={gwIdx >= gws.length - 1}
                aria-label="Next gameweek"
                className="inline-flex h-7 w-7 items-center justify-center rounded border border-border text-muted-foreground transition hover:text-foreground disabled:opacity-40"
              >
                <ChevronRight className="h-4 w-4" />
              </button>
            </div>

            {nextDeadlineMs && <DeadlineClock deadlineMs={nextDeadlineMs} />}

            <div className="ml-auto flex items-center gap-2">
              {/* Undo */}
              <button
                onClick={() => dispatch({ type: "UNDO" })}
                disabled={state.history.length === 0}
                title={`Undo: ${state.history[state.history.length - 1]?.label ?? "nothing"}`}
                className="inline-flex h-7 items-center gap-1.5 rounded border border-border px-2 text-xs text-muted-foreground transition hover:text-foreground disabled:opacity-40"
              >
                <Undo2 className="h-3.5 w-3.5" />
                {state.history.length > 0 && (
                  <span className="font-mono">{state.history.length}</span>
                )}
              </button>

              {/* Reset */}
              <button
                onClick={() => {
                  if (confirm("Reset all planned transfers, chips and lineup changes?")) {
                    dispatch({ type: "RESET_PLAN" });
                  }
                }}
                title="Reset plan"
                className="inline-flex h-7 items-center gap-1.5 rounded border border-border px-2 text-xs text-muted-foreground transition hover:text-foreground"
              >
                <RotateCcw className="h-3.5 w-3.5" />
              </button>

              {/* Hide xPts */}
              <button
                onClick={() => dispatch({ type: "TOGGLE_HIDE_XPTS" })}
                title={state.hideXpts ? "Show xPts" : "Hide xPts"}
                className="inline-flex h-7 items-center gap-1.5 rounded border border-border px-2 text-xs text-muted-foreground transition hover:text-foreground"
              >
                {state.hideXpts
                  ? <Eye className="h-3.5 w-3.5" />
                  : <EyeOff className="h-3.5 w-3.5" />}
              </button>

              {/* Change manager */}
              <button
                onClick={() => {
                  loadedIdRef.current = null;
                  dispatch({ type: "LOAD_START" });
                  dispatch({ type: "LOAD_ERROR", error: "" });
                  // Slight hack: show the form by transitioning to idle
                  dispatch({ type: "LOAD_ERROR", error: "" });
                  if (typeof window !== "undefined") localStorage.removeItem(STORAGE_KEY);
                  window.location.reload();
                }}
                className="inline-flex h-7 items-center gap-1.5 rounded border border-border px-2 text-xs text-muted-foreground transition hover:text-foreground"
              >
                Change manager
              </button>
            </div>
          </>
        )}
      </nav>

      {/* ── Bank bar (only when squad loaded) ── */}
      {derived && (
        <BankBar
          bank={derived.bank}
          ft={derived.ft}
          hits={derived.hits}
          chip={derived.chip}
          gw={state.planGw}
          ftOverride={derived.ftOverride}
        />
      )}

      {/* ── Body ── */}
      <div className="flex-1">
        {state.loadStatus === "idle" || state.loadStatus === "error" ? (
          <ManagerForm
            onLoad={loadManager}
            loading={false}
            error={state.loadError || null}
          />
        ) : state.loadStatus === "loading" ? (
          <div className="flex items-center justify-center py-32">
            <div className="flex flex-col items-center gap-3">
              <Loader2 className="h-8 w-8 animate-spin text-primary" />
              <p className="text-sm text-muted-foreground">Loading squad…</p>
            </div>
          </div>
        ) : (
          <SquadSummary state={state} />
        )}
      </div>
    </div>
  );
}
