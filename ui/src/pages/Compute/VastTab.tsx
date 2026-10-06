import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Cloud, RotateCw, Search, Trash2 } from "lucide-react";
import {
  cloudQuery,
  computeKeys,
  destroyAllVastInstances,
  destroyVastInstance,
  getVastStatus,
  listVastInstances,
  searchVastOffers,
  type VastInstanceRow,
  type VastOfferQuery,
} from "@/api/compute";
import type { VastOffer } from "@/api/types";
import {
  Button,
  Callout,
  Card,
  CardBody,
  CardHeader,
  ConfirmDialog,
  CopyButton,
  EmptyState,
  ErrorState,
  Input,
  Select,
  Skeleton,
  Stat,
  useToast,
  type Column,
} from "@/components";
import { fmtAbsolute, fmtDuration, fmtRate, fmtRelative, fmtUsd } from "@/lib/format";
import { CloudError, CommandHint, FixturesBadge, OwnerCell, OwnerWarning, TableOrEmpty } from "./shared";

const DEFAULT_GPUS = ["RTX_4090", "RTX_A6000", "L40S", "A100_SXM4", "A100_PCIE", "H100_SXM", "H100_PCIE"];

function readOfferQuery(sp: URLSearchParams): VastOfferQuery {
  const num = (k: string, d: number) => {
    const v = Number(sp.get(k));
    return sp.get(k) != null && Number.isFinite(v) && v > 0 ? v : d;
  };
  return { gpu: sp.get("gpu") || "RTX_4090", num_gpus: num("gpus", 1), max_price: num("max", 0.8), disk_gb: num("disk", 80) };
}

