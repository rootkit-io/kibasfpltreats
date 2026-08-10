"use client";

/**
 * TransferMarket — player search and selection panel.
 *
 * Opens when a player is marked for transfer out. Shows a filterable,
 * sorted list of all available players of the same position. Selecting
 * one previews the bank/FT/hit delta before confirming.
 *
 * Position filter is locked to the out-player's position (FPL rule).
 * Sort options: xPts (default), price desc, ownership desc.
 */

import { useMemo, useState } from "react";
import { Search, X } from "lucide-react";

import { cn } from "@/lib/utils";
import { KitShirt } from "@/components/ui/KitShirt";
import type {
  FplPlayer,
  FplTeam,
  PlannerPick,
  TransferRecord,
  ElementType,
  ELEMENT_TYPE_LABEL,
} from "@/lib/planner/types";
import { ELEMENT_TYPE_LABEL as POS_LABEL } from "@/lib/planner/types";
import type { PlannerState, PlannerAction } from "@/lib/planner/state";
import type { DerivedGwState } from "@/lib/planner/types";

// ── Helpers ───────────────────────────────────────────────────────────────────

function pence(tenths: number): string {
  return `£${(tenths / 10).toFixed(1)}m`;
}

function statusLabel(status: string): string {
  return { a: "", d: "Doubt", i: "Inj", n: "N/A", s: "Susp", u: "N/A" }[status] ?? "";
}

function statusColor(status: string): string {
  return { a: "", d: "text-amber-400", i: "text-rose-400", n: "text-muted-foreground", s: "text-rose-400", u: "text-muted-foreground" }[status] ?? "";
}

type SortMode = "xpts" | "price" | "ownership";

// ── Component ─────────────────────────────────────────────────────────────────

