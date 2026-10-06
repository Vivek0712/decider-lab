import { useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { Download } from "lucide-react";
import { getPredictions, getReliability, getRun, getRunLatency, predictionsCsvUrl, resultKeys } from "@/api/results";
import { chartColor } from "@/charts";
import { Badge } from "@/components/Badge";
import { Button } from "@/components/Button";
import { Card, CardBody, CardHeader } from "@/components/Card";
import { CodeBlock } from "@/components/Code";
import { ErrorState } from "@/components/ErrorState";
import { SegmentedControl } from "@/components/Field";
import { PageHeader } from "@/components/PageHeader";
import { Skeleton } from "@/components/Skeleton";
import { KpiCard } from "@/components/Stat";
import { DataTable, type Column } from "@/components/Table";
import type { Prediction } from "@/api/types";
import { fmt3, fmtCI, fmtIntelligence, fmtLatency, fmtPct } from "@/lib/format";
import { CompetenceBars, LatencyHistogram, ReliabilityDiagram } from "./charts";

const PAGE = 50;

/** One model on one suite: scores, calibration, latency, every prediction, provenance. */
export default function RunPage() {
  const { rootId = "", model = "", suite = "" } = useParams();
  const [show, setShow] = useState<"all" | "wrong" | "errors">("all");
  const [offset, setOffset] = useState(0);
  const run = useQuery({ queryKey: resultKeys.run(rootId, model, suite), queryFn: () => getRun(rootId, model, suite) });
  const rel = useQuery({ queryKey: resultKeys.reliability(rootId, model, suite, "all", 10), queryFn: () => getReliability(rootId, model, suite) });
  const lat = useQuery({ queryKey: resultKeys.runLatency(rootId, model, suite), queryFn: () => getRunLatency(rootId, model, suite) });
  const pq = { offset, limit: PAGE, ...(show === "wrong" && { correct: "0" }), ...(show === "errors" && { error: "1" }) };
  const preds = useQuery({ queryKey: resultKeys.predictions(rootId, model, suite, pq), queryFn: () => getPredictions(rootId, model, suite, pq), placeholderData: keepPreviousData });

  if (run.isError) return <ErrorState error={run.error} onRetry={() => run.refetch()} />;
  const d = run.data;
  const s = d?.scores;
  const color = chartColor(0, !!d?.is_baseline);
  const competence = Object.fromEntries(Object.entries(s?.by_kind ?? {}).map(([k, v]) => [k, (v as { competence: number }).competence]));
  const cols: Column<Prediction>[] = [
    { id: "id", header: "Row", cell: (p) => <span className="font-mono text-small">{p.id}</span> },
    { id: "task", header: "Family", cell: (p) => <span className="text-small text-muted">{p.task}</span>, hideBelow: "sm" },
    { id: "kind", header: "Kind", cell: (p) => <Badge tone="neutral">{p.kind}</Badge>, hideBelow: "sm" },
    { id: "gold", header: "Gold", cell: (p) => p.label, align: "right" },
    { id: "top", header: "Top", cell: (p) => p.top ?? "—", align: "right" },
    { id: "p", header: "P(gold)", cell: (p) => (p.probs ? p.probs[p.label]?.toFixed(3) : "—"), align: "right" },
    { id: "ok", header: "Result", cell: (p) => p.error ? <Badge tone="danger">error</Badge> : p.proxy_right ? <Badge tone="success">right</Badge> : <Badge tone="danger">wrong</Badge>,
      sortValue: (p) => (p.error ? -1 : p.proxy_right ? 1 : 0) },
    { id: "band", header: "Band", cell: (p) => (p.in_band ? <Badge tone="warning">in band</Badge> : null), hideBelow: "md" },
    { id: "lat", header: "Latency", cell: (p) => fmtLatency(p.latency_s), align: "right", hideBelow: "md" },
  ];

  return (
    <div data-testid="page-run">
      <PageHeader
        title={`${model} on ${suite}`}
        breadcrumbs={[{ label: "Results", to: "/results" }, { label: d?.lab ?? rootId, to: `/results/${encodeURIComponent(rootId)}` }, { label: `${model} / ${suite}` }]}
        description="Intelligence is a local proxy computed with the JevBench v1.5 rules, never a board score."
        actions={<a href={predictionsCsvUrl(rootId, model, suite)} download data-testid="predictions-csv"><Button icon={Download} tabIndex={-1}>predictions.csv</Button></a>}
      />
      {!s ? <Skeleton className="h-28" /> : (
        <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-6" data-testid="run-kpis">
          <KpiCard label="Intelligence (proxy)" value={fmtIntelligence(s.intelligence)} sub={`95% CI ${fmtCI(s.intelligence_ci95)}`} />
          <KpiCard label="Accuracy" value={fmtPct(s.accuracy)} />
          <KpiCard label="NLL" value={fmt3(s.nll)} />
          <KpiCard label="ECE" value={fmt3(s.ece)} />
          <KpiCard label="Rows" value={String(s.n)} sub={`scored split: ${s.scored_split ?? "all"}`} />
          <KpiCard label="Errors" value={String(s.errors)} tone={s.errors ? "danger" : undefined} />
        </div>
      )}
      <div className="mb-5 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card><CardHeader title="Competence by question kind" /><CardBody>{s ? <CompetenceBars values={competence} /> : <Skeleton className="h-32" />}</CardBody></Card>
        <Card><CardBody data-testid={`reliability-${model}`}>
          {rel.data ? <ReliabilityDiagram model={model} color={color} kind="all" raw={{ bins: rel.data.bins, ece: rel.data.ece, n: rel.data.n }} cal={rel.data.calibrated} />
            : rel.isError ? <ErrorState error={rel.error} /> : <Skeleton className="h-56" />}
        </CardBody></Card>
        <Card><CardBody>
          {lat.data ? <LatencyHistogram model={model} bins={lat.data.histogram} color={color} />
            : lat.isError ? <ErrorState error={lat.error} /> : <Skeleton className="h-56" />}
        </CardBody></Card>
      </div>
      {d && d.errors_sample.length > 0 && (
        <Card className="mb-5"><CardHeader title="Errors (sample)" /><CardBody>
          <ul className="flex flex-col gap-1 font-mono text-small">{d.errors_sample.map((e) => <li key={e.id}><span className="text-subtle">{e.id}</span> {e.error}</li>)}</ul>
        </CardBody></Card>
      )}
      <Card className="mb-5"><CardHeader title="Predictions" actions={
        <SegmentedControl label="Show" value={show} onChange={(v) => { setShow(v); setOffset(0); }} data-testid="predictions-filter"
          options={[{ value: "all", label: "All" }, { value: "wrong", label: "Wrong" }, { value: "errors", label: "Errors" }]} />
      } /><CardBody>
        <DataTable data-testid="predictions-table" columns={cols} rows={preds.data?.items ?? []} rowKey={(p) => p.id} loading={preds.isLoading} caption="Predictions" mobile="cards" />
        {preds.data && preds.data.total > PAGE && (
          <div className="mt-3 flex items-center justify-between text-small text-muted">
            <span>{offset + 1}–{Math.min(offset + PAGE, preds.data.total)} of {preds.data.total}</span>
            <div className="flex gap-2">
              <Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Previous</Button>
              <Button size="sm" disabled={offset + PAGE >= preds.data.total} onClick={() => setOffset(offset + PAGE)}>Next</Button>
            </div>
          </div>
        )}
      </CardBody></Card>
      {d && (
        <Card><CardHeader title="Provenance (run.json)" /><CardBody data-testid="provenance">
          <CodeBlock code={JSON.stringify({ run: d.run, source: d.source, calibration: d.calibration }, null, 2)} />
        </CardBody></Card>
      )}
    </div>
  );
}
