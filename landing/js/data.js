/* ═══════════════════════════════════════════════
   KFT — sample dataset (deterministic model)
   Swap with live CSV/FPL-API feeds in production.
   ═══════════════════════════════════════════════ */

export const GW_START = 22;
export const GW_COUNT = 6;
export const GWS = Array.from({ length: GW_COUNT }, (_, i) => GW_START + i);

export const TEAMS = {
  ARS: { name: "Arsenal",        color: "#EF0107", str: 5 },
  AVL: { name: "Aston Villa",    color: "#95BFE5", str: 4 },
  BOU: { name: "Bournemouth",    color: "#DA291C", str: 3 },
  BRE: { name: "Brentford",      color: "#E30613", str: 3 },
  BHA: { name: "Brighton",       color: "#0057B8", str: 3 },
  BUR: { name: "Burnley",        color: "#6C1D45", str: 2 },
  CHE: { name: "Chelsea",        color: "#034694", str: 4 },
  CRY: { name: "Crystal Palace", color: "#1B458F", str: 3 },
  EVE: { name: "Everton",        color: "#003399", str: 2 },
  FUL: { name: "Fulham",         color: "#B6B6B6", str: 3 },
  LEE: { name: "Leeds",          color: "#FFCD00", str: 2 },
  LIV: { name: "Liverpool",      color: "#C8102E", str: 5 },
  MCI: { name: "Man City",       color: "#6CABDD", str: 5 },
  MUN: { name: "Man Utd",        color: "#DA020E", str: 3 },
  NEW: { name: "Newcastle",      color: "#BBBDBF", str: 4 },
  NFO: { name: "Nott'm Forest",  color: "#DD0000", str: 3 },
  SUN: { name: "Sunderland",     color: "#EB172B", str: 1 },
  TOT: { name: "Spurs",          color: "#8E9DBC", str: 4 },
  WHU: { name: "West Ham",       color: "#7A263A", str: 2 },
  WOL: { name: "Wolves",         color: "#FDB913", str: 2 },
};

/* six consistent rounds — [home, away] */
const ROUNDS = [
  [["ARS","CHE"],["LIV","MUN"],["MCI","TOT"],["NEW","AVL"],["BHA","CRY"],["EVE","FUL"],["LEE","BUR"],["NFO","BOU"],["SUN","WOL"],["WHU","BRE"]],
  [["CHE","LIV"],["MUN","MCI"],["TOT","NEW"],["AVL","BHA"],["CRY","EVE"],["FUL","LEE"],["BUR","NFO"],["BOU","SUN"],["WOL","WHU"],["BRE","ARS"]],
  [["ARS","MUN"],["LIV","TOT"],["MCI","AVL"],["NEW","CRY"],["BHA","FUL"],["EVE","BUR"],["LEE","BOU"],["NFO","WOL"],["SUN","BRE"],["WHU","CHE"]],
  [["TOT","ARS"],["AVL","LIV"],["CRY","MCI"],["FUL","NEW"],["BUR","BHA"],["BOU","EVE"],["WOL","LEE"],["BRE","NFO"],["CHE","SUN"],["MUN","WHU"]],
  [["ARS","LIV"],["MCI","CHE"],["NEW","MUN"],["TOT","BHA"],["AVL","EVE"],["CRY","LEE"],["FUL","NFO"],["BUR","SUN"],["BOU","WHU"],["WOL","BRE"]],
  [["LIV","MCI"],["CHE","NEW"],["MUN","TOT"],["BHA","ARS"],["EVE","AVL"],["LEE","CRY"],["NFO","BUR"],["SUN","FUL"],["WHU","WOL"],["BRE","BOU"]],
];

const clamp = (v, a, b) => Math.min(b, Math.max(a, v));

/* per-team fixture list: [{opp, home, fdr} × 6] */
export const FIXTURES = {};
Object.keys(TEAMS).forEach((code) => (FIXTURES[code] = []));
ROUNDS.forEach((round) => {
  round.forEach(([h, a]) => {
    FIXTURES[h].push({ opp: a, home: true,  fdr: clamp(TEAMS[a].str - 1 + (TEAMS[a].str >= 5 ? 1 : 0), 1, 5) });
    FIXTURES[a].push({ opp: h, home: false, fdr: clamp(TEAMS[h].str,     1, 5) });
  });
});

