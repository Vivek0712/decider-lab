import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Boxes, ClipboardCopy, Copy, Download, Trash2 } from "lucide-react";
import { deleteModel, listModels, modelKeys, ref8, labSnippet, type CachedModel } from "@/api/models";
import { Badge } from "@/components/Badge";
import { Button, IconButton } from "@/components/Button";
import { Card, CardBody, CardHeader } from "@/components/Card";
import { CodeInline, useCopy } from "@/components/Code";
import { ConfirmDialog } from "@/components/Confirm";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { PageHeader } from "@/components/PageHeader";
import { DataTable, type Column } from "@/components/Table";
import { TabPanel, Tabs, useTabParam } from "@/components/Tabs";
import { useToast } from "@/components/Toast";
import { Tooltip } from "@/components/Tooltip";
import { useHotkeys } from "@/hooks/useHotkeys";
import { fmtAbsolute, fmtRelative, truncateMiddle } from "@/lib/format";
import { useRegisterCommands } from "@/palette/registry";
import { PullForm } from "./PullForm";

const fmtGb = (x: number | null | undefined) => (x == null ? "—" : `${x.toFixed(2)} GB`);

// Owner: Models area. Tabs: Cache | Pull. DESIGN.md 4.5; API.md section 8 (src/api/models.ts).
export default function ModelsPage() {
  const [tab, setTab] = useTabParam("cache");
  const [prefill, setPrefill] = useState<string | null>(null);
  useHotkeys([{ keys: "n", handler: () => setTab("pull") }]);
  useRegisterCommands("models-page", [
    { id: "model.pull", label: "Pull a model…", section: "Commands", icon: Download, shortcut: ["n"], run: ({ close }) => { close(); setTab("pull"); } },
  ], []);
  return (
    <div data-testid="page-models">
      <PageHeader
        title="Models"
        description="Checkpoints pulled into the decider-lab cache, and pulling new ones."
        actions={
          <Button variant="primary" icon={Download} kbd={["n"]} onClick={() => setTab("pull")} data-testid="pull-open">
            Pull a model…
          </Button>
        }
      />
      <Tabs
        label="Models"
        value={tab}
        onChange={setTab}
        testIdPrefix="models-tab"
        tabs={[
          { id: "cache", label: "Cache" },
          { id: "pull", label: "Pull" },
        ]}
        className="mb-5"
      />
      <TabPanel id="cache" active={tab === "cache"}>
        <CacheTab onPull={(src) => { setPrefill(src); setTab("pull"); }} />
      </TabPanel>
      <TabPanel id="pull" active={tab === "pull"}>
        <PullForm initialSource={prefill} onShowCache={() => setTab("cache")} />
      </TabPanel>
    </div>
  );
}

