import { useParams } from "react-router-dom";
import { Placeholder } from "../Placeholder";

// Tabs: Summary | Editor | Runs; Run dialog at ?run=1 (DESIGN.md 4.2.2).
export default function LabPage() {
  const { labId = "" } = useParams();
  return (
    <Placeholder
      area="lab"
      title="Lab"
      description={`Lab ${labId}`}
      command="decider-lab run lab.yaml"
      breadcrumbs={[{ label: "Labs", to: "/labs" }, { label: "Lab" }]}
    />
  );
}