export function VastTab() {
  const qc = useQueryClient();
  const toast = useToast();
  const status = useQuery({ queryKey: computeKeys.vastStatus, queryFn: getVastStatus, ...cloudQuery });
  const configured = status.data?.cli && status.data?.api_key;
  const instances = useQuery({
    queryKey: computeKeys.vastInstances,
    queryFn: listVastInstances,
    ...cloudQuery,
    enabled: !!configured,
    refetchInterval: 30_000,
  });
  const [destroying, setDestroying] = useState<VastInstanceRow | null>(null);
  const [destroyAll, setDestroyAll] = useState(false);

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: computeKeys.vast });
  };
  const fake = status.data?.fake;
  const credit = status.data?.credit_usd ?? null;
  const items = instances.data?.items ?? [];
  // vast.ai bills from creation, so loading and scheduling machines count too
  const running = items.filter((i) => !["exited", "stopped", "offline", "destroyed"].includes(String(i.status)));
  const idle = items.filter((i) => i.idle).length;

  const removeRow = (id: number) =>
    qc.setQueryData(computeKeys.vastInstances, (old: typeof instances.data) => (old ? { ...old, items: old.items.filter((x) => x.id !== id) } : old));

  const columns: Column<VastInstanceRow>[] = [
    { id: "id", header: "Instance", cell: (r) => <span className="font-mono text-mono">{r.id}</span>, sortValue: (r) => r.id },
    { id: "label", header: "Label", cell: (r) => <span className="font-mono text-mono">{r.label}</span>, sortValue: (r) => r.label, hideBelow: "md" },
    { id: "status", header: "Status", cell: (r) => r.status, sortValue: (r) => r.status },
    { id: "gpu", header: "GPU", cell: (r) => r.gpu, sortValue: (r) => r.gpu },
    { id: "rate", header: "Cost rate (est.)", label: "Cost rate (est.)", align: "right", cell: (r) => fmtRate(r.dph_total), sortValue: (r) => r.dph_total },
    {
      id: "up",
      header: "Up",
      align: "right",
      cell: (r) => (
        <span title={r.started_at ? `Started ${fmtAbsolute(r.started_at)}` : undefined}>
          {fmtDuration(r.uptime_s)}
          <span className="block text-caption text-muted">≈ {fmtUsd(r.cost_so_far_usd)} so far (estimate)</span>
        </span>
      ),
      sortValue: (r) => r.uptime_s,
    },
    { id: "owner", header: "Used by", cell: (r) => <OwnerCell idle={r.idle} jobId={r.job_id} jobTitle={r.job_title} testId={`vast-idle-${r.id}`} /> },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      label: "Actions",
      align: "right",
      cell: (r) => (
        <Button size="sm" variant="danger" icon={Trash2} onClick={() => setDestroying(r)} data-testid={`vast-destroy-${r.id}`} aria-label={`Destroy instance ${r.id}`}>
          Destroy…
        </Button>
      ),
    },
  ];

  return (
    <div className="flex flex-col gap-6">
      <Card aria-busy={status.isLoading || undefined}>
        <CardHeader
          title={
            <span className="inline-flex flex-wrap items-center gap-2">
              vast.ai <FixturesBadge fake={fake} />
            </span>
          }
          description={
            <>
              Rented GPUs for <code className="font-mono">--on vast</code>. Other machines in your vast.ai account are not shown and never touched.
            </>
          }
          actions={
            <Button size="sm" icon={RotateCw} onClick={refresh} loading={status.isFetching || instances.isFetching} data-testid="vast-refresh">
              Refresh
            </Button>
          }
        />
        <CardBody>
          {status.isLoading && <Skeleton lines={2} />}
          {status.isError && <ErrorState error={status.error} onRetry={() => void status.refetch()} />}
          {status.data && !configured && (
            <EmptyState
              data-testid="vast-not-configured"
              icon={Cloud}
              title="The vastai CLI is not installed or has no API key."
              body={
                <>
                  Install the extra, then set your key in a terminal (Studio never asks for it).
                  {status.data.error && <span className="mt-1 block font-mono text-caption">{status.data.error}</span>}
                </>
              }
              action={
                <div className="flex flex-col items-start gap-2 text-left">
                  <CommandHint label="1." command="pip install 'decider-lab[vast]'" />
                  <CommandHint label="2." command="vastai set api-key <key>" />
                </div>
              }
            />
          )}
          {configured && (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <Stat
                data-testid="vast-credit"
                label="Credit"
                value={fmtUsd(credit)}
                tone={credit != null && credit < 5 ? "warning" : "neutral"}
                sub={status.data?.as_of ? `as of ${fmtRelative(status.data.as_of)}` : undefined}
                title={status.data?.as_of ? fmtAbsolute(status.data.as_of) : undefined}
              />
              <Stat data-testid="vast-running" label="Active instances (billing)" value={instances.data ? String(running.length) : "—"} sub={idle ? `${idle} idle · billing` : "none idle"} tone={idle ? "warning" : "neutral"} />
              <Stat
                data-testid="vast-burn"
                label="Burn rate (est.)"
                value={instances.data ? fmtRate(instances.data.usd_per_hour ?? 0) : "—"}
                sub="sum of active decider-lab instances"
              />
            </div>
          )}
        </CardBody>
      </Card>

      {configured && (
        <Card>
          <CardHeader
            title="Instances started by decider-lab"
            description="Machines labelled decider-lab. decider-lab destroys them when a run ends; anything listed here is still billing."
            actions={
              items.length > 0 && (
                <Button size="sm" variant="danger" icon={Trash2} onClick={() => setDestroyAll(true)} data-testid="vast-destroy-all">
                  Destroy all…
                </Button>
              )
            }
          />
          <CardBody className="px-0 py-0 sm:px-0">
            {instances.isError ? (
              <div className="p-4">
                <CloudError error={instances.error} onRetry={() => void instances.refetch()} />
              </div>
            ) : (
              <TableOrEmpty
                data-testid="vast-instances"
                caption="vast.ai instances started by decider-lab"
                columns={columns}
                rows={items}
                rowKey={(r) => String(r.id)}
                rowTestId={(r) => `vast-instance-${r.id}`}
                loading={instances.isLoading}
                mobile="cards"
                className="max-sm:p-3"
                empty={<EmptyState title="No decider-lab instances are running." body="Nothing is billing on vast.ai for decider-lab right now." command="decider-lab compute ls --on vast" />}
              />
            )}
          </CardBody>
        </Card>
      )}

      {configured && <OffersPanel fake={fake} />}

      <ConfirmDialog
        open={destroying != null}
        onOpenChange={(o) => !o && setDestroying(null)}
        title={`Destroy instance ${destroying?.id ?? ""}`}
        tone="danger"
        confirmLabel="Destroy instance"
        phrase={destroying ? String(destroying.id) : undefined}
        body={
          destroying && (
            <>
              This destroys vast.ai instance <span className="font-mono">{destroying.id}</span> ({destroying.gpu}, {fmtRate(destroying.dph_total)}) and everything on its disk. Billing stops once it is gone.
              <OwnerWarning jobId={destroying.job_id} jobTitle={destroying.job_title} />
            </>
          )
        }
        onConfirm={async (typed) => {
          const id = destroying!.id;
          const res = await destroyVastInstance(id, typed);
          if (!res.destroyed) throw new Error(res.message ?? `Instance ${id} is still listed. Check \`vastai show instances\`.`);
          removeRow(id);
          toast({ title: `Destroyed instance ${id}` });
          void qc.invalidateQueries({ queryKey: computeKeys.vast });
        }}
      />
      <ConfirmDialog
        open={destroyAll}
        onOpenChange={setDestroyAll}
        title="Destroy all decider-lab instances"
        tone="danger"
        confirmLabel="Destroy all instances"
        phrase="destroy all"
        body={
          <>
            This destroys {items.length} vast.ai instance{items.length === 1 ? "" : "s"} labelled decider-lab. Machines a running job uses make that job fail.
          </>
        }
        onConfirm={async (typed) => {
          const res = await destroyAllVastInstances(typed);
          const left = res.results.filter((r) => !r.destroyed);
          void qc.invalidateQueries({ queryKey: computeKeys.vast });
          if (left.length) throw new Error(`Instances ${left.map((r) => r.id).join(", ")} are still listed. Check \`vastai show instances\`.`);
          toast({ title: `Destroyed ${res.results.length} instance${res.results.length === 1 ? "" : "s"}` });
        }}
      />
    </div>
  );
}

