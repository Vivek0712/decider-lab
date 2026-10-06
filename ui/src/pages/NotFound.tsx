import { Link } from "react-router-dom";
import { Compass } from "lucide-react";
import { Card } from "@/components/Card";
import { EmptyState } from "@/components/EmptyState";

export default function NotFound() {
  return (
    <div data-testid="page-not-found">
      <h1 className="sr-only">Page not found</h1>
      <Card>
        <EmptyState icon={Compass} title="This page does not exist" body="The address may be out of date." action={<Link to="/">Go to Overview</Link>} />
      </Card>
    </div>
  );
}
