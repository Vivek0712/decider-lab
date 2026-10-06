import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Boxes,
  CheckCircle2,
  Circle,
  Download,
  Eye,
  EyeOff,
  FileUp,
  FlaskConical,
  GitCompare,
  Play,
  Plus,
  Stethoscope,
  X,
} from "lucide-react";
import { getOverview, overviewKeys, type LabItem, type Overview, type RecentResult, type RecentRoot } from "@/api/overview";
import { putSettings, systemKeys } from "@/api/system";
import type { CI } from "@/api/types";
import { Badge } from "@/components/Badge";
import { Button, IconButton } from "@/components/Button";
import { Card, CardBody, CardHeader } from "@/components/Card";
import { CodeInline } from "@/components/Code";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { Select } from "@/components/Field";
import { Dialog } from "@/components/Overlay";
import { PageHeader } from "@/components/PageHeader";
import { ProgressBar } from "@/components/ProgressBar";
import { Skeleton } from "@/components/Skeleton";
import { Stat } from "@/components/Stat";
import { StatusPill } from "@/components/StatusPill";
import { DataTable, type Column } from "@/components/Table";
import { useHotkeys } from "@/hooks/useHotkeys";
import { DASH, fmtAbsolute, fmtCI, fmtGbOrDash, fmtInt, fmtIntelligence, fmtPct, fmtRate, fmtRelative, fmtUsd } from "./format";
import { useRegisterCommands } from "@/palette/registry";
import { QuickEvalDialog } from "./QuickEvalDialog";

const PROXY_NOTE =
  "Intelligence (local proxy): JevBench v1.5 rules applied to this suite. It ranks these runs against each other; it is not a JevBench board score.";

// Owner: Overview area. DESIGN.md 4.1; data: GET /api/overview (src/api/overview.ts).
export default function OverviewPage() {
  const [sp, setSp] = useSearchParams();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const quickEval = sp.get("quick-eval") === "1";
  const [runLab, setRunLab] = useState(false);
  const setQuickEval = (open: boolean) =>
    setSp(
      (prev) => {
        const n = new URLSearchParams(prev);
        if (open) n.set("quick-eval", "1");
        else n.delete("quick-eval");
        return n;
      },
      { replace: true },
    );
  const q = useQuery({
    queryKey: overviewKeys.all,
    queryFn: getOverview,
    refetchInterval: (query) => ((query.state.data?.kpis.jobs_active ?? 0) > 0 ? 2_000 : 15_000),
  });
  useHotkeys([{ keys: "e", handler: () => setQuickEval(true) }]);
  useRegisterCommands(
    "overview-page",
    [{ id: "eval.quick", label: "Quick eval…", section: "Commands", icon: FlaskConical, shortcut: ["e"], run: ({ close }) => { close(); setQuickEval(true); } }],
    [],
  );
  const data = q.data;
  const showOnboarding = !!data && !data.onboarding.dismissed && data.kpis.labs === 0 && data.kpis.run_roots === 0;

  return (
    <div data-testid="page-overview">
      <PageHeader
        title="Overview"
        description="What is running, what finished, and what to do next."
        actions={
          <>
            <Button icon={FlaskConical} kbd={["e"]} onClick={() => setQuickEval(true)} data-testid="quick-eval">
              Quick eval
            </Button>
            <Button variant="primary" icon={Plus} onClick={() => navigate("/labs/new")} data-testid="overview-new-lab">
              New lab
            </Button>
          </>
        }
      />
      {q.error ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : !data ? (
        <LoadingShape />
      ) : (
        <div className="flex flex-col gap-5">
          {showOnboarding ? (
            <Onboarding
              data={data}
              onDismiss={async () => {
                await putSettings({ onboarding_dismissed: true });
                void qc.invalidateQueries({ queryKey: systemKeys.settings });
                void qc.invalidateQueries({ queryKey: overviewKeys.all });
              }}
            />
          ) : (
            <Kpis data={data} />
          )}
          <div className="grid gap-5 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
            <ActiveJobs data={data} />
            <QuickActions onQuickEval={() => setQuickEval(true)} onRunLab={() => setRunLab(true)} hasLabs={data.labs.length > 0} />
          </div>
          <RecentResults data={data} onRunLab={() => setRunLab(true)} />
          {data.recent_roots.length > 0 && <RecentRoots roots={data.recent_roots} />}
        </div>
      )}
      <QuickEvalDialog open={quickEval} onOpenChange={setQuickEval} />
      <RunLabDialog open={runLab} onOpenChange={setRunLab} labs={data?.labs ?? []} />
    </div>
  );
}

