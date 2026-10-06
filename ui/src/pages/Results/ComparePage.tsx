import { Placeholder } from "../Placeholder";

// /results/compare?a=&b=&split= (API.md GET /api/compare).
export default function ComparePage() {
  return <Placeholder area="compare" title="Compare two runs" description="Paired difference with a 95% CI." command="decider-lab compare RUN_A RUN_B" breadcrumbs={[{ label: "Results", to: "/results" }, { label: "Compare" }]} />;
}
