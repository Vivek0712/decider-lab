import { useState, type ReactNode } from "react";
import { Table2 } from "lucide-react";
import { cn } from "@/lib/cn";
import { EmptyState } from "@/components/EmptyState";
import { Skeleton } from "@/components/Skeleton";

export type TableData = { columns: string[]; rows: (string | number | null)[][] };

/**
 * Wrapper every chart uses (DESIGN.md 7): h3 title, one-line caption, role="img" with a
 * data-generated aria-label, a "View as table" toggle rendering the same data, loading and empty.
 */
export function ChartFrame({
  title,
  caption,
  ariaLabel,
  table,
  height = 220,
  loading,
  empty,
  actions,
  showTable: showTableProp,
  className,
  children,
  "data-testid": testId,
}: {
  title: ReactNode;
  caption?: ReactNode;
  ariaLabel: string;
  table: TableData;
  height?: number;
  loading?: boolean;
  empty?: ReactNode | boolean;
  actions?: ReactNode;
  showTable?: boolean;
  className?: string;
  children: ReactNode;
  "data-testid"?: string;
}) {
  const [asTable, setAsTable] = useState(!!showTableProp);
  return (
    <figure className={cn("m-0 min-w-0", className)} data-testid={testId}>
      <div className="mb-2 flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-h3">{title}</h3>
          {caption && <figcaption className="text-small text-muted">{caption}</figcaption>}
        </div>
        <div className="flex items-center gap-2">
          {actions}
          <button
            type="button"
            aria-pressed={asTable}
            onClick={() => setAsTable(!asTable)}
            className={cn(
              "inline-flex h-8 items-center gap-1 rounded-sm px-2 text-small max-sm:min-h-[44px]",
              asTable ? "bg-tint-accent text-accent" : "text-muted hover:bg-surface-3 hover:text-text",
            )}
          >
            <Table2 size={14} aria-hidden /> View as table
          </button>
        </div>
      </div>
      {loading ? (
        <Skeleton shape="block" height={height} />
      ) : empty ? (
        typeof empty === "boolean" ? <EmptyState title="No data yet" className="py-6" /> : empty
      ) : asTable ? (
        <div className="scrollbar-thin max-h-[420px] overflow-auto rounded-sm border border-border">
          <table className="w-full text-small">
            <caption className="sr-only">{ariaLabel}</caption>
            <thead className="sticky top-0 bg-surface-2">
              <tr>
                {table.columns.map((c) => (
                  <th key={c} scope="col" className="px-3 py-1.5 text-left text-caption font-semibold uppercase text-muted">
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {table.rows.map((r, i) => (
                <tr key={i} className="border-t border-border">
                  {r.map((v, j) => (
                    <td key={j} className={cn("px-3 py-1", typeof v === "number" && "tnum text-right")}>
                      {v == null ? "—" : String(v)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div role="img" aria-label={ariaLabel} style={{ height }} className="relative w-full">
          {children}
        </div>
      )}
      <table className="sr-only">
        <caption>{ariaLabel}</caption>
        <tbody>
          {table.rows.slice(0, 50).map((r, i) => (
            <tr key={i}>
              {r.map((v, j) => (
                <td key={j}>{v == null ? "—" : String(v)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
