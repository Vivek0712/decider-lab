import type { ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import type { JobSummary } from "@/api/types";
import { ProgressBar } from "@/components/ProgressBar";
import { StatusPill } from "@/components/StatusPill";
import { DataTable, type Column } from "@/components/Table";
import { Tooltip } from "@/components/Tooltip";
import { fmtAbsolute, fmtDuration, fmtRelative, fmtUsd } from "@/lib/format";

const KIND: Record<string, string> = { run: "run", eval: "eval", pull: "pull", jevbench: "jevbench", calibrate: "calibrate", suite_build: "suite build" };

export function JobProgressCell({ j }: { j: JobSummary }) {
  if (j.status === "queued") {
    return (
      <Tooltip content="Waiting for a free slot (max jobs at once is set in Settings > Workspace)">
        <span tabIndex={0} className="text-small text-muted" data-testid="job-queue-pos">
          #{j.queue_position ?? "?"} in queue
        </span>
      </Tooltip>
    );
  }
  const active = j.status === "running" || j.status === "cancelling";
  const label = j.progress.label || (active ? "starting…" : "—");
  if (!active) return <span className="tnum text-small text-muted">{label}</span>;
  return (
    <div className="flex min-w-[140px] items-center gap-2">
      <ProgressBar value={j.progress.fraction} label={label} className="w-[80px] flex-none" />
      <span className="tnum truncate text-small text-muted">{label}</span>
    </div>
  );
}

export function costCell(j: JobSummary): ReactNode {
  if (j.backend !== "vast" && j.backend !== "aws") return <span className="text-muted">—</span>;
  if (j.status === "lost") return <span title="unknown: the job's process is gone">?</span>;
  return j.cost_so_far_usd != null ? <span title="estimate">≈ {fmtUsd(j.cost_so_far_usd)}</span> : <span className="text-muted">—</span>;
}

/** Jobs as a table (Jobs page, a lab's Runs tab); cards below 640 px. */
export function JobsTable({ jobs, loading, empty }: { jobs: JobSummary[]; loading?: boolean; empty?: ReactNode }) {
  const nav = useNavigate();
  const columns: Column<JobSummary>[] = [
    { id: "status", header: "Status", cell: (j) => <StatusPill status={j.status} data-testid="job-status" />, sortValue: (j) => j.status },
    {
      id: "title",
      header: "Title",
      cell: (j) => (
        <Link to={`/jobs/${j.job_id}`} className="font-medium text-text no-underline hover:underline" onClick={(e) => e.stopPropagation()}>
          {j.title}
        </Link>
      ),
      sortValue: (j) => j.title,
    },
    { id: "kind", header: "Kind", cell: (j) => KIND[j.kind] ?? j.kind, sortValue: (j) => j.kind, hideBelow: "sm" },
    { id: "backend", header: "Backend", cell: (j) => j.backend, sortValue: (j) => j.backend, hideBelow: "sm" },
    { id: "progress", header: "Progress", label: "Progress", cell: (j) => <JobProgressCell j={j} /> },
    { id: "cost", header: "Cost ≈", label: "Cost (est.)", align: "right", cell: costCell, sortValue: (j) => j.cost_so_far_usd ?? -1, hideBelow: "md" },
    {
      id: "started",
      header: "Started",
      cell: (j) =>
        j.started_at ? (
          <Tooltip content={fmtAbsolute(j.started_at)}>
            <span tabIndex={0}>{fmtRelative(j.started_at)}</span>
          </Tooltip>
        ) : (
          <span className="text-muted">—</span>
        ),
      sortValue: (j) => j.created_at,
    },
    { id: "duration", header: "Duration", align: "right", cell: (j) => fmtDuration(j.duration_s), sortValue: (j) => j.duration_s ?? -1 },
  ];
  return (
    <DataTable
      caption="Jobs"
      columns={columns}
      rows={jobs}
      rowKey={(j) => j.job_id}
      rowTestId={(j) => `job-row-${j.job_id}`}
      onRowClick={(j) => nav(`/jobs/${j.job_id}`)}
      loading={loading}
      mobile="cards"
      empty={empty}
      data-testid="jobs-table"
    />
  );
}