function LoadingShape() {
  return (
    <div aria-busy="true" aria-label="Loading overview" className="flex flex-col gap-5">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {Array.from({ length: 8 }, (_, i) => (
          <Skeleton key={i} shape="block" height={108} />
        ))}
      </div>
      <Skeleton shape="block" height={200} />
    </div>
  );
}

// ---- KPIs --------------------------------------------------------------------------------------

function Kpis({ data }: { data: Overview }) {
  const k = data.kpis;
  const best = k.best;
  const cloud = k.cloud;
  const [reveal, setReveal] = useState(false);
  const account = cloud.aws?.account ?? null;
  const rate = !cloud.known ? DASH : cloud.errors.length ? "?" : fmtRate(cloud.usd_per_hour);
  const rateSub = !cloud.known
    ? "No cloud backend configured"
    : cloud.errors.length
      ? `Could not read ${cloud.errors.map((e) => e.split(":")[0]).join(", ")}`
      : `${fmtInt(cloud.instances)} ${cloud.instances === 1 ? "instance" : "instances"}${cloud.idle_instances ? ` · ⚠ ${cloud.idle_instances} idle` : ""}`;
  return (
    <section aria-label="Key numbers" className="grid grid-cols-1 gap-3 min-[420px]:grid-cols-2 md:grid-cols-4" data-testid="kpis">
      <Stat
        label="Labs"
        value={fmtInt(k.labs)}
        sub={k.labs_invalid ? `${k.labs_invalid} invalid` : "all valid"}
        tone={k.labs_invalid ? "warning" : "neutral"}
        href={k.labs_invalid ? "/labs?invalid=1" : "/labs"}
        data-testid="kpi-labs"
      />
      <Stat
        label="Runs scored"
        value={fmtInt(k.runs_scored)}
        sub={`${fmtInt(k.run_roots)} run ${k.run_roots === 1 ? "root" : "roots"}${k.runs_with_errors ? ` · ${k.runs_with_errors} with errors` : ""}`}
        href="/results"
        data-testid="kpi-runs"
      />
      <Stat
        label={best ? `Top on ${best.suite}` : "Top run"}
        value={best ? <>{fmtIntelligence(best.intelligence)} <span className="text-small text-muted">(proxy)</span></> : DASH}
        sub={
          best
            ? best.tied_with > 0
              ? `${best.model} · ≈ tied with ${best.tied_with} ${best.tied_with === 1 ? "other" : "others"}`
              : `${best.model} · 95% CI ${fmtCI(best.ci95 as CI)}`
            : "No scored non-smoke suite yet"
        }
        title="Intelligence (local proxy). Only comparable with runs on the same suite and rows."
        href={best ? `/results/${best.root_id}` : "/results"}
        data-testid="kpi-top"
      />
      <Stat
        label="Active jobs"
        value={fmtInt(k.jobs_active)}
        sub={`${k.jobs_active_remote ? `${k.jobs_active_remote} remote` : "none remote"}${k.jobs_queued ? ` · ${k.jobs_queued} queued` : ""}`}
        tone={k.jobs_active ? "accent" : "neutral"}
        href="/jobs?status=active"
        data-testid="kpi-jobs"
      />
      <Stat
        label="Cost rate (est.)"
        value={rate}
        sub={rateSub}
        tone={cloud.idle_instances ? "warning" : "neutral"}
        title="Sum of the hourly rates of running decider-lab machines on vast.ai and AWS. A rate, not money spent."
        href={cloud.idle_instances ? "/compute?idle=1" : "/compute"}
        data-testid="kpi-spend"
      />
      <Stat label="Cached models" value={fmtInt(k.cached_models)} sub={fmtGbOrDash(k.cached_models_gb)} href="/models" data-testid="kpi-models" />
      <Stat
        label="vast.ai credit"
        value={cloud.vast?.configured && cloud.vast.credit_usd != null ? fmtUsd(cloud.vast.credit_usd) : DASH}
        sub={cloud.vast?.configured ? (cloud.vast.error ? cloud.vast.error : `${fmtInt(cloud.vast.instances ?? 0)} running · ${fmtRate(cloud.vast.usd_per_hour ?? null)}`) : "vastai CLI not set up"}
        href="/compute?tab=vast"
        data-testid="kpi-vast"
      />
      <div className="relative" data-testid="kpi-aws">
        <Stat
          label="AWS account"
          value={account ? (reveal ? account : `••••${account.slice(-4)}`) : DASH}
          sub={cloud.aws?.configured ? (cloud.aws.error ? cloud.aws.error : `${cloud.aws.profile ?? "default"} · ${cloud.aws.region ?? "us-east-1"} · ${fmtInt(cloud.aws.instances ?? 0)} running`) : "No AWS credentials found"}
          href="/compute?tab=aws"
        />
        {account && (
          <IconButton
            icon={reveal ? EyeOff : Eye}
            size="sm"
            label={reveal ? "Hide the AWS account id" : "Show the AWS account id"}
            onClick={() => setReveal(!reveal)}
            className="absolute right-2 top-2"
            data-testid="kpi-aws-reveal"
          />
        )}
      </div>
      {cloud.fake && <p className="col-span-full -mt-1 text-caption text-subtle">Cloud numbers come from fixtures: cloud calls return fixtures. Nothing is created or billed.</p>}
    </section>
  );
}

