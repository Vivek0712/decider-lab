import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import type { Job, JobStatus } from "@/api/types";
import { getPullJob, modelKeys } from "@/api/models";
import { Callout } from "@/components/Callout";
import { CodeInline } from "@/components/Code";
import { ProgressBar } from "@/components/ProgressBar";
import { StageTimeline } from "@/components/StageTimeline";
import { StatusPill } from "@/components/StatusPill";
import { cn } from "@/lib/cn";

const ACTIVE: JobStatus[] = ["queued", "running", "cancelling"];
export const isActive = (s: JobStatus | undefined) => !!s && ACTIVE.includes(s);

/** Poll one job (GET /api/jobs/:id) every second while it is active; `onDone` fires once when it ends. */
export function useJob(jobId: string | null, onDone?: (job: Job) => void) {
  const done = useRef<string | null>(null);
  const q = useQuery({
    queryKey: modelKeys.job(jobId ?? ""),
    queryFn: () => getPullJob(jobId!),
    enabled: !!jobId,
    refetchInterval: (query) => (isActive(query.state.data?.status) || !query.state.data ? 1000 : false),
    staleTime: 0,
  });
  const job = q.data;
  const cb = useRef(onDone);
  cb.current = onDone;
  useEffect(() => {
    if (job && !isActive(job.status) && done.current !== job.job_id) {
      done.current = job.job_id;
      cb.current?.(job);
    }
  }, [job]);
  return q;
}

/** A started job, inline where the action was: status, progress, stages, failures, link to its page. */
export function JobCard({ jobId, onDone, compact, className }: { jobId: string; onDone?: (job: Job) => void; compact?: boolean; className?: string }) {
  const { data: job, error } = useJob(jobId, onDone);
  if (error) {
    return (
      <Callout tone="warning" className={className}>
        Could not read the job. <Link to={`/jobs/${jobId}`}>Open it on the Jobs page</Link>.
      </Callout>
    );
  }
  const progress = job?.progress;
  const bytes = progress?.bytes;
  return (
    <div data-testid={`job-card-${jobId}`} className={cn("rounded-md border border-border bg-surface-2 p-3", className)} aria-live="polite">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          {job ? <StatusPill status={job.status} data-testid="job-card-status" /> : <span className="text-small text-muted">starting…</span>}
          <span className="min-w-0 truncate text-small font-medium" title={job?.title}>
            {job?.title ?? jobId}
          </span>
        </div>
        <Link to={`/jobs/${jobId}`} className="inline-flex items-center gap-1 text-small" data-testid="job-card-link">
          Open job <ExternalLink size={12} aria-hidden />
        </Link>
      </div>
      {job && isActive(job.status) && (
        <ProgressBar
          className="mt-2"
          value={progress?.fraction ?? null}
          indeterminate={progress?.fraction == null}
          label={progress?.label || (bytes ? `${bytes.done} bytes` : "working")}
          showLabel
        />
      )}
      {!compact && job && job.stages.length > 0 && <StageTimeline stages={job.stages} className="mt-3" />}
      {job && job.failures.length > 0 && (
        <Callout tone="danger" className="mt-3" title="The job failed" data-testid="job-card-failures">
          <ul className="list-none p-0">
            {job.failures.map((f, i) => (
              <li key={i} className="break-words">
                {f}
              </li>
            ))}
          </ul>
        </Callout>
      )}
      {!compact && job?.command && (
        <div className="mt-2 text-caption text-subtle">
          <CodeInline code={job.command} copy />
        </div>
      )}
    </div>
  );
}
