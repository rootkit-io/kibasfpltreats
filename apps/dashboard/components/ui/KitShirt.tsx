/**
 * KitShirt — full shirt illustration for FPL clubs.
 *
 * ORIGINAL ARTWORK NOTICE
 * Not club crests, not sponsor marks. Each shirt is a generic silhouette
 * shaded with SVG gradients, using two facts anyone may state: widely known
 * kit colours and the way the shirt reads (solid / stripes / pinstripes /
 * halves / hoop / sash / yoke). No external images fetched, no crest drawn.
 *
 * Accepts either `teamCode` (3-letter e.g. "ARS") or `teamName` (full name
 * e.g. "Arsenal"). teamCode takes precedence.
 *
 * Depth from gradients only — diagonal lighting across the body, soft centre
 * fold, seam shadow at each sleeve. Cheap enough to render in every table row.
 */

interface KitData {
  body: string;
  sleeve: string;
  detail: string;
  collar: string;
  pattern: "solid" | "stripes" | "pinstripes" | "halves" | "hoop" | "sash" | "yoke";
}

/** Keyed by uppercase 3-letter FPL code. */
const KITS_BY_CODE: Record<string, KitData> = {
  ARS: { body: "#EF0107", sleeve: "#FFFFFF", detail: "#FFFFFF", collar: "#FFFFFF", pattern: "solid" },
  AVL: { body: "#670E36", sleeve: "#95BFE5", detail: "#95BFE5", collar: "#95BFE5", pattern: "solid" },
  BHA: { body: "#0057B8", sleeve: "#0057B8", detail: "#FFFFFF", collar: "#FFFFFF", pattern: "stripes" },
  BOU: { body: "#DA291C", sleeve: "#111111", detail: "#111111", collar: "#111111", pattern: "stripes" },
  BRE: { body: "#E30613", sleeve: "#FFFFFF", detail: "#FFFFFF", collar: "#111111", pattern: "stripes" },
  BUR: { body: "#6C1D45", sleeve: "#6C1D45", detail: "#99D6EA", collar: "#99D6EA", pattern: "hoop" },
  CHE: { body: "#034694", sleeve: "#034694", detail: "#FFFFFF", collar: "#FFFFFF", pattern: "solid" },
  COV: { body: "#6CB4E4", sleeve: "#12275C", detail: "#12275C", collar: "#12275C", pattern: "yoke" },
  CRY: { body: "#1B458F", sleeve: "#1B458F", detail: "#C4122E", collar: "#C4122E", pattern: "sash" },
  EVE: { body: "#274488", sleeve: "#274488", detail: "#FFFFFF", collar: "#B0C4E8", pattern: "yoke" },
  FUL: { body: "#FFFFFF", sleeve: "#111111", detail: "#111111", collar: "#111111", pattern: "solid" },
  HUL: { body: "#F5A12D", sleeve: "#111111", detail: "#111111", collar: "#111111", pattern: "stripes" },
  IPS: { body: "#0044A9", sleeve: "#FFFFFF", detail: "#FFFFFF", collar: "#FFFFFF", pattern: "solid" },
  LEE: { body: "#FFFFFF", sleeve: "#FFFFFF", detail: "#FFCD00", collar: "#1D428A", pattern: "yoke" },
  LEI: { body: "#003090", sleeve: "#003090", detail: "#FDBE11", collar: "#FDBE11", pattern: "solid" },
  LIV: { body: "#C8102E", sleeve: "#C8102E", detail: "#00B2A9", collar: "#00B2A9", pattern: "solid" },
  MCI: { body: "#6CABDD", sleeve: "#6CABDD", detail: "#FFFFFF", collar: "#FFFFFF", pattern: "solid" },
  MUN: { body: "#DA291C", sleeve: "#DA291C", detail: "#111111", collar: "#111111", pattern: "solid" },
  NEW: { body: "#241F20", sleeve: "#241F20", detail: "#FFFFFF", collar: "#FFFFFF", pattern: "stripes" },
  NFO: { body: "#DD0000", sleeve: "#DD0000", detail: "#FFFFFF", collar: "#FFFFFF", pattern: "yoke" },
  SOU: { body: "#D71920", sleeve: "#FFFFFF", detail: "#FFFFFF", collar: "#111111", pattern: "pinstripes" },
  SUN: { body: "#EB172B", sleeve: "#111111", detail: "#FFFFFF", collar: "#111111", pattern: "pinstripes" },
  TOT: { body: "#FFFFFF", sleeve: "#FFFFFF", detail: "#132257", collar: "#132257", pattern: "solid" },
  WHU: { body: "#7A263A", sleeve: "#1BB1E7", detail: "#1BB1E7", collar: "#FFFFFF", pattern: "solid" },
  WOL: { body: "#FDB913", sleeve: "#FDB913", detail: "#111111", collar: "#111111", pattern: "solid" },
};

