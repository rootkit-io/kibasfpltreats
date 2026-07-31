import { TEAM_KITS, type TeamKitColors } from "@/lib/constants/teamColors";

const FALLBACK_KIT: TeamKitColors = {
  primary: "#3F3F46",
  secondary: "#A1A1AA",
  trim: "#D4D4D8",
};

export interface TeamKitProps {
  teamCode?: string | null;
  size?: number;
  className?: string;
}

/** Decorative, compact shirt mark for pairing a player name with their club. */
export function TeamKit({ teamCode, size = 18, className }: TeamKitProps) {
  const normalizedCode = teamCode?.trim().toUpperCase() ?? "";
  const colors = TEAM_KITS[normalizedCode] ?? FALLBACK_KIT;

  return (
    <svg
      aria-hidden="true"
      className={`inline-block shrink-0 align-[-0.125em] ${className ?? ""}`}
      focusable="false"
      height={size}
      viewBox="0 0 24 28"
      width={size}
      xmlns="http://www.w3.org/2000/svg"
    >
      <path
        d="M8.2 4.1 3.4 6.8.9 13.2l4.4 2.1 2-3.2V27h9.4V12.1l2 3.2 4.4-2.1-2.5-6.4-4.8-2.7-1.3 2.6h-5l-1.3-2.6Z"
        fill={colors.secondary}
      />
      <path
        d="M8.2 4.1 9.5 6.7h5l1.3-2.6 1.1.6v22.2H7.1V4.7l1.1-.6Z"
        fill={colors.primary}
      />
      {colors.trim && (
        <path d="M7.1 15.6h9.8" fill="none" stroke={colors.trim} strokeWidth="1" />
      )}
      <path
        d="M9.5 4.2c.6 1.5 1.4 2.2 2.5 2.2s1.9-.7 2.5-2.2"
        fill="none"
        stroke={colors.secondary}
        strokeLinecap="round"
        strokeWidth="1.15"
      />
    </svg>
  );
}

export default TeamKit;
