import type { ReactNode } from "react";
import { Construction } from "lucide-react";
import { Card } from "@/components/Card";
import { EmptyState } from "@/components/EmptyState";
import { PageHeader } from "@/components/PageHeader";

/**
 * Temporary page body used by the foundation for every route. Area teams replace their page's use
 * of it; delete this file once no page imports it.
 */
export function Placeholder({
  area,
  title,
  description,
  command,
  children,
  breadcrumbs,
}: {
  area: string;
  title: string;
  description: string;
  command?: string;
  children?: ReactNode;
  breadcrumbs?: { label: string; to?: string }[];
}) {
  return (
    <div data-testid={`page-${area}`}>
      <PageHeader title={title} description={description} breadcrumbs={breadcrumbs} />
      {children}
      <Card>
        <EmptyState
          icon={Construction}
          title={`${title} is being built`}
          body="This page is a placeholder in the Studio foundation. Its area team fills it in."
          command={command}
        />
      </Card>
    </div>
  );
}
