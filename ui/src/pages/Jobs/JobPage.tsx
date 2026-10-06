import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Download, RotateCcw, Square, Trash2 } from "lucide-react";
import { cancelJob, deleteJob, getJob, getTelemetry, jobEvents, jobKeys, jobLogTxtUrl } from "@/api/jobs";
import type { Job, JobProgress, JobStatus, Stage, TelemetrySample } from "@/api/types";
import { Badge } from "@/components/Badge";
import { Button } from "@/components/Button";
import { Callout } from "@/components/Callout";
import { Card, CardBody, CardHeader } from "@/components/Card";
import { CodeBlock, CodeInline, useCopy } from "@/components/Code";
import { ConfirmDialog } from "@/components/Confirm";
import { ErrorState } from "@/components/ErrorState";
import { useAnnounce } from "@/components/LiveRegion";
import { JobLogViewer } from "@/components/LogViewer";
import { PageHeader } from "@/components/PageHeader";
import { Skeleton } from "@/components/Skeleton";
import { StageTimeline } from "@/components/StageTimeline";
import { StatusPill } from "@/components/StatusPill";
import { TabPanel, Tabs, useTabParam } from "@/components/Tabs";
import { useToast } from "@/components/Toast";
import { useEventSource } from "@/hooks/useEventSource";
import { useMeta } from "@/hooks/useMeta";
import { fmtAbsolute, fmtClock, fmtDuration, fmtInt, fmtRate, fmtUsd } from "@/lib/format";
import { useRegisterCommands } from "@/palette/registry";
import { OverflowMenu } from "../Labs/Menu";
import { RunProgressTable } from "./RunProgress";
import { TelemetryPanel } from "./TelemetryPanel";

const ACTIVE: JobStatus[] = ["queued", "running", "cancelling"];
const REMOTE = new Set(["ssh", "aws", "vast"]);

function failedModels(job: Job): string[] {
  const fromRuns = job.progress.runs.filter((r) => r.status === "failed").map((r) => r.model);
  const fromFailures = job.failures.map((f) => f.split(":")[0].split(" / ")[0].trim()).filter((m) => m && !m.includes(" "));
  const known = new Set(job.progress.runs.map((r) => r.model));
  return [...new Set([...fromRuns, ...fromFailures])].filter((m) => known.has(m) || fromRuns.includes(m));
}

