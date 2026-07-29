import PrecomputedRunUploader from "@/components/admin/PrecomputedRunUploader";
import WeeklyRunWizard from "@/components/admin/WeeklyRunWizard";

// The season is chosen in the wizard's run controls (defaults to the
// current season; past seasons selectable for replays/backfills).
// Server component shell: nothing sensitive renders here.
//
// Two intake paths share this page:
//   * PrecomputedRunUploader -- ingest CSVs from a local model run
//   * WeeklyRunWizard        -- run the model server-side from minutes inputs
// Both stage a DRAFT run, so preview/publish downstream is identical.
export default function ProjectionsPage() {
  return (
    <div className="space-y-8">
      <PrecomputedRunUploader />
      <WeeklyRunWizard />
    </div>
  );
}
