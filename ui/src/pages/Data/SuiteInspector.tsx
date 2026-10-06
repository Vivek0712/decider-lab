import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Save } from "lucide-react";
import type { SuiteStats } from "@/api/types";
import { dataKeys, exportSuite, getSuiteRows, getSuiteStats, type SuiteRow } from "@/api/data";
import { Badge } from "@/components/Badge";
import { Button } from "@/components/Button";
import { Callout } from "@/components/Callout";
import { CodeInline } from "@/components/Code";
import { ErrorState } from "@/components/ErrorState";
import { Input, Select } from "@/components/Field";
import { Drawer } from "@/components/Overlay";
import { Skeleton } from "@/components/Skeleton";
import { useToast } from "@/components/Toast";
import { ApiError } from "@/lib/api";
import { fmtInt } from "@/lib/format";
import { JobCard } from "../Models/JobCard";
import { BreakdownBars, LabelBalanceBars, OutPath, kindLabel, slug } from "./shared";

const PAGE = 25;

/** The SuiteInspector drawer (DESIGN.md 4.6): stats, label balance, family breakdown, paged rows, export. */
export function SuiteInspector({ suiteRef, title, onClose }: { suiteRef: string | null; title: string; onClose: () => void }) {
  return (
    <Drawer open={!!suiteRef} onOpenChange={(o) => !o && onClose()} title={`Inspect ${title}`} width={880} data-testid="suite-inspector">
      {suiteRef && <InspectorBody key={suiteRef} suiteRef={suiteRef} />}
    </Drawer>
  );
}

function InspectorBody({ suiteRef }: { suiteRef: string }) {
  const qc = useQueryClient();
  const [buildJob, setBuildJob] = useState<string | null>(null);
  const stats = useQuery({ queryKey: dataKeys.stats(suiteRef), queryFn: () => getSuiteStats(suiteRef), staleTime: 60_000 });
  useEffect(() => {
    const d = stats.data as { job_id?: string } | undefined;
    if (d?.job_id) setBuildJob(d.job_id);
  }, [stats.data]);
  const s = stats.data && !("job_id" in stats.data) ? (stats.data as SuiteStats) : null;

  return (
    <div className="flex flex-col gap-6 px-4 py-4 sm:px-5">
      {stats.error ? (
        <ErrorState error={stats.error} onRetry={() => stats.refetch()} />
      ) : buildJob && !s ? (
        <div className="flex flex-col gap-2">
          <Callout tone="info">Building this suite takes a while; its stats show when the build job ends.</Callout>
          <JobCard jobId={buildJob} onDone={() => void qc.invalidateQueries({ queryKey: dataKeys.stats(suiteRef) })} />
        </div>
      ) : !s ? (
        <div aria-busy="true" className="flex flex-col gap-3">
          <Skeleton shape="block" height={72} />
          <Skeleton shape="block" height={160} />
        </div>
      ) : (
        <StatsPanel s={s} />
      )}
      {s && <RowsBrowser suiteRef={suiteRef} />}
      {s && <ExportForm suiteRef={suiteRef} name={s.name} />}
    </div>
  );
}

function StatsPanel({ s }: { s: SuiteStats }) {
  return (
    <section data-testid="suite-stats" aria-label="Suite statistics" className="flex flex-col gap-5">
      <dl className="m-0 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Kpi label="Rows" value={fmtInt(s.rows)} testId="suite-stats-rows" />
        {Object.entries(s.by_kind)
          .sort()
          .map(([k, v]) => (
            <Kpi key={k} label={kindLabel(k)} value={fmtInt(v)} testId={`suite-stats-kind-${k}`} />
          ))}
      </dl>
      <div className="flex flex-wrap items-center gap-2 text-small text-muted">
        <span>Splits:</span>
        {Object.entries(s.by_split).map(([k, v]) => (
          <Badge key={k} tone={k === "None" ? "neutral" : "info"}>
            {k === "None" ? "no split" : k} {fmtInt(v)}
          </Badge>
        ))}
        <span className="ml-2">With images: {fmtInt(s.with_images)}</span>
      </div>
      <div className="flex min-w-0 flex-wrap items-center gap-2 text-small text-muted">
        sha256 <CodeInline code={s.sha256} copy className="min-w-0" />
      </div>
      <div className="grid gap-6 md:grid-cols-2">
        <LabelBalanceBars balance={s.label_balance} />
        <BreakdownBars title="Families" caption="Rows per task family (top 30)" data={s.by_task} testId="suite-families" />
      </div>
    </section>
  );
}

function Kpi({ label, value, testId }: { label: string; value: string; testId?: string }) {
  return (
    <div className="rounded-md border border-border bg-surface-2 px-3 py-2" data-testid={testId}>
      <dt className="text-caption uppercase tracking-wide text-muted">{label}</dt>
      <dd className="tnum m-0 text-h2">{value}</dd>
    </div>
  );
}

