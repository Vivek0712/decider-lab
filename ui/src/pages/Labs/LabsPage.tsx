import { useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, FileInput, FolderOpen, Play, Plus, Trash2 } from "lucide-react";
import { deleteLab, duplicateLab, importLab, labKeys, listLabs } from "@/api/labs";
import { globalEvents } from "@/api/jobs";
import type { LabListItem } from "@/api/types";
import { Button } from "@/components/Button";
import { Callout } from "@/components/Callout";
import { Card } from "@/components/Card";
import { useCopy } from "@/components/Code";
import { ConfirmDialog } from "@/components/Confirm";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { Input, Select } from "@/components/Field";
import { Dialog } from "@/components/Overlay";
import { PageHeader } from "@/components/PageHeader";
import { StatusPill } from "@/components/StatusPill";
import { DataTable, type Column } from "@/components/Table";
import { useToast } from "@/components/Toast";
import { Tooltip } from "@/components/Tooltip";
import { useEventSource } from "@/hooks/useEventSource";
import { useHotkeys } from "@/hooks/useHotkeys";
import { ApiError } from "@/lib/api";
import { fmtAbsolute, fmtRelative } from "@/lib/format";
import { useRegisterCommands } from "@/palette/registry";
import { OverflowMenu } from "./Menu";

const BACKEND: Record<string, string> = { local: "local", ssh: "ssh", aws: "aws", vast: "vast" };

export function LabStatus({ valid, errors, warnings, testId }: { valid: boolean; errors: number; warnings?: number; testId?: string }) {
  return valid ? (
    <StatusPill status="valid" data-testid={testId} label={warnings ? `valid · ${warnings} warning${warnings === 1 ? "" : "s"}` : "valid"} />
  ) : (
    <StatusPill status="invalid" data-testid={testId} label={`${errors} error${errors === 1 ? "" : "s"}`} />
  );
}

