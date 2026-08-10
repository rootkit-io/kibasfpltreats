/**
 * Squad PNG export.
 *
 * Renders the current squad to a canvas and triggers a download.
 * No external dependencies — pure Canvas 2D API.
 *
 * Layout (600×520):
 *   header bar  — manager name + GW
 *   pitch grid  — 4 rows (GK/DEF/MID/FWD) with coloured position dots
 *   bench row   — 4 smaller cards
 *   footer      — bank / FT / hits + KFT watermark
 */

import type { FplPlayer, FplTeam, PlannerPick } from "@/lib/planner/types";
import type { PlannerState } from "@/lib/planner/state";
import type { DerivedGwState } from "@/lib/planner/types";

// ── Colours ───────────────────────────────────────────────────────────────────

const POSITION_COLOR: Record<number, string> = {
  1: "#D97706", 2: "#059669", 3: "#2563EB", 4: "#DC2626",
};

const BG = "#0D1117";
const CARD_BG = "#161B22";
const CARD_BORDER = "#30363D";
const TEXT_PRIMARY = "#F0F6FC";
const TEXT_MUTED = "#8B949E";
const PITCH_BG_TOP = "#1A3A1A";
const PITCH_BG_BOTTOM = "#1E4A1E";

// ── Helpers ───────────────────────────────────────────────────────────────────

function pence(tenths: number): string {
  return `£${(tenths / 10).toFixed(1)}m`;
}

function truncate(ctx: CanvasRenderingContext2D, text: string, maxWidth: number): string {
  if (ctx.measureText(text).width <= maxWidth) return text;
  let t = text;
  while (t.length > 1 && ctx.measureText(t + "…").width > maxWidth) t = t.slice(0, -1);
  return t + "…";
}

function drawRoundRect(
  ctx: CanvasRenderingContext2D,
  x: number, y: number, w: number, h: number,
  r: number,
  fill: string,
) {
  ctx.beginPath();
  ctx.roundRect(x, y, w, h, r);
  ctx.fillStyle = fill;
  ctx.fill();
}

function drawCard(
  ctx: CanvasRenderingContext2D,
  pick: PlannerPick,
  player: FplPlayer | undefined,
  cx: number, cy: number,
  cardW: number, cardH: number,
  xpts: number | null,
  isBench: boolean,
  showXpts: boolean,
) {
  const x = cx - cardW / 2;
  const y = cy - cardH / 2;
  const posColor = POSITION_COLOR[player?.element_type ?? 3] ?? "#888";

  drawRoundRect(ctx, x, y, cardW, cardH, 4, CARD_BG);
  ctx.strokeStyle = CARD_BORDER;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.roundRect(x, y, cardW, cardH, 4);
  ctx.stroke();

  // Position dot
  ctx.beginPath();
  ctx.arc(cx, y + 8, 4, 0, Math.PI * 2);
  ctx.fillStyle = posColor;
  ctx.fill();

  // Name
  const nameSize = isBench ? 9 : 10;
  ctx.font = `600 ${nameSize}px -apple-system, system-ui, sans-serif`;
  ctx.fillStyle = TEXT_PRIMARY;
  ctx.textAlign = "center";
  const name = truncate(ctx, player?.web_name ?? `#${pick.element}`, cardW - 6);
  ctx.fillText(name, cx, y + cardH / 2 + 2);

  // Captain/VC badge
  if (pick.multiplier >= 2 || pick.isViceCaptain) {
    const badge = pick.multiplier === 3 ? "TC" : pick.multiplier >= 2 ? "C" : "V";
    const badgeColor = pick.multiplier === 3 ? "#A855F7" : pick.multiplier >= 2 ? "#F59E0B" : "#6B7280";
    drawRoundRect(ctx, cx + cardW / 2 - 14, y - 1, 12, 12, 3, badgeColor);
    ctx.font = "bold 8px -apple-system, system-ui, sans-serif";
    ctx.fillStyle = "#fff";
    ctx.textAlign = "center";
    ctx.fillText(badge, cx + cardW / 2 - 8, y + 9);
  }

  // xPts
  if (showXpts && xpts !== null) {
    ctx.font = `500 8px -apple-system, system-ui, sans-serif`;
    ctx.fillStyle = xpts >= 6 ? "#34D399" : xpts >= 4 ? TEXT_PRIMARY : TEXT_MUTED;
    ctx.textAlign = "center";
    ctx.fillText(xpts.toFixed(1), cx, y + cardH - 4);
  }
}

// ── Main export function ──────────────────────────────────────────────────────

