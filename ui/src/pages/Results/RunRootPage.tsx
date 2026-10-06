import { useState } from "react";
import { useQuery, useQueries, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { Download, FileText, GitCompare, RefreshCw, Trash2 } from "lucide-react";
import {
  deleteRunRoot, exportUrls, getCalibration, getFamilies, getJevBench, getLeaderboard, getProvenance, getReliability,
  getRootLatency, getRunRoot, getVsBaseline, rebuildReport, resultKeys, type LeaderboardFull, type RunRootFull, type ScoresMode,
} from "@/api/results";
import { chartColor } from "@/charts";
import { Badge } from "@/components/Badge";
import { Button } from "@/components/Button";
import { Callout } from "@/components/Callout";
import { Card, CardBody, CardHeader } from "@/components/Card";
import { CodeInline } from "@/components/Code";
import { ConfirmDialog } from "@/components/Confirm";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { SegmentedControl } from "@/components/Field";
import { PageHeader } from "@/components/PageHeader";
import { Skeleton } from "@/components/Skeleton";
import { DataTable, type Column } from "@/components/Table";
import { TabPanel, Tabs, useTabParam } from "@/components/Tabs";
import { useToast } from "@/components/Toast";
import { fmt3, fmtCI, fmtDuration, fmtIntelligence, fmtLatency, fmtPct, fmtRelative, fmtSigned } from "@/lib/format";
import {
  FamilyHeatmap, ForestPlot, IntelligenceBars, LatencyDotRange, ReliabilityDiagram, verdictOf, verdictSentence,
  type ForestMetric, type IntelBar,
} from "./charts";
import { RowsTab } from "./RowsTab";

const PROXY = "Intelligence is a local proxy computed with the JevBench v1.5 rules on these rows. It ranks runs against each other here; it is not a JevBench board score.";

export default function RunRootPage() {
  const { rootId = "" } = useParams();
  const [sp, setSp] = useSearchParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const [tab, setTab] = useTabParam("leaderboard");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const root = useQuery({ queryKey: resultKeys.root(rootId), queryFn: () => getRunRoot(rootId), enabled: !!rootId });

  const r = root.data;
  const suite = sp.get("suite") ?? r?.suites[0]?.name ?? "";
  const suiteInfo = r?.suites.find((s) => s.name === suite);
  const scores = (sp.get("scores") as ScoresMode) || "raw";
  const set = (k: string, v: string) => { const n = new URLSearchParams(sp); n.set(k, v); setSp(n, { replace: true }); };

  if (root.isError) return <ErrorState error={root.error} onRetry={() => root.refetch()} />;
  if (!r) return <Skeleton className="h-64" />;

  const colorOf = (m: string) => {
    const info = r.models.find((x) => x.name === m);
    return chartColor(info?.color_index ?? 0, !!info?.is_baseline);
  };
  const urls = exportUrls(rootId, suite, scores);
  const tabs = [
    { id: "leaderboard", label: "Leaderboard" },
    { id: "baseline", label: "Vs baseline", disabled: !r.baseline },
    { id: "families", label: "Families" },
    { id: "calibration", label: "Calibration" },
    { id: "latency", label: "Latency" },
    ...(r.jevbench_models.length ? [{ id: "jevbench", label: "JevBench" }] : []),
    { id: "rows", label: "Rows" },
    { id: "provenance", label: "Provenance" },
  ];

  return (
    <div data-testid="page-runroot">
      <PageHeader
        title={r.title || r.lab}
        breadcrumbs={[{ label: "Results", to: "/results" }, { label: r.lab }]}
        description={<>
          {r.models.length} models · {r.suites.length} suites{r.baseline ? <> · baseline <b>{r.baseline}</b></> : null}
          {r.finished_at ? <> · finished {fmtRelative(r.finished_at)}</> : null}
          {r.wall_s ? <> · {fmtDuration(r.wall_s)}</> : null}
        </>}
        actions={
          <div className="flex flex-wrap gap-2" data-testid="export-menu">
            <Button icon={GitCompare} onClick={() => nav("/results/compare")}>Compare</Button>
            {r.report_md_available && <a href={urls.reportMd} download data-testid="export-report-md"><Button icon={FileText} tabIndex={-1}>REPORT.md</Button></a>}
            <a href={urls.reportJson} download data-testid="export-report-json"><Button icon={Download} tabIndex={-1}>report.json</Button></a>
            <a href={urls.leaderboardCsv} download data-testid="export-leaderboard-csv"><Button icon={Download} tabIndex={-1}>CSV</Button></a>
            <Button icon={RefreshCw} data-testid="results-rebuild" onClick={async () => {
              try { await rebuildReport(rootId); await qc.invalidateQueries({ queryKey: resultKeys.all }); toast({ tone: "success", title: "Report rebuilt" }); }
              catch (e) { toast({ tone: "info", title: "Could not rebuild the report", description: String((e as Error).message) }); }
            }}>Rebuild report</Button>
            <Button icon={Trash2} variant="danger" data-testid="results-delete" onClick={() => setConfirmDelete(true)}>Delete</Button>
          </div>
        }
      />
      {r.failures.length > 0 && (
        <Callout tone="danger" title={`${r.failures.length} part(s) of this run failed`} className="mb-4">
          <ul className="list-disc pl-5">{r.failures.map((f) => <li key={f} className="font-mono text-small">{f}</li>)}</ul>
        </Callout>
      )}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <span className="text-small text-muted">Suite</span>
        <div className="flex flex-wrap gap-1" role="group" aria-label="Suite">
          {r.suites.map((s) => (
            <button key={s.name} type="button" data-testid={`suite-chip-${s.name}`} aria-pressed={s.name === suite}
              onClick={() => set("suite", s.name)}
              className={`rounded-full border px-3 py-1 text-small ${s.name === suite ? "border-accent bg-tint-accent text-text" : "border-border text-muted hover:text-text"}`}>
              {s.name}
            </button>
          ))}
        </div>
        {suiteInfo?.has_calibrated && (
          <SegmentedControl label="Scores" value={scores} onChange={(v) => set("scores", v)} data-testid="scores-toggle"
            options={[{ value: "raw", label: "Raw", testId: "scores-toggle-raw" }, { value: "cal", label: "Calibrated", testId: "scores-toggle-cal" },
              { value: "both", label: "Both", testId: "scores-toggle-both" }]} />
        )}
        {suiteInfo && <span className="text-small text-subtle">scored on the <b>{suiteInfo.scored_split}</b> split</span>}
      </div>
      <p className="mb-4 text-small text-muted" data-testid="proxy-notice">{PROXY}</p>
      <Tabs label="Result views" value={tab} onChange={setTab} testIdPrefix="results-tab" tabs={tabs} className="mb-5" />
      <TabPanel id="leaderboard" active={tab === "leaderboard"}><LeaderboardTab rootId={rootId} suite={suite} scores={scores} colorOf={colorOf} /></TabPanel>
      <TabPanel id="baseline" active={tab === "baseline"}><BaselineTab rootId={rootId} suite={suite} baseline={r.baseline} hasCal={!!suiteInfo?.has_calibrated} /></TabPanel>
      <TabPanel id="families" active={tab === "families"}><FamiliesTab rootId={rootId} suite={suite} hasCal={!!suiteInfo?.has_calibrated} /></TabPanel>
      <TabPanel id="calibration" active={tab === "calibration"}><CalibrationTab root={r} suite={suite} colorOf={colorOf} /></TabPanel>
      <TabPanel id="latency" active={tab === "latency"}><LatencyTab rootId={rootId} suite={suite} colorOf={colorOf} /></TabPanel>
      <TabPanel id="jevbench" active={tab === "jevbench"}><JevBenchTab rootId={rootId} /></TabPanel>
      <TabPanel id="rows" active={tab === "rows"}><RowsTab rootId={rootId} suite={suite} /></TabPanel>
      <TabPanel id="provenance" active={tab === "provenance"}><ProvenanceTab rootId={rootId} /></TabPanel>
      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        tone="danger"
        title="Delete this run root?"
        body={<>This deletes <CodeInline code={r.path} /> from disk: predictions, scores and the report. It cannot be undone.</>}
        phrase={`delete ${r.title}`}
        confirmLabel="Delete run root"
        onConfirm={async (typed) => {
          await deleteRunRoot(rootId, typed);
          await qc.invalidateQueries({ queryKey: resultKeys.all });
          toast({ tone: "success", title: `Deleted ${r.lab}` });
          nav("/results");
        }}
      />
    </div>
  );
}

// ---- Leaderboard -----------------------------------------------------------------------------------

function LeaderboardTab({ rootId, suite, scores, colorOf }: { rootId: string; suite: string; scores: ScoresMode; colorOf: (m: string) => string }) {
  const nav = useNavigate();
  const [picked, setPicked] = useState<string[]>([]);
  const q = useQuery({ queryKey: resultKeys.leaderboard(rootId, suite, scores), queryFn: () => getLeaderboard(rootId, suite, scores), enabled: !!suite });
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const lb: LeaderboardFull | undefined = q.data;
  const bars: IntelBar[] = (lb?.rows ?? []).map((row) => ({
    key: `${row.model}:${row.variant}`, label: row.variant === "cal" ? `${row.model} +cal` : row.model,
    value: row.intelligence, ci: row.ci95, color: colorOf(row.model), baseline: !!(row as { is_baseline?: boolean }).is_baseline,
    note: row.status !== "ok" ? row.message ?? row.status : undefined,
  }));
  const cols: Column<LeaderboardFull["rows"][number]>[] = [
    { id: "pick", header: <span className="sr-only">Select for compare</span>, label: "Select", cell: (row) => (
      <input type="checkbox" aria-label={`select ${row.model}`} data-testid={`leaderboard-select-${row.model}`} checked={picked.includes(`${row.model}:${row.variant}`)}
        onClick={(e) => e.stopPropagation()}
        onChange={(e) => { const k = `${row.model}:${row.variant}`; setPicked((p) => e.target.checked ? [...p, k].slice(-2) : p.filter((x) => x !== k)); }} />
    ), width: 36 },
    { id: "model", header: "Model", cell: (row) => (
      <span className="flex items-center gap-2"><span className="inline-block size-2.5 rounded-full" style={{ background: colorOf(row.model) }} aria-hidden />
        <span className="font-medium text-text">{row.model}</span>{row.variant === "cal" && <Badge tone="info">+cal</Badge>}{row.is_baseline && <Badge tone="neutral">baseline</Badge>}
        {row.status !== "ok" && <Badge tone="danger">{row.status}</Badge>}</span>
    ), sortValue: (row) => row.model },
    { id: "intel", header: "Intelligence (95% CI)", cell: (row) => <span className="tabular-nums">{fmtIntelligence(row.intelligence)} <span className="text-subtle">[{fmtCI(row.ci95)}]</span></span>, sortValue: (row) => row.intelligence, align: "right" },
    { id: "acc", header: "Accuracy", cell: (row) => fmtPct(row.accuracy), sortValue: (row) => row.accuracy, align: "right" },
    { id: "nll", header: "NLL", cell: (row) => fmt3(row.nll), sortValue: (row) => row.nll, align: "right", hideBelow: "sm" },
    { id: "ece", header: "ECE", cell: (row) => fmt3(row.ece), sortValue: (row) => row.ece, align: "right", hideBelow: "sm" },
    { id: "band", header: "Yes/no in band", cell: (row) => fmtPct(row.noul_in_band), sortValue: (row) => row.noul_in_band, align: "right", hideBelow: "md" },
    { id: "errors", header: "Errors", cell: (row) => row.errors ? <Badge tone="danger">{row.errors}</Badge> : <span className="text-subtle">0</span>, sortValue: (row) => row.errors, align: "right" },
    { id: "lat", header: "Median latency", cell: (row) => fmtLatency(row.latency_s?.median), sortValue: (row) => row.latency_s?.median ?? null, align: "right", hideBelow: "md" },
  ];
  const sameRows = lb?.same_rows;
  return (
    <div className="flex flex-col gap-5">
      {sameRows && !sameRows.consistent && (
        <Callout tone="warning" title="These models did not score the same rows" data-testid="same-rows-warning">
          Row counts or suite fingerprints differ{sameRows.limited.length ? ` (run with --limit: ${sameRows.limited.join(", ")})` : ""}. Paired comparisons use only the rows both answered.
        </Callout>
      )}
      <Card><CardBody>
        {q.isLoading ? <Skeleton className="h-48" /> : (
          <IntelligenceBars items={bars} domain={lb?.domain ?? [-100, 100]} title={`Intelligence on ${suite}`} caption={lb?.proxy_note ?? PROXY} />
        )}
      </CardBody></Card>
      <div className="flex items-center justify-between">
        <span className="text-small text-muted">Pick two rows to compare them with a paired bootstrap.</span>
        <Button icon={GitCompare} disabled={picked.length !== 2} data-testid="leaderboard-compare" onClick={() => {
          const [a, b] = picked.map((k) => k.split(":"));
          const ref = ([m, v]: string[]) => [rootId, m, v === "cal" ? `${suite}+cal` : suite].map(encodeURIComponent).join(":");
          nav(`/results/compare?a=${encodeURIComponent(ref(a))}&b=${encodeURIComponent(ref(b))}`);
        }}>Compare selected</Button>
      </div>
      <DataTable data-testid="leaderboard" columns={cols} rows={lb?.rows ?? []} rowKey={(row) => `${row.model}:${row.variant}`} loading={q.isLoading}
        defaultSort={{ id: "intel", dir: "desc" }} rowTestId={(row) => `leaderboard-row-${row.model}${row.variant === "cal" ? "-cal" : ""}`}
        onRowClick={(row) => nav(`/results/${encodeURIComponent(rootId)}/${encodeURIComponent(row.model)}/${encodeURIComponent(row.variant === "cal" ? `${suite}+cal` : suite)}`)}
        caption={`Leaderboard for ${suite}`} mobile="cards" />
    </div>
  );
}

// ---- Vs baseline ------------------------------------------------------------------------------------

function BaselineTab({ rootId, suite, baseline, hasCal }: { rootId: string; suite: string; baseline: string | null; hasCal: boolean }) {
  const [metric, setMetric] = useState<ForestMetric>("intelligence");
  const [variant, setVariant] = useState<"raw" | "cal">("raw");
  const q = useQuery({ queryKey: resultKeys.vsBaseline(rootId, suite, variant), queryFn: () => getVsBaseline(rootId, suite, variant), enabled: !!baseline && !!suite });
  if (!baseline) return <EmptyState title="No baseline" body="Set `baseline:` in the lab file to compare every model with one of them, row by row." />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const items = (q.data?.items ?? []).map((it) => ({ model: it.model, diff: it[metric], n_paired: it.n_paired }));
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap gap-3">
        <SegmentedControl label="Metric" value={metric} onChange={setMetric} data-testid="forest-metric"
          options={[{ value: "intelligence", label: "Intelligence" }, { value: "accuracy", label: "Accuracy" }, { value: "nll", label: "NLL" }]} />
        {hasCal && <SegmentedControl label="Scores" value={variant} onChange={setVariant} options={[{ value: "raw", label: "Raw" }, { value: "cal", label: "Calibrated" }]} />}
      </div>
      <Card><CardBody>
        {q.isLoading ? <Skeleton className="h-40" /> : q.data?.message ? <p className="text-muted">{q.data.message}</p> : (
          <ForestPlot items={items} metric={metric} baseline={baseline} />
        )}
      </CardBody></Card>
      <ul className="flex flex-col gap-1 text-small" aria-label="Verdicts">
        {(q.data?.items ?? []).map((it) => {
          const v = verdictOf(it[metric], metric === "nll");
          return <li key={it.model} data-testid={`verdict-${it.model}`}><b>{it.model}</b>: {fmtSigned(it[metric].diff, metric === "nll" ? 3 : 1)} [{fmtCI(it[metric].ci95, metric === "nll" ? 3 : 1)}] on {it.n_paired} paired rows. {verdictSentence(v, baseline, metric === "nll")}</li>;
        })}
      </ul>
    </div>
  );
}

