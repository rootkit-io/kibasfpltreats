"use client";

/**
 * HistorySidebar -- the Admin Panel's run history (Phase 10).
 *
 * Fetches run summaries from the BFF (GET /api/admin/projections/runs --
 * metadata only, newest first), and on click fetches that run's full state
 * (GET /api/admin/projections/runs/{id}) and dispatches HISTORY_LOADED so
 * the wizard's ProjectionsGrid re-hydrates with the persisted tables.
 *
 * `refreshKey` bumps after every draft save / publish so the list stays
 * current without polling. 503 from the BFF means persistence is not
 * configured -- rendered as a quiet notice, not an error wall.
 */

import { useCallback, useEffect, useState } from "react";
import { Clock, History, Loader2, RefreshCw } from "lucide-react";

import { cn } from "@/lib/utils";
import {
  runDetailResponseSchema,
  runListResponseSchema,
  type RunStatus,
  type RunSummary,
} from "@/lib/validations/runs";
import type { PreviewTables, WizardAction } from "@/components/admin/WeeklyRunWizard";

interface HistorySidebarProps {
  dispatch: (action: WizardAction) => void;
  refreshKey: number;
  activeRunId: string | null;
  disabled?: boolean;
}

type ListState =
  | { status: "loading" }
  | { status: "unavailable"; message: string }
  | { status: "error"; message: string }
  | { status: "ready"; runs: RunSummary[] };

const STATUS_BADGE: Record<RunStatus, string> = {
  draft: "border-amber-300 bg-amber-50 text-amber-800",
  published: "border-emerald-300 bg-emerald-50 text-emerald-800",
  archived: "border-border bg-muted text-muted-foreground",
};

