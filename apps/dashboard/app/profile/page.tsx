"use client";

/**
 * Profile page — FPL manager rank, history, leagues, career.
 *
 * Mirrors the KFT2627 profile.html feature set, rebuilt in React with
 * our existing Clerk auth + BFF pattern. Data comes from three BFF routes
 * that proxy the FPL public API:
 *   GET /api/fpl/manager/[id]   → profile, history, transfers, total_players
 *   GET /api/fpl/league/[id]    → classic-league standings
 *   GET /api/fpl/live/[id]      → live GW snapshot for a compare manager
 *
 * Nothing is stored server-side for this page; the manager ID is in the
 * URL (?id=) and also persisted to localStorage so return visits skip the
 * entry form.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { Users, Trophy, TrendingUp, ChevronLeft, ChevronRight, X, Plus } from "lucide-react";
import { cn } from "@/lib/utils";

// ─── Types ────────────────────────────────────────────────────────────────────

interface GwHistory {
  event: number;
  points: number;
  total_points: number;
  rank: number;
  overall_rank: number;
  event_transfers: number;
  event_transfers_cost: number;
  value: number;
}

interface PastSeason {
  season_name: string;
  total_points: number;
  rank: number;
  rank_percentage: string | null;
}

interface ChipPlay {
  name: string;
  event: number;
}

interface ClassicLeague {
  id: number;
  name: string;
  entry_rank: number | null;
  entry_last_rank: number | null;
  league_type: string;
}

interface FplProfile {
  player_name: string;
  name: string;
  current_event: number | null;
  summary_overall_rank: number | null;
  summary_overall_points: number | null;
  summary_event_points: number | null;
  leagues: { classic: ClassicLeague[] };
}

interface ProfileData {
  profile: FplProfile;
  history: { current: GwHistory[]; past: PastSeason[]; chips: ChipPlay[] };
  total_players: number | null;
}

interface CompareManager {
  id: number;
  name: string;
  team: string;
  history: GwHistory[];
  color: string;
}

interface LeagueStanding {
  id: number;
  entry: number;
  entry_name: string;
  player_name: string;
  rank: number;
  last_rank: number;
  total: number;
  event_total: number;
}

// ─── Constants ────────────────────────────────────────────────────────────────

const PALETTE = [
  "#2563EB","#059669","#DC2626","#7C3AED","#D97706",
  "#0891B2","#DB2777","#65A30D","#4F46E5","#EA580C",
];

const CHIP_LABELS: Record<string, string> = {
  wildcard: "WC", freehit: "FH", bboost: "BB", "3xc": "TC",
};

const STORAGE_KEY = "kft_profile_manager_id";

// ─── Helpers ──────────────────────────────────────────────────────────────────

function fmtRank(r: number | null | undefined): string {
  if (r == null) return "—";
  if (r >= 1_000_000) return `${(r / 1_000_000).toFixed(1)}M`;
  if (r >= 1_000) return `${(r / 1_000).toFixed(0)}K`;
  return r.toLocaleString();
}

function fmtPct(raw: string | null | undefined): string {
  if (!raw) return "";
  const n = parseFloat(raw);
  if (!Number.isFinite(n)) return "";
  if (n === 0) return "Top <0.1%";
  if (n < 1) return `Top ${n.toFixed(1)}%`;
  return `Top ${Math.round(n)}%`;
}

function chipLabel(name: string): string {
  return CHIP_LABELS[name.toLowerCase()] ?? name.toUpperCase().slice(0, 2);
}

// ─── Rank Chart ───────────────────────────────────────────────────────────────

const CHART_W = 600;
const CHART_H = 200;
const PAD = { t: 14, r: 20, b: 38, l: 64 };
const PW = CHART_W - PAD.l - PAD.r;
const PH = CHART_H - PAD.t - PAD.b;

function rankY(rank: number, lo: number, hi: number): number {
  const clamped = Math.max(lo, Math.min(hi, rank));
  return PAD.t + ((clamped - lo) / (hi - lo)) * PH;
}

function RankChart({
  primary,
  compare,
  chips,
}: {
  primary: GwHistory[];
  compare: CompareManager[];
  chips: ChipPlay[];
}) {
  const all = [primary, ...compare.map((c) => c.history)].flatMap((h) =>
    h.map((e) => e.overall_rank).filter((r) => r > 0),
  );
  if (!all.length) return null;

  const lo = Math.min(...all) * 0.92;
  const hi = Math.max(...all) * 1.08;
  const gws = primary.map((e) => e.event);
  const xFor = (i: number) => PAD.l + (i / Math.max(gws.length - 1, 1)) * PW;

  const linePath = (history: GwHistory[]) =>
    history
      .map((e, i) => `${i === 0 ? "M" : "L"}${xFor(i).toFixed(1)},${rankY(e.overall_rank, lo, hi).toFixed(1)}`)
      .join(" ");

  const gridRanks = [1_000, 10_000, 100_000, 500_000, 1_000_000, 5_000_000]
    .filter((r) => r >= lo * 0.8 && r <= hi * 1.2);

  return (
    <svg
      viewBox={`0 0 ${CHART_W} ${CHART_H}`}
      preserveAspectRatio="xMidYMid meet"
      className="w-full"
      style={{ maxHeight: 220 }}
      aria-label="Rank history chart"
    >
      {/* Grid lines */}
      {gridRanks.map((r) => {
        const y = rankY(r, lo, hi);
        return (
          <g key={r}>
            <line x1={PAD.l} x2={CHART_W - PAD.r} y1={y} y2={y}
              stroke="currentColor" strokeOpacity="0.08" strokeWidth="1" />
            <text x={PAD.l - 6} y={y + 4} textAnchor="end"
              className="fill-muted-foreground" style={{ fontSize: 9, fontFamily: "monospace" }}>
              {fmtRank(r)}
            </text>
          </g>
        );
      })}

      {/* X axis GW labels */}
      {gws.filter((_, i) => i === 0 || i === gws.length - 1 || i % 5 === 4).map((gw, _, arr) => {
        const i = gws.indexOf(gw);
        return (
          <text key={gw} x={xFor(i)} y={CHART_H - PAD.b + 14} textAnchor="middle"
            className="fill-muted-foreground" style={{ fontSize: 9, fontFamily: "monospace" }}>
            {gw}
          </text>
        );
      })}

      {/* Compare lines */}
      {compare.map((c) => (
        <path key={c.id} d={linePath(c.history)} fill="none"
          stroke={c.color} strokeWidth="1.5" strokeOpacity="0.7" strokeLinecap="round" strokeLinejoin="round" />
      ))}

      {/* Primary line */}
      {primary.length > 0 && (
        <path d={linePath(primary)} fill="none"
          stroke="#FF5F1F" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
      )}

      {/* Chip badges */}
      {chips.map((chip) => {
        const i = gws.indexOf(chip.event);
        if (i < 0) return null;
        const entry = primary[i];
        if (!entry) return null;
        const cx = xFor(i);
        const cy = rankY(entry.overall_rank, lo, hi);
        return (
          <g key={`${chip.name}-${chip.event}`}>
            <circle cx={cx} cy={cy} r="7" fill="#FF5F1F" />
            <text x={cx} y={cy + 4} textAnchor="middle"
              style={{ fontSize: 7, fontFamily: "monospace", fontWeight: 700, fill: "white" }}>
              {chipLabel(chip.name)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

// ─── GW Timeline ──────────────────────────────────────────────────────────────

function GwTimeline({ history, managerId }: { history: GwHistory[]; managerId: number }) {
  return (
    <div className="grid grid-cols-[repeat(auto-fill,minmax(52px,1fr))] gap-1">
      {history.map((gw, i) => {
        const prev = history[i - 1];
        const delta = prev ? prev.overall_rank - gw.overall_rank : null;
        const dir = delta === null ? null : delta > 0 ? "up" : delta < 0 ? "down" : "flat";
        return (
          <a
            key={gw.event}
            href={`https://fantasy.premierleague.com/entry/${managerId}/event/${gw.event}`}
            target="_blank"
            rel="noopener noreferrer"
            className={cn(
              "flex flex-col items-center rounded border border-border bg-card px-1 py-2 text-center transition hover:border-primary/50 hover:bg-muted",
            )}
          >
            <span className="font-mono text-[10px] text-muted-foreground">GW{gw.event}</span>
            <span className="mt-0.5 font-mono text-sm font-bold tabular-nums text-foreground">
              {gw.points}
            </span>
            <span className={cn("mt-0.5 font-mono text-[9px] tabular-nums",
              dir === "up" ? "text-emerald-400" : dir === "down" ? "text-rose-400" : "text-zinc-500")}>
              {dir === "up" ? `↑${Math.abs(delta!).toLocaleString()}` :
               dir === "down" ? `↓${Math.abs(delta!).toLocaleString()}` : "–"}
            </span>
          </a>
        );
      })}
    </div>
  );
}

// ─── Career Section ───────────────────────────────────────────────────────────

function CareerSection({ past }: { past: PastSeason[] }) {
  if (!past.length) return null;
  return (
    <div className="border border-border bg-card">
      <h3 className="border-b border-border px-4 py-2.5 text-sm font-semibold">Career History</h3>
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-border/50 text-left text-muted-foreground">
            <th className="px-4 py-2 font-medium">Season</th>
            <th className="px-4 py-2 text-right font-medium">Points</th>
            <th className="px-4 py-2 text-right font-medium">Rank</th>
            <th className="px-4 py-2 text-right font-medium">Percentile</th>
          </tr>
        </thead>
        <tbody>
          {[...past].reverse().map((s) => (
            <tr key={s.season_name} className="border-b border-border/30 last:border-0">
              <td className="px-4 py-2 font-medium">{s.season_name}</td>
              <td className="px-4 py-2 text-right font-mono tabular-nums">{s.total_points.toLocaleString()}</td>
              <td className="px-4 py-2 text-right font-mono tabular-nums">{fmtRank(s.rank)}</td>
              <td className="px-4 py-2 text-right font-mono text-muted-foreground">{fmtPct(s.rank_percentage)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ─── League Section ───────────────────────────────────────────────────────────

function LeagueSection({
  leagues,
  primaryId,
  compare,
  onAddCompare,
  onRemoveCompare,
}: {
  leagues: ClassicLeague[];
  primaryId: number;
  compare: CompareManager[];
  onAddCompare: (id: number) => void;
  onRemoveCompare: (id: number) => void;
}) {
  const [selectedId, setSelectedId] = useState<number | null>(leagues[0]?.id ?? null);
  const [standings, setStandings] = useState<LeagueStanding[]>([]);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const PER_PAGE = 10;

  const filteredLeagues = leagues.filter((l) => l.league_type === "x" || l.league_type === "s");

  useEffect(() => {
    if (!selectedId) return;
    setLoading(true);
    setPage(1);
    fetch(`/api/fpl/league/${selectedId}`)
      .then((r) => r.json())
      .then((d) => setStandings(d.standings ?? []))
      .catch(() => setStandings([]))
      .finally(() => setLoading(false));
  }, [selectedId]);

  const page_rows = standings.slice((page - 1) * PER_PAGE, page * PER_PAGE);
  const totalPages = Math.ceil(standings.length / PER_PAGE);
  const compareIds = new Set(compare.map((c) => c.id));

  return (
    <div className="border border-border bg-card">
      <div className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-2.5">
        <h3 className="text-sm font-semibold">Classic Leagues</h3>
        <select
          value={selectedId ?? ""}
          onChange={(e) => setSelectedId(Number(e.target.value))}
          className="rounded border border-border bg-background px-2 py-1 text-xs outline-none focus:border-primary"
        >
          {filteredLeagues.map((l) => (
            <option key={l.id} value={l.id}>{l.name}</option>
          ))}
        </select>
        {loading && <span className="text-xs text-muted-foreground">Loading…</span>}
      </div>

      {standings.length > 0 && (
        <>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-border/50 text-left text-muted-foreground">
                <th className="px-4 py-2 font-medium">Rank</th>
                <th className="px-4 py-2 font-medium">Manager</th>
                <th className="px-4 py-2 text-right font-medium">GW</th>
                <th className="px-4 py-2 text-right font-medium">Total</th>
                <th className="px-4 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {page_rows.map((row) => (
                <tr key={row.id}
                  className={cn("border-b border-border/30 last:border-0",
                    row.entry === primaryId && "bg-primary/5")}>
                  <td className="px-4 py-2 font-mono tabular-nums">{row.rank}</td>
                  <td className="px-4 py-2">
                    <div className="font-medium">{row.entry_name}</div>
                    <div className="text-muted-foreground">{row.player_name}</div>
                  </td>
                  <td className="px-4 py-2 text-right font-mono tabular-nums">{row.event_total}</td>
                  <td className="px-4 py-2 text-right font-mono tabular-nums">{row.total.toLocaleString()}</td>
                  <td className="px-4 py-2">
                    {row.entry !== primaryId && (
                      compareIds.has(row.entry) ? (
                        <button onClick={() => onRemoveCompare(row.entry)}
                          title="Remove from compare"
                          className="text-muted-foreground hover:text-rose-400">
                          <X className="h-3.5 w-3.5" />
                        </button>
                      ) : compare.length < 5 ? (
                        <button onClick={() => onAddCompare(row.entry)}
                          title="Add to compare"
                          className="text-muted-foreground hover:text-primary">
                          <Plus className="h-3.5 w-3.5" />
                        </button>
                      ) : null
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {totalPages > 1 && (
            <div className="flex items-center justify-between border-t border-border px-4 py-2">
              <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1}
                className="disabled:opacity-40">
                <ChevronLeft className="h-4 w-4" />
              </button>
              <span className="text-xs text-muted-foreground">Page {page} / {totalPages}</span>
              <button onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page === totalPages}
                className="disabled:opacity-40">
                <ChevronRight className="h-4 w-4" />
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ─── Main page component ──────────────────────────────────────────────────────

function ProfileInner() {
  const router = useRouter();
  const params = useSearchParams();

  const [inputId, setInputId] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<ProfileData | null>(null);
  const [managerId, setManagerId] = useState<number | null>(null);
  const [compare, setCompare] = useState<CompareManager[]>([]);

  // Auto-load from URL or localStorage on mount
  useEffect(() => {
    const fromUrl = params.get("id");
    const fromStorage = typeof window !== "undefined" ? localStorage.getItem(STORAGE_KEY) : null;
    const id = fromUrl ?? fromStorage;
    if (id) { setInputId(id); loadManager(Number(id)); }
  }, []);

  const loadManager = useCallback(async (id: number) => {
    if (!Number.isFinite(id) || id < 1) { setError("Enter a valid manager ID."); return; }
    setLoading(true); setError(null);
    try {
      const r = await fetch(`/api/fpl/manager/${id}`);
      if (!r.ok) throw new Error(`${r.status}`);
      const d: ProfileData = await r.json();
      setData(d);
      setManagerId(id);
      setCompare([]);
      if (typeof window !== "undefined") localStorage.setItem(STORAGE_KEY, String(id));
      router.replace(`?id=${id}`, { scroll: false });
    } catch (e) {
      setError("Could not load this manager. Check the ID and try again.");
    } finally {
      setLoading(false);
    }
  }, [router]);

  const addCompare = useCallback(async (entryId: number) => {
    if (compare.length >= 5) return;
    const colorIdx = compare.length % PALETTE.length;
    try {
      const r = await fetch(`/api/fpl/manager/${entryId}`);
      const d: ProfileData = await r.json();
      setCompare((prev) => [
        ...prev,
        {
          id: entryId,
          name: d.profile.player_name,
          team: d.profile.name,
          history: d.history.current,
          color: PALETTE[colorIdx],
        },
      ]);
    } catch { /* silently skip */ }
  }, [compare]);

  const removeCompare = useCallback((id: number) => {
    setCompare((prev) => prev.filter((c) => c.id !== id));
  }, []);

  const currentGw = data?.history.current;
  const lastGw = currentGw?.[currentGw.length - 1];
  const rank = data?.profile.summary_overall_rank;
  const pts = data?.profile.summary_overall_points;

  return (
    <main className="mx-auto max-w-5xl space-y-6 px-4 py-6">
      {/* Nav */}
      <div className="flex items-center gap-3 text-sm">
        <a href="/" className="text-muted-foreground hover:text-foreground">← Analytics</a>
        <span className="text-muted-foreground">/</span>
        <span className="font-medium text-foreground">FPL Profile</span>
      </div>

      {/* Entry form */}
      {!data && (
        <section className="border border-border bg-card px-6 py-8">
          <h1 className="text-xl font-bold">FPL Manager Profile</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Enter your FPL manager ID to see rank history, gameweek timeline, leagues and career.
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            Find your ID in the FPL URL: fantasy.premierleague.com/entry/<strong>123456</strong>/…
          </p>
          <form onSubmit={(e) => { e.preventDefault(); loadManager(Number(inputId)); }}
            className="mt-5 flex flex-wrap gap-2">
            <input
              type="number"
              value={inputId}
              onChange={(e) => setInputId(e.target.value)}
              placeholder="Manager ID e.g. 123456"
              className="h-10 w-56 rounded border border-border bg-background px-3 text-sm outline-none focus:border-primary"
            />
            <button type="submit" disabled={loading}
              className="h-10 rounded bg-primary px-4 text-sm font-medium text-white hover:bg-primary/90 disabled:opacity-60">
              {loading ? "Loading…" : "Load Profile"}
            </button>
          </form>
          {error && <p className="mt-3 text-sm text-rose-400">{error}</p>}
        </section>
      )}

      {/* Profile content */}
      {data && managerId && (
        <>
          {/* Summary cards */}
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div>
              <h1 className="text-lg font-bold">{data.profile.player_name}</h1>
              <p className="text-sm text-muted-foreground">{data.profile.name}</p>
            </div>
            <button onClick={() => { setData(null); setManagerId(null); setCompare([]); router.replace("/profile"); }}
              className="text-xs text-muted-foreground underline hover:text-foreground">
              Change manager
            </button>
          </div>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              { label: "Overall Rank", value: fmtRank(rank), sub: rank && data.total_players ? fmtPct(String(Math.round((rank / data.total_players) * 10) / 10)) : "" },
              { label: "Total Points", value: pts?.toLocaleString() ?? "—", sub: `${currentGw?.length ?? 0} gameweeks` },
              { label: "This GW", value: lastGw?.points?.toString() ?? "—", sub: `GW ${lastGw?.event ?? "—"}` },
              { label: "GW Rank", value: fmtRank(lastGw?.rank), sub: "" },
            ].map((card) => (
              <div key={card.label} className="border border-border bg-card px-4 py-3">
                <p className="text-[11px] uppercase tracking-wide text-muted-foreground">{card.label}</p>
                <p className="mt-1 font-mono text-2xl font-bold tabular-nums text-foreground">{card.value}</p>
                {card.sub && <p className="mt-0.5 text-xs text-muted-foreground">{card.sub}</p>}
              </div>
            ))}
          </div>

          {/* Rank chart */}
          {currentGw && currentGw.length > 1 && (
            <div className="border border-border bg-card px-4 py-3">
              <div className="flex flex-wrap items-center justify-between gap-2 pb-2">
                <h3 className="text-sm font-semibold">Rank History</h3>
                {compare.length > 0 && (
                  <div className="flex flex-wrap gap-2 text-xs">
                    <span className="inline-flex items-center gap-1.5">
                      <span className="inline-block h-2 w-4 rounded" style={{ background: "#FF5F1F" }} />
                      {data.profile.player_name}
                    </span>
                    {compare.map((c) => (
                      <span key={c.id} className="inline-flex items-center gap-1.5">
                        <span className="inline-block h-2 w-4 rounded" style={{ background: c.color }} />
                        {c.name}
                        <button onClick={() => removeCompare(c.id)} className="text-muted-foreground hover:text-foreground">
                          <X className="h-3 w-3" />
                        </button>
                      </span>
                    ))}
                  </div>
                )}
              </div>
              <RankChart primary={currentGw} compare={compare} chips={data.history.chips} />
            </div>
          )}

          {/* GW timeline */}
          {currentGw && currentGw.length > 0 && (
            <div className="border border-border bg-card px-4 py-3">
              <h3 className="mb-3 text-sm font-semibold">Gameweek Timeline</h3>
              <GwTimeline history={currentGw} managerId={managerId} />
            </div>
          )}

          {/* League standings */}
          {data.profile.leagues.classic.length > 0 && (
            <LeagueSection
              leagues={data.profile.leagues.classic}
              primaryId={managerId}
              compare={compare}
              onAddCompare={addCompare}
              onRemoveCompare={removeCompare}
            />
          )}

          {/* Career history */}
          {data.history.past.length > 0 && (
            <CareerSection past={data.history.past} />
          )}
        </>
      )}

      {/* Loading state */}
      {loading && !data && (
        <div className="border border-border bg-card px-6 py-16 text-center">
          <p className="text-sm text-muted-foreground">Loading manager data…</p>
        </div>
      )}
    </main>
  );
}

export default function ProfilePage() {
  return (
    <Suspense fallback={
      <main className="mx-auto max-w-5xl px-4 py-6">
        <p className="text-sm text-muted-foreground">Loading…</p>
      </main>
    }>
      <ProfileInner />
    </Suspense>
  );
}
