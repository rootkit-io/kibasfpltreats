/**
 * TableSkeleton -- boot-up placeholder for the projections grid.
 *
 * Deliberately NOT a spinner. A spinner communicates "something is happening";
 * a skeleton communicates "this exact layout is arriving", which stops the
 * page reflowing when data lands and reads as an instrument warming up.
 *
 * Geometry is kept in lockstep with ProjectionsTable: same 38px row pitch,
 * same toolbar block, same column rhythm and responsive hide breakpoints. If
 * the table's ROW_HEIGHT changes, change SKELETON_ROW_HEIGHT with it.
 *
 * Server component -- no state, no effects, so it costs nothing on the client.
 */

const SKELETON_ROW_HEIGHT = 38;
const ROWS = 14;

/** Mirrors the real column widths so the shimmer lines up with real content. */
const COLUMNS: { width: string; align: "left" | "right"; hide?: string }[] = [
  { width: "38%", align: "left" },
  { width: "12%", align: "left", hide: "hidden sm:block" },
  { width: "10%", align: "left" },
  { width: "10%", align: "right" },
  { width: "12%", align: "right", hide: "hidden lg:block" },
  { width: "10%", align: "right", hide: "hidden md:block" },
  { width: "14%", align: "right" },
  { width: "12%", align: "right", hide: "hidden md:block" },
];

function Block({
  className = "",
  style,
}: {
  className?: string;
  style?: React.CSSProperties;
}) {
  return <div className={`skeleton ${className}`} style={style} aria-hidden />;
}

export default function TableSkeleton() {
  return (
    <section
      className="flex flex-col gap-3"
      role="status"
      aria-busy="true"
      aria-label="Loading projections"
    >
      {/* ------------------------------------------------------- toolbar */}
      <div className="flex flex-col gap-3 border border-border bg-card p-3">
        <div className="flex flex-wrap items-center gap-2">
          <Block className="h-7 w-[168px]" />
          <Block className="h-7 w-[124px]" />
          <Block className="ml-auto h-7 w-full max-w-[220px]" />
        </div>
        <div className="flex flex-wrap items-center gap-1">
          {[44, 40, 46, 46, 46].map((w, i) => (
            <Block key={i} className="h-6" style={{ width: w }} />
          ))}
          <Block className="ml-auto h-6 w-24" />
        </div>
      </div>

      {/* --------------------------------------------------------- table */}
      <div className="border border-border bg-card">
        {/* header band: matches the sticky glass header's height */}
        <div className="flex items-center gap-3 border-b border-border px-3 py-2">
          <div className="w-4 shrink-0" />
          {COLUMNS.map((column, i) => (
            <div
              key={i}
              className={`min-w-0 flex-1 ${column.hide ?? ""}`}
              style={{ flexBasis: column.width }}
            >
              <Block
                className={`h-2.5 ${column.align === "right" ? "ml-auto w-10" : "w-16"}`}
              />
            </div>
          ))}
        </div>

        {/* rows */}
        <div>
          {Array.from({ length: ROWS }).map((_, rowIndex) => (
            <div
              key={rowIndex}
              className="flex items-center gap-3 border-t border-border/60 px-3"
              style={{
                height: SKELETON_ROW_HEIGHT,
                /* Fade the stack toward the fold so the surface reads as
                   depth rather than a flat wall of grey. */
                opacity: 1 - rowIndex * 0.032,
              }}
            >
              <div className="w-4 shrink-0">
                <Block className="h-3 w-3" />
              </div>
              {COLUMNS.map((column, columnIndex) => (
                <div
                  key={columnIndex}
                  className={`min-w-0 flex-1 ${column.hide ?? ""}`}
                  style={{ flexBasis: column.width }}
                >
                  <Block
                    className={`h-2.5 ${column.align === "right" ? "ml-auto" : ""}`}
                    style={{
                      /* Deterministic pseudo-random widths: a fixed pattern
                         looks like a grid, true random would hydrate
                         mismatched. */
                      width:
                        column.align === "right"
                          ? `${34 + ((rowIndex * 7 + columnIndex * 13) % 22)}px`
                          : `${46 + ((rowIndex * 11 + columnIndex * 5) % 38)}%`,
                    }}
                  />
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>

      <span className="sr-only">Loading projections…</span>
    </section>
  );
}
