"use client";

/**
 * WeeklyRunWizard -- the Admin Panel's weekly workflow shell (Phase 6).
 *
 * Owns the workflow state machine:
 *   idle -> parsed -> running -> previewed(run_id?) -> publishing -> published
 * with contract_error / publish_error edges.
 *
 * This is the scaffold: the state machine and BFF wiring are real; the
 * MinutesCsvDropzone / MinutesReviewTable / ProjectionsGrid children are
 * placeholder blocks to be replaced with PapaParse + TanStack Table in the
 * next phase.
 */

import { useCallback, useReducer, useState } from "react";

import HistorySidebar from "@/components/admin/HistorySidebar";
import MinutesCsvDropzone from "@/components/admin/MinutesCsvDropzone";
import MinutesReviewTable from "@/components/admin/MinutesReviewTable";
import OverridesCsvDropzone, {
  EMPTY_OVERRIDES_UPLOAD,
  type OverridesUpload,
} from "@/components/admin/OverridesCsvDropzone";
import ProjectionsGrid from "@/components/admin/ProjectionsGrid";
import { BrandLogo } from "@/components/ui/brand-logo";
import { DEFAULT_SEASON, SEASONS, seasonLabel } from "@/lib/seasons";
import type {
  CsvRowError,
  PlayerMinutesState,
  MinuteOverrideState,
  ReviewRow,
} from "@/lib/validations/minutes";

// ------------------------------------------------------------------ types

/** One entry of the backend's 400 detail.errors -- loc maps to a CSV line. */
export interface ContractError {
  loc: (string | number)[];
  msg: string;
  type: string;
}

/** tables.* from the run response: arrays of records, rendered by the grids. */
export type PreviewTables = Record<string, Record<string, unknown>[]>;

export interface RunPreview {
  runId: string | null;
  tables: PreviewTables;
  minutesModelLoaded: boolean;
  includeMc: boolean;
}

interface ParsedPayload {
  states: PlayerMinutesState[];
  overrides: MinuteOverrideState[];
  preflightErrors: CsvRowError[];
  reviewRows: ReviewRow[];
  fileName: string;
  rowCount: number;
}

export type WizardState =
  | { step: "idle" }
  | ({ step: "invalid_csv" } & ParsedPayload)
  | ({ step: "parsed" } & ParsedPayload)
  | ({ step: "running" } & ParsedPayload & { saveAsDraft: boolean })
  | ({ step: "contract_error" } & ParsedPayload & {
      message: string;
      errors: ContractError[];
    })
  | ({ step: "previewed" } & ParsedPayload & { preview: RunPreview })
  | ({ step: "publishing" } & ParsedPayload & {
      preview: RunPreview & { runId: string };
    })
  | ({ step: "published" } & ParsedPayload & {
      preview: RunPreview & { runId: string };
      publishedAt: string;
    })
  | ({ step: "publish_error" } & ParsedPayload & {
      preview: RunPreview & { runId: string };
      message: string;
    });

export type WizardAction =
  | { type: "CSV_PARSED"; payload: ParsedPayload }
  | {
      type: "INVALID_CSV";
      fileName: string;
      rowCount: number;
      preflightErrors: CsvRowError[];
      reviewRows: ReviewRow[];
    }
  | { type: "RESET" }
  | { type: "RUN_STARTED"; saveAsDraft: boolean }
  | { type: "RUN_SUCCEEDED"; preview: RunPreview }
  | { type: "RUN_REJECTED"; message: string; errors: ContractError[] }
  | { type: "PUBLISH_STARTED" }
  | { type: "PUBLISH_SUCCEEDED"; publishedAt: string }
  | { type: "PUBLISH_FAILED"; message: string }
  | { type: "HISTORY_LOADED"; label: string; preview: RunPreview };

