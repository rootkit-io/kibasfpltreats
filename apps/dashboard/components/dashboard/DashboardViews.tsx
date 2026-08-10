"use client";

/**
 * DashboardViews -- tab shell over the projections table and the Monte Carlo
 * views.
 *
 * The active tab lives in the query string (`?view=`) like every other filter
 * on this page, so a link to a specific view survives a refresh and a share.
 *
 * The MC views are per-gameweek by nature (a distribution is only defined for
 * a single fixture window), so they own a gameweek selector independent of the
 * projections table's range window.
 */

import { useMemo, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { BarChart3, CalendarDays, ChartScatter, GitCompareArrows, Radar, Shield, Table2 } from "lucide-react";

import { cn } from "@/lib/utils";
import ProjectionsTable from "@/components/dashboard/ProjectionsTable";
import RiskRewardScatter from "@/components/dashboard/RiskRewardScatter";
import XgiScatterMap from "@/components/dashboard/XgiScatterMap";
import DefconScatterMap from "@/components/dashboard/DefconScatterMap";
import XptsComparePanel from "@/components/dashboard/XptsComparePanel";
import BracketDistribution from "@/components/dashboard/BracketDistribution";
import PlayerCompareModal from "@/components/dashboard/PlayerCompareModal";
import XgiRadar from "@/components/dashboard/XgiRadar";
import FixtureTicker from "@/components/dashboard/FixtureTicker";
import type { FixtureRow } from "@/lib/api/fixtures";
import type { ProjectionRow } from "@/lib/validations/projections";
import {
  enrichWithPositions,
  simulationGameweeks,
  type SimulationRow,
} from "@/lib/api/simulations";

type View = "table" | "radar" | "xgi_map" | "defcon_map" | "xpts_compare" | "risk" | "ticker" | "compare";

/** `needs` gates tabs when the published run lacks simulation data. */
const TABS: { key: View; label: string; icon: typeof Table2; needs?: "sim" }[] = [
  { key: "table", label: "Projections", icon: Table2 },
  { key: "radar", label: "xGI Radar", icon: Radar },
  { key: "xgi_map", label: "xGI Map", icon: ChartScatter },
  { key: "defcon_map", label: "Defensive Map", icon: Shield },
  { key: "xpts_compare", label: "xPts Compare", icon: GitCompareArrows },
  { key: "risk", label: "Risk & Reward", icon: BarChart3, needs: "sim" },
  { key: "ticker", label: "Ticker", icon: CalendarDays },
  { key: "compare", label: "MC Compare", icon: BarChart3, needs: "sim" },
];

export default function DashboardViews({
  rows,
  simulations,
  fixtures,
  season,
}: {
  rows: ProjectionRow[];
  simulations: SimulationRow[];
  fixtures: FixtureRow[];
  season: string;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  const view = (TABS.find((t) => t.key === params.get("view"))?.key ?? "table") as View;
  const hasSimulations = simulations.length > 0;

  /** Gameweeks present in the projections payload (radar is projection-based). */
  const projectionGameweeks = useMemo(() => {
    const seen = new Set<number>();
    for (const row of rows) {
      if (typeof row.gameweek_id === "number") seen.add(row.gameweek_id);
    }
    return [...seen].sort((a, b) => a - b);
  }, [rows]);
  const [radarGw, setRadarGw] = useState<number | null>(null);

  const gameweeks = useMemo(() => simulationGameweeks(simulations), [simulations]);
  const [gw, setGw] = useState<number | null>(gameweeks[0] ?? null);

  const enriched = useMemo(() => {
    const scoped = gw === null ? simulations : simulations.filter((s) => s.gameweek_id === gw);
    return enrichWithPositions(scoped, rows);
  }, [simulations, rows, gw]);

  const setView = (next: View) => {
    const sp = new URLSearchParams(params.toString());
    if (next === "table") sp.delete("view");
    else sp.set("view", next);
    router.replace(`${pathname}?${sp.toString()}`, { scroll: false });
  };

  return (
    <section className="flex flex-col gap-3">
      {/* ----------------------------------------------------------- tabs */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="inline-flex rounded-lg border border-border bg-card p-0.5">
          {TABS.map((tab) => {
            const disabled = tab.needs === "sim" && !hasSimulations;
            const Icon = tab.icon;
            return (
              <button
                key={tab.key}
                onClick={() => !disabled && setView(tab.key)}
                disabled={disabled}
                title={
                  disabled ? "This published run carries no simulation data" : undefined
                }
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium transition",
                  view === tab.key
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:text-foreground",
                  disabled && "cursor-not-allowed opacity-40 hover:text-muted-foreground",
                )}
              >
                <Icon className="h-3.5 w-3.5" aria-hidden />
                {tab.label}
              </button>
            );
          })}
        </div>

        {/* gameweek selector: MC views only (radar and ticker own theirs) */}
        {(view === "risk" || view === "compare") && gameweeks.length > 0 && (
          <div className="inline-flex flex-wrap items-center gap-1">
            <span className="mr-1 text-xs text-muted-foreground">Gameweek</span>
            {gameweeks.map((g) => (
              <button
                key={g}
                onClick={() => setGw(g)}
                className={cn(
                  "rounded-md border px-2 py-1 font-mono text-xs transition",
                  gw === g
                    ? "border-primary bg-primary/10 text-foreground"
                    : "border-border text-muted-foreground hover:text-foreground",
                )}
              >
                GW{g}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* ----------------------------------------------------------- body */}
      {view === "table" && <ProjectionsTable rows={rows} season={season} />}

      {view === "radar" && (
        <XgiRadar
          rows={rows}
          gameweeks={projectionGameweeks}
          gameweek={radarGw ?? projectionGameweeks[0] ?? null}
          onGameweekChange={setRadarGw}
        />
      )}

      {view === "xgi_map" && (
        <XgiScatterMap rows={rows} gameweeks={projectionGameweeks} />
      )}

      {view === "defcon_map" && (
        <DefconScatterMap rows={rows} gameweeks={projectionGameweeks} />
      )}

      {view === "xpts_compare" && (
        <XptsComparePanel rows={rows} gameweeks={projectionGameweeks} />
      )}

      {view === "ticker" && <FixtureTicker fixtures={fixtures} />}

      {view === "risk" && (
        <div className="flex flex-col gap-4">
          <RiskRewardScatter rows={enriched} />
          <BracketDistribution rows={enriched} />
        </div>
      )}

      {view === "compare" && <PlayerCompareModal rows={enriched} />}
    </section>
  );
}