function RowsBrowser({ suiteRef }: { suiteRef: string }) {
  const [kind, setKind] = useState("");
  const [split, setSplit] = useState("");
  const [task, setTask] = useState("");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<number | null>(null);
  useEffect(() => {
    const t = window.setTimeout(() => setQuery(q.trim()), 250);
    return () => window.clearTimeout(t);
  }, [q]);
  useEffect(() => setOffset(0), [kind, split, task, query]);
  const params = { offset, limit: PAGE, kind: kind || undefined, split: split || undefined, task: task || undefined, q: query || undefined };
  const rows = useQuery({ queryKey: dataKeys.rows(suiteRef, params), queryFn: () => getSuiteRows(suiteRef, params), placeholderData: (p) => p });
  const page = rows.data;
  const facets = page?.facets;
  const total = page?.total ?? 0;

  return (
    <section aria-label="Rows" className="flex flex-col gap-3" data-testid="suite-rows">
      <h3 className="text-h3">Rows</h3>
      <div className="grid gap-3 sm:grid-cols-4">
        <Select
          label="Kind"
          value={kind}
          onChange={setKind}
          data-testid="rows-filter-kind"
          options={[{ value: "", label: "All kinds" }, ...(facets?.kinds ?? []).map((k) => ({ value: k, label: kindLabel(k) }))]}
        />
        <Select
          label="Split"
          value={split}
          onChange={setSplit}
          data-testid="rows-filter-split"
          options={[{ value: "", label: "All splits" }, ...(facets?.splits ?? []).map((k) => ({ value: k, label: k === "None" ? "no split" : k }))]}
        />
        <Select
          label="Family"
          value={task}
          onChange={setTask}
          data-testid="rows-filter-task"
          options={[{ value: "", label: "All families" }, ...(facets?.tasks ?? []).map((k) => ({ value: k, label: k }))]}
        />
        <Input label="Search" placeholder="state, question, id" value={q} onChange={(e) => setQ(e.target.value)} data-testid="rows-search" type="search" />
      </div>
      {rows.error ? (
        <ErrorState error={rows.error} onRetry={() => rows.refetch()} />
      ) : !page ? (
        <Skeleton lines={6} />
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2 text-small text-muted">
            <span data-testid="rows-count" aria-live="polite">
              {total === 0 ? "No rows match." : `${fmtInt(offset + 1)}–${fmtInt(Math.min(offset + PAGE, total))} of ${fmtInt(total)}`}
            </span>
            <span className="flex gap-1">
              <Button size="sm" icon={ChevronLeft} disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))} data-testid="rows-prev">
                Previous
              </Button>
              <Button size="sm" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)} data-testid="rows-next">
                Next <ChevronRight size={16} aria-hidden />
              </Button>
            </span>
          </div>
          <ol className="m-0 flex list-none flex-col gap-2 p-0" data-testid="rows-list">
            {page.items.map((r) => (
              <RowItem key={r.index} r={r} open={open === r.index} onToggle={() => setOpen(open === r.index ? null : r.index)} />
            ))}
          </ol>
        </>
      )}
    </section>
  );
}

function RowItem({ r, open, onToggle }: { r: SuiteRow; open: boolean; onToggle: () => void }) {
  const state = typeof r.state === "string" ? r.state : JSON.stringify(r.state);
  return (
    <li className="rounded-md border border-border bg-surface-2" data-testid={`row-${r.index}`}>
      <button type="button" onClick={onToggle} aria-expanded={open} className="flex w-full min-w-0 flex-col gap-1 px-3 py-2 text-left hover:bg-surface-3">
        <span className="flex flex-wrap items-center gap-2 text-caption text-muted">
          <span className="tnum">#{r.index}</span>
          <Badge>{kindLabel(r.kind)}</Badge>
          {r.split && <Badge tone="info">{r.split}</Badge>}
          <span className="font-mono">{r.task}</span>
        </span>
        <span className="line-clamp-2 break-words text-small">{state}</span>
        <span className="text-small font-medium">{r.instructions}</span>
      </button>
      {open && (
        <div className="border-t border-border px-3 py-2 text-small" data-testid={`row-detail-${r.index}`}>
          <ol className="m-0 flex list-none flex-col gap-1 p-0">
            {r.options.map(([name, desc], i) => (
              <li key={name} className={i === r.label ? "font-semibold text-success" : "text-muted"}>
                {i === r.label ? "★ " : "  "}
                <code className="font-mono">{name}</code> {desc !== name ? desc : ""}
                {i === r.label && <span className="sr-only"> (gold label)</span>}
              </li>
            ))}
          </ol>
          {r.state_truncated && <p className="mt-1 text-caption text-subtle">State shortened to 2,000 characters here.</p>}
          <p className="mt-1 break-all text-caption text-subtle">
            id <code className="font-mono">{r.id}</code>
          </p>
        </div>
      )}
    </li>
  );
}

function ExportForm({ suiteRef, name }: { suiteRef: string; name: string }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [out, setOut] = useState(`data/${slug(suiteRef.startsWith("file:") ? `${name}-copy` : suiteRef.replace(/[:=,]/g, "-"))}.jsonl`);
  const [overwrite, setOverwrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<ApiError | Error | null>(null);
  return (
    <section aria-label="Save as JSONL" className="flex flex-col gap-3 border-t border-border pt-4" data-testid="suite-export">
      <h3 className="text-h3">Save as JSONL</h3>
      <p className="text-small text-muted">Writes these rows to a file in the workspace (CLI: decider-lab suites --build … --out).</p>
      <OutPath value={out} onChange={setOut} overwrite={overwrite} onOverwrite={setOverwrite} testId="suite-export" />
      {err && (
        <Callout tone="danger" alert title={err.message}>
          {err instanceof ApiError ? err.hint : null}
        </Callout>
      )}
      <div>
        <Button
          icon={Save}
          loading={busy}
          data-testid="suite-export-save"
          onClick={async () => {
            setBusy(true);
            setErr(null);
            try {
              const r = await exportSuite({ ref: suiteRef, out: out.trim(), overwrite });
              toast({ title: `Saved ${fmtInt(r.rows)} rows`, description: r.path });
              void qc.invalidateQueries({ queryKey: dataKeys.suites });
            } catch (e) {
              setErr(e as Error);
            } finally {
              setBusy(false);
            }
          }}
        >
          Save as JSONL
        </Button>
      </div>
    </section>
  );
}

