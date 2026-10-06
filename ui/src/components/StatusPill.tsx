import type { JobStatus } from "@/api/types";
import { cn } from "@/lib/cn";

// DESIGN.md 4.3: icon + text, never color alone.
const MAP: Record<JobStatus | "valid" | "invalid", { icon: string; label: string; cls: string; pulse?: boolean }> = {
  queued: { icon: "○", label: "queued", cls: "text-muted" },
  running: { icon: "●", label: "running", cls: "text-accent", pulse: true },
  cancelling: { icon: "◌", label: "cancelling", cls: "text-warning" },
  succeeded: { icon: "✓", label: "succeeded", cls: "text-success" },
  partial: { icon: "◐", label: "partial", cls: "text-warning" },
  failed: { icon: "✕", label: "failed", cls: "text-danger" },
  cancelled: { icon: "■", label: "cancelled", cls: "text-muted" },
  lost: { icon: "?", label: "lost", cls: "text-warning" },
  valid: { icon: "✓", label: "valid", cls: "text-success" },
  invalid: { icon: "✕", label: "invalid", cls: "text-danger" },
};

export function StatusPill({
  status,
  label,
  className,
  "data-testid": testId,
}: {
  status: JobStatus | "valid" | "invalid";
  label?: string;
  className?: string;
  "data-testid"?: string;
}) {
  const m = MAP[status];
  return (
    <span data-testid={testId} data-status={status} className={cn("inline-flex items-center gap-1.5 text-small font-medium", m.cls, className)}>
      <span aria-hidden className={cn("inline-block w-3 text-center", m.pulse && "dl-pulse")}>
        {m.icon}
      </span>
      <span>{label ?? m.label}</span>
    </span>
  );
}

export const JobStatusBadge = ({ status }: { status: JobStatus }) => <StatusPill status={status} />;