// ---- Families -----------------------------------------------------------------------------------------

function FamiliesTab({ rootId, suite, hasCal }: { rootId: string; suite: string; hasCal: boolean }) {
  const [metric, setMetric] = useState<"intelligence" | "accuracy">("intelligence");
  const [variant, setVariant] = useState<"raw" | "cal">("raw");
  const q = useQuery({ queryKey: resultKeys.families(rootId, suite, variant), queryFn: () => getFamilies(rootId, suite, variant), enabled: !!suite });
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap gap-3">
        <SegmentedControl label="Metric" value={metric} onChange={setMetric} options={[{ value: "intelligence", label: "Intelligence" }, { value: "accuracy", label: "Accuracy" }]} />
        {hasCal && <SegmentedControl label="Scores" value={variant} onChange={setVariant} options={[{ value: "raw", label: "Raw" }, { value: "cal", label: "Calibrated" }]} />}
      </div>
      <Card><CardBody>
        {q.isLoading ? <Skeleton className="h-48" /> : q.data && q.data.families.length ? (
          <FamilyHeatmap families={q.data.families} models={q.data.models} cells={q.data.cells} metric={metric} />
        ) : <EmptyState title="No families" body="This suite's rows carry no task families." />}
      </CardBody></Card>
    </div>
  );
}

