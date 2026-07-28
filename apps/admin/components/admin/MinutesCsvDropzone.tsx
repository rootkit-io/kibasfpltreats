"use client";

/**
 * MinutesCsvDropzone -- weekly manual_minutes.csv intake (Phase 7).
 *
 * Browser-side flow (no file ever leaves the machine -- ADR-0001's
 * zero-disk request path extends to the client):
 *   drop/browse CSV -> PapaParse (header: true)
 *   -> CSV_TO_CONTRACT column mapping (mins -> likely_minutes, ...)
 *   -> preflightManualMinutes (Zod mirror, line-numbered, all-or-nothing)
 *   -> dispatch CSV_PARSED (valid) or INVALID_CSV (line-numbered errors).
 */

import { useCallback, useRef, useState } from "react";
import Papa from "papaparse";
import { FileSpreadsheet, Loader2, UploadCloud } from "lucide-react";

import { cn } from "@/lib/utils";
import {
  buildReviewRows,
  preflightManualMinutes,
  type CsvRowError,
} from "@/lib/validations/minutes";
import type { WizardAction } from "@/components/admin/WeeklyRunWizard";

interface MinutesCsvDropzoneProps {
  dispatch: (action: WizardAction) => void;
  disabled?: boolean;
}

type DropState =
  | { status: "empty" }
  | { status: "parsing"; fileName: string }
  | { status: "accepted"; fileName: string; rowCount: number }
  | { status: "rejected"; fileName: string; errorCount: number };

export default function MinutesCsvDropzone({
  dispatch,
  disabled = false,
}: MinutesCsvDropzoneProps) {
  const [dropState, setDropState] = useState<DropState>({ status: "empty" });
  const [isDragging, setIsDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFile = useCallback(
    (file: File) => {
      if (!file.name.toLowerCase().endsWith(".csv")) {
        setDropState({ status: "rejected", fileName: file.name, errorCount: 1 });
        dispatch({
          type: "INVALID_CSV",
          fileName: file.name,
          rowCount: 0,
          preflightErrors: [
            { line: 0, issues: ["file must be a .csv (weekly minutes template)"] },
          ],
          reviewRows: [],
        });
        return;
      }

      setDropState({ status: "parsing", fileName: file.name });
      Papa.parse<Record<string, unknown>>(file, {
        header: true,
        skipEmptyLines: "greedy",
        complete: (results) => {
          // Structural parse errors first (bad quoting, ragged rows...):
          // Papa's row index excludes the header, so line = row + 2 --
          // the same convention as the backend's ManualMinutesError.
          const parseErrors: CsvRowError[] = results.errors.map((error) => ({
            line: typeof error.row === "number" ? error.row + 2 : 0,
            issues: [error.message],
          }));

          const preflight = preflightManualMinutes(results.data);
          const allErrors = [...parseErrors, ...preflight.errors];
          const reviewRows = buildReviewRows(results.data, allErrors);

          if (allErrors.length > 0) {
            setDropState({
              status: "rejected",
              fileName: file.name,
              errorCount: allErrors.length,
            });
            dispatch({
              type: "INVALID_CSV",
              fileName: file.name,
              rowCount: results.data.length,
              preflightErrors: allErrors,
              reviewRows,
            });
            return;
          }

          setDropState({
            status: "accepted",
            fileName: file.name,
            rowCount: results.data.length,
          });
          dispatch({
            type: "CSV_PARSED",
            payload: {
              states: preflight.states,
              overrides: [], // override CSVs get their own dropzone later
              preflightErrors: [],
              reviewRows,
              fileName: file.name,
              rowCount: results.data.length,
            },
          });
        },
        error: (error) => {
          setDropState({ status: "rejected", fileName: file.name, errorCount: 1 });
          dispatch({
            type: "INVALID_CSV",
            fileName: file.name,
            rowCount: 0,
            preflightErrors: [{ line: 0, issues: [error.message] }],
            reviewRows: [],
          });
        },
      });
    },
    [dispatch],
  );

  const onDrop = useCallback(
    (event: React.DragEvent<HTMLDivElement>) => {
      event.preventDefault();
      setIsDragging(false);
      if (disabled) return;
      const file = event.dataTransfer.files?.[0];
      if (file) handleFile(file);
    },
    [disabled, handleFile],
  );

  return (
    <div
      role="button"
      tabIndex={0}
      aria-label="Upload weekly manual minutes CSV"
      onClick={() => !disabled && inputRef.current?.click()}
      onKeyDown={(event) => {
        if ((event.key === "Enter" || event.key === " ") && !disabled) {
          inputRef.current?.click();
        }
      }}
      onDragOver={(event) => {
        event.preventDefault();
        if (!disabled) setIsDragging(true);
      }}
      onDragLeave={() => setIsDragging(false)}
      onDrop={onDrop}
      className={cn(
        "flex h-36 cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed text-sm transition-colors",
        isDragging
          ? "border-emerald-500 bg-emerald-50 text-emerald-700"
          : "border-border bg-muted/30 text-muted-foreground hover:bg-muted/60",
        disabled && "pointer-events-none opacity-50",
        dropState.status === "rejected" && "border-red-300 bg-red-50/50",
      )}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".csv,text/csv"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) handleFile(file);
          event.target.value = ""; // allow re-uploading the same file
        }}
      />

      {dropState.status === "parsing" ? (
        <>
          <Loader2 className="h-6 w-6 animate-spin" />
          <p>Parsing {dropState.fileName}…</p>
        </>
      ) : dropState.status === "accepted" ? (
        <>
          <FileSpreadsheet className="h-6 w-6 text-emerald-600" />
          <p>
            <span className="font-medium text-foreground">{dropState.fileName}</span>{" "}
            — {dropState.rowCount} rows passed preflight
          </p>
          <p className="text-xs">Drop a new file to replace it</p>
        </>
      ) : dropState.status === "rejected" ? (
        <>
          <FileSpreadsheet className="h-6 w-6 text-red-500" />
          <p className="text-red-700">
            {dropState.fileName} — {dropState.errorCount} error
            {dropState.errorCount === 1 ? "" : "s"}, see panel below
          </p>
          <p className="text-xs">Fix the CSV and drop it again</p>
        </>
      ) : (
        <>
          <UploadCloud className="h-6 w-6" />
          <p>
            Drop <span className="font-mono text-xs">manual_minutes.csv</span> here, or
            click to browse
          </p>
          <p className="text-xs">
            Weekly template columns: GW, player_id, player_key, start, mins, …
          </p>
        </>
      )}
    </div>
  );
}
