import { useEffect, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Check, X } from "lucide-react";
import { getRow, getRows, resultKeys, type RowItem, type RowsQuery } from "@/api/results";
import { Badge } from "@/components/Badge";
import { Button } from "@/components/Button";
import { Callout } from "@/components/Callout";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { Input, SegmentedControl, Select } from "@/components/Field";
import { Drawer } from "@/components/Overlay";
import { Skeleton } from "@/components/Skeleton";
import { DataTable, type Column } from "@/components/Table";
import { ProbBars } from "./charts";

const PAGE = 25;

/** Row explorer: every row of a suite, joined across models (API.md GET /api/runs/{root}/rows). */
export function RowsTab({ rootId, suite }: { rootId: string; suite: string }) {
  const [show, setShow] = useState<NonNullable<RowsQuery["show"]>>("all");
  const [kind, setKind] = useState("");
  const [family, setFamily] = useState("");
  const [split, setSplit] = useState("");
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<string | null>(null);
  useEffect(() => { const t = setTimeout(() => setQ(search), 250); return () => clearTimeout(t); }, [search]);
  useEffect(() => setOffset(0), [show, kind, family, split, q, suite]);

  const query: RowsQuery = { suite, offset, limit: PAGE, show, ...(kind && { kind }), ...(family && { family }), ...(split && { split }), ...(q && { q }) };
  const rows = useQuery({ queryKey: resultKeys.rows(rootId, query), queryFn: () => getRows(rootId, query), enabled: !!suite, placeholderData: keepPreviousData });
  if (rows.isError) return <ErrorState error={rows.error} onRetry={() => rows.refetch()} />;
  const d = rows.data;
  const models = d?.models ?? [];

  const columns: Column<RowItem>[] = [
    { id: "q", header: "Question", cell: (r) => (
      <div className="min-w-0 max-w-[36rem]">
        <div className="truncate text-text">{r.instructions_excerpt ?? r.id}</div>
        <div className="truncate text-small text-subtle">{r.state_excerpt ?? ""}</div>
      </div>
    ) },
    { id: "meta", header: "Kind / family", label: "Kind", cell: (r) => <span className="flex flex-wrap gap-1"><Badge tone="neutral">{r.kind}</Badge><span className="text-small text-muted">{r.task}</span></span>, hideBelow: "md" },
    { id: "gold", header: "Gold", cell: (r) => <span className="text-small">{r.gold_name ?? r.label}</span>, hideBelow: "sm" },
    ...models.map((m): Column<RowItem> => ({
      id: `m-${m}`, header: m, align: "right", cell: (r) => {
        const a = r.answers[m];
        if (!a) return <span className="text-subtle">—</span>;
        if (a.error) return <Badge tone="danger">error</Badge>;
        const ok = a.proxy_right;
        return (
          <span className="inline-flex items-center justify-end gap-1 tabular-nums" title={`top: ${a.top_name ?? a.top}, p(top) ${a.p_top?.toFixed(2)}, p(gold) ${a.p_gold?.toFixed(2)}${a.in_band ? ", inside the yes/no band" : ""}`}>
            {ok ? <Check className="size-3.5 text-success" aria-label="right" /> : <X className="size-3.5 text-danger" aria-label="wrong" />}
            {a.p_gold?.toFixed(2) ?? "—"}{a.in_band && <Badge tone="warning">band</Badge>}
          </span>
        );
      },
    })),
  ];

  return (
    <div className="flex flex-col gap-4">
      {d && d.content_available === false && (
        <Callout tone="info" title="Row text is not available">{d.content_reason ?? "The suite could not be rebuilt from run.json; answers are shown by row id."}</Callout>
      )}
      <div className="flex flex-wrap items-end gap-3">
        <SegmentedControl label="Show" value={show} onChange={setShow} data-testid="rows-show"
          options={[{ value: "all", label: "All" }, { value: "wrong", label: "Any wrong", testId: "rows-show-wrong" }, { value: "disagree", label: "Models disagree", testId: "rows-show-disagree" }, { value: "errors", label: "Errors" }]} />
        <Select label="Kind" value={kind} onChange={setKind} data-testid="rows-kind"
          options={[{ value: "", label: "All kinds" }, ...(d?.kinds ?? []).map((k) => ({ value: k, label: k }))]} />
        <Select label="Family" value={family} onChange={setFamily} data-testid="rows-family"
          options={[{ value: "", label: "All families" }, ...(d?.families ?? []).map((k) => ({ value: k, label: k }))]} />
        <Select label="Split" value={split} onChange={setSplit} data-testid="rows-split"
          options={[{ value: "", label: "Scored split" }, ...(d?.splits ?? []).map((k) => ({ value: k, label: k }))]} />
        <div className="w-64"><Input label="Search" placeholder="Text in the question or input" value={search} onChange={(e) => setSearch(e.target.value)} data-testid="rows-search" /></div>
      </div>
      <DataTable data-testid="rows-table" columns={columns} rows={d?.items ?? []} rowKey={(r) => r.id} loading={rows.isLoading}
        onRowClick={(r) => setOpen(r.id)} rowTestId={(r) => `row-${r.id}`} caption={`Rows of ${suite}`} mobile="cards"
        empty={<EmptyState title="No rows match" body="Change the filters above." />} />
      {d && d.total > PAGE && (
        <div className="flex items-center justify-between text-small text-muted">
          <span data-testid="rows-range">{offset + 1}–{Math.min(offset + PAGE, d.total)} of {d.total}</span>
          <div className="flex gap-2">
            <Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))} data-testid="rows-prev">Previous</Button>
            <Button size="sm" disabled={offset + PAGE >= d.total} onClick={() => setOffset(offset + PAGE)} data-testid="rows-next">Next</Button>
          </div>
        </div>
      )}
      <Drawer open={!!open} onOpenChange={(o) => !o && setOpen(null)} title="Row" width={560} data-testid="row-detail">
        {open && <RowDetail rootId={rootId} suite={suite} rowId={open} />}
      </Drawer>
    </div>
  );
}