function CacheTab({ onPull }: { onPull: (source: string | null) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [copy] = useCopy();
  const [deleting, setDeleting] = useState<CachedModel | null>(null);
  const q = useQuery({ queryKey: modelKeys.all, queryFn: listModels, refetchInterval: 15_000 });
  const items = useMemo(() => q.data?.items ?? [], [q.data]);
  const ours = items.filter((m) => m.location === "decider-lab");
  const hf = items.filter((m) => m.location === "hf");

  const columns: Column<CachedModel>[] = [
    {
      id: "source",
      header: "Source",
      sortValue: (m) => m.source,
      cell: (m) => (
        <span className="flex min-w-0 items-center gap-2">
          <Tooltip content={<span className="font-mono">{m.source}</span>}>
            <span tabIndex={0} className="min-w-0 truncate font-mono text-mono" aria-label={m.source}>
              {truncateMiddle(m.source, 52)}
            </span>
          </Tooltip>
          {m.location === "hf" && <Badge tone="info">HF cache</Badge>}
        </span>
      ),
    },
    { id: "kind", header: "Kind", sortValue: (m) => m.kind, cell: (m) => <Badge>{m.kind}</Badge> },
    {
      id: "ref",
      header: "Ref",
      label: "Ref",
      cell: (m) => (
        <span className="flex items-center gap-2 whitespace-nowrap">
          <code className="font-mono text-mono" title={m.ref ?? undefined}>
            {m.ref_kind === "sha256" ? "sha256 " : m.ref_kind === "etags" ? "etags " : ""}
            {m.ref ? m.ref.slice(0, 8) : "—"}
          </code>
          {m.pinned ? (
            <Badge tone="success" title={m.ref_kind === "commit" ? "a full commit" : "sha256 verified at pull time"}>
              pinned
            </Badge>
          ) : (
            <Badge
              tone="warning"
              title={m.ref_kind === "commit" ? `resolved to ${m.ref?.slice(0, 12)} at pull time; the branch may move` : "the hash was recorded, not verified against a given sha256"}
            >
              unpinned
            </Badge>
          )}
        </span>
      ),
    },
    { id: "size", header: "Size", align: "right", sortValue: (m) => m.size_gb, cell: (m) => fmtGb(m.size_gb) },
    {
      id: "pulled",
      header: "Pulled",
      sortValue: (m) => m.pulled_at,
      hideBelow: "md",
      cell: (m) => <span title={fmtAbsolute(m.pulled_at)}>{fmtRelative(m.pulled_at)}</span>,
    },
    {
      id: "labs",
      header: "Used by",
      hideBelow: "md",
      cell: (m) => (m.used_by_labs.length ? m.used_by_labs.join(", ") : <span className="text-subtle">no lab</span>),
    },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      label: "Actions",
      cell: (m) => (
        <span className="flex items-center justify-end gap-1">
          <IconButton
            icon={Copy}
            size="sm"
            label={`Copy source ${m.source}`}
            data-testid={`model-copy-${ref8(m)}`}
            onClick={async () => {
              await copy(m.source);
              toast({ title: "Source copied" });
            }}
          />
          <IconButton
            icon={ClipboardCopy}
            size="sm"
            label={`Copy lab snippet for ${m.source}`}
            data-testid={`model-snippet-${ref8(m)}`}
            onClick={async () => {
              await copy(labSnippet(m));
              toast({ title: "Lab snippet copied", description: "Paste it under models: in a lab (Labs > Editor)." });
            }}
          />
          {m.deletable && (
            <IconButton
              icon={Trash2}
              size="sm"
              label={`Delete ${m.source} from the cache`}
              data-testid={`model-delete-${ref8(m)}`}
              disabled={!!m.in_use_by_job}
              onClick={() => setDeleting(m)}
            />
          )}
        </span>
      ),
    },
  ];

  if (q.error) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const empty = (
    <EmptyState
      icon={Boxes}
      title="No models pulled yet"
      body={
        <>
          Labs pull their <code className="font-mono">serve:</code> sources on first run; pull one now to check it.
        </>
      }
      action={
        <Button variant="primary" icon={Download} onClick={() => onPull(null)}>
          Pull a model…
        </Button>
      }
      command="decider-lab pull hf://org/repo@commit"
    />
  );

  return (
    <div className="flex flex-col gap-5">
      <Card>
        <CardHeader
          title="decider-lab cache"
          description={
            q.data ? (
              <span data-testid="models-summary">
                <CodeInline code={q.data.cache_dir} copy /> · {ours.length} {ours.length === 1 ? "model" : "models"} · {fmtGb(q.data.total_gb)}
              </span>
            ) : (
              "Loading…"
            )
          }
        />
        <CardBody className="p-0 sm:p-0">
          <DataTable
            caption="Models in the decider-lab cache"
            columns={columns}
            rows={ours}
            rowKey={(m) => m.model_key}
            rowTestId={(m) => `model-row-${ref8(m)}`}
            loading={q.isLoading}
            empty={empty}
            mobile={ours.length ? "cards" : "scroll"}
            data-testid="models-table"
            defaultSort={{ id: "pulled", dir: "desc" }}
          />
        </CardBody>
      </Card>
      <Card>
        <CardHeader
          as="h2"
          title="Hugging Face cache"
          description={q.data?.hf_cache_note ?? "Strands Decider snapshots in the Hugging Face cache."}
        />
        <CardBody className="p-0 sm:p-0">
          <DataTable
            caption="Strands Decider snapshots in the Hugging Face cache"
            columns={columns}
            rows={hf}
            rowKey={(m) => m.model_key}
            rowTestId={(m) => `model-row-${ref8(m)}`}
            loading={q.isLoading}
            mobile={hf.length ? "cards" : "scroll"}
            data-testid="models-hf-table"
            empty={<p className="px-4 py-6 text-small text-muted">No strands-decider snapshots in the Hugging Face cache.</p>}
          />
        </CardBody>
      </Card>
      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        title="Delete from the cache"
        tone="danger"
        confirmLabel="Delete model"
        phrase={deleting ? ref8(deleting) : undefined}
        body={
          deleting && (
            <div className="flex flex-col gap-2">
              <p className="break-all">
                Removes <code className="font-mono">{deleting.dir}</code> ({fmtGb(deleting.size_gb)}). The Hugging Face cache is never
                touched.
              </p>
              {deleting.used_by_labs.length > 0 && (
                <p className="text-warning">
                  {deleting.used_by_labs.join(", ")} {deleting.used_by_labs.length === 1 ? "uses" : "use"} this model; its next run will pull it
                  again.
                </p>
              )}
            </div>
          )
        }
        onConfirm={async (typed) => {
          if (!deleting) return;
          const res = await deleteModel(deleting.model_key, typed);
          toast({ title: "Model deleted", description: `${fmtGb(res.freed_gb)} freed` });
          await qc.invalidateQueries({ queryKey: modelKeys.all });
        }}
      />
    </div>
  );
}
