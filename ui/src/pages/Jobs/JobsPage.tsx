import { useMemo, useRef } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { globalEvents, jobKeys, listJobs } from "@/api/jobs";
import { labKeys, listLabs } from "@/api/labs";
import { Card } from "@/components/Card";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { Input, SegmentedControl, Select } from "@/components/Field";
import { PageHeader } from "@/components/PageHeader";
import { useEventSource } from "@/hooks/useEventSource";
import { useHotkeys } from "@/hooks/useHotkeys";
import { useRegisterCommands } from "@/palette/registry";
import { JobsTable } from "./JobsTable";

type Filter = "active" | "finished" | "all";

export default function JobsPage() {
  const [sp, setSp] = useSearchParams();
  const filter = (sp.get("status") as Filter) || "all";
  const kind = sp.get("kind") ?? "";
  const lab = sp.get("lab") ?? "";
  const q = sp.get("q") ?? "";
  const qc = useQueryClient();
  const search = useRef<HTMLInputElement>(null);
  const set = (k: string, v: string) =>
    setSp(
      (prev) => {
        const n = new URLSearchParams(prev);
        if (!v || (k === "status" && v === "all")) n.delete(k);
        else n.set(k, v);
        return n;
      },
      { replace: true },
    );

  const query = { status: filter === "all" ? undefined : filter, kind: kind || undefined, lab_id: lab || undefined, limit: 500 };
  const jobs = useQuery({
    queryKey: jobKeys.list(query),
    queryFn: () => listJobs(query),
    refetchInterval: (qq) => ((qq.state.data?.items ?? []).some((j) => ["queued", "running", "cancelling"].includes(j.status)) ? 3000 : false),
  });
  const labs = useQuery({ queryKey: labKeys.list(), queryFn: () => listLabs() });
  useEventSource(
    globalEvents,
    {
      "job.created": () => qc.invalidateQueries({ queryKey: jobKeys.all }),
      "job.updated": () => qc.invalidateQueries({ queryKey: jobKeys.all }),
      "job.finished": () => qc.invalidateQueries({ queryKey: jobKeys.all }),
    },
    { enabled: jobs.isSuccess, deps: [jobs.isSuccess] },
  );
  useHotkeys([{ keys: "/", handler: () => search.current?.focus() }]);
  useRegisterCommands(
    "jobs-page",
    (jobs.data?.items ?? []).slice(0, 30).map((j) => ({ id: `job.open.${j.job_id}`, label: `Open job ${j.title}`, detail: j.status, section: "Jobs" as const, href: `/jobs/${j.job_id}` })),
    [jobs.data],
  );

  const rows = useMemo(() => {
    const ql = q.toLowerCase();
    return (jobs.data?.items ?? []).filter((j) => !ql || j.title.toLowerCase().includes(ql) || j.job_id.includes(ql));
  }, [jobs.data, q]);

  return (
    <div data-testid="page-jobs">
      <PageHeader title="Jobs" description="Runs, pulls and evals Studio started, with live progress and logs. Jobs keep running when you close the tab." />
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <SegmentedControl
          label="Status filter"
          value={filter}
          onChange={(v) => set("status", v)}
          options={[
            { value: "active", label: "Active", testId: "jobs-filter-active" },
            { value: "finished", label: "Finished", testId: "jobs-filter-finished" },
            { value: "all", label: "All", testId: "jobs-filter-all" },
          ]}
        />
        <div className="w-[160px]">
          <Select
            label="Kind"
            hideLabel
            value={kind}
            onChange={(v) => set("kind", v)}
            data-testid="jobs-kind-filter"
            options={[
              { value: "", label: "Kind: all" },
              ...["run", "eval", "pull", "jevbench", "calibrate", "suite_build"].map((k) => ({ value: k, label: `Kind: ${k.replace("_", " ")}` })),
            ]}
          />
        </div>
        <div className="w-[200px]">
          <Select
            label="Lab"
            hideLabel
            value={lab}
            onChange={(v) => set("lab", v)}
            data-testid="jobs-lab-filter"
            options={[{ value: "", label: "Lab: all" }, ...(labs.data?.items ?? []).map((l) => ({ value: l.lab_id, label: `Lab: ${l.name}` }))]}
          />
        </div>
        <div className="w-full sm:w-[260px]">
          <Input ref={search} label="Search jobs" hideLabel type="search" placeholder="Search jobs…" value={q} onChange={(e) => set("q", e.target.value)} data-testid="jobs-search" />
        </div>
      </div>
      <Card>
        {jobs.isError ? (
          <ErrorState error={jobs.error} onRetry={() => jobs.refetch()} className="m-4" />
        ) : (
          <JobsTable
            jobs={rows}
            loading={jobs.isLoading}
            empty={
              (jobs.data?.items.length ?? 0) === 0 && filter === "all" && !kind && !lab ? (
                <EmptyState title="No jobs yet" body="Runs, pulls and evals you start appear here with live logs." command="decider-lab run lab.yaml" />
              ) : (
                <EmptyState title="No jobs match" body="Change the filters or the search." />
              )
            }
          />
        )}
      </Card>
    </div>
  );
}