function RowDetail({ rootId, suite, rowId }: { rootId: string; suite: string; rowId: string }) {
  const q = useQuery({ queryKey: resultKeys.row(rootId, suite, rowId), queryFn: () => getRow(rootId, suite, rowId) });
  if (q.isError) return <ErrorState error={q.error} />;
  if (!q.data) return <Skeleton className="h-64" />;
  const r = q.data;
  const state = typeof r.state === "string" ? r.state : JSON.stringify(r.state, null, 2);
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap gap-1"><Badge tone="neutral">{r.kind}</Badge><Badge tone="neutral">{r.task}</Badge>{r.split && <Badge tone="neutral">{r.split}</Badge>}</div>
      {r.content_available ? (
        <>
          <section><h3 className="mb-1 text-h3">Question</h3><p className="text-body">{r.instructions}</p></section>
          <section><h3 className="mb-1 text-h3">Input</h3><pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-sm bg-surface-2 p-3 text-small" tabIndex={0}>{state}</pre></section>
          {r.images.length > 0 && <div className="flex flex-wrap gap-2">{r.images.map((im, i) => im.src ? <img key={i} src={im.src} alt={im.alt} className="max-h-40 rounded-sm" /> : null)}</div>}
        </>
      ) : <Callout tone="info" title="Row text is not available">{r.content_reason}</Callout>}
      <section>
        <h3 className="mb-2 text-h3">Answers</h3>
        <div className="flex flex-col gap-4">
          {r.models.map((m) => {
            const a = r.answers[m];
            return (
              <div key={m} data-testid={`row-answer-${m}`}>
                <div className="mb-1 flex items-center gap-2 text-small font-medium">{m}
                  {a?.error ? <Badge tone="danger">error</Badge> : a?.proxy_right ? <Badge tone="success">right</Badge> : <Badge tone="danger">wrong</Badge>}
                  {a?.latency_s != null && <span className="text-subtle">{a.latency_s.toFixed(2)} s</span>}
                </div>
                {a?.error ? <p className="font-mono text-small text-danger">{a.error}</p> : r.options ? (
                  <ProbBars options={r.options} probs={a?.probs ?? null} gold={r.label} kind={r.kind} top={a?.top ?? null} />
                ) : null}
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}
