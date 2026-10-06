import { useParams } from "react-router-dom";
import { Placeholder } from "../Placeholder";

// Tabs: Progress | Logs | Telemetry | Command. Building blocks: StageTimeline, JobLogViewer,
// TimeSeriesChart (src/components, src/charts).
export default function JobPage() {
  const { jobId = "" } = useParams();
  return <Placeholder area="job" title={`Job ${jobId}`} description="Stages, progress, logs and telemetry." breadcrumbs={[{ label: "Jobs", to: "/jobs" }, { label: jobId }]} />;
}