/* seeded prng — deterministic wobble */
function mulberry32(seed) {
  return function () {
    seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/* base per-90 ratings */
const RAW = [
  // name, team, pos, price, baseXG, baseXA, volatility
  ["Erling Haaland",     "MCI", "FWD", 14.3, 0.92, 0.14, 1.5],
  ["Mohamed Salah",      "LIV", "MID", 13.1, 0.58, 0.34, 1.3],
  ["Cole Palmer",        "CHE", "MID", 10.6, 0.46, 0.36, 1.2],
  ["Bukayo Saka",        "ARS", "MID", 10.2, 0.42, 0.35, 1.1],
  ["Alexander Isak",     "LIV", "FWD",  9.6, 0.62, 0.12, 1.4],
  ["Viktor Gyökeres",    "ARS", "FWD",  9.1, 0.60, 0.11, 1.4],
  ["Ollie Watkins",      "AVL", "FWD",  8.6, 0.52, 0.18, 1.3],
  ["Bryan Mbeumo",       "MUN", "MID",  8.2, 0.38, 0.28, 1.2],
  ["Hugo Ekitiké",       "LIV", "FWD",  8.0, 0.48, 0.16, 1.3],
  ["Phil Foden",         "MCI", "MID",  7.9, 0.35, 0.30, 1.2],
  ["João Pedro",         "CHE", "FWD",  7.7, 0.44, 0.20, 1.3],
  ["Omar Marmoush",      "MCI", "MID",  7.6, 0.40, 0.22, 1.3],
  ["Anthony Gordon",     "NEW", "MID",  7.4, 0.34, 0.26, 1.2],
  ["Matheus Cunha",      "MUN", "MID",  7.3, 0.36, 0.24, 1.3],
  ["Bruno Fernandes",    "MUN", "MID",  8.4, 0.28, 0.33, 1.1],
  ["Morgan Rogers",      "AVL", "MID",  7.1, 0.30, 0.27, 1.1],
  ["Yoane Wissa",        "NEW", "FWD",  7.0, 0.46, 0.10, 1.3],
  ["Jarrod Bowen",       "WHU", "MID",  7.6, 0.33, 0.24, 1.2],
  ["Antoine Semenyo",    "BOU", "MID",  7.2, 0.35, 0.20, 1.2],
  ["Mohammed Kudus",     "TOT", "MID",  6.8, 0.28, 0.25, 1.1],
  ["Jean-Philippe Mateta","CRY","FWD",  7.0, 0.44, 0.09, 1.3],
  ["Chris Wood",         "NFO", "FWD",  6.9, 0.45, 0.06, 1.3],
  ["Igor Thiago",        "BRE", "FWD",  6.4, 0.40, 0.08, 1.3],
  ["Danny Welbeck",      "BHA", "FWD",  6.3, 0.36, 0.13, 1.2],
  ["Iliman Ndiaye",      "EVE", "MID",  6.2, 0.28, 0.18, 1.1],
  ["Cody Gakpo",         "LIV", "MID",  7.5, 0.34, 0.21, 1.2],
  ["Kevin Schade",       "BRE", "MID",  6.1, 0.30, 0.14, 1.2],
  ["Morgan Gibbs-White", "NFO", "MID",  6.6, 0.22, 0.26, 1.0],
  ["Jørgen Strand Larsen","WOL","FWD",  6.0, 0.38, 0.08, 1.3],
  ["Dominic Calvert-Lewin","LEE","FWD", 5.9, 0.36, 0.10, 1.3],
  ["Alex Iwobi",         "FUL", "MID",  6.4, 0.22, 0.24, 1.0],
  ["Wilson Isidor",      "SUN", "FWD",  5.6, 0.32, 0.08, 1.3],
  ["Lyle Foster",        "BUR", "FWD",  5.4, 0.28, 0.08, 1.2],
  ["Declan Rice",        "ARS", "MID",  6.7, 0.18, 0.24, 0.9],
  ["Gabriel Magalhães",  "ARS", "DEF",  6.3, 0.14, 0.04, 0.9],
  ["Virgil van Dijk",    "LIV", "DEF",  6.0, 0.10, 0.05, 0.8],
  ["Daniel Muñoz",       "CRY", "DEF",  5.6, 0.10, 0.14, 0.9],
  ["Marcos Senesi",      "BOU", "DEF",  4.9, 0.08, 0.04, 0.8],
  ["Milos Kerkez",       "LIV", "DEF",  5.8, 0.05, 0.13, 0.8],
  ["Rúben Dias",         "MCI", "DEF",  5.7, 0.07, 0.04, 0.7],
];

const fdrMult = (fdr) => [0, 1.45, 1.22, 1.0, 0.78, 0.58][fdr];
const goalPts = { GK: 10, DEF: 6, MID: 5, FWD: 4 };

export const PLAYERS = RAW.map(([name, team, pos, price, bxg, bxa, vol], idx) => {
  const rnd = mulberry32(idx * 1000 + 7);
  const fx = FIXTURES[team];
  const xg = [], xa = [], xpts = [];

  fx.forEach((f) => {
    const m = fdrMult(f.fdr) * (f.home ? 1.07 : 0.93);
    const wob = () => 0.88 + rnd() * 0.24;
    const g = +(bxg * m * wob()).toFixed(2);
    const a = +(bxa * m * wob()).toFixed(2);
    const csP = clamp(0.46 - 0.085 * (f.fdr - 1), 0.04, 0.6);
    let p = 2 + goalPts[pos] * g + 3 * a;
    if (pos === "DEF" || pos === "GK") p += 4 * csP;
    if (pos === "MID") p += 1 * csP;
    p += 0.55 * (g + a); // bonus estimate
    xg.push(g); xa.push(a); xpts.push(+p.toFixed(1));
  });

  const total = +xpts.reduce((s, v) => s + v, 0).toFixed(1);
  const mean = +(total / GW_COUNT).toFixed(1);

  /* bracket model → [≤2, 3–6, 7–9, 10–14, 15+] % */
  let b0 = clamp(56 - mean * 7.4 - vol * 3, 3, 68);
  let b4 = clamp((mean - 3) * 2.3 + vol * 3.6, 0.4, 19);
  let b3 = clamp((mean - 2.4) * 3.5 + vol * 2.4, 1, 27);
  let b2 = clamp(mean * 2.7 + vol * 1.5, 5, 31);
  let b1 = Math.max(4, 100 - b0 - b2 - b3 - b4);
  const s = 100 / (b0 + b1 + b2 + b3 + b4);
  const brackets = [b0, b1, b2, b3, b4].map((v) => +(v * s).toFixed(1));

  return {
    id: idx, name, team, pos, price, xg, xa, xpts, total, mean, brackets,
    haul: +(brackets[3] + brackets[4]).toFixed(1),
    blank: brackets[0],
    floor: Math.max(1, Math.round(mean * 0.32)),
    median: Math.max(2, Math.round(mean * 0.92)),
    ceil: Math.round(mean * 2.05 + vol * 2),
  };
});

export const BRACKET_LABELS = ["≤2 pts", "3–6 pts", "7–9 pts", "10–14 pts", "15+ pts"];
