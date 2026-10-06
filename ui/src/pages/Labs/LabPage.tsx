import { useCallback, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BarChart3, Copy, Play, Trash2 } from "lucide-react";
import { deleteLab, duplicateLab, getLab, labKeys } from "@/api/labs";
import { jobKeys, listJobs } from "@/api/jobs";
import type { Lab } from "@/api/types";
import { Button } from "@/components/Button";
import { Callout } from "@/components/Callout";
import { Card, CardBody, CardHeader } from "@/components/Card";
import { CodeInline, useCopy } from "@/components/Code";
import { ConfirmDialog } from "@/components/Confirm";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { PageHeader } from "@/components/PageHeader";
import { Skeleton } from "@/components/Skeleton";
import { TabPanel, Tabs, useTabParam } from "@/components/Tabs";
import { useToast } from "@/components/Toast";
import { Tooltip } from "@/components/Tooltip";
import { useHotkeys } from "@/hooks/useHotkeys";
import { useRegisterCommands } from "@/palette/registry";
import { JobsTable } from "../Jobs/JobsTable";
import { LabEditor } from "./LabEditor";
import { LabSummaryView } from "./LabSummary";
import { LabStatus } from "./LabsPage";
import { OverflowMenu } from "./Menu";
import { RunDialog } from "./RunDialog";

export default function LabPage() {
  const { labId = "" } = useParams();
  const [sp, setSp] = useSearchParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const [copy] = useCopy();
  const [tab, setTab] = useTabParam("summary");
  const [dirty, setDirty] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const lab = useQuery({ queryKey: labKeys.one(labId), queryFn: () => getLab(labId) });
  const runOpen = sp.get("run") === "1";
  const only = sp.get("only")?.split(",").filter(Boolean) ?? null;
  const setRun = useCallback(
    (open: boolean) =>
      setSp(
        (prev) => {
          const n = new URLSearchParams(prev);
          if (open) n.set("run", "1");
          else {
            n.delete("run");
            n.delete("only");
          }
          return n;
        },
        { replace: !open },
      ),
    [setSp],
  );

  const l = lab.data;
  const canRun = !!l && l.valid && !dirty;
  useHotkeys([{ keys: "r", handler: () => canRun && setRun(true) }]);
  useRegisterCommands("lab-page", l ? [{ id: "lab.run", label: `Run ${l.name}…`, section: "Commands", shortcut: ["r"], run: () => setRun(true) }] : [], [l?.lab_id, canRun]);

  if (lab.isLoading) {
    return (
      <div data-testid="page-lab" aria-busy="true">
        <Skeleton width={260} height={28} shape="block" className="mb-3" />
        <Skeleton width="40%" className="mb-6" />
        <Skeleton shape="block" height={320} />
      </div>
    );
  }
  if (lab.isError || !l) {
    return (
      <div data-testid="page-lab">
        <PageHeader title="Lab" breadcrumbs={[{ label: "Labs", to: "/labs" }, { label: "Lab" }]} />
        <ErrorState error={lab.error} onRetry={() => lab.refetch()} />
      </div>
    );
  }

  const onSaved = (saved: Lab) => {
    qc.setQueryData(labKeys.one(labId), saved);
  };
  const runButton = (
    <Button variant="primary" icon={Play} disabled={!canRun} onClick={() => setRun(true)} data-testid="lab-run" kbd={["R"]}>
      Run…
    </Button>
  );

  return (
    <div data-testid="page-lab">
      <PageHeader
        title={l.name}
        status={<LabStatus valid={l.valid} errors={l.problems.filter((p) => p.severity === "error").length} testId="lab-status" />}
        description={<CodeInline code={l.path} copy />}
        breadcrumbs={[{ label: "Labs", to: "/labs" }, { label: l.name }]}
        actions={
          <>
            {canRun ? (
              runButton
            ) : (
              <Tooltip content={dirty ? "Save your edits first" : "Fix the errors in lab.yaml first"}>
                <span tabIndex={0}>{runButton}</span>
              </Tooltip>
            )}
            <OverflowMenu
              label={`More actions for ${l.name}`}
              data-testid="lab-more"
              items={[
                {
                  id: "dup",
                  label: "Duplicate",
                  icon: Copy,
                  testId: "lab-more-duplicate",
                  onSelect: async () => {
                    const name = `${l.name}-copy`;
                    try {
                      const item = await duplicateLab(l.lab_id, { name, path: l.path.replace(/[^/]+$/, `${name}.yaml`) });
                      await qc.invalidateQueries({ queryKey: labKeys.all });
                      toast({ title: `Created ${item.name}` });
                      nav(`/labs/${item.lab_id}`);
                    } catch (e) {
                      setActionError(`Could not duplicate: ${e instanceof Error ? e.message : "unknown error"}`);
                    }
                  },
                },
                {
                  id: "copy",
                  label: "Copy command",
                  icon: Copy,
                  testId: "lab-more-copy",
                  onSelect: () => {
                    void copy(l.command);
                    toast({ title: "Command copied", description: l.command });
                  },
                },
                ...(l.run_root_id ? [{ id: "results", label: "Open results", icon: BarChart3, onSelect: () => nav(`/results/${l.run_root_id}`) }] : []),
                { id: "delete", label: "Delete file…", icon: Trash2, danger: true, testId: "lab-more-delete", onSelect: () => setDeleting(true) },
              ]}
            />
          </>
        }
      />
      {actionError && (
        <Callout tone="danger" alert className="mb-4" actions={<Button size="sm" onClick={() => setActionError(null)}>Dismiss</Button>}>
          {actionError}
        </Callout>
      )}
      <Tabs
        label="Lab sections"
        value={tab}
        onChange={setTab}
        testIdPrefix="lab-tab"
        className="mb-5"
        tabs={[
          { id: "summary", label: "Summary" },
          { id: "editor", label: dirty ? "Editor ●" : "Editor", count: l.problems.length || undefined },
          { id: "runs", label: "Runs" },
        ]}
      />
      <TabPanel id="summary" active={tab === "summary"}>
        {l.summary ? (
          <LabSummaryView summary={l.summary} />
        ) : (
          <Card>
            <EmptyState title="This lab does not parse" body="Open the editor to see the problem and fix it." action={<Button onClick={() => setTab("editor")}>Open editor</Button>} />
          </Card>
        )}
      </TabPanel>
      <TabPanel id="editor" active={tab === "editor"}>
        <LabEditor lab={l} onSaved={onSaved} onDirtyChange={setDirty} />
      </TabPanel>
      <TabPanel id="runs" active={tab === "runs"}>
        <LabRuns lab={l} onRun={() => setRun(true)} />
      </TabPanel>
      {runOpen && l.summary && <RunDialog lab={l} open={runOpen} onClose={() => setRun(false)} initialOnly={only} />}
      <ConfirmDialog
        open={deleting}
        onOpenChange={setDeleting}
        title={`Delete ${l.name}?`}
        tone="danger"
        confirmLabel="Delete file"
        phrase={l.name}
        body={
          <>
            This deletes <code className="font-mono">{l.path}</code> only. Its runs, results and data files are kept.
          </>
        }
        onConfirm={async (typed) => {
          await deleteLab(l.lab_id, typed);
          await qc.invalidateQueries({ queryKey: labKeys.all });
          toast({ title: `Deleted ${l.name}` });
          nav("/labs");
        }}
      />
    </div>
  );
}

