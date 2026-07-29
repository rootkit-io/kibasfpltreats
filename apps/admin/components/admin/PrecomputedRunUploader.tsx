"use client";

/**
 * PrecomputedRunUploader -- ingest the two native local-model exports.
 *
 * Architectural note: the manual-minutes dropzones parse in the browser and
 * post JSON, because that path feeds a server-side model run. This path is the
 * opposite -- the model already ran locally, so the CSVs themselves are the
 * payload and are streamed to the backend as multipart form data.
 *
 * Both files are sent in ONE request: the backend cross-checks them against
 * each other (matching gameweek bounds and an identical player-gameweek key
 * set) to reject stale-file mixing, which is only possible if it sees both.
 */

import { useCallback, useRef, useState } from "react";
import { AlertCircle, CheckCircle2, FileSpreadsheet, Loader2, UploadCloud } from "lucide-react";

import { cn } from "@/lib/utils";

const WEEKLY_FILENAME = "weekly_player_week.csv";
const MC_FILENAME = "mc_brackets_full_player_week.csv";

interface IngestSuccess {
  run_id: string;
  season: string;
  gameweeks: number[];
  weekly_rows: number;
  simulation_rows: number;
  players: number;
  unmatched_players: { player: string; team: string; reason: string }[];
  dropped_columns: { weekly: string[]; monte_carlo: string[] };
}

type Status =
  | { kind: "idle" }
  | { kind: "uploading" }
  | { kind: "error"; message: string; details: string[] }
  | { kind: "success"; result: IngestSuccess };