// ---- Calibration ----------------------------------------------------------------------------------------

function CalibrationTab({ root, suite, colorOf }: { root: RunRootFull; suite: string; colorOf: (m: string) => string }) {
  const models = root.suites.find((s) => s.name === suite)?.models ?? [];
  const cal = useQuery({ queryKey: resultKeys.calibration(root.root_id, suite), queryFn: () => getCalibration(root.root_id, suite), enabled: !!suite });
  const rel = useQueries({ queries: models.map((m) => ({
    queryKey: resultKeys.reliability(root.root_id, m, suite, "all", 10), queryFn: () => getReliability(root.root_id, m, suite, "all", 10),
  })) });
  if (cal.isError) return <ErrorState error={cal.error} onRetry={() => cal.refetch()} />;
  const items = cal.data?.items ?? [];
  return (
    <div className="flex flex-col gap-5">
      {items.length > 0 ? (
        <Card><CardHeader title="Temperature per question kind, fitted on dev, scored on test" /><CardBody>
          <table className="w-full text-small">
            <thead><tr className="text-left text-muted"><th className="py-1">Model</th><th>Temperatures</th><th className="text-right">Intelligence before → after</th><th className="text-right">NLL before → after</th><th className="text-right">Δ Intelligence (paired, 95% CI)</th></tr></thead>
            <tbody>{items.map((it) => (
              <tr key={it.model} className="border-t border-border" data-testid={`calibration-delta-${it.model}`}>
                <td className="py-1.5 font-medium">{it.model}</td>
                <td className="font-mono text-small">{it.temperatures ? Object.entries(it.temperatures).map(([k, v]) => `${k} ${v}`).join(" · ") : "—"}</td>
                <td className="text-right tabular-nums">{fmtIntelligence(it.before?.intelligence)} → {fmtIntelligence(it.after?.intelligence)}</td>
                <td className="text-right tabular-nums">{fmt3(it.before?.nll)} → {fmt3(it.after?.nll)}</td>
                <td className="text-right tabular-nums">{it.delta ? <>{fmtSigned(it.delta.intelligence.diff)} [{fmtCI(it.delta.intelligence.ci95)}]</> : "—"}</td>
              </tr>
            ))}</tbody>
          </table>
          <p className="mt-3 text-small text-muted">Calibration makes probabilities honest (lower NLL). Under the 0.2–0.8 band rule, honest uncertainty on yes/no can lower Intelligence; both are shown so you can see it.</p>
        </CardBody></Card>
      ) : !cal.isLoading && (
        <Callout tone="info" title="No calibrated runs for this suite">
          {(cal.data?.ineligible ?? []).map((x) => <div key={x.model}><b>{x.model}</b>: {x.reason}</div>)}
          {!cal.data?.ineligible?.length && "Set calibrate: true in the lab; suites need at least 30 dev rows per kind."}
        </Callout>
      )}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {models.map((m, i) => {
          const d = rel[i]?.data;
          return (
            <Card key={m}><CardBody data-testid={`reliability-${m}`}>
              {d ? <ReliabilityDiagram model={m} color={colorOf(m)} kind="all" raw={{ bins: d.bins, ece: d.ece, n: d.n }} cal={d.calibrated} testId={`reliability-chart-${m}`} />
                : rel[i]?.isError ? <ErrorState error={rel[i].error} /> : <Skeleton className="h-56" />}
            </CardBody></Card>
          );
        })}
      </div>
    </div>
  );
}

