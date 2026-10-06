import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { cn } from "@/lib/cn";
import type { Tone } from "./Badge";

const toneText: Record<Tone, string> = {
  neutral: "text-text",
  accent: "text-accent",
  success: "text-success",
  warning: "text-warning",
  danger: "text-danger",
  info: "text-info",
};

/** KPI card (DESIGN.md KpiCard): label, big value, sub line; the whole card links when href is set. */
export function Stat({
  label,
  value,
  sub,
  href,
  tone = "neutral",
  title,
  className,
  "data-testid": testId,
}: {
  label: ReactNode;
  value: ReactNode;
  sub?: ReactNode;
  href?: string;
  tone?: Tone;
  title?: string;
  className?: string;
  "data-testid"?: string;
}) {
  const body = (
    <>
      <div className="text-caption uppercase tracking-wide text-muted">{label}</div>
      <div className={cn("tnum mt-1 text-display", toneText[tone])}>{value}</div>
      {sub && <div className="mt-1 truncate text-small text-muted">{sub}</div>}
    </>
  );
  const cls = cn(
    "block min-w-0 rounded-md border border-border bg-surface-1 px-4 py-4 shadow-elev-1",
    href && "transition-colors duration-fast hover:border-border-strong hover:bg-surface-2 no-underline text-text",
    className,
  );
  return href ? (
    <Link to={href} className={cls} title={title} data-testid={testId}>
      {body}
    </Link>
  ) : (
    <div className={cls} title={title} data-testid={testId}>
      {body}
    </div>
  );
}
export const KpiCard = Stat;