// The reducer is intentionally strict: illegal transitions return the
// current state unchanged, so UI bugs cannot corrupt the workflow.
function wizardReducer(state: WizardState, action: WizardAction): WizardState {
  switch (action.type) {
    case "CSV_PARSED":
      return { step: "parsed", ...action.payload };
    case "INVALID_CSV":
      // Preflight failed: block the run path entirely (all-or-nothing,
      // matching the server contract) and surface line-numbered errors.
      return {
        step: "invalid_csv",
        states: [],
        overrides: [],
        preflightErrors: action.preflightErrors,
        reviewRows: action.reviewRows,
        fileName: action.fileName,
        rowCount: action.rowCount,
      };
    case "RESET":
      return { step: "idle" };
    case "RUN_STARTED":
      if (state.step !== "parsed" && state.step !== "contract_error" && state.step !== "previewed") return state;
      return { ...basePayload(state), step: "running", saveAsDraft: action.saveAsDraft };
    case "RUN_SUCCEEDED":
      if (state.step !== "running") return state;
      return { ...basePayload(state), step: "previewed", preview: action.preview };
    case "RUN_REJECTED":
      if (state.step !== "running") return state;
      return { ...basePayload(state), step: "contract_error", message: action.message, errors: action.errors };
    case "PUBLISH_STARTED":
      if (state.step !== "previewed" || state.preview.runId === null) return state;
      return {
        ...basePayload(state),
        step: "publishing",
        preview: { ...state.preview, runId: state.preview.runId },
      };
    case "PUBLISH_SUCCEEDED":
      if (state.step !== "publishing") return state;
      return { ...basePayload(state), step: "published", preview: state.preview, publishedAt: action.publishedAt };
    case "PUBLISH_FAILED":
      if (state.step !== "publishing") return state;
      return { ...basePayload(state), step: "publish_error", preview: state.preview, message: action.message };
    case "HISTORY_LOADED": {
      // Re-hydrating a saved run's tables into the preview grid (Phase 10).
      // Never allowed mid-flight; a parsed CSV survives (the payload is
      // kept), so re-running this week's inputs stays one click away.
      if (state.step === "running" || state.step === "publishing") return state;
      const payload: ParsedPayload =
        state.step === "idle"
          ? {
              states: [],
              overrides: [],
              preflightErrors: [],
              reviewRows: [],
              fileName: action.label,
              rowCount: 0,
            }
          : basePayload(state);
      return { ...payload, step: "previewed", preview: action.preview };
    }
    default:
      return state;
  }
}

function basePayload(state: Exclude<WizardState, { step: "idle" }>): ParsedPayload {
  return {
    states: state.states,
    overrides: state.overrides,
    preflightErrors: state.preflightErrors,
    reviewRows: state.reviewRows,
    fileName: state.fileName,
    rowCount: state.rowCount,
  };
}

// ------------------------------------------------------------- component

const STEPS = ["Upload", "Review", "Run", "Preview", "Publish"] as const;

function stepIndex(state: WizardState): number {
  switch (state.step) {
    case "idle": return 0;
    case "invalid_csv": case "parsed": return 1;
    case "running": case "contract_error": return 2;
    case "previewed": return 3;
    default: return 4;
  }
}