export default function TransferMarket({
  state,
  derived,
  dispatch,
  getXpts,
}: {
  state: PlannerState;
  derived: DerivedGwState;
  dispatch: React.Dispatch<PlannerAction>;
  getXpts: (element: number, gw: number) => number | null;
}) {
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortMode>("xpts");
  const [preview, setPreview] = useState<number | null>(null); // element ID being previewed

  const { playerMap, teamMap, pendingTransferOut, planGw } = state;

  // The player being transferred out
  const outPick = derived.squad.find((p) => p.element === pendingTransferOut) ?? null;
  const outPlayer = outPick ? playerMap.get(outPick.element) : null;

  // All element IDs currently in the squad (to exclude from market)
  const squadIds = useMemo(
    () => new Set(derived.squad.map((p) => p.element)),
    [derived.squad],
  );

  // Players of the same position, not in squad, not "u" status
  const candidates = useMemo<FplPlayer[]>(() => {
    if (!outPlayer) return [];
    const pos = outPlayer.element_type;
    const q = query.trim().toLowerCase();
    return [...playerMap.values()]
      .filter((p) => {
        if (p.element_type !== pos) return false;
        if (squadIds.has(p.id)) return false;
        if (p.status === "u") return false;
        if (q && !p.web_name.toLowerCase().includes(q) &&
            !p.second_name.toLowerCase().includes(q) &&
            !(teamMap.get(p.team)?.short_name ?? "").toLowerCase().includes(q)) return false;
        return true;
      })
      .sort((a, b) => {
        if (sort === "price") return b.now_cost - a.now_cost;
        if (sort === "ownership") {
          return parseFloat(b.selected_by_percent) - parseFloat(a.selected_by_percent);
        }
        // xpts: use getXpts, fall back to ep_next, then form
        const ax = getXpts(a.id, planGw) ?? parseFloat(a.ep_next ?? a.form ?? "0");
        const bx = getXpts(b.id, planGw) ?? parseFloat(b.ep_next ?? b.form ?? "0");
        return bx - ax;
      })
      .slice(0, 60);
  }, [outPlayer, query, sort, playerMap, squadIds, teamMap, getXpts, planGw]);

  // Budget delta for a candidate
  function budgetDelta(inPlayer: FplPlayer): number {
    const sellPrice = outPick?.sellingPrice ?? 0;
    return derived.bank + sellPrice - inPlayer.now_cost;
  }

  function canAfford(inPlayer: FplPlayer): boolean {
    return budgetDelta(inPlayer) >= 0;
  }

  // Club count check: max 3 from any one club
  function clubCount(teamId: number): number {
    return derived.squad.filter((p) => {
      const pl = playerMap.get(p.element);
      return pl?.team === teamId && p.element !== pendingTransferOut;
    }).length;
  }

  function clubWarning(inPlayer: FplPlayer): string | null {
    if (clubCount(inPlayer.team) >= 3) return ">3 from this club";
    return null;
  }

  function confirmTransfer(inPlayer: FplPlayer) {
    if (!outPick || !outPlayer) return;
    const uid = `${planGw}-${outPlayer.id}-${inPlayer.id}-${Date.now()}`;
    const record: TransferRecord = {
      uid,
      gw: planGw,
      outId: outPlayer.id,
      inId: inPlayer.id,
      outPrice: outPick.sellingPrice,
      inPrice: inPlayer.now_cost,
      purchasePrice: inPlayer.now_cost,
      planOrder: 0, // reducer assigns real order
      warnings: [
        ...(!canAfford(inPlayer) ? ["Over budget"] : []),
        ...(clubWarning(inPlayer) ? [clubWarning(inPlayer)!] : []),
      ],
    };
    dispatch({ type: "ADD_TRANSFER", record });
    setPreview(null);
    setQuery("");
  }

  if (!outPlayer || !outPick) {
    return (
      <div className="flex items-center justify-center py-16 text-sm text-muted-foreground">
        Select a player to transfer out first.
      </div>
    );
  }

  const outTeam = teamMap.get(outPlayer.team);

  return (
    <div className="flex flex-col overflow-hidden" style={{ maxHeight: "calc(100vh - 180px)" }}>
      {/* ── Header: out player ── */}
      <div className="flex items-center gap-3 border-b border-border bg-rose-500/10 px-4 py-3">
        <KitShirt teamCode={outTeam?.short_name} size={24} idSuffix={`out-${outPlayer.id}`} />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">{outPlayer.web_name}</p>
          <p className="text-[11px] text-muted-foreground">
            {outTeam?.short_name ?? "—"} · {POS_LABEL[outPlayer.element_type]} · Sell {pence(outPick.sellingPrice)}
          </p>
        </div>
        <button
          onClick={() => dispatch({ type: "CLOSE_MODAL" })}
          className="text-muted-foreground hover:text-foreground"
          aria-label="Close transfer market"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {/* ── Controls ── */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2.5">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={`Search ${POS_LABEL[outPlayer.element_type]}s…`}
            aria-label="Search players"
            className="h-8 w-full rounded border border-border bg-background pl-8 pr-3 text-xs outline-none focus:border-primary"
          />
          {query && (
            <button onClick={() => setQuery("")} className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground">
              <X className="h-3 w-3" />
            </button>
          )}
        </div>

        <div className="inline-flex rounded border border-border p-0.5">
          {(["xpts", "price", "ownership"] as SortMode[]).map((m) => (
            <button
              key={m}
              onClick={() => setSort(m)}
              className={cn(
                "rounded px-2 py-0.5 text-[10px] font-semibold transition",
                sort === m ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {m === "xpts" ? "xPts" : m === "price" ? "£" : "Own%"}
            </button>
          ))}
        </div>
      </div>

      {/* ── Bank available ── */}
      <div className="border-b border-border/50 bg-muted/20 px-4 py-1.5 font-mono text-[11px] text-muted-foreground">
        Available to spend:{" "}
        <span className="font-semibold text-foreground">
          {pence(derived.bank + (outPick?.sellingPrice ?? 0))}
        </span>
        {" "}(bank {pence(derived.bank)} + sell {pence(outPick.sellingPrice)})
      </div>

      {/* ── Player list ── */}
      <div className="flex-1 overflow-y-auto">
        {candidates.length === 0 ? (
          <p className="py-10 text-center text-xs text-muted-foreground">No players match.</p>
        ) : (
          <ul>
            {candidates.map((player) => {
              const team = teamMap.get(player.team);
              const affordable = canAfford(player);
              const warn = clubWarning(player);
              const xp = getXpts(player.id, planGw);
              const xpDisplay = xp !== null
                ? xp.toFixed(1)
                : player.ep_next ?? "—";
              const isPreviewing = preview === player.id;
              const delta = budgetDelta(player);

              return (
                <li key={player.id}>
                  {isPreviewing ? (
                    /* Expanded preview row */
                    <div className="border-b border-border bg-primary/5 px-4 py-3">
                      <div className="flex items-center gap-3">
                        <KitShirt teamCode={team?.short_name} size={24} idSuffix={`prev-${player.id}`} />
                        <div className="min-w-0 flex-1">
                          <p className="text-sm font-semibold">{player.web_name}</p>
                          <p className="text-[11px] text-muted-foreground">
                            {team?.short_name ?? "—"} · {POS_LABEL[player.element_type]}
                          </p>
                        </div>
                        <button onClick={() => setPreview(null)} className="text-muted-foreground hover:text-foreground">
                          <X className="h-3.5 w-3.5" />
                        </button>
                      </div>
                      <dl className="mt-2 grid grid-cols-3 gap-x-4 gap-y-1 font-mono text-[11px]">
                        <dt className="text-muted-foreground">Cost</dt>
                        <dd className="col-span-2">{pence(player.now_cost)}</dd>
                        <dt className="text-muted-foreground">Bank after</dt>
                        <dd className={cn("col-span-2 font-semibold", delta < 0 ? "text-rose-400" : "text-emerald-400")}>
                          {pence(delta)}
                        </dd>
                        <dt className="text-muted-foreground">xPts</dt>
                        <dd className="col-span-2">{xpDisplay}</dd>
                        <dt className="text-muted-foreground">Own%</dt>
                        <dd className="col-span-2">{player.selected_by_percent}%</dd>
                      </dl>
                      {warn && (
                        <p className="mt-1.5 text-[11px] font-semibold text-amber-400">⚠ {warn}</p>
                      )}
                      {!affordable && (
                        <p className="mt-1 text-[11px] font-semibold text-rose-400">⚠ Over budget by {pence(Math.abs(delta))}</p>
                      )}
                      <div className="mt-3 flex gap-2">
                        <button
                          onClick={() => confirmTransfer(player)}
                          className={cn(
                            "flex-1 rounded py-1.5 text-xs font-semibold transition",
                            affordable && !warn
                              ? "bg-primary text-primary-foreground hover:bg-primary/90"
                              : "bg-amber-500/20 text-amber-300 hover:bg-amber-500/30",
                          )}
                        >
                          {affordable ? "Confirm transfer" : "Transfer anyway"}
                        </button>
                        <button
                          onClick={() => setPreview(null)}
                          className="rounded border border-border px-4 py-1.5 text-xs text-muted-foreground hover:text-foreground"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : (
                    /* Normal list row */
                    <button
                      onClick={() => setPreview(player.id)}
                      className={cn(
                        "flex w-full items-center gap-3 border-b border-border/50 px-4 py-2 text-left transition hover:bg-muted/30",
                        !affordable && "opacity-60",
                      )}
                    >
                      <KitShirt teamCode={team?.short_name} size={20} idSuffix={`mkt-${player.id}`} />
                      <div className="min-w-0 flex-1">
                        <span className="block truncate text-xs font-medium">{player.web_name}</span>
                        <span className="text-[10px] text-muted-foreground">
                          {team?.short_name ?? "—"}
                          {statusLabel(player.status) && (
                            <span className={cn("ml-1", statusColor(player.status))}>
                              {statusLabel(player.status)}
                            </span>
                          )}
                        </span>
                      </div>
                      <div className="shrink-0 text-right">
                        <span className="block font-mono text-[11px] font-semibold tabular-nums text-foreground">
                          {pence(player.now_cost)}
                        </span>
                        <span className={cn(
                          "block font-mono text-[10px] tabular-nums",
                          !affordable ? "text-rose-400" : "text-muted-foreground",
                        )}>
                          {!affordable ? `−${pence(Math.abs(delta))}` : xpDisplay}
                        </span>
                      </div>
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
