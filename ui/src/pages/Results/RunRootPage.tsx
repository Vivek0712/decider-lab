import { useParams } from "react-router-dom";
import { Placeholder } from "../Placeholder";

// Tabs: Leaderboard | Vs baseline | Families | Calibration | Latency | JevBench | Rows | Provenance.
export default function RunRootPage() {
  const { rootId = "" } = useParams();
  return <Placeholder area="runroot" title="Run root" description={`Run root ${rootId}`} breadcrumbs={[{ label: "Results", to: "/results" }, { label: "Run root" }]} />;
}