// ---- Latency ----------------------------------------------------------------------------------------------

function LatencyTab({ rootId, suite, colorOf }: { rootId: string; suite: string; colorOf: (m: string) => string }) {
  const q = useQuery({ queryKey: resultKeys.latency(rootId, suite), queryFn: () => getRootLatency(rootId, suite), enabled: !!suite });
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  return (
    <div className="flex flex-col gap-4">
      {q.data && !q.data.same_machine && <Callout tone="warning" title="Different machines">These runs did not all run on the same host, so their latencies are not directly comparable.</Callout>}
      <Card><CardBody>
        {q.isLoading ? <Skeleton className="h-40" /> : (
          <LatencyDotRange items={(q.data?.items ?? []).map((it) => ({ ...it, color: colorOf(it.model) }))} />
        )}
      </CardBody></Card>
    </div>
  );
}

// ---- JevBench --------------------------------------------------------------------------------------------

function JevBenchTab({ rootId }: { rootId: string }) {
  const q = useQuery({ queryKey: resultKeys.jevbench(rootId), queryFn: () => getJevBench(rootId) });
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (q.isLoading) return <Skeleton className="h-32" />;
  return (
    <div className="flex flex-col gap-4">
      <Callout tone="info" title={q.data?.label ?? "JevBench public tasks, local proxy"}>{q.data?.note}</Callout>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {(q.data?.items ?? []).map((it) => (
          <Card key={it.model}><CardBody data-testid={`jevbench-panel-${it.model}`}>
            <div className="text-small text-muted">{it.model}</div>
            <div className="text-h2 tabular-nums">{fmtIntelligence(it.intelligence_proxy)} <span className="text-small font-normal text-muted">proxy Intelligence</span></div>
            <div className="mt-1 text-small">{it.n_correct}/{it.tasks} right · yes/no in band {it.yes_no_in_band}/{it.yes_no}</div>
            <div className="mt-2 flex flex-wrap gap-2 text-small">{Object.entries(it.competence_by_type).map(([k, v]) => <Badge key={k} tone="neutral">{k} {fmtIntelligence(v)}</Badge>)}</div>
            <div className="mt-2 font-mono text-caption text-subtle">harness {it.jevbench_commit.slice(0, 10)}</div>
          </CardBody></Card>
        ))}
      </div>
    </div>
  );
}

