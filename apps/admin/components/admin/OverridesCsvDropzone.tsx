"use client";

/**
 * OverridesCsvDropzone -- optional minute_overrides.csv intake (Phase 10).
 *
 * Same browser-side flow as MinutesCsvDropzone (the file never leaves the
 * machine): drop/browse CSV -> PapaParse (header: true) ->
 * OVERRIDES_CSV_TO_CONTRACT mapping -> preflightMinuteOverrides (Zod mirror
 * of MinuteOverrideState, line-numbered, all-or-nothing).
 *
 * Overrides are an optional layer, so this component is self-contained: it
 * owns its parse/error state and reports the validated states up through
 * `onChange`. An *invalid* file still blocks the run (the wizard disables
 * the run buttons) -- silently running without a file the admin dropped
 * would be worse than stopping them.
 */

import { useCallback, useRef, useState } from "react";
import Papa from "papaparse";
import { Loader2, SlidersHorizontal, UploadCloud, X } from "lucide-react";

import { cn } from "@/lib/utils";
import {
  preflightMinuteOverrides,
  type CsvRowError,
  type MinuteOverrideState,
} from "@/lib/validations/minutes";

/** What the wizard holds: the validated layer plus its upload status. */
export interface OverridesUpload {
  status: "empty" | "valid" | "invalid";
  overrides: MinuteOverrideState[];
  fileName: string | null;
  errors: CsvRowError[];
}

export const EMPTY_OVERRIDES_UPLOAD: OverridesUpload = {
  status: "empty",
  overrides: [],
  fileName: null,
  errors: [],
};

interface OverridesCsvDropzoneProps {
  upload: OverridesUpload;
  onChange: (upload: OverridesUpload) => void;
  disabled?: boolean;
}

export default function OverridesCsvDropzone({
  upload,
  onChange,
  disabled = false,
}: OverridesCsvDropzoneProps) {
  const [isParsing, setIsParsing] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFile = useCallback(
    (file: File) => {
      if (!file.name.toLowerCase().endsWith(".csv")) {
        onChange({
          status: "invalid",
          overrides: [],
          fileName: file.name,
          errors: [
            { line: 0, issues: ["file must be a .csv (minute overrides)"] },
          ],
        });
        return;
      }

      setIsParsing(true);
      Papa.parse<Record<string, unknown>>(file, {
        header: true,
        skipEmptyLines: "greedy",
        complete: (results) => {
          setIsParsing(false);
          // Structural parse errors first; Papa's row index excludes the
          // header, so line = row + 2 (backend ManualMinutesError convention).
          const parseErrors: CsvRowError[] = results.errors.map((error) => ({
            line: typeof error.row === "number" ? error.row + 2 : 0,
            issues: [error.message],
          }));

          const preflight = preflightMinuteOverrides(
            results.data,
            results.meta.fields,
          );
          const allErrors = [...parseErrors, ...preflight.errors];

          onChange(
            allErrors.length > 0
              ? {
                  status: "invalid",
                  overrides: [],
                  fileName: file.name,
                  errors: allErrors,
                }
              : {
                  status: "valid",
                  overrides: preflight.states,
                  fileName: file.name,
                  errors: [],
                },
          );
        },
        error: (error) => {
          setIsParsing(false);
          onChange({
            status: "invalid",
            overrides: [],
            fileName: file.name,
            errors: [{ line: 0, issues: [error.message] }],
          });
        },
      });
    },
    [onChange],
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
    <div className="flex flex-col gap-2">
      <div
        role="button"
        tabIndex={0}
        aria-label="Upload minute overrides CSV (optional)"
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
          "flex h-24 cursor-pointer flex-col items-center justify-center gap-1 rounded-lg border-2 border-dashed text-sm transition-colors",
          isDragging
            ? "border-sky-500 bg-sky-50 text-sky-700"
            : "border-border bg-muted/30 text-muted-foreground hover:bg-muted/60",
          disabled && "pointer-events-none opacity-50",
          upload.status === "invalid" && "border-red-300 bg-red-50/50",
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

        {isParsing ? (
          <>
            <Loader2 className="h-5 w-5 animate-spin" />
            <p>Parsing…</p>
          </>
        ) : upload.status === "valid" ? (
          <>
            <SlidersHorizontal className="h-5 w-5 text-sky-600" />
            <p>
              <span className="font-medium text-foreground">{upload.fileName}</span>{" "}
              — {upload.overrides.length} override
              {upload.overrides.length === 1 ? "" : "s"} passed preflight
            </p>
          </>
        ) : upload.status === "invalid" ? (
          <>
            <SlidersHorizontal className="h-5 w-5 text-red-500" />
            <p className="text-red-700">
              {upload.fileName} — {upload.errors.length} error
              {upload.errors.length === 1 ? "" : "s"}, run blocked
            </p>
          </>
        ) : (
          <>
            <UploadCloud className="h-5 w-5" />
            <p>
              Optional: drop{" "}
              <span className="font-mono text-xs">minute_overrides.csv</span>
            </p>
            <p className="text-xs">Columns: GW, player_id/player_key, mins, fixture_in_week</p>
          </>
        )}
      </div>

      {upload.status !== "empty" && (
        <button
          type="button"
          className="self-start text-xs text-muted-foreground underline-offset-2 hover:underline disabled:opacity-40"
          disabled={disabled}
          onClick={() => onChange(EMPTY_OVERRIDES_UPLOAD)}
        >
          <span className="inline-flex items-center gap-1">
            <X className="h-3 w-3" /> Clear overrides
          </span>
        </button>
      )}

      {upload.status === "invalid" && (
        <div className="rounded-lg border border-red-300 bg-red-50 p-3 text-xs text-red-900">
          <p className="font-medium">
            {upload.fileName} failed preflight — fix and re-upload, or clear
            it to run without overrides (all-or-nothing, like the server
            contract)
          </p>
          <ul className="mt-1 list-inside list-disc space-y-0.5">
            {upload.errors.map((error, i) => (
              <li key={i}>
                {error.line > 0 ? `line ${error.line}: ` : "file: "}
                {error.issues.join("; ")}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
