import { Placeholder } from "../Placeholder";

// Owner: Compute area. Tabs: Local | vast.ai | AWS | SSH hosts. DESIGN.md 4.7; API.md section 10.
export default function ComputePage() {
  return <Placeholder area="compute" title="Compute" description="This machine, rented GPUs and your own servers." command="decider-lab compute ls --on vast" />;
}