function LabRuns({ lab, onRun }: { lab: Lab; onRun: () => void }) {
  const jobs = useQuery({ queryKey: jobKeys.list({ lab_id: lab.lab_id }), queryFn: () => listJobs({ lab_id: lab.lab_id }), refetchInterval: 5000 });
  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader
          title="Run root"
          as="h2"
          description={lab.run_root_id ? "Results of the last run of this lab." : undefined}
          actions={
            lab.run_root_id ? (
              <Link to={`/results/${lab.run_root_id}`} className="text-small font-semibold" data-testid="lab-open-results">
                Open results →
              </Link>
            ) : undefined
          }
        />
        {!lab.run_root_id && (
          <EmptyState
            title="This lab has not been run yet."
            action={
              <Button variant="primary" icon={Play} onClick={onRun} disabled={!lab.valid}>
                Run…
              </Button>
            }
            command={lab.command}
          />
        )}
        {lab.run_root_id && (
          <CardBody className="text-small text-muted">
            Same as <CodeInline code={lab.command} copy />
          </CardBody>
        )}
      </Card>
      <Card>
        <CardHeader title="Jobs" as="h2" />
        {jobs.isError ? (
          <ErrorState error={jobs.error} onRetry={() => jobs.refetch()} className="m-4" />
        ) : (
          <JobsTable jobs={jobs.data?.items ?? []} loading={jobs.isLoading} empty={<EmptyState title="No jobs for this lab yet" body="Runs you start appear here with live progress." />} />
        )}
      </Card>
    </div>
  );
}
