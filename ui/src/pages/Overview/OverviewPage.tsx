import { Placeholder } from "../Placeholder";

// Owner: Overview area. DESIGN.md 4.1; data: GET /api/overview (src/api/overview.ts).
export default function OverviewPage() {
  return (
    <Placeholder
      area="overview"
      title="Overview"
      description="What is running, what finished, and what to do next."
      command="decider-lab doctor"
    />
  );
}