/** Also resolve by lowercase full name. */
const KITS_BY_NAME: Record<string, KitData> = {
  arsenal: KITS_BY_CODE.ARS,
  "aston villa": KITS_BY_CODE.AVL,
  brighton: KITS_BY_CODE.BHA,
  "brighton and hove albion": KITS_BY_CODE.BHA,
  bournemouth: KITS_BY_CODE.BOU,
  brentford: KITS_BY_CODE.BRE,
  burnley: KITS_BY_CODE.BUR,
  chelsea: KITS_BY_CODE.CHE,
  "coventry city": KITS_BY_CODE.COV,
  "crystal palace": KITS_BY_CODE.CRY,
  everton: KITS_BY_CODE.EVE,
  fulham: KITS_BY_CODE.FUL,
  "hull city": KITS_BY_CODE.HUL,
  "ipswich town": KITS_BY_CODE.IPS,
  leeds: KITS_BY_CODE.LEE,
  "leeds united": KITS_BY_CODE.LEE,
  "leicester city": KITS_BY_CODE.LEI,
  leicester: KITS_BY_CODE.LEI,
  liverpool: KITS_BY_CODE.LIV,
  "manchester city": KITS_BY_CODE.MCI,
  "man city": KITS_BY_CODE.MCI,
  "manchester united": KITS_BY_CODE.MUN,
  "man utd": KITS_BY_CODE.MUN,
  "man united": KITS_BY_CODE.MUN,
  "newcastle united": KITS_BY_CODE.NEW,
  newcastle: KITS_BY_CODE.NEW,
  "nottingham forest": KITS_BY_CODE.NFO,
  "nott'm forest": KITS_BY_CODE.NFO,
  southampton: KITS_BY_CODE.SOU,
  sunderland: KITS_BY_CODE.SUN,
  tottenham: KITS_BY_CODE.TOT,
  spurs: KITS_BY_CODE.TOT,
  "west ham": KITS_BY_CODE.WHU,
  "west ham united": KITS_BY_CODE.WHU,
  wolves: KITS_BY_CODE.WOL,
  "wolverhampton wanderers": KITS_BY_CODE.WOL,
};

const FALLBACK: KitData = { body: "#3F3F46", sleeve: "#52525B", detail: "#A1A1AA", collar: "#A1A1AA", pattern: "solid" };

function resolve(teamCode?: string | null, teamName?: string | null): KitData {
  if (teamCode) {
    const byCode = KITS_BY_CODE[teamCode.trim().toUpperCase()];
    if (byCode) return byCode;
  }
  if (teamName) {
    const byName = KITS_BY_NAME[teamName.trim().toLowerCase()];
    if (byName) return byName;
  }
  return FALLBACK;
}

function isLight(hex: string): boolean {
  const v = hex.replace("#", "");
  if (v.length !== 6) return false;
  const r = parseInt(v.slice(0, 2), 16);
  const g = parseInt(v.slice(2, 4), 16);
  const b = parseInt(v.slice(4, 6), 16);
  return r * 0.299 + g * 0.587 + b * 0.114 > 150;
}

const BODY_PATH = "M22 6 L28 3 h8 l6 3 12 6 -5 12 -7 -3 v29 H22 V21 l-7 3 -5 -12 Z";

export interface KitShirtProps {
  teamCode?: string | null;
  teamName?: string | null;
  size?: number;
  className?: string;
  /** Unique suffix to avoid gradient/clip ID collisions when many kits share a document. */
  idSuffix?: string;
}

let _counter = 0;
function nextId() { return `ks${++_counter}`; }

