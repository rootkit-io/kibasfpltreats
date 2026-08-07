/**
 * ClubMark -- club identity disc for the fixture ticker.
 *
 * ORIGINAL ARTWORK NOTICE
 * These are NOT club crests and are not derived from them. Real crests are
 * trademarked, so nothing here traces, copies or approximates one. Each mark
 * is a plain coloured disc built from two facts anyone may state -- a club's
 * widely known kit colours and its standard three-letter competition code --
 * plus a simple pattern (solid, stripes, halves, hoop, sash, sleeves) that
 * echoes how the shirt reads. The composition is generic: a coloured circle
 * with a code on it.
 *
 * Mirrors the approach used on the marketing site so the two surfaces agree.
 * Pure SVG, no assets, no network, no dependencies.
 */

interface ClubStyle {
  primary: string;
  secondary: string;
  pattern: "solid" | "stripes" | "halves" | "hoop" | "sash" | "sleeves";
}

/** Keyed by lowercase club name as the API returns it (`teams.name`). */
const CLUBS: Record<string, ClubStyle> = {
  arsenal: { primary: "#EF0107", secondary: "#FFFFFF", pattern: "sleeves" },
  "aston villa": { primary: "#670E36", secondary: "#95BFE5", pattern: "halves" },
  bournemouth: { primary: "#DA291C", secondary: "#111111", pattern: "stripes" },
  brentford: { primary: "#E30613", secondary: "#FFFFFF", pattern: "stripes" },
  brighton: { primary: "#0057B8", secondary: "#FFFFFF", pattern: "stripes" },
  burnley: { primary: "#6C1D45", secondary: "#99D6EA", pattern: "sleeves" },
  chelsea: { primary: "#034694", secondary: "#FFFFFF", pattern: "solid" },
  "coventry city": { primary: "#6CB4E4", secondary: "#FFFFFF", pattern: "solid" },
  "crystal palace": { primary: "#1B458F", secondary: "#C4122E", pattern: "sash" },
  everton: { primary: "#274488", secondary: "#FFFFFF", pattern: "solid" },
  fulham: { primary: "#FFFFFF", secondary: "#111111", pattern: "hoop" },
  "hull city": { primary: "#F5A12D", secondary: "#111111", pattern: "stripes" },
  "ipswich town": { primary: "#0044A9", secondary: "#FFFFFF", pattern: "solid" },
  leeds: { primary: "#FFFFFF", secondary: "#FFCD00", pattern: "hoop" },
  "leicester city": { primary: "#003090", secondary: "#FDBE11", pattern: "solid" },
  liverpool: { primary: "#C8102E", secondary: "#FFFFFF", pattern: "solid" },
  "man city": { primary: "#6CABDD", secondary: "#FFFFFF", pattern: "solid" },
  "manchester city": { primary: "#6CABDD", secondary: "#FFFFFF", pattern: "solid" },
  "man utd": { primary: "#DA291C", secondary: "#111111", pattern: "solid" },
  "manchester united": { primary: "#DA291C", secondary: "#111111", pattern: "solid" },
  newcastle: { primary: "#241F20", secondary: "#FFFFFF", pattern: "stripes" },
  "newcastle united": { primary: "#241F20", secondary: "#FFFFFF", pattern: "stripes" },
  "nott'm forest": { primary: "#DD0000", secondary: "#FFFFFF", pattern: "solid" },
  "nottingham forest": { primary: "#DD0000", secondary: "#FFFFFF", pattern: "solid" },
  southampton: { primary: "#D71920", secondary: "#FFFFFF", pattern: "stripes" },
  spurs: { primary: "#FFFFFF", secondary: "#132257", pattern: "solid" },
  tottenham: { primary: "#FFFFFF", secondary: "#132257", pattern: "solid" },
  sunderland: { primary: "#EB172B", secondary: "#FFFFFF", pattern: "stripes" },
  "west ham": { primary: "#7A263A", secondary: "#1BB1E7", pattern: "sleeves" },
  wolves: { primary: "#FDB913", secondary: "#111111", pattern: "solid" },
  "wolverhampton wanderers": { primary: "#FDB913", secondary: "#111111", pattern: "solid" },
};

const FALLBACK: ClubStyle = {
  primary: "#3F3F46",
  secondary: "#A1A1AA",
  pattern: "solid",
};

function styleFor(clubName: string | null | undefined): ClubStyle {
  if (!clubName) return FALLBACK;
  return CLUBS[clubName.trim().toLowerCase()] ?? FALLBACK;
}

/**
 * The code sits on a neutral plate rather than directly on the kit colours.
 *
 * Deriving ink from `primary` alone is not enough: a pattern band can land
 * squarely behind the text, so Fulham (white shirt, black hoop) rendered dark
 * ink on a black hoop and Hull (orange, black stripes) broke up mid-word. The
 * plate is narrow enough that the kit still reads around it.
 */

export default function ClubMark({
  clubName,
  code,
  size = 18,
}: {
  clubName: string | null | undefined;
  /** Three-letter competition code, e.g. "ARS". */
  code: string;
  size?: number;
}) {
  const { primary, secondary, pattern } = styleFor(clubName);
  // Unique per render so multiple marks on one page cannot share a clip path.
  const clipId = `clubmark-${code}-${pattern}`;

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      role="img"
      aria-hidden="true"
      focusable="false"
      className="shrink-0"
    >
      <defs>
        <clipPath id={clipId}>
          <circle cx="16" cy="16" r="15" />
        </clipPath>
      </defs>

      <circle cx="16" cy="16" r="15" fill={primary} />

      <g clipPath={`url(#${clipId})`}>
        {pattern === "stripes" && (
          <>
            <rect x="6" y="0" width="5" height="32" fill={secondary} />
            <rect x="16" y="0" width="5" height="32" fill={secondary} />
            <rect x="26" y="0" width="5" height="32" fill={secondary} />
          </>
        )}
        {pattern === "halves" && <rect x="16" y="0" width="16" height="32" fill={secondary} />}
        {pattern === "hoop" && <rect x="0" y="12" width="32" height="8" fill={secondary} />}
        {pattern === "sash" && (
          <polygon points="0,26 26,0 32,6 6,32" fill={secondary} />
        )}
        {pattern === "sleeves" && (
          <>
            <rect x="0" y="0" width="6" height="32" fill={secondary} />
            <rect x="26" y="0" width="6" height="32" fill={secondary} />
          </>
        )}
      </g>

      <circle cx="16" cy="16" r="15" fill="none" stroke="rgba(0,0,0,0.4)" strokeWidth="1" />

      <rect x="2" y="10.5" width="28" height="11" rx="5.5" fill="rgba(9,9,11,0.72)" />
      <text
        x="16"
        y="16.5"
        textAnchor="middle"
        dominantBaseline="middle"
        fontSize="10"
        fontWeight="700"
        letterSpacing="0.4"
        fontFamily="ui-monospace, SFMono-Regular, Menlo, monospace"
        fill="#FAFAFA"
      >
        {code.slice(0, 3).toUpperCase()}
      </text>
    </svg>
  );
}
