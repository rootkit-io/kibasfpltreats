export type TeamKitColors = {
  primary: string;
  secondary: string;
  trim?: string;
};

/**
 * Premier League kit palette keyed by FPL's three-letter team abbreviations.
 * Recent promoted clubs stay in the registry so historical projections retain
 * their visual identity when an older published run is viewed.
 */
export const TEAM_KITS: Record<string, TeamKitColors> = {
  ARS: { primary: "#EF0107", secondary: "#FFFFFF", trim: "#DB0007" },
  AVL: { primary: "#670E36", secondary: "#95BFE5", trim: "#FFFFFF" },
  BHA: { primary: "#0057B8", secondary: "#FFFFFF", trim: "#F7D117" },
  BOU: { primary: "#DA291C", secondary: "#111111", trim: "#FFFFFF" },
  BRE: { primary: "#E30613", secondary: "#FFFFFF", trim: "#111111" },
  BUR: { primary: "#6C1D45", secondary: "#99D6EA", trim: "#FFFFFF" },
  CHE: { primary: "#034694", secondary: "#FFFFFF", trim: "#D4A017" },
  CRY: { primary: "#1B458F", secondary: "#C4122E", trim: "#FFFFFF" },
  EVE: { primary: "#003399", secondary: "#FFFFFF", trim: "#FFFFFF" },
  FUL: { primary: "#FFFFFF", secondary: "#111111", trim: "#CC0000" },
  IPS: { primary: "#3A64A3", secondary: "#FFFFFF", trim: "#E31B23" },
  LEE: { primary: "#FFCD00", secondary: "#FFFFFF", trim: "#1D428A" },
  LEI: { primary: "#003090", secondary: "#FFFFFF", trim: "#FDBE11" },
  LIV: { primary: "#C8102E", secondary: "#C8102E", trim: "#FFFFFF" },
  MCI: { primary: "#6CABDD", secondary: "#6CABDD", trim: "#FFFFFF" },
  MUN: { primary: "#DA020E", secondary: "#111111", trim: "#FBE122" },
  NEW: { primary: "#FFFFFF", secondary: "#111111", trim: "#241F20" },
  NFO: { primary: "#DD0000", secondary: "#FFFFFF", trim: "#FFFFFF" },
  SOU: { primary: "#D71920", secondary: "#FFFFFF", trim: "#111111" },
  SUN: { primary: "#EB172B", secondary: "#FFFFFF", trim: "#111111" },
  TOT: { primary: "#FFFFFF", secondary: "#132257", trim: "#132257" },
  WHU: { primary: "#7A263A", secondary: "#1BB1E7", trim: "#FFFFFF" },
  WOL: { primary: "#FDB913", secondary: "#231F20", trim: "#231F20" },
};
