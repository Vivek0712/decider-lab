import { cn } from "@/lib/cn";

export function ProgressBar({
  value,
  label,
  indeterminate,
  tone = "accent",
  showLabel = false,
  className,
  "data-testid": testId,
}: {
  /** 0..1 */
  value?: number | null;
  /** Accessible name, e.g. "4/6 runs". */
  label: string;
  indeterminate?: boolean;
  tone?: "accent" | "warning" | "danger";
  showLabel?: boolean;
  className?: string;
  "data-testid"?: string;
}) {
  const unknown = indeterminate || value == null;
  const pct = unknown ? undefined : Math.round(Math.max(0, Math.min(1, value!)) * 100);
  const fill = tone === "danger" ? "bg-danger" : tone === "warning" ? "bg-warning" : "bg-accent";
  return (
    <div className={cn("flex min-w-0 items-center gap-2", className)} data-testid={testId}>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={pct}
        aria-valuetext={label}
        className="relative h-1.5 min-w-[48px] flex-1 overflow-hidden rounded-full bg-surface-3"
      >
        {unknown ? (
          <span className={cn("absolute inset-y-0 w-2/5 rounded-full animate-[dl-indeterminate_1.4s_ease-in-out_infinite]", fill)} />
        ) : (
          <span className={cn("absolute inset-y-0 left-0 rounded-full transition-[width] duration", fill)} style={{ width: `${pct}%` }} />
        )}
      </div>
      {showLabel && <span className="tnum shrink-0 text-small text-muted">{label}</span>}
    </div>
  );
}
