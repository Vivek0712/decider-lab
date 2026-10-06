import { useEffect, useState } from "react";
import type { Stage } from "@/api/types";
import { cn } from "@/lib/cn";
import { fmtDuration } from "@/lib/format";

const ICON: Record<Stage["status"], string> = { pending: "○", active: "●", done: "✓", failed: "✕", skipped: "–", warning: "⚠" };
const COLOR: Record<Stage["status"], string> = {
  pending: "text-subtle border-border",
  active: "text-accent border-accent",
  done: "text-success border-success",
  failed: "text-danger border-danger",
  skipped: "text-subtle border-border",
  warning: "text-warning border-warning",
};

function elapsed(s: Stage, now: number): string {
  const a = s.started_at ? Date.parse(s.started_at) : NaN;
  if (Number.isNaN(a)) return "";
  const b = s.ended_at ? Date.parse(s.ended_at) : now;
  return fmtDuration((b - a) / 1000) + (s.status === "active" ? "…" : "");
}

/** Job stages (API.md Stage): horizontal from 640 px, a vertical list below. Test id job-stage-<name>. */
export function StageTimeline({ stages, className }: { stages: Stage[]; className?: string }) {
  const [now, setNow] = useState(Date.now());
  const anyActive = stages.some((s) => s.status === "active");
  useEffect(() => {
    if (!anyActive) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [anyActive]);
  return (
    <ol className={cn("flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-start sm:gap-0", className)} aria-label="Stages">
      {stages.map((s, i) => (
        <li
          key={s.name}
          data-testid={`job-stage-${s.name}`}
          data-status={s.status}
          aria-current={s.status === "active" ? "step" : undefined}
          className="flex min-w-0 items-start gap-2 sm:flex-1 sm:flex-col sm:items-stretch sm:gap-1"
        >
          <div className="flex items-center gap-2 sm:w-full">
            <span
              aria-hidden
              className={cn(
                "inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full border-2 text-caption font-bold",
                COLOR[s.status],
                s.status === "active" && "dl-pulse",
              )}
            >
              {ICON[s.status]}
            </span>
            {i < stages.length - 1 && (
              <span aria-hidden className={cn("hidden h-0.5 flex-1 sm:block", s.status === "done" ? "bg-success" : "bg-border")} />
            )}
          </div>
          <div className="min-w-0 sm:pr-3">
            <div className="text-small font-medium">
              {s.label}
              <span className="sr-only">: {s.status}</span>
            </div>
            <div className="tnum text-caption text-subtle">{elapsed(s, now)}</div>
            {s.detail && <div className={cn("break-words text-caption", s.status === "failed" ? "text-danger" : "text-muted")}>{s.detail}</div>}
          </div>
        </li>
      ))}
    </ol>
  );
}
