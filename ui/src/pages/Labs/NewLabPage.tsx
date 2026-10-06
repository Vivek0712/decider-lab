import { Placeholder } from "../Placeholder";

export default function NewLabPage() {
  return (
    <Placeholder
      area="lab-new"
      title="New lab"
      description="Start from the eval or finetune template."
      command="decider-lab init my-lab --template eval"
      breadcrumbs={[{ label: "Labs", to: "/labs" }, { label: "New lab" }]}
    />
  );
}
