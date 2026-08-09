/**
 * TeamKit — club kit mark for inline use in tables and lists.
 *
 * Delegates to KitShirt which carries the full artwork (solid / stripes /
 * pinstripes / halves / hoop / sash / yoke, gradients, seam shadows).
 * The interface is unchanged so every existing call site gets the upgrade
 * for free.
 */
import { KitShirt } from "@/components/ui/KitShirt";

export interface TeamKitProps {
  teamCode?: string | null;
  /** Full club name when available, improves kit resolution. */
  teamName?: string | null;
  size?: number;
  className?: string;
}

export function TeamKit({ teamCode, teamName, size = 18, className }: TeamKitProps) {
  return (
    <KitShirt
      teamCode={teamCode}
      teamName={teamName}
      size={size}
      className={className}
    />
  );
}

export default TeamKit;