export function KitShirt({ teamCode, teamName, size = 28, className, idSuffix }: KitShirtProps) {
  const kit = resolve(teamCode, teamName);
  const outline = isLight(kit.body) ? "rgba(15,23,42,.34)" : "rgba(255,255,255,.20)";
  const height = Math.round(size * (24 / 28));
  const id = idSuffix ?? nextId();

  const pattern = (() => {
    const d = kit.detail;
    switch (kit.pattern) {
      case "stripes":
        return (
          <g clipPath={`url(#${id}b)`}>
            <rect x="22" y="8" width="5" height="40" fill={d} />
            <rect x="32" y="8" width="5" height="40" fill={d} />
            <rect x="42" y="8" width="5" height="40" fill={d} />
          </g>
        );
      case "pinstripes":
        return (
          <g clipPath={`url(#${id}b)`} opacity="0.92">
            <rect x="24" y="8" width="2.4" height="40" fill={d} />
            <rect x="30" y="8" width="2.4" height="40" fill={d} />
            <rect x="36" y="8" width="2.4" height="40" fill={d} />
            <rect x="42" y="8" width="2.4" height="40" fill={d} />
          </g>
        );
      case "halves":
        return <g clipPath={`url(#${id}b)`}><rect x="32" y="8" width="24" height="40" fill={d} /></g>;
      case "hoop":
        return <g clipPath={`url(#${id}b)`}><rect x="8" y="26" width="48" height="8" fill={d} /></g>;
      case "sash":
        return <g clipPath={`url(#${id}b)`}><path d="M14 48 L40 8 L48 8 L22 48 Z" fill={d} /></g>;
      case "yoke":
        return <g clipPath={`url(#${id}b)`}><path d="M8 8 h48 v9 H8 z" fill={d} opacity="0.95" /></g>;
      default:
        return null;
    }
  })();

  return (
    <svg
      aria-hidden="true"
      className={`inline-block shrink-0 ${className ?? ""}`}
      focusable="false"
      width={size}
      height={height}
      viewBox="0 0 64 56"
      xmlns="http://www.w3.org/2000/svg"
    >
      <defs>
        {/* Diagonal lighting: lit top-left, falling away bottom-right */}
        <linearGradient id={`${id}g`} x1="0" y1="0" x2="0.85" y2="1">
          <stop offset="0" stopColor="#ffffff" stopOpacity="0.26" />
          <stop offset="0.45" stopColor="#ffffff" stopOpacity="0" />
          <stop offset="1" stopColor="#000000" stopOpacity="0.28" />
        </linearGradient>
        {/* Soft vertical fold down the torso centre */}
        <linearGradient id={`${id}f`} x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#000000" stopOpacity="0" />
          <stop offset="0.5" stopColor="#000000" stopOpacity="0.16" />
          <stop offset="1" stopColor="#000000" stopOpacity="0" />
        </linearGradient>
        <clipPath id={`${id}b`}><path d={BODY_PATH} /></clipPath>
      </defs>

      {/* Body fill */}
      <path d={BODY_PATH} fill={kit.body} />

      {/* Pattern */}
      {pattern}

      {/* Sleeves with seam shadow */}
      <path d="M15 24 L10 12 L22 6 L26 16 Z" fill={kit.sleeve} />
      <path d="M49 24 L54 12 L42 6 L38 16 Z" fill={kit.sleeve} />
      <path d="M26 16 L22 6 L24 5 L28 15 Z" fill="#000" opacity="0.18" />
      <path d="M38 16 L42 6 L40 5 L36 15 Z" fill="#000" opacity="0.18" />

      {/* Collar + shadow it casts on the chest */}
      <path d="M28 3 h8 l-4 8 Z" fill={kit.collar} />
      <path d="M27 4 h10 l-1.5 3 h-7 Z" fill="#000" opacity="0.22" />

      {/* Fold + overall lighting, clipped to shirt body */}
      <g clipPath={`url(#${id}b)`}>
        <rect x="28" y="8" width="8" height="40" fill={`url(#${id}f)`} />
        <rect x="0" y="0" width="64" height="56" fill={`url(#${id}g)`} />
      </g>

      {/* Outline */}
      <path d={BODY_PATH} fill="none" stroke={outline} strokeWidth="1.6" strokeLinejoin="round" />
    </svg>
  );
}

export default KitShirt;