function FileSlot({
  label, expected, file, onPick, disabled,
}: {
  label: string;
  expected: string;
  file: File | null;
  onPick: (file: File | null) => void;
  disabled: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  // Accept on name match so the two slots cannot be filled the wrong way
  // round -- a swap would fail preflight upstream with a confusing message.
  const accept = useCallback(
    (picked: File | null) => {
      if (picked && picked.name !== expected) {
        onPick(null);
        window.alert(`Expected ${expected}, got ${picked.name}`);
        return;
      }
      onPick(picked);
    },
    [expected, onPick],
  );

  return (
    <div
      onDragOver={(e) => { e.preventDefault(); if (!disabled) setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        if (!disabled) accept(e.dataTransfer.files?.[0] ?? null);
      }}
      onClick={() => !disabled && inputRef.current?.click()}
      className={cn(
        "flex cursor-pointer flex-col items-center gap-2 rounded-lg border-2 border-dashed p-6 text-center transition",
        dragging ? "border-primary bg-primary/5" : "border-muted-foreground/30",
        file && "border-emerald-500/60 bg-emerald-500/5",
        disabled && "cursor-not-allowed opacity-60",
      )}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".csv,text/csv"
        className="hidden"
        disabled={disabled}
        onChange={(e) => accept(e.target.files?.[0] ?? null)}
      />
      {file ? (
        <FileSpreadsheet className="h-6 w-6 text-emerald-500" />
      ) : (
        <UploadCloud className="h-6 w-6 text-muted-foreground" />
      )}
      <div className="text-sm font-medium">{label}</div>
      <div className="text-xs text-muted-foreground">
        {file ? `${file.name} (${(file.size / 1024).toFixed(0)} KB)` : expected}
      </div>
    </div>
  );
}

export default function PrecomputedRunUploader() {
  const [weeklyFile, setWeeklyFile] = useState<File | null>(null);
  const [mcFile, setMcFile] = useState<File | null>(null);
  const [season, setSeason] = useState("");
  const [notes, setNotes] = useState("");
  const [status, setStatus] = useState<Status>({ kind: "idle" });

  const ready = Boolean(weeklyFile && mcFile && season.trim());
  const busy = status.kind === "uploading";

  async function submit() {
    if (!ready || !weeklyFile || !mcFile) return;
    setStatus({ kind: "uploading" });

    const form = new FormData();
    form.append("weekly_file", weeklyFile);
    form.append("mc_file", mcFile);
    form.append("season", season.trim());
    if (notes.trim()) form.append("notes", notes.trim());

    try {
      const response = await fetch("/api/admin/projections/ingest", {
        method: "POST",
        body: form, // no Content-Type: the browser sets the multipart boundary
      });
      const body = await response.json().catch(() => null);

      if (!response.ok) {
        const detail = body?.detail ?? body;
        const message =
          detail?.message ?? body?.error ?? `ingest failed (HTTP ${response.status})`;
        const details: string[] = (detail?.errors ?? [])
          .slice(0, 10)
          .map((e: Record<string, unknown>) =>
            typeof e === "string" ? e : JSON.stringify(e),
          );
        setStatus({ kind: "error", message, details });
        return;
      }
      setStatus({ kind: "success", result: body as IngestSuccess });
    } catch {
      setStatus({ kind: "error", message: "network error contacting the admin API", details: [] });
    }
  }

  return (
    <section className="space-y-4 rounded-xl border border-border p-6">
      <header>
        <h2 className="text-lg font-semibold">Ingest a precomputed run</h2>
        <p className="text-sm text-muted-foreground">
          Upload both native model exports together. They are validated against
          each other, staged as a draft run, and then follow the normal preview
          and publish flow.
        </p>
      </header>

      <div className="grid gap-4 sm:grid-cols-2">
        <FileSlot
          label="Weekly projections"
          expected={WEEKLY_FILENAME}
          file={weeklyFile}
          onPick={setWeeklyFile}
          disabled={busy}
        />
        <FileSlot
          label="Monte Carlo brackets"
          expected={MC_FILENAME}
          file={mcFile}
          onPick={setMcFile}
          disabled={busy}
        />
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm">
          <span className="mb-1 block font-medium">
            Season <span className="text-muted-foreground">(neither CSV carries it)</span>
          </span>
          <input
            value={season}
            onChange={(e) => setSeason(e.target.value)}
            placeholder="2627"
            disabled={busy}
            className="w-full rounded-md border border-border bg-background px-3 py-2"
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block font-medium">Notes (optional)</span>
          <input
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            disabled={busy}
            className="w-full rounded-md border border-border bg-background px-3 py-2"
          />
        </label>
      </div>

      <button
        type="button"
        onClick={submit}
        disabled={!ready || busy}
        className={cn(
          "inline-flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium",
          ready && !busy
            ? "bg-primary text-primary-foreground"
            : "cursor-not-allowed bg-muted text-muted-foreground",
        )}
      >
        {busy && <Loader2 className="h-4 w-4 animate-spin" />}
        {busy ? "Ingesting…" : "Stage draft run"}
      </button>

      {status.kind === "error" && (
        <div className="flex gap-3 rounded-md border border-destructive/40 bg-destructive/5 p-4 text-sm">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
          <div className="space-y-1">
            <p className="font-medium text-destructive">{status.message}</p>
            {status.details.length > 0 && (
              <ul className="list-disc space-y-0.5 pl-4 text-xs text-muted-foreground">
                {status.details.map((d, i) => <li key={i}>{d}</li>)}
              </ul>
            )}
          </div>
        </div>
      )}

      {status.kind === "success" && (
        <div className="flex gap-3 rounded-md border border-emerald-500/40 bg-emerald-500/5 p-4 text-sm">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-500" />
          <div className="space-y-1">
            <p className="font-medium">
              Draft run staged:{" "}
              <code className="rounded bg-muted px-1">{status.result.run_id}</code>
            </p>
            <p className="text-muted-foreground">
              Season {status.result.season} · GW{" "}
              {status.result.gameweeks.join(", ")} · {status.result.players} players ·{" "}
              {status.result.weekly_rows} weekly rows ·{" "}
              {status.result.simulation_rows} simulation rows
            </p>
            {status.result.unmatched_players.length > 0 && (
              <p className="text-amber-600">
                {status.result.unmatched_players.length} player(s) were not matched
                and were excluded.
              </p>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
