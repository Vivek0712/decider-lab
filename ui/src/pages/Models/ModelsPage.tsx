import { Placeholder } from "../Placeholder";

// Owner: Models area. Tabs: Cache | Pull. DESIGN.md 4.5; API.md section 8 (src/api/models.ts).
export default function ModelsPage() {
  return <Placeholder area="models" title="Models" description="Checkpoints pulled into the decider-lab cache, and pulling new ones." command="decider-lab models" />;
}