// ---- Provenance ------------------------------------------------------------------------------------------

function ProvenanceTab({ rootId }: { rootId: string }) {
  const q = useQuery({ queryKey: resultKeys.provenance(rootId), queryFn: () => getProvenance(rootId) });
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const cols: Column<NonNullable<typeof q.data>["items"][number]>[] = [
    { id: "run", header: "Run", cell: (p) => <Link className="text-accent hover:underline" to={`/results/${encodeURIComponent(rootId)}/${encodeURIComponent(p.model)}/${encodeURIComponent(p.suite)}`}>{p.model} / {p.suite}</Link>, sortValue: (p) => `${p.model}/${p.suite}` },
    { id: "answerer", header: "Answered by", cell: (p) => <span className="font-mono text-small">{String((p.answerer as Record<string, unknown> | null)?.adapter ?? "—")}{(p.answerer as Record<string, unknown> | null)?.model ? ` · ${String((p.answerer as Record<string, unknown>).model)}` : ""}</span> },
    { id: "source", header: "Source", cell: (p) => <span className="font-mono text-small">{p.source ? String((p.source as Record<string, unknown>).source ?? "") : "—"}</span>, hideBelow: "md" },
    { id: "sha", header: "Suite sha256", cell: (p) => <span className="font-mono text-small">{p.suite_sha256 ? p.suite_sha256.slice(0, 12) : "—"}</span> },
    { id: "rows", header: "Rows", cell: (p) => <>{p.n_rows ?? "—"}{p.limit ? <Badge tone="warning">limit {p.limit}</Badge> : null}</>, align: "right" },
    { id: "host", header: "Host / GPU", cell: (p) => <span className="text-small">{p.host ?? "—"}{p.gpu ? ` · ${p.gpu}` : ""}</span>, hideBelow: "md" },
    { id: "ver", header: "decider-lab", cell: (p) => p.decider_lab ?? "—", hideBelow: "sm" },
    { id: "when", header: "Finished", cell: (p) => fmtRelative(p.finished_utc), sortValue: (p) => p.finished_utc ?? "" },
  ];
  return (
    <div className="flex flex-col gap-4" data-testid="provenance">
      {q.data && Object.entries(q.data.same_rows).filter(([, v]) => !v.consistent).map(([s, v]) => (
        <Callout key={s} tone="warning" title={`Suite ${s}: runs used different rows`}>Mismatched: {v.mismatched_models.join(", ")}</Callout>
      ))}
      <DataTable columns={cols} rows={q.data?.items ?? []} rowKey={(p) => `${p.model}/${p.suite}`} loading={q.isLoading} caption="Provenance per run" mobile="cards" />
      {q.data?.job_id && <p className="text-small text-muted">Produced by job <Link className="text-accent hover:underline" to={`/jobs/${q.data.job_id}`}>{q.data.job_id}</Link>.</p>}
    </div>
  );
}