export default function JobPage() {
  const { jobId = "" } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const announce = useAnnounce();
  const [copy] = useCopy();
  const [tab, setTab] = useTabParam("progress");
  const [cancelOpen, setCancelOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const job = useQuery({
    queryKey: jobKeys.one(jobId),
    queryFn: () => getJob(jobId),
    refetchInterval: (q) => (q.state.data && ACTIVE.includes(q.state.data.status) ? 4000 : false),
  });
  const j = job.data;
  const active = !!j && ACTIVE.includes(j.status);
  const tele = useQuery({
    queryKey: jobKeys.telemetry(jobId),
    queryFn: () => getTelemetry(jobId),
    enabled: !!j && (j.kind === "run" || j.kind === "eval"),
    refetchInterval: active ? 5000 : false,
  });

  // live: stages, progress, telemetry and the final status over SSE (logs have their own stream)
  const patch = (fn: (j: Job) => Job) => qc.setQueryData<Job>(jobKeys.one(jobId), (old) => (old ? fn(old) : old));
  useEventSource(
    () => jobEvents(jobId),
    {
      snapshot: (d: { job: Job }) => qc.setQueryData(jobKeys.one(jobId), d.job),
      stage: (d: { stage: Stage }) => patch((o) => ({ ...o, stages: o.stages.map((s) => (s.name === d.stage.name ? d.stage : s)) })),
      progress: (d: { progress: JobProgress }) => patch((o) => ({ ...o, progress: d.progress })),
      telemetry: (d: { sample: TelemetrySample }) =>
        qc.setQueryData(jobKeys.telemetry(jobId), (old: typeof tele.data) => (old ? { ...old, samples: [...old.samples, d.sample].slice(-2000) } : old)),
      status: () => {
        void qc.invalidateQueries({ queryKey: jobKeys.one(jobId) });
        void qc.invalidateQueries({ queryKey: jobKeys.telemetry(jobId) });
        void qc.invalidateQueries({ queryKey: ["jobs", "list"] });
      },
    },
    { enabled: active, deps: [jobId, active] },
  );

  // announce the final status politely
  const lastStatus = useRef<JobStatus | null>(null);
  useEffect(() => {
    if (!j) return;
    if (lastStatus.current && lastStatus.current !== j.status) announce(`${j.title} ${j.status}`);
    lastStatus.current = j.status;
  }, [j?.status, j?.title, announce, j]);

  const failed = useMemo(() => (j ? failedModels(j) : []), [j]);
  const rerunHref = j?.lab_id ? `/labs/${j.lab_id}?run=1` : null;
  useRegisterCommands(
    "job-page",
    j
      ? [
          ...(active ? [{ id: "job.cancel", label: `Cancel ${j.title}…`, section: "Jobs" as const, run: () => setCancelOpen(true) }] : []),
          { id: "job.copyCommand", label: "Copy job command", section: "Jobs" as const, run: () => void copy(j.command ?? j.argv.join(" ")) },
        ]
      : [],
    [j?.job_id, active],
  );

  if (job.isLoading) {
    return (
      <div data-testid="page-job" aria-busy="true">
        <Skeleton width={320} height={28} shape="block" className="mb-3" />
        <Skeleton width="30%" className="mb-6" />
        <Skeleton shape="block" height={260} />
      </div>
    );
  }
  if (job.isError || !j) {
    return (
      <div data-testid="page-job">
        <PageHeader title="Job" breadcrumbs={[{ label: "Jobs", to: "/jobs" }, { label: jobId }]} />
        <ErrorState error={job.error} onRetry={() => job.refetch()} />
      </div>
    );
  }

  const remote = REMOTE.has(j.backend);
  const machine = j.progress.machine;
  const cancelling = j.status === "cancelling";
  const mayStillRun = j.failures.some((f) => f.includes("may still be running")) || j.stages.some((s) => s.name === "release" && s.status === "failed");
  const resultRoot = (j.result?.root_id as string | undefined) ?? j.root_id;
  const reportMd = j.result?.report_md as string | undefined;

  return (
    <div data-testid="page-job">
      <PageHeader
        title={j.title}
        status={<StatusPill status={j.status} data-testid="job-status" />}
        breadcrumbs={[{ label: "Jobs", to: "/jobs" }, { label: j.job_id }]}
        description={
          <span className="tnum">
            {j.started_at ? `started ${fmtClock(j.started_at)}` : "not started yet"}
            {j.duration_s != null ? ` · ${fmtDuration(j.duration_s)}` : ""} · {j.kind} on {j.backend}
            {j.cost_so_far_usd != null && <> · ≈ {fmtUsd(j.cost_so_far_usd)} so far (estimate)</>}
          </span>
        }
        actions={
          <>
            {active && (
              <Button
                variant="danger"
                icon={Square}
                disabled={cancelling}
                onClick={() => setCancelOpen(true)}
                data-testid="job-cancel"
                aria-label={cancelling ? undefined : `Cancel job ${j.title}`}
              >
                {cancelling ? (remote ? "Cancelling… releasing machine" : "Cancelling…") : j.kind === "run" ? "Cancel run" : "Cancel job"}
              </Button>
            )}
            <OverflowMenu
              label={`More actions for ${j.title}`}
              data-testid="job-more"
              items={[
                ...(rerunHref && j.kind === "run" ? [{ id: "rerun", label: "Re-run with same settings…", icon: RotateCcw, testId: "job-rerun", onSelect: () => nav(rerunHref) }] : []),
                ...(rerunHref && failed.length && !active
                  ? [{ id: "rerun-failed", label: "Re-run failed models…", icon: RotateCcw, onSelect: () => nav(`${rerunHref}&only=${failed.join(",")}`) }]
                  : []),
                {
                  id: "copy",
                  label: "Copy command",
                  icon: Copy,
                  testId: "job-copy-command",
                  onSelect: () => {
                    void copy(j.command ?? j.argv.join(" "));
                    toast({ title: "Command copied" });
                  },
                },
                { id: "log", label: "Download log", icon: Download, testId: "job-download-log", onSelect: () => window.open(jobLogTxtUrl(j.job_id), "_blank", "noopener") },
                ...(!active ? [{ id: "delete", label: "Delete record…", icon: Trash2, danger: true, testId: "job-delete", onSelect: () => setDeleteOpen(true) }] : []),
              ]}
            />
          </>
        }
      />

      <div className="mb-4 flex flex-col gap-3">
        {j.status === "lost" && (
          <Callout tone="warning" title="Studio stopped while this job was running." data-testid="job-lost-callout">
            Its log up to then is kept; the job's process is gone.
            {remote && (
              <span className="mt-1 block text-danger">
                A machine may still be billing.{" "}
                <Link to={`/compute?tab=${j.backend === "aws" ? "aws" : "vast"}${j.machine_id ? `&q=${encodeURIComponent(j.machine_id)}` : ""}`}>Open Compute</Link>
              </span>
            )}
          </Callout>
        )}
        {mayStillRun && j.status !== "lost" && (
          <Callout tone="danger" title="The machine may still be running" data-testid="job-machine-callout">
            Release it in <Link to={`/compute?tab=${j.backend === "aws" ? "aws" : "vast"}`}>Compute</Link>
            {j.machine_id ? ` (${j.machine_id})` : ""}.
          </Callout>
        )}
        {(j.status === "partial" || j.status === "failed") && j.failures.length > 0 && (
          <Callout
            tone="danger"
            title={j.status === "partial" ? "Finished; some models failed" : "This job failed"}
            data-testid="job-failures"
            actions={
              rerunHref && failed.length > 0 ? (
                <Button size="sm" icon={RotateCcw} onClick={() => nav(`${rerunHref}&only=${failed.join(",")}`)} data-testid="job-rerun-failed">
                  Re-run failed models…
                </Button>
              ) : undefined
            }
          >
            <ul className="list-disc pl-4">
              {j.failures.map((f) => (
                <li key={f} className="break-words font-mono text-caption">
                  {f}
                </li>
              ))}
            </ul>
          </Callout>
        )}
      </div>

      <Tabs
        label="Job sections"
        value={tab}
        onChange={setTab}
        testIdPrefix="job-tab"
        className="mb-5"
        tabs={[
          { id: "progress", label: "Progress" },
          { id: "logs", label: "Logs", count: j.log.lines || undefined },
          { id: "telemetry", label: "Telemetry", disabled: !(j.kind === "run" || j.kind === "eval") },
          { id: "command", label: "Command" },
        ]}
      />
      <TabPanel id="progress" active={tab === "progress"}>
        <div className="flex flex-col gap-4">
          <Card>
            <CardHeader title="Stages" as="h2" />
            <CardBody>
              <StageTimeline stages={j.stages} />
              {machine && (
                <p className="tnum mt-4 text-small text-muted" data-testid="job-machine">
                  machine{" "}
                  <span className="text-text">
                    {machine.provider} {machine.provider === "aws" ? "instance" : machine.provider === "vast" ? "instance" : "host"} {machine.id ?? "…"}
                  </span>
                  {machine.gpu ? ` · ${machine.gpu}` : ""}
                  {machine.target ? ` · ${machine.target}` : ""}
                  {machine.usd_per_hour != null ? ` · ${fmtRate(machine.usd_per_hour)} (cost rate, est.)` : ""}
                  {machine.cost_so_far_usd != null ? ` · ≈ ${fmtUsd(machine.cost_so_far_usd)} so far (estimate)` : ""}
                </p>
              )}
              {j.progress.bytes && (
                <p className="tnum mt-3 text-small text-muted">
                  {fmtInt(Math.round(j.progress.bytes.done / 1e6))} MB{j.progress.bytes.total ? ` of ${fmtInt(Math.round(j.progress.bytes.total / 1e6))} MB` : ""}
                </p>
              )}
            </CardBody>
          </Card>
          {j.progress.finetune && j.progress.finetune.length > 0 && (
            <Card>
              <CardHeader title="Fine-tune" as="h2" />
              <CardBody>
                <ul className="flex flex-col gap-1 text-small">
                  {j.progress.finetune.map((f) => (
                    <li key={f.model} data-testid={`job-finetune-${f.model}`}>
                      <span className="font-semibold">{f.model}</span>{" "}
                      <span className="text-muted">
                        {f.status}
                        {f.step != null ? ` · step ${f.step}/${f.total_steps ?? "?"}` : f.status === "running" ? " · training… (see logs)" : ""}
                        {f.loss != null ? ` · loss ${f.loss.toFixed(3)}` : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              </CardBody>
            </Card>
          )}
          {j.progress.runs.length > 0 && (
            <Card>
              <CardHeader title="Runs" as="h2" description={j.progress.label} />
              <CardBody className="px-0 sm:px-0">
                <RunProgressTable progress={j.progress} />
              </CardBody>
            </Card>
          )}
          {(j.kind === "run" || j.kind === "eval") && (
            <Card>
              <CardBody>
                <TelemetryPanel data={tele.data} loading={tele.isLoading} error={tele.error} onRetry={() => tele.refetch()} only="rows" />
              </CardBody>
            </Card>
          )}
          {!active && (
            <Card data-testid="job-result">
              <CardHeader title="Result" as="h2" />
              <CardBody className="flex flex-wrap items-center gap-3 text-small">
                {resultRoot ? (
                  <>
                    <Link to={`/results/${resultRoot}`} className="font-semibold" data-testid="job-result-link">
                      Open results →
                    </Link>
                    {reportMd && <CodeInline code={reportMd} copy />}
                  </>
                ) : j.result ? (
                  <CodeBlock code={JSON.stringify(j.result, null, 2)} language="json" className="w-full" />
                ) : (
                  <span className="text-muted">No results were written.</span>
                )}
              </CardBody>
            </Card>
          )}
        </div>
      </TabPanel>
      <TabPanel id="logs" active={tab === "logs"}>
        <JobLogViewer jobId={j.job_id} height="min(62vh, 640px)" />
      </TabPanel>
      <TabPanel id="telemetry" active={tab === "telemetry"}>
        <Card>
          <CardBody>
            <TelemetryPanel data={tele.data} loading={tele.isLoading} error={tele.error} onRetry={() => tele.refetch()} />
          </CardBody>
        </Card>
      </TabPanel>
      <TabPanel id="command" active={tab === "command"}>
        <CommandTab job={j} />
      </TabPanel>

      <ConfirmDialog
        open={cancelOpen}
        onOpenChange={setCancelOpen}
        title={j.kind === "run" ? "Cancel this run?" : "Cancel this job?"}
        confirmLabel={j.kind === "run" ? "Cancel run" : "Cancel job"}
        tone="danger"
        body={remote ? "A remote machine is released before the job stops. Results written so far are kept." : "The job stops; results written so far are kept, and a later run resumes from them."}
        onConfirm={async () => {
          const r = await cancelJob(j.job_id);
          patch((o) => ({ ...o, status: r.status as JobStatus }));
          void qc.invalidateQueries({ queryKey: jobKeys.all });
        }}
      />
      <ConfirmDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title="Delete this job record?"
        confirmLabel="Delete record"
        tone="danger"
        phrase="delete"
        body="This removes the job's record and log. Its results are kept."
        onConfirm={async (typed) => {
          await deleteJob(j.job_id, typed);
          await qc.invalidateQueries({ queryKey: jobKeys.all });
          toast({ title: "Job record deleted" });
          nav("/jobs");
        }}
      />
    </div>
  );
}

function CommandTab({ job }: { job: Job }) {
  const meta = useMeta();
  const logPath = meta.data ? `${meta.data.state_dir}/jobs/${job.job_id}/log.txt` : null;
  return (
    <Card>
      <CardBody className="flex flex-col gap-4">
        <div>
          <h2 className="mb-1 text-h3">Command</h2>
          <CodeBlock code={job.command ?? job.argv.join(" ")} language="bash" data-testid="job-command" />
        </div>
        <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-small sm:grid-cols-[180px,1fr]">
          <dt className="text-muted">Working directory</dt>
          <dd className="break-all font-mono">{job.cwd}</dd>
          <dt className="text-muted">Environment variables passed</dt>
          <dd>
            {job.env_names.length ? (
              <span className="flex flex-wrap gap-1">
                {job.env_names.map((n) => (
                  <Badge key={n}>{n}</Badge>
                ))}
              </span>
            ) : (
              <span className="text-muted">none (values are never shown)</span>
            )}
          </dd>
          <dt className="text-muted">Exit code</dt>
          <dd className="tnum">{job.exit_code ?? "—"}</dd>
          <dt className="text-muted">Created</dt>
          <dd>{fmtAbsolute(job.created_at)}</dd>
          <dt className="text-muted">Started</dt>
          <dd>{fmtAbsolute(job.started_at)}</dd>
          <dt className="text-muted">Ended</dt>
          <dd>{fmtAbsolute(job.ended_at)}</dd>
          <dt className="text-muted">Log file</dt>
          <dd className="break-all font-mono">{logPath ?? "—"}</dd>
          <dt className="text-muted">Run root</dt>
          <dd>{job.root_id ? <Link to={`/results/${job.root_id}`}>Open in Results</Link> : <span className="text-muted">—</span>}</dd>
          <dt className="text-muted">Job id</dt>
          <dd className="font-mono">{job.job_id}</dd>
        </dl>
      </CardBody>
    </Card>
  );
}
