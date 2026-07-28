import WeeklyRunWizard from "@/components/admin/WeeklyRunWizard";

// The season is chosen in the wizard's run controls (defaults to the
// current season; past seasons selectable for replays/backfills).
// Server component shell: nothing sensitive renders here.
export default function ProjectionsPage() {
  return <WeeklyRunWizard />;
}
