import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { BarChart3, GitCompare } from "lucide-react";
import { listRunRoots, resultKeys, type RunRootListItem } from "@/api/results";
import { Badge } from "@/components/Badge";
import { Button } from "@/components/Button";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { Input } from "@/components/Field";
import { PageHeader } from "@/components/PageHeader";
import { DataTable, type Column } from "@/components/Table";
import { fmtCI, fmtIntelligence, fmtRelative } from "@/lib/format";
import { useRegisterCommands } from "@/palette/registry";

// Owner: Results area. Every run root in the workspace (DESIGN.md 4.4; API.md GET /api/runs).
export default function ResultsPage() {
  const nav = useNavigate();
  const [q, setQ] = useState("");
  const query = useQuery({ queryKey: resultKeys.list(), queryFn: () => listRunRoots() });
  useRegisterCommands("results-page", [
    { id: "results.compare", label: "Compare two runs…", section: "Commands", icon: GitCompare, run: ({ close }) => { close(); nav("/results/compare"); } },
  ], []);

  const rows = useMemo(() => {
    const items = query.data?.items ?? [];
    const needle = q.trim().toLowerCase();
    return needle ? items.filter((r) => `${r.lab} ${r.models.join(" ")} ${r.suites.join(" ")}`.toLowerCase().includes(needle)) : items;
  }, [query.data, q]);

  const columns: Column<RunRootListItem>[] = [
    { id: "lab", header: "Lab", cell: (r) => <span className="font-medium text-text">{r.lab}</span>, sortValue: (r) => r.lab },
    { id: "models", header: "Models", cell: (r) => <span className="text-muted">{r.models.join(", ")}</span>, hideBelow: "md" },
    { id: "suites", header: "Suites", cell: (r) => (
      <span className="flex flex-wrap gap-1">{r.suites.map((s) => <Badge key={s} tone="neutral">{s}</Badge>)}</span>
    ) },
    { id: "best", header: "Best (Intelligence, local proxy)", label: "Best", cell: (r) => r.best ? (
      <span className="tabular-nums"><span className="font-medium text-text">{r.best.model}</span> · {fmtIntelligence(r.best.intelligence)}{" "}
        <span className="text-subtle">[{fmtCI(r.best.ci95)}]</span> <span className="text-subtle">on {r.best.suite}</span></span>
    ) : <span className="text-subtle">—</span>, sortValue: (r) => r.best?.intelligence ?? null },
    { id: "flags", header: "", label: "Notes", cell: (r) => (
      <span className="flex gap-1">
        {r.has_calibrated && <Badge tone="info">+cal</Badge>}
        {r.has_jevbench && <Badge tone="neutral">JevBench</Badge>}
        {r.failures.length > 0 && <Badge tone="danger">{r.failures.length} failed</Badge>}
      </span>
    ), hideBelow: "sm" },
    { id: "when", header: "Finished", cell: (r) => <span className="text-muted">{fmtRelative(r.finished_at)}</span>, sortValue: (r) => r.finished_at ?? "", align: "right" },
  ];

  return (
    <div data-testid="page-results">
      <PageHeader
        title="Results"
        description="Every finished lab and evaluation in this workspace. Intelligence is a local proxy computed with the JevBench v1.5 rules, never a board score."
        actions={<Button icon={GitCompare} onClick={() => nav("/results/compare")} data-testid="results-compare-open">Compare two runs</Button>}
      />
      <div className="mb-4 max-w-sm">
        <Input label="Filter" hideLabel placeholder="Filter by lab, model or suite" value={q} onChange={(e) => setQ(e.target.value)} data-testid="results-filter" />
      </div>
      {query.isError ? (
        <ErrorState error={query.error} onRetry={() => query.refetch()} />
      ) : (
        <DataTable
          data-testid="results-table"
          columns={columns}
          rows={rows}
          rowKey={(r) => r.root_id}
          loading={query.isLoading}
          defaultSort={{ id: "when", dir: "desc" }}
          onRowClick={(r) => nav(`/results/${encodeURIComponent(r.root_id)}`)}
          rowTestId={(r) => `runroot-card-${r.lab}`}
          mobile="cards"
          caption="Run roots"
          empty={
            <EmptyState
              icon={BarChart3}
              title={q ? "Nothing matches the filter" : "No results yet"}
              body={q ? "Clear the filter to see every run." : "Run a lab from the Labs page, or a quick evaluation from the Overview, and its report appears here."}
              action={q ? <Button onClick={() => setQ("")}>Clear filter</Button> : <Button variant="primary" onClick={() => nav("/labs")}>Go to Labs</Button>}
            />
          }
        />
      )}
    </div>
  );
}