export default function LabsPage() {
  const [sp, setSp] = useSearchParams();
  const q = sp.get("q") ?? "";
  const status = sp.get("status") ?? "all";
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const [copy] = useCopy();
  const search = useRef<HTMLInputElement>(null);
  const [dup, setDup] = useState<LabListItem | null>(null);
  const [del, setDel] = useState<LabListItem | null>(null);
  const [importing, setImporting] = useState(false);

  const labs = useQuery({ queryKey: labKeys.list(), queryFn: () => listLabs() });
  useEventSource(globalEvents, { "labs.changed": () => qc.invalidateQueries({ queryKey: labKeys.all }) }, { enabled: labs.isSuccess, deps: [labs.isSuccess] });

  const setParam = (k: string, v: string) =>
    setSp(
      (prev) => {
        const n = new URLSearchParams(prev);
        if (!v || v === "all") n.delete(k);
        else n.set(k, v);
        return n;
      },
      { replace: true },
    );

  const rows = useMemo(() => {
    const items = labs.data?.items ?? [];
    const ql = q.toLowerCase();
    return items.filter(
      (l) =>
        (!ql || l.name.toLowerCase().includes(ql) || l.path.toLowerCase().includes(ql)) &&
        (status === "all" || (status === "valid" ? l.valid : !l.valid)),
    );
  }, [labs.data, q, status]);

  useHotkeys([
    { keys: "/", handler: () => search.current?.focus() },
    { keys: "n", handler: () => nav("/labs/new") },
  ]);
  useRegisterCommands(
    "labs-page",
    (labs.data?.items ?? []).map((l) => ({ id: `lab.open.${l.lab_id}`, label: `Open lab ${l.name}`, detail: l.path, section: "Labs" as const, href: `/labs/${l.lab_id}` })),
    [labs.data],
  );

  const columns: Column<LabListItem>[] = [
    {
      id: "name",
      header: "Name",
      cell: (l) => (
        <Link to={`/labs/${l.lab_id}`} className="font-semibold text-text no-underline hover:underline" onClick={(e) => e.stopPropagation()}>
          {l.name}
        </Link>
      ),
      sortValue: (l) => l.name,
    },
    { id: "file", header: "File", cell: (l) => <code className="font-mono text-mono text-muted">{l.path}</code>, sortValue: (l) => l.path, hideBelow: "md" },
    {
      id: "models",
      header: "Models",
      align: "right",
      cell: (l) => (
        <span>
          {l.models}
          {l.finetune ? <span className="text-muted"> (+{l.finetune} ft)</span> : null}
        </span>
      ),
      sortValue: (l) => l.models,
    },
    { id: "suites", header: "Suites", align: "right", cell: (l) => l.suites, sortValue: (l) => l.suites },
    { id: "backend", header: "Compute", cell: (l) => BACKEND[l.backend] ?? l.backend, sortValue: (l) => l.backend, hideBelow: "sm" },
    {
      id: "last",
      header: "Last run",
      cell: (l) =>
        l.last_run_at ? (
          <Tooltip content={fmtAbsolute(l.last_run_at)}>
            <span tabIndex={0}>{fmtRelative(l.last_run_at)}</span>
          </Tooltip>
        ) : (
          <span className="text-muted">never</span>
        ),
      sortValue: (l) => l.last_run_at ?? "",
    },
    { id: "status", header: "Status", cell: (l) => <LabStatus valid={l.valid} errors={l.errors} />, sortValue: (l) => (l.valid ? 0 : 1) },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      label: "Actions",
      cell: (l) => (
        <OverflowMenu
          label={`Actions for ${l.name}`}
          data-testid={`lab-menu-${l.name}`}
          items={[
            { id: "open", label: "Open", icon: FolderOpen, onSelect: () => nav(`/labs/${l.lab_id}`), testId: "lab-menu-open" },
            { id: "run", label: "Run…", icon: Play, onSelect: () => nav(`/labs/${l.lab_id}?run=1`), disabled: !l.valid, testId: "lab-menu-run" },
            { id: "dup", label: "Duplicate…", icon: Copy, onSelect: () => setDup(l), testId: "lab-menu-duplicate" },
            {
              id: "copy",
              label: "Copy path",
              icon: Copy,
              onSelect: () => {
                void copy(l.path);
                toast({ title: "Path copied", description: l.path });
              },
              testId: "lab-menu-copy",
            },
            { id: "delete", label: "Delete file…", icon: Trash2, danger: true, onSelect: () => setDel(l), testId: "lab-menu-delete" },
          ]}
        />
      ),
    },
  ];

  return (
    <div data-testid="page-labs">
      <PageHeader
        title="Labs"
        description="Lab files: which models answer which suites, the baseline, and where they run."
        actions={
          <>
            <Button icon={FileInput} onClick={() => setImporting(true)} data-testid="lab-import">
              Import file…
            </Button>
            <Button variant="primary" icon={Plus} onClick={() => nav("/labs/new")} data-testid="lab-new" kbd={["N"]}>
              New lab
            </Button>
          </>
        }
      />
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div className="w-full sm:w-[320px]">
          <Input
            ref={search}
            label="Search labs"
            hideLabel
            type="search"
            placeholder="Search labs…"
            value={q}
            onChange={(e) => setParam("q", e.target.value)}
            onKeyDown={(e) => e.key === "Escape" && setParam("q", "")}
            data-testid="labs-search"
          />
        </div>
        <div className="w-[180px]">
          <Select
            label="Status"
            hideLabel
            value={status}
            onChange={(v) => setParam("status", v)}
            options={[
              { value: "all", label: "Status: all" },
              { value: "valid", label: "Status: valid" },
              { value: "invalid", label: "Status: invalid" },
            ]}
            data-testid="labs-status-filter"
          />
        </div>
      </div>
      <Card>
        {labs.isError ? (
          <ErrorState error={labs.error} onRetry={() => labs.refetch()} className="m-4" />
        ) : (
          <DataTable
            caption="Lab files in this workspace"
            columns={columns}
            rows={rows}
            rowKey={(l) => l.lab_id}
            rowTestId={(l) => `lab-row-${l.name}`}
            onRowClick={(l) => nav(`/labs/${l.lab_id}`)}
            loading={labs.isLoading}
            mobile="cards"
            data-testid="labs-table"
            empty={
              (labs.data?.items.length ?? 0) === 0 ? (
                <EmptyState
                  title="No labs in this workspace"
                  body="A lab is one YAML file that says which models answer which suites."
                  action={
                    <Button variant="primary" icon={Plus} onClick={() => nav("/labs/new")}>
                      New lab
                    </Button>
                  }
                  command="decider-lab init my-lab"
                />
              ) : (
                <EmptyState title="No labs match" body="Clear the search or the status filter." />
              )
            }
          />
        )}
      </Card>
      <DuplicateDialog lab={dup} onClose={() => setDup(null)} />
      <ConfirmDialog
        open={!!del}
        onOpenChange={(o) => !o && setDel(null)}
        title={`Delete ${del?.name ?? ""}?`}
        tone="danger"
        confirmLabel="Delete file"
        phrase={del?.name}
        body={
          <>
            This deletes <code className="font-mono">{del?.path}</code> only. Its runs, results and data files are kept.
          </>
        }
        onConfirm={async (typed) => {
          await deleteLab(del!.lab_id, typed);
          toast({ title: `Deleted ${del!.name}` });
          await qc.invalidateQueries({ queryKey: labKeys.all });
        }}
      />
      <ImportDialog open={importing} onClose={() => setImporting(false)} />
    </div>
  );
}