// ---- onboarding --------------------------------------------------------------------------------

function Onboarding({ data, onDismiss }: { data: Overview; onDismiss: () => void }) {
  const ob = data.onboarding;
  const firstLab = data.labs[0];
  const steps = [
    { n: 1, title: "Check this machine", body: "decider-lab doctor: Python, GPU, extras and cloud tools", done: ob.doctor_seen, action: <Link to="/compute?tab=local">Open doctor</Link> },
    { n: 2, title: "Create a lab", body: "From the eval or finetune template", done: ob.has_lab, action: <Link to="/labs/new">New lab</Link> },
    {
      n: 3,
      title: "Run it",
      body: "smoke takes seconds, no GPU needed",
      done: ob.has_run,
      action: firstLab ? <Link to={`/labs/${firstLab.lab_id}?run=1`}>Run</Link> : <span className="text-subtle" aria-disabled="true">Run (create a lab first)</span>,
    },
    { n: 4, title: "Read the results", body: "Leaderboard, CIs, vs baseline", done: ob.has_results, action: ob.has_results ? <Link to="/results">Open results</Link> : <span className="text-subtle" aria-disabled="true">Open results</span> },
  ];
  return (
    <Card data-testid="onboarding">
      <CardHeader
        title="Welcome to decider-lab Studio"
        description="Four steps from an empty folder to a leaderboard. Each step checks itself off from what is on disk."
        actions={<IconButton icon={X} label="Dismiss the getting-started checklist" onClick={onDismiss} data-testid="onboarding-dismiss" />}
      />
      <CardBody className="p-0 sm:p-0">
        <ol className="m-0 flex list-none flex-col divide-y divide-border p-0">
          {steps.map((s) => (
            <li key={s.n} className="flex flex-wrap items-center gap-3 px-4 py-3 sm:px-5" data-testid={`onboarding-step-${s.n}`} data-done={s.done ? "true" : "false"}>
              {s.done ? <CheckCircle2 size={20} className="shrink-0 text-success" aria-hidden /> : <Circle size={20} className="shrink-0 text-subtle" aria-hidden />}
              <div className="min-w-0 flex-1">
                <div className="font-semibold">
                  {s.n}. {s.title} {s.done && <span className="sr-only">(done)</span>}
                </div>
                <div className="text-small text-muted">{s.body}</div>
              </div>
              <div className="text-small font-semibold">{s.action}</div>
            </li>
          ))}
        </ol>
      </CardBody>
    </Card>
  );
}

// ---- jobs and quick actions --------------------------------------------------------------------