export async function exportSquadPng(
  state: PlannerState,
  derived: DerivedGwState,
  getXpts: (element: number, gw: number) => number | null,
): Promise<void> {
  const W = 600, H = 540;
  const canvas = document.createElement("canvas");
  canvas.width = W * 2;   // 2× for retina
  canvas.height = H * 2;
  canvas.style.width = `${W}px`;
  canvas.style.height = `${H}px`;

  const ctx = canvas.getContext("2d")!;
  ctx.scale(2, 2);

  // ── Background
  ctx.fillStyle = BG;
  ctx.fillRect(0, 0, W, H);

  // ── Header bar
  const headerH = 40;
  ctx.fillStyle = CARD_BG;
  ctx.fillRect(0, 0, W, headerH);
  ctx.font = "bold 14px -apple-system, system-ui, sans-serif";
  ctx.fillStyle = TEXT_PRIMARY;
  ctx.textAlign = "left";
  ctx.fillText(state.managerName ?? "My Squad", 16, 26);
  ctx.font = "500 12px -apple-system, system-ui, sans-serif";
  ctx.fillStyle = TEXT_MUTED;
  ctx.textAlign = "right";
  ctx.fillText(`GW${state.planGw}`, W - 16, 26);

  // ── Pitch background
  const pitchY = headerH;
  const pitchH = 400;
  const grad = ctx.createLinearGradient(0, pitchY, 0, pitchY + pitchH);
  grad.addColorStop(0, PITCH_BG_TOP);
  grad.addColorStop(1, PITCH_BG_BOTTOM);
  ctx.fillStyle = grad;
  ctx.fillRect(0, pitchY, W, pitchH);

  // Pitch line
  ctx.strokeStyle = "rgba(255,255,255,0.15)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(40, pitchY + pitchH / 2);
  ctx.lineTo(W - 40, pitchY + pitchH / 2);
  ctx.stroke();

  // ── Sort starters by position slot
  const starters = derived.squad.filter((p) => p.position <= 11).sort((a, b) => a.position - b.position);
  const bench = derived.squad.filter((p) => p.position > 11).sort((a, b) => a.position - b.position);
  const { playerMap } = state;

  function getRow(elementType: number): PlannerPick[] {
    return starters.filter((p) => (playerMap.get(p.element)?.element_type ?? 3) === elementType);
  }

  const rows = [getRow(1), getRow(2), getRow(3), getRow(4)];
  const rowYPositions = [pitchY + 60, pitchY + 145, pitchY + 245, pitchY + 330];
  const cardW = 68, cardH = 44;

  for (let r = 0; r < rows.length; r++) {
    const row = rows[r];
    const cy = rowYPositions[r];
    const totalW = row.length * cardW + (row.length - 1) * 8;
    const startX = (W - totalW) / 2 + cardW / 2;
    for (let i = 0; i < row.length; i++) {
      const pick = row[i];
      const player = playerMap.get(pick.element);
      const xpts = getXpts(pick.element, state.planGw);
      drawCard(ctx, pick, player, startX + i * (cardW + 8), cy, cardW, cardH, xpts, false, true);
    }
  }

  // ── Bench strip
  const benchY = pitchY + pitchH;
  const benchH = 52;
  ctx.fillStyle = "rgba(0,0,0,0.3)";
  ctx.fillRect(0, benchY, W, benchH);
  ctx.strokeStyle = "rgba(255,255,255,0.08)";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, benchY);
  ctx.lineTo(W, benchY);
  ctx.stroke();

  const bCardW = 60, bCardH = 36;
  const bTotalW = bench.length * bCardW + (bench.length - 1) * 8;
  const bStartX = (W - bTotalW) / 2 + bCardW / 2;
  for (let i = 0; i < bench.length; i++) {
    const pick = bench[i];
    const player = playerMap.get(pick.element);
    const xpts = getXpts(pick.element, state.planGw);
    drawCard(ctx, pick, player, bStartX + i * (bCardW + 8), benchY + benchH / 2, bCardW, bCardH, xpts, true, true);
  }

  // ── Footer
  const footerY = benchY + benchH;
  ctx.fillStyle = CARD_BG;
  ctx.fillRect(0, footerY, W, H - footerY);
  ctx.font = "500 10px -apple-system, system-ui, sans-serif";
  ctx.fillStyle = TEXT_MUTED;
  ctx.textAlign = "left";
  ctx.fillText(
    `Bank ${pence(derived.bank)} · ${derived.ft} FT${derived.hits > 0 ? ` · −${derived.hits} pts` : ""}`,
    16, footerY + 16,
  );
  ctx.fillStyle = TEXT_MUTED;
  ctx.textAlign = "right";
  ctx.fillText("kibasfpltreats.com", W - 16, footerY + 16);

  // ── Trigger download
  const url = canvas.toDataURL("image/png");
  const a = document.createElement("a");
  a.href = url;
  a.download = `kft-squad-gw${state.planGw}.png`;
  a.click();
}