export default function WeeklyRunWizard({
  defaultSeason = DEFAULT_SEASON,
}: {
  defaultSeason?: string;
}) {
  const [state, dispatch] = useReducer(wizardReducer, { step: "idle" });
  // The season a draft saves under. A run parameter like the overrides
  // layer, so it lives beside the FSM; frozen while a run/publish is in
  // flight so the payload can't drift mid-request.
  const [season, setSeason] = useState<string>(defaultSeason);
  // Overrides are an optional, orthogonal layer: they live beside the FSM
  // (not inside it) so dropping/clearing an overrides file never disturbs
  // the manual-minutes workflow state.
  const [overridesUpload, setOverridesUpload] = useState<OverridesUpload>(
    EMPTY_OVERRIDES_UPLOAD,
  );
  // Bumped after every draft save / publish so the HistorySidebar refetches.
  const [historyVersion, setHistoryVersion] = useState(0);

  const runProjection = useCallback(
    async (saveAsDraft: boolean) => {
      if (state.step !== "parsed" && state.step !== "contract_error" && state.step !== "previewed") return;
      if (overridesUpload.status === "invalid") return; // fix or clear first
      dispatch({ type: "RUN_STARTED", saveAsDraft });
      const response = await fetch("/api/admin/projections/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          manual_minutes: state.states,
          overrides: overridesUpload.overrides,
          include_mc: saveAsDraft, // preview fast; MC on the real run
          save_as_draft: saveAsDraft,
          season: saveAsDraft ? season : undefined,
        }),
      });
      const body = await response.json();
      if (response.ok) {
        dispatch({
          type: "RUN_SUCCEEDED",
          preview: {
            runId: body.run_id ?? null,
            tables: body.tables ?? {},
            minutesModelLoaded: Boolean(body.minutes_model_loaded),
            includeMc: Boolean(body.include_mc),
          },
        });
        if (body.run_id) setHistoryVersion((version) => version + 1);
      } else {
        dispatch({
          type: "RUN_REJECTED",
          message: body?.detail?.message ?? body?.error ?? "run failed",
          errors: body?.detail?.errors ?? [],
        });
      }
    },
    [state, season, overridesUpload],
  );

  const publishRun = useCallback(async () => {
    if (state.step !== "previewed" || state.preview.runId === null) return;
    dispatch({ type: "PUBLISH_STARTED" });
    const response = await fetch(
      `/api/admin/projections/runs/${state.preview.runId}/publish`,
      { method: "POST" },
    );
    const body = await response.json();
    if (response.ok) {
      dispatch({ type: "PUBLISH_SUCCEEDED", publishedAt: body.published_at });
      setHistoryVersion((version) => version + 1);
    } else {
      dispatch({
        type: "PUBLISH_FAILED",
        message: body?.detail ?? body?.error ?? "publish failed",
      });
    }
  }, [state]);

  const active = stepIndex(state);
  const busy = state.step === "running" || state.step === "publishing";
  const activeRunId =
    state.step === "previewed" ||
    state.step === "publishing" ||
    state.step === "published" ||
    state.step === "publish_error"
      ? state.preview.runId
      : null;

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-6 p-6">
      {/* ---------------------------------------------------- header/stepper */}
      <header className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-2">
            <BrandLogo size="md" className="text-[#FF5F1F]" />
            <h1 className="text-xl font-semibold tracking-tight">Weekly projection run</h1>
          </div>
          <p className="text-sm text-muted-foreground">
            Season {seasonLabel(season)} · upload → review → run → publish
          </p>
        </div>
        <ol className="flex items-center gap-2 text-xs">
          {STEPS.map((label, index) => (
            <li key={label} className="flex items-center gap-2">
              <span
                className={
                  "flex h-6 w-6 items-center justify-center rounded-full border text-[11px] font-medium " +
                  (index < active
                    ? "border-emerald-600 bg-emerald-600 text-white"
                    : index === active
                      ? "border-foreground bg-background text-foreground"
                      : "border-border text-muted-foreground")
                }
              >
                {index + 1}
              </span>
              <span className={index === active ? "font-medium" : "text-muted-foreground"}>
                {label}
              </span>
              {index < STEPS.length - 1 && <span className="text-border">—</span>}
            </li>
          ))}
        </ol>
      </header>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* ------------------------------------------ left: upload + review */}
        <section className="flex flex-col gap-4 lg:col-span-2">
          <MinutesCsvDropzone
            dispatch={dispatch}
            disabled={busy}
          />

          <OverridesCsvDropzone
            upload={overridesUpload}
            onChange={setOverridesUpload}
            disabled={busy}
          />

          {state.step === "idle" ? (
            <div className="flex h-80 items-center justify-center rounded-lg border border-border bg-card text-sm text-muted-foreground">
              Parsed rows appear here after upload
            </div>
          ) : (
            <MinutesReviewTable rows={state.reviewRows} />
          )}

          {state.step === "invalid_csv" && (
            <div className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-900">
              <p className="font-medium">
                {state.fileName} failed preflight — fix and re-upload
                (all-or-nothing, like the server contract)
              </p>
              <ul className="mt-2 list-inside list-disc space-y-1">
                {state.preflightErrors.map((error, i) => (
                  <li key={i}>
                    {error.line > 0 ? `line ${error.line}: ` : "file: "}
                    {error.issues.join("; ")}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {state.step === "contract_error" && (
            <div className="rounded-lg border border-red-300 bg-red-50 p-4 text-sm text-red-900">
              <p className="font-medium">{state.message}</p>
              <ul className="mt-2 list-inside list-disc space-y-1">
                {state.errors.map((error, i) => (
                  <li key={i}>
                    {/* loc = [manual_inputs, layer, row, field] -> CSV line row+2 */}
                    line {typeof error.loc[2] === "number" ? error.loc[2] + 2 : "?"}:{" "}
                    {String(error.loc.at(-1))} — {error.msg}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>

        {/* -------------------------------------------- right: run controls */}
        <aside className="flex flex-col gap-4">
          <div className="rounded-lg border border-border bg-card p-4">
            <h2 className="text-sm font-medium">Run controls</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              {state.step === "idle"
                ? "Upload a CSV to begin."
                : `${state.states.length} manual rows · ${overridesUpload.overrides.length} overrides · ${state.preflightErrors.length} preflight errors`}
            </p>
            <label className="mt-3 block text-xs font-medium" htmlFor="season-select">
              Season
            </label>
            <select
              id="season-select"
              value={season}
              disabled={busy}
              onChange={(event) => setSeason(event.target.value)}
              className="mt-1 w-full rounded-md border border-border bg-background px-2.5 py-1.5 text-sm disabled:opacity-40"
            >
              {SEASONS.map((code) => (
                <option key={code} value={code}>
                  {seasonLabel(code)}
                  {code === DEFAULT_SEASON ? " (current)" : ""}
                </option>
              ))}
            </select>
            <p className="mt-1 text-[11px] text-muted-foreground">
              Drafts save under this season code ({season}).
            </p>
            {overridesUpload.status === "invalid" && (
              <p className="mt-1 text-xs text-red-700">
                Overrides CSV failed preflight — fix or clear it to run.
              </p>
            )}
            <div className="mt-4 flex flex-col gap-2">
              <button
                className="rounded-md border border-border px-3 py-2 text-sm font-medium hover:bg-muted disabled:opacity-40"
                disabled={state.step === "idle" || busy || overridesUpload.status === "invalid"}
                onClick={() => runProjection(false)}
              >
                {state.step === "running" ? "Running…" : "Preview (no MC)"}
              </button>
              <button
                className="rounded-md bg-foreground px-3 py-2 text-sm font-medium text-background hover:opacity-90 disabled:opacity-40"
                disabled={state.step === "idle" || busy || overridesUpload.status === "invalid"}
                onClick={() => runProjection(true)}
              >
                Run &amp; Save Draft (MC)
              </button>
            </div>
          </div>

          {/* ------------------------------------------------- publish bar */}
          <div className="rounded-lg border border-border bg-card p-4">
            <h2 className="text-sm font-medium">Publish</h2>
            {state.step === "previewed" && state.preview.runId && (
              <>
                <p className="mt-1 truncate font-mono text-xs text-muted-foreground">
                  run {state.preview.runId}
                </p>
                <button
                  className="mt-3 w-full rounded-md bg-emerald-600 px-3 py-2 text-sm font-medium text-white hover:bg-emerald-700"
                  onClick={publishRun}
                >
                  Publish Run
                </button>
                <p className="mt-2 text-[11px] text-muted-foreground">
                  Publishing an older run rolls the dashboard back to it.
                </p>
              </>
            )}
            {state.step === "publishing" && (
              <p className="mt-2 text-sm text-muted-foreground">Publishing…</p>
            )}
            {state.step === "published" && (
              <p className="mt-2 text-sm text-emerald-700">
                Published at {state.publishedAt} — the public dashboard is now
                serving this run.
              </p>
            )}
            {state.step === "publish_error" && (
              <p className="mt-2 text-sm text-red-700">{state.message}</p>
            )}
            {(state.step === "idle" || state.step === "parsed") && (
              <p className="mt-1 text-xs text-muted-foreground">
                Save a draft to enable publishing.
              </p>
            )}
          </div>

          {/* -------------------------------------------------- run history */}
          <HistorySidebar
            dispatch={dispatch}
            refreshKey={historyVersion}
            activeRunId={activeRunId}
            disabled={busy}
          />
        </aside>
      </div>

      {/* ------------------------------------------------- preview grids */}
      {state.step === "previewed" ||
      state.step === "publishing" ||
      state.step === "published" ||
      state.step === "publish_error" ? (
        <ProjectionsGrid tables={state.preview.tables} />
      ) : (
        <section className="flex h-40 items-center justify-center rounded-lg border border-border bg-card text-sm text-muted-foreground">
          Run a projection to preview expected minutes and xPts
        </section>
      )}
    </div>
  );
}
