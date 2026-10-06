import { useParams } from "react-router-dom";
import { Placeholder } from "../Placeholder";

export default function RunPage() {
  const { rootId = "", model = "", suite = "" } = useParams();
  return (
    <Placeholder
      area="run"
      title={`${model} / ${suite}`}
      description="One model on one suite: scores, reliability, latency, predictions."
      breadcrumbs={[{ label: "Results", to: "/results" }, { label: "Run root", to: `/results/${rootId}` }, { label: `${model} / ${suite}` }]}
    />
  );
}