function ActiveJobs({ data }: { data: Overview }) {
  return (
    <Card data-testid="active-jobs">
      <CardHeader title="Active jobs" actions={<Link to="/jobs" className="text-small">View all →</Link>} />
      <CardBody>
        {data.active_jobs.length === 0 ? (
          <p className="text-small text-muted">No jobs running. Runs, pulls and evals you start show here with their progress.</p>
        ) : (
          <ul className="m-0 flex list-none flex-col gap-3 p-0">
            {data.active_jobs.map((j) => (
              <li key={j.job_id} className="flex min-w-0 flex-col gap-1" data-testid={`active-job-${j.job_id}`}>
                <div className="flex min-w-0 items-center gap-2">
                  <StatusPill status={j.status} />
                  <Link to={`/jobs/${j.job_id}`} className="min-w-0 truncate text-small font-medium">
                    {j.title}
                  </Link>
                  {j.backend !== "local" && <Badge tone="info">{j.backend}</Badge>}
                </div>
                <ProgressBar value={j.progress.fraction} indeterminate={j.progress.fraction == null} label={j.progress.label || j.status} showLabel />
              </li>
            ))}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}

function QuickActions({ onQuickEval, onRunLab, hasLabs }: { onQuickEval: () => void; onRunLab: () => void; hasLabs: boolean }) {
  const navigate = useNavigate();
  const items: { id: string; label: string; icon: typeof Plus; onClick: () => void; disabled?: boolean }[] = [
    { id: "new-lab", label: "New lab", icon: Plus, onClick: () => navigate("/labs/new") },
    { id: "run-lab", label: "Run a lab…", icon: Play, onClick: onRunLab, disabled: !hasLabs },
    { id: "quick-eval", label: "Quick eval (smoke)…", icon: FlaskConical, onClick: onQuickEval },
    { id: "pull", label: "Pull a model…", icon: Download, onClick: () => navigate("/models?tab=pull") },
    { id: "csv", label: "Upload CSV…", icon: FileUp, onClick: () => navigate("/data?tab=csv") },
    { id: "compare", label: "Compare two runs…", icon: GitCompare, onClick: () => navigate("/results/compare") },
    { id: "doctor", label: "Check this machine", icon: Stethoscope, onClick: () => navigate("/compute?tab=local") },
    { id: "models", label: "Model cache", icon: Boxes, onClick: () => navigate("/models") },
  ];
  return (
    <Card data-testid="quick-actions">
      <CardHeader title="Quick actions" />
      <CardBody className="grid grid-cols-1 gap-2 min-[420px]:grid-cols-2">
        {items.map((it) => (
          <Button key={it.id} variant="ghost" icon={it.icon} onClick={it.onClick} disabled={it.disabled} className="justify-start" data-testid={`qa-${it.id}`}>
            {it.label}
          </Button>
        ))}
      </CardBody>
    </Card>
  );
}

function RunLabDialog({ open, onOpenChange, labs }: { open: boolean; onOpenChange: (o: boolean) => void; labs: LabItem[] }) {
  const navigate = useNavigate();
  const valid = labs.filter((l) => l.valid);
  const [labId, setLabId] = useState("");
  const chosen = valid.find((l) => l.lab_id === labId) ?? valid[0];
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Run a lab"
      description="Pick a lab; its Run dialog shows where it runs, the plan and any cost before anything starts."
      size="sm"
      data-testid="run-lab-dialog"
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Close
          </Button>
          <Button
            variant="primary"
            icon={Play}
            disabled={!chosen}
            onClick={() => {
              if (!chosen) return;
              onOpenChange(false);
              navigate(`/labs/${chosen.lab_id}?run=1`);
            }}
            data-testid="run-lab-open"
          >
            Open run dialog
          </Button>
        </>
      }
    >
      {valid.length === 0 ? (
        <EmptyState title="No valid lab" body="Create a lab, or fix the one you have, first." action={<Link to="/labs/new">New lab</Link>} />
      ) : (
        <div className="flex flex-col gap-3">
          <Select
            label="Lab"
            value={chosen?.lab_id ?? ""}
            onChange={setLabId}
            data-testid="run-lab-select"
            options={valid.map((l) => ({ value: l.lab_id, label: `${l.name} (${l.path})` }))}
          />
          {chosen && (
            <div className="text-small text-muted">
              From a terminal: <CodeInline code={`decider-lab run ${chosen.path}`} copy />
            </div>
          )}
        </div>
      )}
    </Dialog>
  );
}

// ---- results -----------------------------------------------------------------------------------

function RecentResults({ data, onRunLab }: { data: Overview; onRunLab: () => void }) {
  const navigate = useNavigate();
  const rows = data.recent_results;
  const cols: Column<RecentResult>[] = [
    { id: "lab", header: "Lab", cell: (r) => <span className="font-medium">{r.lab}</span> },
    {
      id: "model",
      header: "Model",
      cell: (r) => (
        <span className="inline-flex items-center gap-1.5">
          {r.model}
          {r.is_baseline && <Badge>baseline</Badge>}
        </span>
      ),
    },
    { id: "suite", header: "Suite", cell: (r) => <span className="font-mono text-mono">{r.suite || DASH}</span> },
    {
      id: "intel",
      header: "Intelligence (proxy) · 95% CI",
      label: "Intelligence (proxy)",
      cell: (r) =>
        r.status === "failed" ? (
          <span className="text-danger" title={r.message ?? undefined}>
            ✕ failed: {(r.message ?? "").slice(0, 80)}
          </span>
        ) : (
          <span className="tnum whitespace-nowrap">
            <span className="font-semibold">{fmtIntelligence(r.intelligence)}</span>{" "}
            <span className="text-muted" title="95% bootstrap confidence interval over rows (1,000 resamples)">
              {fmtCI(r.ci95)}
            </span>
          </span>
        ),
    },
    { id: "acc", header: "Accuracy", align: "right", hideBelow: "md", cell: (r) => fmtPct(r.accuracy) },
    { id: "n", header: "Rows", align: "right", hideBelow: "md", cell: (r) => fmtInt(r.n) },
    {
      id: "errors",
      header: "Errors",
      align: "right",
      cell: (r) =>
        r.status === "failed" ? <span className="text-danger">✕</span> : r.errors ? <span className="text-warning">{r.errors} ⚠</span> : fmtInt(r.errors),
    },
    { id: "when", header: "When", hideBelow: "sm", cell: (r) => <span title={fmtAbsolute(r.finished_at)}>{fmtRelative(r.finished_at)}</span> },
  ];
  return (
    <Card>
      <CardHeader title="Recent results" description="Newest first. Rows mix suites, so they are never ranked against each other here." />
      <CardBody className="p-0 sm:p-0">
        <DataTable
          caption="Recent results"
          columns={cols}
          rows={rows}
          rowKey={(r) => `${r.root_id}-${r.model}-${r.suite}-${r.status}`}
          rowTestId={(r) => `recent-row-${r.model}-${r.suite || "failed"}`}
          onRowClick={(r) => (r.status === "ok" ? navigate(`/results/${r.root_id}/${encodeURIComponent(r.model)}/${encodeURIComponent(r.suite)}`) : navigate(`/results/${r.root_id}`))}
          mobile={rows.length ? "cards" : "scroll"}
          data-testid="recent-results"
          empty={
            <EmptyState
              title="No scored runs yet"
              body="Run a lab to see results here."
              action={
                <Button variant="primary" icon={Play} onClick={onRunLab} disabled={data.labs.length === 0}>
                  Run a lab…
                </Button>
              }
              command="decider-lab run lab.yaml"
            />
          }
        />
        <p className="border-t border-border px-4 py-3 text-caption text-subtle sm:px-5" data-testid="proxy-notice">
          {PROXY_NOTE}
        </p>
      </CardBody>
    </Card>
  );
}

function RecentRoots({ roots }: { roots: RecentRoot[] }) {
  return (
    <Card data-testid="recent-roots">
      <CardHeader title="Recent run roots" description="The top non-baseline model per suite, with its 95% CI. Compare within a suite only." />
      <CardBody className="grid gap-3 md:grid-cols-2">
        {roots.map((r) => (
          <Link
            key={r.root_id}
            to={`/results/${r.root_id}`}
            className="block min-w-0 rounded-md border border-border bg-surface-2 p-3 text-text no-underline hover:border-border-strong"
            data-testid={`recent-root-${r.title}`}
          >
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <span className="font-semibold">{r.title}</span>
              <span className="text-caption text-muted" title={fmtAbsolute(r.finished_at)}>
                {fmtRelative(r.finished_at)}
              </span>
            </div>
            <div className="truncate font-mono text-caption text-subtle">{r.path}</div>
            {r.failures > 0 && <div className="mt-1 text-small text-danger">✕ {r.failures} failed {r.failures === 1 ? "model" : "models"}</div>}
            <dl className="m-0 mt-2 flex flex-col gap-1">
              {r.suites.map((s) => (
                <div key={s.suite} className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 text-small">
                  <dt className="font-mono text-mono">{s.suite}</dt>
                  <dd className="tnum m-0 text-right">
                    {s.top ? (
                      <>
                        <span className="font-semibold">{s.top.model}</span> {fmtIntelligence(s.top.intelligence)}{" "}
                        <span className="text-muted">{fmtCI(s.top.ci95)}</span>
                        {s.top.tied_with > 0 && <span className="text-muted"> · ≈ tied with {s.top.tied_with}</span>}
                        {s.baseline && (
                          <span className="text-subtle">
                            {" "}
                            · {s.baseline.model} {fmtIntelligence(s.baseline.intelligence)}
                          </span>
                        )}
                      </>
                    ) : (
                      <span className="text-muted">baselines only</span>
                    )}
                  </dd>
                </div>
              ))}
            </dl>
          </Link>
        ))}
      </CardBody>
    </Card>
  );
}