function formatTimestamp(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function HistorySidebar({
  dispatch,
  refreshKey,
  activeRunId,
  disabled = false,
}: HistorySidebarProps) {
  const [list, setList] = useState<ListState>({ status: "loading" });
  const [loadingRunId, setLoadingRunId] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const fetchRuns = useCallback(async (signal?: AbortSignal) => {
    setList({ status: "loading" });
    try {
      const response = await fetch("/api/admin/projections/runs?limit=20", {
        cache: "no-store",
        signal,
      });
      const body: unknown = await response.json();
      if (response.status === 503) {
        setList({
          status: "unavailable",
          message: "Run history needs the database (DATABASE_URL unset).",
        });
        return;
      }
      if (!response.ok) {
        const detail =
          typeof body === "object" && body !== null
            ? ((body as Record<string, unknown>).detail ??
              (body as Record<string, unknown>).error)
            : null;
        setList({
          status: "error",
          message: typeof detail === "string" ? detail : "failed to load run history",
        });
        return;
      }
      const parsed = runListResponseSchema.safeParse(body);
      if (!parsed.success) {
        setList({ status: "error", message: "unexpected run-history shape" });
        return;
      }
      // Server returns newest first; sort defensively all the same.
      const runs = [...parsed.data.runs].sort((a, b) =>
        b.created_at.localeCompare(a.created_at),
      );
      setList({ status: "ready", runs });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setList({ status: "error", message: "run history unreachable" });
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void fetchRuns(controller.signal);
    return () => controller.abort();
  }, [fetchRuns, refreshKey]);

  const loadRun = useCallback(
    async (summary: RunSummary) => {
      if (disabled || loadingRunId !== null) return;
      setLoadError(null);
      setLoadingRunId(summary.run_id);
      try {
        const response = await fetch(
          `/api/admin/projections/runs/${summary.run_id}`,
          { cache: "no-store" },
        );
        const body: unknown = await response.json();
        if (!response.ok) {
          const detail =
            typeof body === "object" && body !== null
              ? ((body as Record<string, unknown>).detail ??
                (body as Record<string, unknown>).error)
              : null;
          setLoadError(
            typeof detail === "string"
              ? detail
              : `failed to load run ${summary.run_id.slice(0, 8)}`,
          );
          return;
        }
        const parsed = runDetailResponseSchema.safeParse(body);
        if (!parsed.success) {
          setLoadError("unexpected run-detail shape");
          return;
        }
        const detail = parsed.data;
        dispatch({
          type: "HISTORY_LOADED",
          label: `${detail.season}${detail.gameweek ? ` GW${detail.gameweek}` : ""} · ${detail.status} · ${detail.run_id.slice(0, 8)}`,
          preview: {
            runId: detail.run_id,
            tables: detail.tables as PreviewTables,
            minutesModelLoaded: Boolean(detail.minutes_model_loaded),
            includeMc: Boolean(detail.include_mc),
          },
        });
      } catch {
        setLoadError("run detail unreachable");
      } finally {
        setLoadingRunId(null);
      }
    },
    [disabled, dispatch, loadingRunId],
  );

  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-center justify-between">
        <h2 className="inline-flex items-center gap-1.5 text-sm font-medium">
          <History className="h-4 w-4" /> Run history
        </h2>
        <button
          type="button"
          aria-label="Refresh run history"
          className="rounded p-1 text-muted-foreground hover:bg-muted disabled:opacity-40"
          disabled={list.status === "loading"}
          onClick={() => void fetchRuns()}
        >
          <RefreshCw
            className={cn("h-3.5 w-3.5", list.status === "loading" && "animate-spin")}
          />
        </button>
      </div>

      {list.status === "loading" && (
        <p className="mt-3 inline-flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" /> Loading runs…
        </p>
      )}

      {list.status === "unavailable" && (
        <p className="mt-3 text-xs text-muted-foreground">{list.message}</p>
      )}

      {list.status === "error" && (
        <p className="mt-3 text-sm text-red-700">{list.message}</p>
      )}

      {list.status === "ready" && list.runs.length === 0 && (
        <p className="mt-3 text-xs text-muted-foreground">
          No saved runs yet — “Run &amp; Save Draft” creates the first one.
        </p>
      )}

      {list.status === "ready" && list.runs.length > 0 && (
        <ul className="mt-3 flex max-h-96 flex-col gap-1.5 overflow-y-auto">
          {list.runs.map((run) => {
            const isActive = run.run_id === activeRunId;
            const isLoading = run.run_id === loadingRunId;
            return (
              <li key={run.run_id}>
                <button
                  type="button"
                  onClick={() => void loadRun(run)}
                  disabled={disabled || loadingRunId !== null}
                  className={cn(
                    "w-full rounded-md border px-2.5 py-2 text-left text-xs transition-colors",
                    isActive
                      ? "border-foreground bg-muted/60"
                      : "border-border hover:bg-muted/40",
                    (disabled || (loadingRunId !== null && !isLoading)) &&
                      "opacity-50",
                  )}
                >
                  <span className="flex items-center justify-between gap-2">
                    <span className="font-medium text-foreground">
                      {run.season}
                      {run.gameweek ? ` · GW ${run.gameweek}` : ""}
                    </span>
                    <span
                      className={cn(
                        "rounded-full border px-1.5 py-0.5 text-[10px] font-medium",
                        STATUS_BADGE[run.status],
                      )}
                    >
                      {run.status}
                    </span>
                  </span>
                  <span className="mt-1 flex items-center justify-between gap-2 text-muted-foreground">
                    <span className="inline-flex items-center gap-1">
                      {isLoading ? (
                        <Loader2 className="h-3 w-3 animate-spin" />
                      ) : (
                        <Clock className="h-3 w-3" />
                      )}
                      {formatTimestamp(run.created_at)}
                    </span>
                    <span className="truncate font-mono text-[10px]">
                      {run.run_id.slice(0, 8)}
                    </span>
                  </span>
                  {run.notes && (
                    <span className="mt-1 block truncate text-muted-foreground">
                      {run.notes}
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {loadError && <p className="mt-2 text-xs text-red-700">{loadError}</p>}
    </div>
  );
}
