import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { ArrowLeftRight } from "lucide-react";
import { getCompare, listRunRefs, refString, resultKeys, type RunRefItem } from "@/api/results";
import type { Diff } from "@/api/types";
import { Badge } from "@/components/Badge";
import { Button } from "@/components/Button";
import { Callout } from "@/components/Callout";
import { Card, CardBody } from "@/components/Card";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { Select } from "@/components/Field";
import { PageHeader } from "@/components/PageHeader";
import { Skeleton } from "@/components/Skeleton";
import { fmtCI, fmtIntelligence, fmtSigned } from "@/lib/format";
import { verdictOf, verdictSentence } from "./charts";

const label = (r: RunRefItem) =>
  `${r.lab} · ${r.model} · ${r.suite}${r.n != null ? ` (${r.n} rows${r.limit ? `, limit ${r.limit}` : ""})` : ""} · ${fmtIntelligence(r.intelligence)}`;

/** Paired comparison of any two runs (API.md GET /api/compare): a minus b on the rows both answered. */
export default function ComparePage() {
  const [sp, setSp] = useSearchParams();
  const a = sp.get("a") ?? "";
  const b = sp.get("b") ?? "";
  const split = sp.get("split") ?? "test";
  const set = (k: string, v: string) => { const n = new URLSearchParams(sp); n.set(k, v); setSp(n, { replace: true }); };
  const refs = useQuery({ queryKey: resultKeys.refs(), queryFn: listRunRefs });
  const cmp = useQuery({ queryKey: resultKeys.compare(a, b, split), queryFn: () => getCompare(a, b, split), enabled: !!a && !!b && a !== b });
  const options = [{ value: "", label: "Choose a run…" }, ...(refs.data?.items ?? []).map((r) => ({ value: refString(r), label: label(r) }))];

  return (
    <div data-testid="page-compare">
      <PageHeader title="Compare two runs" breadcrumbs={[{ label: "Results", to: "/results" }, { label: "Compare" }]}
        description="A minus B on the rows both runs answered, with a paired bootstrap 95% interval. An interval that crosses zero is no measured difference." />
      {refs.isError ? <ErrorState error={refs.error} onRetry={() => refs.refetch()} /> : refs.data && refs.data.items.length < 2 ? (
        <EmptyState title="Not enough runs to compare" body="Finish at least two runs (any lab, any model) and come back." />
      ) : (
        <Card className="mb-5"><CardBody>
          <div className="grid grid-cols-1 items-end gap-3 md:grid-cols-[1fr_auto_1fr_10rem]">
            <Select label="A" value={a} onChange={(v) => set("a", v)} options={options} data-testid="compare-a" />
            <Button iconOnly icon={ArrowLeftRight} aria-label="Swap A and B" onClick={() => { const n = new URLSearchParams(sp); n.set("a", b); n.set("b", a); setSp(n, { replace: true }); }} data-testid="compare-swap" />
            <Select label="B" value={b} onChange={(v) => set("b", v)} options={options} data-testid="compare-b" />
            <Select label="Split" value={split} onChange={(v) => set("split", v)} data-testid="compare-split"
              options={[{ value: "test", label: "test" }, { value: "dev", label: "dev" }, { value: "all", label: "all rows" }]} />
          </div>
        </CardBody></Card>
      )}
      {a && b && a === b && <Callout tone="warning" title="A and B are the same run">Pick two different runs.</Callout>}
      {cmp.isError && <ErrorState error={cmp.error} onRetry={() => cmp.refetch()} />}
      {cmp.isLoading && a && b && a !== b && <Skeleton className="h-40" />}
      {cmp.data && (
        <div className="flex flex-col gap-4" data-testid="compare-result">
          {!cmp.data.same_suite_sha256 && (
            <Callout tone="warning" title="Different suite fingerprints" data-testid="same-rows-warning">These runs did not score the same suite; only rows with the same id are paired.</Callout>
          )}
          <p className="text-small text-muted">
            <b>A</b> {cmp.data.a.label} vs <b>B</b> {cmp.data.b.label} · {cmp.data.n_paired} paired rows
            {cmp.data.only_a || cmp.data.only_b ? ` (${cmp.data.only_a} only in A, ${cmp.data.only_b} only in B)` : ""} · split {cmp.data.split} · {cmp.data.bootstrap} bootstrap draws
          </p>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            <DiffCard title="Δ Intelligence (local proxy)" d={cmp.data.intelligence} digits={2} lowerIsBetter={false} testId="compare-intelligence" />
            <DiffCard title="Δ Accuracy (points)" d={cmp.data.accuracy} digits={2} lowerIsBetter={false} testId="compare-accuracy" />
            <DiffCard title="Δ NLL (lower is better)" d={cmp.data.nll} digits={4} lowerIsBetter testId="compare-nll" />
          </div>
          {cmp.data.notes.length > 0 && <ul className="list-disc pl-5 text-small text-muted">{cmp.data.notes.map((n) => <li key={n}>{n}</li>)}</ul>}
          {cmp.data.proxy_note && <p className="text-small text-subtle">{cmp.data.proxy_note}</p>}
        </div>
      )}
    </div>
  );
}

function DiffCard({ title, d, digits, lowerIsBetter, testId }: { title: string; d: Diff; digits: number; lowerIsBetter: boolean; testId: string }) {
  const v = d.verdict ?? verdictOf(d, lowerIsBetter);
  const tone = v === "better" ? "success" : v === "worse" ? "danger" : "neutral";
  return (
    <Card><CardBody data-testid={testId}>
      <div className="text-small text-muted">{title}</div>
      <div className="mt-1 flex items-center gap-2"><span className="text-h1 tabular-nums">{fmtSigned(d.diff, digits)}</span><Badge tone={tone}>{v}</Badge></div>
      <div className="text-small tabular-nums text-muted">95% CI [{fmtCI(d.ci95, digits)}]</div>
      <p className="mt-2 text-small">{verdictSentence(v, "B", lowerIsBetter)}</p>
    </CardBody></Card>
  );
}