function DuplicateDialog({ lab, onClose }: { lab: LabListItem | null; onClose: () => void }) {
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [seed, setSeed] = useState<string | null>(null);
  const qc = useQueryClient();
  const toast = useToast();
  const nav = useNavigate();
  if (lab && seed !== lab.lab_id) {
    setSeed(lab.lab_id);
    const n = `${lab.name}-copy`;
    setName(n);
    setPath(lab.path.replace(/[^/]+$/, `${n}.yaml`));
  }
  const m = useMutation({
    mutationFn: () => duplicateLab(lab!.lab_id, { name, path }),
    onSuccess: async (item) => {
      toast({ title: `Created ${item.name}`, action: { label: "Open", onClick: () => nav(`/labs/${item.lab_id}`) } });
      await qc.invalidateQueries({ queryKey: labKeys.all });
      setSeed(null);
      onClose();
    },
  });
  const err = m.error instanceof ApiError ? m.error : null;
  const fieldErr = (f: string) => (err?.detail?.fields as { field: string; message: string }[] | undefined)?.find((x) => x.field === f)?.message;
  return (
    <Dialog
      open={!!lab}
      onOpenChange={(o) => {
        if (!o) {
          setSeed(null);
          m.reset();
          onClose();
        }
      }}
      title={`Duplicate ${lab?.name ?? ""}`}
      description="Copies the file with a new name. Runs are not copied."
      data-testid="dup-dialog"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" loading={m.isPending} onClick={() => m.mutate()} data-testid="dup-submit" disabled={!name || !path}>
            Duplicate
          </Button>
        </>
      }
    >
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          m.mutate();
        }}
      >
        <Input label="Name" value={name} onChange={(e) => setName(e.target.value)} error={fieldErr("name")} data-testid="dup-name" mono />
        <Input
          label="File"
          hint="Workspace-relative path ending in .yaml"
          value={path}
          onChange={(e) => setPath(e.target.value)}
          error={fieldErr("path") ?? (err?.code === "file_exists" ? err.message : undefined)}
          data-testid="dup-path"
          mono
        />
        {err && !fieldErr("name") && !fieldErr("path") && err.code !== "file_exists" && (
          <Callout tone="danger" alert>
            {err.message}
          </Callout>
        )}
      </form>
    </Dialog>
  );
}

function ImportDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [path, setPath] = useState("");
  const qc = useQueryClient();
  const toast = useToast();
  const nav = useNavigate();
  const m = useMutation({
    mutationFn: () => importLab(path.trim()),
    onSuccess: async (item) => {
      toast({ title: `Imported ${item.name}` });
      await qc.invalidateQueries({ queryKey: labKeys.all });
      setPath("");
      onClose();
      nav(`/labs/${item.lab_id}`);
    },
  });
  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o) {
          m.reset();
          onClose();
        }
      }}
      title="Import a lab file"
      description="Any YAML file in the workspace with a top-level models: key. Files deeper than four folders, or under runs/, are not found by the scan; importing lists them."
      data-testid="import-dialog"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" loading={m.isPending} disabled={!path.trim()} onClick={() => m.mutate()} data-testid="import-submit">
            Import
          </Button>
        </>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (path.trim()) m.mutate();
        }}
      >
        <Input
          label="Path"
          placeholder="experiments/q3.yaml"
          value={path}
          onChange={(e) => setPath(e.target.value)}
          mono
          error={m.error instanceof Error ? m.error.message : undefined}
          data-testid="import-path"
        />
      </form>
    </Dialog>
  );
}
