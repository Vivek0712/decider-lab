import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";

/** Page title (the page's one h1), description, actions and optional breadcrumbs. */
export function PageHeader({
  title,
  description,
  actions,
  status,
  breadcrumbs,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  status?: ReactNode;
  breadcrumbs?: { label: string; to?: string }[];
}) {
  return (
    <div className="mb-6">
      {breadcrumbs && breadcrumbs.length > 0 && (
        <nav aria-label="Breadcrumb" className="mb-2">
          <ol className="flex flex-wrap items-center gap-1 text-small text-muted">
            {breadcrumbs.map((b, i) => (
              <li key={i} className="inline-flex items-center gap-1">
                {b.to ? (
                  <Link to={b.to} className="text-muted no-underline hover:text-text hover:underline">
                    {b.label}
                  </Link>
                ) : (
                  <span aria-current="page">{b.label}</span>
                )}
                {i < breadcrumbs.length - 1 && <ChevronRight size={14} aria-hidden />}
              </li>
            ))}
          </ol>
        </nav>
      )}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-h1">{title}</h1>
            {status}
          </div>
          {description && <p className="mt-1 max-w-[80ch] text-body text-muted">{description}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2 max-sm:w-full">{actions}</div>}
      </div>
    </div>
  );
}