function OffersPanel({ fake }: { fake?: boolean }) {
  const [sp, setSp] = useSearchParams();
  const applied = readOfferQuery(sp);
  const [form, setForm] = useState({ gpu: applied.gpu, gpus: String(applied.num_gpus), max: String(applied.max_price), disk: String(applied.disk_gb) });
  const [fieldError, setFieldError] = useState<string | null>(null);
  const offers = useQuery({ queryKey: computeKeys.vastOffers(applied), queryFn: () => searchVastOffers(applied), ...cloudQuery });
  const gpuNames = offers.data?.gpu_names?.length ? offers.data.gpu_names : DEFAULT_GPUS;
  const gpuOptions = (gpuNames.includes(form.gpu) ? gpuNames : [form.gpu, ...gpuNames]).map((g) => ({ value: g, label: g.replace(/_/g, " ") }));

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const gpus = Number(form.gpus);
    const max = Number(form.max);
    const disk = Number(form.disk);
    if (!Number.isInteger(gpus) || gpus < 1 || gpus > 16) return setFieldError("GPUs must be a whole number from 1 to 16.");
    if (!(max > 0) || max > 100) return setFieldError("Max $/h must be more than 0.");
    if (!Number.isInteger(disk) || disk < 10) return setFieldError("Disk must be at least 10 GB.");
    setFieldError(null);
    setSp(
      (prev) => {
        const n = new URLSearchParams(prev);
        n.set("gpu", form.gpu);
        n.set("gpus", String(gpus));
        n.set("max", String(max));
        n.set("disk", String(disk));
        return n;
      },
      { replace: true },
    );
    if (gpus === applied.num_gpus && max === applied.max_price && disk === applied.disk_gb && form.gpu === applied.gpu) void offers.refetch();
  };

  const spec = (o: VastOffer) => `compute: {backend: vast, vast: {gpu: ${o.gpu_name}, num_gpus: ${o.num_gpus}, max_price: ${applied.max_price}, offer: ${o.id}}}`;
  const columns: Column<VastOffer>[] = [
    { id: "id", header: "Offer", cell: (o) => <span className="font-mono text-mono">{o.id}</span>, sortValue: (o) => o.id },
    { id: "gpu", header: "GPU", cell: (o) => `${o.num_gpus}× ${o.gpu_name}`, sortValue: (o) => o.gpu_name },
    { id: "rate", header: "$/h", align: "right", cell: (o) => fmtRate(o.dph_total), sortValue: (o) => o.dph_total },
    { id: "vram", header: "VRAM", align: "right", cell: (o) => `${o.gpu_ram_gb} GB`, sortValue: (o) => o.gpu_ram_gb },
    { id: "cuda", header: "CUDA", align: "right", cell: (o) => o.cuda_max_good ?? "—", sortValue: (o) => o.cuda_max_good, hideBelow: "md" },
    { id: "rel", header: "Reliability", align: "right", cell: (o) => (o.reliability != null ? o.reliability.toFixed(3) : "—"), sortValue: (o) => o.reliability },
    { id: "net", header: "Down", align: "right", cell: (o) => (o.inet_down_mbps != null ? `${Math.round(o.inet_down_mbps)} Mb/s` : "—"), sortValue: (o) => o.inet_down_mbps, hideBelow: "md" },
    { id: "disk", header: "Disk", align: "right", cell: (o) => (o.disk_space_gb != null ? `${Math.round(o.disk_space_gb)} GB` : "—"), sortValue: (o) => o.disk_space_gb, hideBelow: "md" },
    { id: "geo", header: "Location", cell: (o) => o.geolocation ?? "—", sortValue: (o) => o.geolocation },
    {
      id: "use",
      header: <span className="sr-only">Lab spec</span>,
      label: "Lab spec",
      align: "right",
      cell: (o) => (
        <span className="inline-flex items-center gap-1 text-small text-muted" data-testid={`vast-offer-copy-${o.id}`}>
          <span className="max-md:hidden">Copy lab spec</span>
          <CopyButton text={spec(o)} label={`Copy compute spec for offer ${o.id}`} />
        </span>
      ),
    },
  ];
  return (
    <Card>
      <CardHeader
        title={
          <span className="inline-flex flex-wrap items-center gap-2">
            Offers <FixturesBadge fake={fake} />
          </span>
        }
        description="Rentable, verified machines matching your filters, cheapest first. Searching rents nothing."
      />
      <CardBody>
        <form onSubmit={submit} noValidate className="grid grid-cols-2 items-end gap-3 sm:grid-cols-[minmax(160px,1.4fr)_repeat(3,minmax(90px,1fr))_auto]" data-testid="vast-offers-form">
          <div className="col-span-2 sm:col-span-1">
            <Select label="GPU" options={gpuOptions} value={form.gpu} onChange={(v) => setForm({ ...form, gpu: v })} data-testid="vast-offers-gpu" />
          </div>
          <Input label="GPUs" type="number" min={1} max={16} step={1} inputMode="numeric" value={form.gpus} onChange={(e) => setForm({ ...form, gpus: e.target.value })} data-testid="vast-offers-num" />
          <Input label="Max $/h" type="number" min={0.01} step={0.01} inputMode="decimal" value={form.max} onChange={(e) => setForm({ ...form, max: e.target.value })} data-testid="vast-offers-max" />
          <Input label="Disk GB" type="number" min={10} step={10} inputMode="numeric" value={form.disk} onChange={(e) => setForm({ ...form, disk: e.target.value })} data-testid="vast-offers-disk" />
          <Button type="submit" variant="primary" icon={Search} loading={offers.isFetching} data-testid="vast-offers-search" className="max-sm:col-span-2">
            Search
          </Button>
        </form>
        {fieldError && (
          <Callout tone="danger" alert className="mt-3" data-testid="vast-offers-error">
            {fieldError}
          </Callout>
        )}
      </CardBody>
      <div className="border-t border-border max-sm:p-3">
        {offers.isError ? (
          <div className="p-4">
            <CloudError error={offers.error} onRetry={() => void offers.refetch()} />
          </div>
        ) : (
          <TableOrEmpty
            data-testid="vast-offers"
            caption={`vast.ai offers for ${applied.num_gpus}× ${applied.gpu} under ${fmtRate(applied.max_price)}`}
            columns={columns}
            rows={offers.data?.items ?? []}
            rowKey={(o) => String(o.id)}
            rowTestId={(o) => `vast-offer-${o.id}`}
            loading={offers.isLoading}
            defaultSort={{ id: "rate", dir: "asc" }}
            mobile="cards"
            empty={
              <EmptyState
                title="No offers match."
                body="Raise the max $/h, lower the disk, or pick another GPU."
                command={`decider-lab compute offers --on vast --gpu ${applied.gpu}`}
              />
            }
          />
        )}
      </div>
    </Card>
  );
}
