import { useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";
import { cn } from "@/lib/cn";
import { useIsPhone } from "@/hooks/useMediaQuery";
import { Skeleton } from "./Skeleton";

export type Column<T> = {
  id: string;
  header: ReactNode;
  cell: (row: T) => ReactNode;
  /** Makes the column sortable. */
  sortValue?: (row: T) => string | number | null | undefined;
  align?: "left" | "right";
  width?: number | string;
  hideBelow?: "sm" | "md";
  /** Text label for the mobile card layout when header is not a string. */
  label?: string;
};
export type Sort = { id: string; dir: "asc" | "desc" };

/**
 * Data table: sortable headers (aria-sort), keyboard row focus (j/k or arrows, Enter opens),
 * sticky header, optional sticky first column, horizontal scroll inside the card, and a card
 * layout below 640 px with mobile="cards". Uncontrolled sort unless `sort`/`onSort` are passed.
 */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  caption,
  sort: sortProp,
  onSort,
  defaultSort,
  onRowClick,
  rowTestId,
  stickyFirst,
  mobile = "scroll",
  loading,
  empty,
  className,
  "data-testid": testId,
}: {
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  caption: string;
  sort?: Sort | null;
  onSort?: (s: Sort | null) => void;
  defaultSort?: Sort;
  onRowClick?: (row: T) => void;
  rowTestId?: (row: T) => string;
  stickyFirst?: boolean;
  mobile?: "scroll" | "cards";
  loading?: boolean;
  empty?: ReactNode;
  className?: string;
  "data-testid"?: string;
}) {
  const [inner, setInner] = useState<Sort | null>(defaultSort ?? null);
  const sort = sortProp !== undefined ? sortProp : inner;
  const setSort = onSort ?? setInner;
  const phone = useIsPhone();
  const body = useRef<HTMLTableSectionElement>(null);

  const sorted = useMemo(() => {
    if (!sort) return rows;
    const col = columns.find((c) => c.id === sort.id);
    if (!col?.sortValue) return rows;
    const get = col.sortValue;
    const out = [...rows].sort((a, b) => {
      const x = get(a);
      const y = get(b);
      if (x == null && y == null) return 0;
      if (x == null) return 1; // missing values last in both directions
      if (y == null) return -1;
      const c = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y));
      return sort.dir === "asc" ? c : -c;
    });
    return out;
  }, [rows, sort, columns]);

  const toggle = (c: Column<T>) => {
    if (!c.sortValue) return;
    if (!sort || sort.id !== c.id) setSort({ id: c.id, dir: "desc" });
    else if (sort.dir === "desc") setSort({ id: c.id, dir: "asc" });
    else setSort(null);
  };

  const onKey = (e: KeyboardEvent<HTMLTableRowElement>, row: T) => {
    const tr = e.currentTarget;
    const go = (el: Element | null) => (el as HTMLElement | null)?.focus();
    if (e.key === "ArrowDown" || e.key === "j") {
      e.preventDefault();
      go(tr.nextElementSibling);
    } else if (e.key === "ArrowUp" || e.key === "k") {
      e.preventDefault();
      go(tr.previousElementSibling);
    } else if (e.key === "Enter" && onRowClick) {
      e.preventDefault();
      onRowClick(row);
    }
  };

  const hide = (c: Column<T>) => (c.hideBelow === "sm" ? "max-sm:hidden" : c.hideBelow === "md" ? "max-md:hidden" : "");

  if (mobile === "cards" && phone && !loading) {
    return (
      <div data-testid={testId} className={cn("flex flex-col gap-2", className)} aria-label={caption} role="list">
        {sorted.length === 0 && empty}
        {sorted.map((row) => (
          <div
            key={rowKey(row)}
            role="listitem"
            data-testid={rowTestId?.(row)}
            tabIndex={onRowClick ? 0 : undefined}
            onClick={onRowClick ? () => onRowClick(row) : undefined}
            onKeyDown={(e) => e.key === "Enter" && onRowClick?.(row)}
            className={cn("rounded-md border border-border bg-surface-1 p-3", onRowClick && "cursor-pointer hover:bg-surface-2")}
          >
            {columns.map((c, i) => (
              <div key={c.id} className={cn("flex min-w-0 items-baseline justify-between gap-3 py-0.5", i === 0 && "mb-1 font-semibold")}>
                {i > 0 && <span className="shrink-0 text-caption text-muted">{c.label ?? (typeof c.header === "string" ? c.header : c.id)}</span>}
                <span className={cn("min-w-0 truncate", c.align === "right" && "tnum")}>{c.cell(row)}</span>
              </div>
            ))}
          </div>
        ))}
      </div>
    );
  }

  return (
    <div data-testid={testId} className={cn("scrollbar-thin relative max-w-full overflow-x-auto", className)} aria-busy={loading || undefined}>
      <table className="w-full border-collapse text-left text-body">
        <caption className="sr-only">{caption}</caption>
        <thead className="sticky top-0 z-[1] bg-surface-2">
          <tr>
            {columns.map((c, i) => {
              const active = sort?.id === c.id;
              const ariaSort = active ? (sort!.dir === "asc" ? "ascending" : "descending") : c.sortValue ? "none" : undefined;
              return (
                <th
                  key={c.id}
                  scope="col"
                  aria-sort={ariaSort}
                  style={{ width: c.width }}
                  className={cn(
                    "h-9 whitespace-nowrap border-b border-border px-3 text-caption font-semibold uppercase tracking-wide text-muted",
                    c.align === "right" && "text-right",
                    stickyFirst && i === 0 && "sticky left-0 z-[2] bg-surface-2",
                    hide(c),
                  )}
                >
                  {c.sortValue ? (
                    <button
                      type="button"
                      onClick={() => toggle(c)}
                      className={cn("inline-flex items-center gap-1 uppercase hover:text-text", c.align === "right" && "flex-row-reverse")}
                    >
                      {c.header}
                      {active ? (
                        sort!.dir === "asc" ? <ArrowUp size={12} aria-hidden /> : <ArrowDown size={12} aria-hidden />
                      ) : (
                        <ChevronsUpDown size={12} aria-hidden className="opacity-50" />
                      )}
                    </button>
                  ) : (
                    c.header
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody ref={body}>
          {loading &&
            Array.from({ length: 4 }, (_, i) => (
              <tr key={`sk-${i}`} className="h-[var(--row-h)] border-b border-border">
                {columns.map((c) => (
                  <td key={c.id} className={cn("px-3", hide(c))}>
                    <Skeleton width="70%" />
                  </td>
                ))}
              </tr>
            ))}
          {!loading && sorted.length === 0 && empty && (
            <tr>
              <td colSpan={columns.length}>{empty}</td>
            </tr>
          )}
          {!loading &&
            sorted.map((row) => (
              <tr
                key={rowKey(row)}
                data-testid={rowTestId?.(row)}
                tabIndex={onRowClick ? 0 : -1}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                onKeyDown={(e) => onKey(e, row)}
                className={cn(
                  "focus-inset h-[var(--row-h)] border-b border-border last:border-b-0",
                  onRowClick && "cursor-pointer hover:bg-surface-3",
                )}
              >
                {columns.map((c, i) => (
                  <td
                    key={c.id}
                    className={cn(
                      "px-3 py-1.5 align-middle",
                      c.align === "right" && "tnum text-right",
                      stickyFirst && i === 0 && "sticky left-0 bg-surface-1",
                      hide(c),
                    )}
                  >
                    {c.cell(row)}
                  </td>
                ))}
              </tr>
            ))}
        </tbody>
      </table>
    </div>
  );
}
export const Table = DataTable;
