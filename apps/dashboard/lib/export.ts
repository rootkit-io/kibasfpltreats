/**
 * Export + share helpers.
 *
 * CSV export runs entirely in the browser against the ALREADY FILTERED AND
 * SORTED rows, so what downloads is exactly what is on screen -- no second
 * fetch, no server round-trip, and no risk of the export drifting from the
 * view.
 */

/** RFC 4180: quote when the value contains a comma, quote or newline. */
function escapeCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  const text = String(value);
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function toCsv<T extends object>(
  rows: T[],
  columns: { key: keyof T & string; label: string }[],
): string {
  const header = columns.map((c) => escapeCell(c.label)).join(",");
  const body = rows.map((row) =>
    columns.map((c) => escapeCell(row[c.key])).join(","),
  );
  return [header, ...body].join("\r\n");
}

export function downloadCsv(filename: string, csv: string): void {
  // Prepend a BOM so Excel opens UTF-8 player names correctly.
  const blob = new Blob(["﻿", csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

/** Copy the current deep-linked URL. Returns false when the API is blocked. */
export async function copyCurrentUrl(): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(window.location.href);
    return true;
  } catch {
    return false;
  }
}
