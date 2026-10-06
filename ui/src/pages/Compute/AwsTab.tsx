import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { CircleCheck, Eye, EyeOff, Plus, RotateCw, Search, Trash2, TriangleAlert } from "lucide-react";
import {
  cloudQuery,
  computeKeys,
  getAwsIdentity,
  getAwsQuotas,
  listAwsInstances,
  listAwsInstanceTypes,
  listAwsProfiles,
  listBedrockModels,
  maskAccount,
  modelKey,
  terminateAllAwsInstances,
  terminateAwsInstance,
  type AwsInstanceRow,
} from "@/api/compute";
import type { AwsQuota, BedrockModel } from "@/api/types";
import {
  Badge,
  Button,
  Callout,
  Card,
  CardBody,
  CardHeader,
  ConfirmDialog,
  CopyButton,
  DataTable,
  Dialog,
  EmptyState,
  ErrorState,
  IconButton,
  Input,
  ProgressBar,
  Select,
  Skeleton,
  useCopy,
  useToast,
  type Column,
} from "@/components";
import { useLocalStorage } from "@/hooks/useLocalStorage";
import { fmtAbsolute, fmtDuration, fmtRate } from "@/lib/format";
import { CloudError, CommandHint, Fact, FixturesBadge, OwnerCell, OwnerWarning, TableOrEmpty, WrapCode } from "./shared";

const FALLBACK_REGIONS = ["us-east-1", "us-east-2", "us-west-2", "eu-west-1", "eu-central-1", "ap-northeast-1", "ap-southeast-2"];
const FAMILY_LABEL: Record<string, string> = { g: "G and VT", p: "P", standard: "Standard" };
const FAMILY_USE: Record<string, string> = { g: "g4dn, g5, g6, g6e (1-8 GPUs)", p: "p4d, p5 (8 GPUs)", standard: "c7i, m7i (CPU-only evaluation)" };

/** Profile and region live in the URL (`?profile=&region=`), with the last choice remembered per browser. */
function useAwsScope() {
  const [sp, setSp] = useSearchParams();
  const profiles = useQuery({ queryKey: computeKeys.awsProfiles, queryFn: listAwsProfiles, ...cloudQuery, staleTime: 300_000 });
  const [saved, setSaved] = useLocalStorage<{ profile?: string; region?: string }>("dl-compute-aws-scope", {});
  const list = profiles.data?.items ?? [];
  const fallbackProfile = profiles.data?.env_profile ?? (list.includes("default") ? "default" : list[0]) ?? "";
  const urlProfile = sp.get("profile");
  const profile = urlProfile ?? (saved.profile && list.includes(saved.profile) ? saved.profile : fallbackProfile);
  const region = sp.get("region") ?? saved.region ?? profiles.data?.default_region ?? "us-east-1";
  const set = (patch: { profile?: string; region?: string }) => {
    setSaved({ profile, region, ...patch });
    setSp(
      (prev) => {
        const n = new URLSearchParams(prev);
        if (patch.profile !== undefined) n.set("profile", patch.profile);
        if (patch.region !== undefined) n.set("region", patch.region);
        return n;
      },
      { replace: true },
    );
  };
  return { profiles, profile: profile || undefined, region, set };
}

export function AwsTab() {
  const qc = useQueryClient();
  const { profiles, profile, region, set } = useAwsScope();
  const fake = profiles.data?.fake;
  const ready = profiles.isSuccess;
  const regions = profiles.data?.regions?.length ? profiles.data.regions : FALLBACK_REGIONS;
  const regionOptions = (regions.includes(region) ? regions : [region, ...regions]).map((r) => ({ value: r, label: r }));
  const profileItems = profiles.data?.items ?? [];
  const profileOptions = profileItems.length
    ? (profile && !profileItems.includes(profile) ? [profile, ...profileItems] : profileItems).map((p) => ({ value: p, label: p }))
    : [{ value: "", label: "default credential chain" }];

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader
          title={
            <span className="inline-flex flex-wrap items-center gap-2">
              AWS <FixturesBadge fake={fake} />
            </span>
          }
          description="Studio reads AWS with boto3 under the profile you pick. It never reads or shows key material; everything here is read-only except Terminate."
          actions={
            <Button size="sm" icon={RotateCw} onClick={() => void qc.invalidateQueries({ queryKey: computeKeys.aws })} data-testid="aws-refresh">
              Refresh
            </Button>
          }
        />
        <CardBody>
          {profiles.isLoading && <Skeleton lines={2} />}
          {profiles.isError && <ErrorState error={profiles.error} onRetry={() => void profiles.refetch()} />}
          {profiles.data && (
            <div className="flex flex-col gap-3">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:max-w-[640px]">
                <Select
                  label="Profile"
                  options={profileOptions}
                  value={profile ?? ""}
                  onChange={(v) => set({ profile: v })}
                  data-testid="aws-profile"
                  hint={profiles.data.env_profile ? `AWS_PROFILE is ${profiles.data.env_profile}` : "From ~/.aws/config and ~/.aws/credentials (names only)"}
                />
                <Select label="Region" options={regionOptions} value={region} onChange={(v) => set({ region: v })} data-testid="aws-region" />
              </div>
              {!profiles.data.boto3 && (
                <Callout tone="warning" title="boto3 is not installed">
                  <CommandHint label="Install the AWS extra and restart Studio:" command="pip install 'decider-lab[aws]'" />
                </Callout>
              )}
            </div>
          )}
        </CardBody>
      </Card>
      {ready && profiles.data?.boto3 !== false && (
        <>
          <IdentityPanel profile={profile} region={region} fake={fake} />
          <div className="grid gap-6 lg:grid-cols-2">
            <QuotasPanel profile={profile} region={region} fake={fake} />
            <InstanceTypesPanel />
          </div>
          <InstancesPanel profile={profile} region={region} fake={fake} />
          <BedrockPanel profile={profile} region={region} fake={fake} />
        </>
      )}
    </div>
  );
}

function IdentityPanel({ profile, region, fake }: { profile?: string; region: string; fake?: boolean }) {
  const q = useQuery({ queryKey: computeKeys.awsIdentity(profile, region), queryFn: () => getAwsIdentity(profile, region), ...cloudQuery });
  const [reveal, setReveal] = useState(false);
  const d = q.data;
  const shown = profile ?? d?.profile ?? "default";
  return (
    <Card data-testid="aws-identity" aria-busy={q.isLoading || undefined}>
      <CardHeader
        title={
          <span className="inline-flex flex-wrap items-center gap-2">
            Identity <FixturesBadge fake={fake} />
          </span>
        }
        description={`Who these credentials are (sts:GetCallerIdentity) in ${region}.`}
      />
      <CardBody>
        {q.isLoading && <Skeleton lines={2} />}
        {q.isError && <CloudError error={q.error} onRetry={() => void q.refetch()} />}
        {d && !d.ok && (
          <Callout tone="warning" title="These credentials cannot be used" data-testid="aws-identity-error">
            <span className="block break-words">{d.error}</span>
            <span className="mt-2 block">
              <CommandHint label="Fix it in a terminal (Studio never runs it or asks for keys):" command={d.fix ?? `aws sso login --profile ${shown}`} />
            </span>
          </Callout>
        )}
        {d?.ok && (
          <dl className="grid grid-cols-1 gap-4 sm:grid-cols-[auto_minmax(0,1fr)_auto]">
            <Fact label="Account" testId="aws-account">
              <span className="inline-flex items-center gap-1 font-mono text-mono">
                <span data-testid="aws-account-value">{reveal ? d.account : maskAccount(d.account)}</span>
                <IconButton
                  size="sm"
                  icon={reveal ? EyeOff : Eye}
                  label={reveal ? "Hide account id" : "Show account id"}
                  onClick={() => setReveal(!reveal)}
                  data-testid="aws-account-reveal"
                />
              </span>
            </Fact>
            <Fact label="ARN">
              <span className="break-all font-mono text-mono" data-testid="aws-arn">
                {reveal || !d.arn || !d.account ? d.arn : d.arn.split(d.account).join(maskAccount(d.account))}
              </span>
            </Fact>
            <Fact label="Status">
              <Badge tone="success" icon={CircleCheck}>
                credentials valid
              </Badge>
            </Fact>
          </dl>
        )}
      </CardBody>
    </Card>
  );
}

function quotaCommand(q: AwsQuota, region: string, profile?: string) {
  const want = q.family === "p" ? 192 : Math.max(8, (q.limit_vcpus ?? 0) * 2);
  return `aws service-quotas request-service-quota-increase --service-code ec2 --quota-code ${q.code} --desired-value ${want} --region ${region}${profile ? ` --profile ${profile}` : ""}`;
}

function QuotasPanel({ profile, region, fake }: { profile?: string; region: string; fake?: boolean }) {
  const q = useQuery({ queryKey: computeKeys.awsQuotas(profile, region), queryFn: () => getAwsQuotas(profile, region), ...cloudQuery });
  return (
    <Card aria-busy={q.isLoading || undefined} data-testid="aws-quotas">
      <CardHeader
        title={
          <span className="inline-flex flex-wrap items-center gap-2">
            vCPU quotas (On-Demand) <FixturesBadge fake={fake} />
          </span>
        }
        description="New accounts often have 0 vCPUs for G and P instances; request an increase before the first GPU run."
      />
      <CardBody>
        {q.isLoading && <Skeleton lines={4} />}
        {q.isError && <CloudError error={q.error} onRetry={() => void q.refetch()} />}
        {q.data?.error && (
          <Callout tone="warning" className="mb-3">
            {q.data.error}
          </Callout>
        )}
        {q.data && (
          <ul className="flex flex-col divide-y divide-border">
            {q.data.items.map((it) => {
              const limit = it.limit_vcpus;
              const used = it.used_vcpus;
              const zero = limit === 0;
              return (
                <li key={it.family} className="flex flex-col gap-2 py-3 first:pt-0 last:pb-0" data-testid={`aws-quota-${it.family}`}>
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <div className="min-w-0">
                      <div className="font-semibold">{FAMILY_LABEL[it.family] ?? it.family}</div>
                      <div className="text-caption text-muted">{FAMILY_USE[it.family] ?? it.name}</div>
                    </div>
                    <div className="tnum text-right text-body">
                      {limit == null ? "—" : `${limit} vCPU`}
                      {used != null && limit != null && <span className="text-muted"> · {used} used</span>}
                    </div>
                  </div>
                  {limit != null && limit > 0 && (
                    <ProgressBar value={Math.min(1, (used ?? 0) / limit)} label={`${FAMILY_LABEL[it.family] ?? it.family} vCPUs used`} />
                  )}
                  <div className="flex flex-wrap items-center gap-2 text-caption text-muted">
                    <span className="font-mono">{it.code}</span>
                    <span className="max-sm:hidden">{it.name}</span>
                  </div>
                  {it.error && (
                    <Callout tone="warning" data-testid={`aws-quota-error-${it.family}`}>
                      {it.error}
                    </Callout>
                  )}
                  {zero && (
                    <Callout tone="warning" title={`Request an increase of ${it.code} to use ${it.family === "p" ? "p4d/p5" : FAMILY_USE[it.family] ?? it.family}`} data-testid={`aws-quota-hint-${it.family}`}>
                      <span className="block">Service Quotas › Amazon EC2 › {it.name}, or from a terminal:</span>
                      <WrapCode code={quotaCommand(it, region, profile)} label="Copy command" className="mt-2" />
                    </Callout>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </CardBody>
    </Card>
  );
}

function InstanceTypesPanel() {
  const q = useQuery({ queryKey: computeKeys.awsInstanceTypes, queryFn: listAwsInstanceTypes, staleTime: Infinity });
  const columns: Column<NonNullable<typeof q.data>["items"][number]>[] = [
    { id: "type", header: "Type", cell: (t) => <span className="font-mono text-mono">{t.type}</span>, sortValue: (t) => t.type },
    { id: "gpu", header: "GPU", cell: (t) => t.gpu ?? "CPU only", sortValue: (t) => t.gpu ?? "" },
    { id: "vcpus", header: "vCPU", align: "right", cell: (t) => t.vcpus, sortValue: (t) => t.vcpus },
    { id: "rate", header: "Cost rate (est.)", label: "Cost rate (est.)", align: "right", cell: (t) => fmtRate(t.usd_per_hour), sortValue: (t) => t.usd_per_hour },
  ];
  return (
    <Card data-testid="aws-instance-types">
      <CardHeader title="Instance types" description={q.data ? `Approximate on-demand list price (us-east-1, table dated ${q.data.as_of}). Your bill may differ.` : undefined} />
      <div className="scrollbar-thin max-h-[360px] overflow-y-auto max-sm:p-3" tabIndex={0} role="region" aria-label="Instance types and approximate prices">
        {q.isError ? (
          <div className="p-4">
            <ErrorState error={q.error} onRetry={() => void q.refetch()} />
          </div>
        ) : (
          <DataTable caption="AWS instance types and approximate prices" columns={columns} rows={q.data?.items ?? []} rowKey={(t) => t.type} loading={q.isLoading} defaultSort={{ id: "rate", dir: "asc" }} mobile="cards" />
        )}
      </div>
    </Card>
  );
}

function InstancesPanel({ profile, region, fake }: { profile?: string; region: string; fake?: boolean }) {
  const qc = useQueryClient();
  const toast = useToast();
  const key = computeKeys.awsInstances(profile, region);
  const q = useQuery({ queryKey: key, queryFn: () => listAwsInstances(profile, region), ...cloudQuery, refetchInterval: 30_000 });
  const [target, setTarget] = useState<AwsInstanceRow | null>(null);
  const [all, setAll] = useState(false);
  const items = q.data?.items ?? [];
  const scope = { profile, region };
  const columns: Column<AwsInstanceRow>[] = [
    { id: "id", header: "Instance", cell: (r) => <span className="font-mono text-mono">{r.id}</span>, sortValue: (r) => r.id },
    { id: "type", header: "Type", cell: (r) => <span className="font-mono text-mono">{r.type}</span>, sortValue: (r) => r.type },
    { id: "state", header: "State", cell: (r) => r.state, sortValue: (r) => r.state },
    { id: "lab", header: "Lab", cell: (r) => r.lab ?? "—", sortValue: (r) => r.lab, hideBelow: "md" },
    { id: "up", header: "Up", align: "right", cell: (r) => <span title={`Launched ${fmtAbsolute(r.launched_at)}`}>{fmtDuration(r.uptime_s)}</span>, sortValue: (r) => r.uptime_s },
    {
      id: "rate",
      header: "Cost rate (est.)",
      label: "Cost rate (est.)",
      align: "right",
      cell: (r) => (r.usd_per_hour == null ? <span title="Not in the static price table">—</span> : `≈ ${fmtRate(r.usd_per_hour)}`),
      sortValue: (r) => r.usd_per_hour,
    },
    { id: "ip", header: "Public IP", cell: (r) => <span className="font-mono text-mono">{r.public_ip_masked ?? "—"}</span>, hideBelow: "md" },
    { id: "owner", header: "Used by", cell: (r) => <OwnerCell idle={r.idle} jobId={r.job_id} jobTitle={r.job_title} testId={`aws-idle-${r.id}`} /> },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      label: "Actions",
      align: "right",
      cell: (r) => (
        <Button size="sm" variant="danger" icon={Trash2} onClick={() => setTarget(r)} data-testid={`aws-terminate-${r.id}`} aria-label={`Terminate instance ${r.id}`}>
          Terminate…
        </Button>
      ),
    },
  ];
  return (
    <Card data-testid="aws-instances">
      <CardHeader
        title={
          <span className="inline-flex flex-wrap items-center gap-2">
            Instances tagged decider-lab <FixturesBadge fake={fake} />
          </span>
        }
        description={
          q.data ? (
            <>
              {items.length} in {region} · burn rate (est.) <span className="tnum" data-testid="aws-burn">{q.data.usd_per_hour == null ? "—" : fmtRate(q.data.usd_per_hour)}</span>. Other instances in the account are not shown and never touched.
            </>
          ) : (
            `Only instances tagged decider-lab in ${region} are listed or terminable.`
          )
        }
        actions={
          items.length > 0 && (
            <Button size="sm" variant="danger" icon={Trash2} onClick={() => setAll(true)} data-testid="aws-terminate-all">
              Terminate all…
            </Button>
          )
        }
      />
      <div className="max-sm:p-3">
        {q.isError ? (
          <div className="p-4">
            <CloudError error={q.error} onRetry={() => void q.refetch()} />
          </div>
        ) : (
          <TableOrEmpty
            caption={`EC2 instances tagged decider-lab in ${region}`}
            columns={columns}
            rows={items}
            rowKey={(r) => r.id}
            rowTestId={(r) => `aws-instance-${r.id}`}
            loading={q.isLoading}
            mobile="cards"
            empty={<EmptyState title="No decider-lab instances in this region." body="Nothing launched by decider-lab is running or stopped here." command={`decider-lab compute ls --on aws --region ${region}`} />}
          />
        )}
      </div>
      <ConfirmDialog
        open={target != null}
        onOpenChange={(o) => !o && setTarget(null)}
        title="Terminate instance"
        tone="danger"
        confirmLabel="Terminate instance"
        phrase={target?.id}
        body={
          target && (
            <>
              This terminates <span className="font-mono">{target.id}</span> ({target.type}) in {region} and deletes its disk. Billing stops once it has shut down.
              <OwnerWarning jobId={target.job_id} jobTitle={target.job_title} />
            </>
          )
        }
        onConfirm={async (typed) => {
          const id = target!.id;
          await terminateAwsInstance(id, typed, scope);
          qc.setQueryData(key, (old: typeof q.data) => (old ? { ...old, items: old.items.filter((x) => x.id !== id) } : old));
          toast({ title: `Terminating ${id}` });
          void qc.invalidateQueries({ queryKey: key });
        }}
      />
      <ConfirmDialog
        open={all}
        onOpenChange={setAll}
        title="Terminate all decider-lab instances"
        tone="danger"
        confirmLabel="Terminate all instances"
        phrase="terminate all"
        body={<>This terminates {items.length} instance{items.length === 1 ? "" : "s"} tagged decider-lab in {region}. Instances a running job uses make that job fail.</>}
        onConfirm={async (typed) => {
          const res = await terminateAllAwsInstances(typed, scope);
          toast({ title: `Terminating ${res.terminating.length} instance${res.terminating.length === 1 ? "" : "s"}` });
          void qc.invalidateQueries({ queryKey: key });
        }}
      />
    </Card>
  );
}

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return v;
}

function BedrockPanel({ profile, region, fake }: { profile?: string; region: string; fake?: boolean }) {
  const [search, setSearch] = useState("");
  const q = useDebounced(search.trim(), 250);
  const res = useQuery({ queryKey: computeKeys.bedrockModels(profile, region, q), queryFn: () => listBedrockModels(profile, region, q || undefined), ...cloudQuery, placeholderData: (prev) => prev });
  const [adding, setAdding] = useState<BedrockModel | null>(null);
  const [copy] = useCopy();
  const toast = useToast();
  const columns: Column<BedrockModel>[] = [
    { id: "id", header: "Model id", cell: (m) => <span className="break-all font-mono text-mono">{m.invoke_id}</span>, sortValue: (m) => m.invoke_id },
    { id: "name", header: "Name", cell: (m) => m.name, sortValue: (m) => m.name },
    { id: "provider", header: "Provider", cell: (m) => m.provider, sortValue: (m) => m.provider, hideBelow: "md" },
    { id: "input", header: "Input", cell: (m) => m.input_modalities.map((x) => x.toLowerCase()).join(", "), hideBelow: "md" },
    {
      id: "status",
      header: "Status",
      cell: (m) => <Badge tone={m.status === "ACTIVE" ? "success" : "warning"}>{m.status.toLowerCase()}</Badge>,
      sortValue: (m) => m.status,
    },
    {
      id: "actions",
      header: <span className="sr-only">Actions</span>,
      label: "Actions",
      align: "right",
      cell: (m) => (
        <span className="inline-flex flex-wrap justify-end gap-1">
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              void copy(m.spec_yaml);
              toast({ title: "Copied model spec", description: m.spec_yaml, tone: "info" });
            }}
            data-testid={`bedrock-copy-${m.model_id}`}
            aria-label={`Copy model spec for ${m.name}`}
          >
            Copy model spec
          </Button>
          <Button size="sm" icon={Plus} onClick={() => setAdding(m)} data-testid={`bedrock-add-${m.model_id}`} aria-label={`Add ${m.name} to a lab`}>
            Add to a lab…
          </Button>
        </span>
      ),
    },
  ];
  const snippet = useMemo(() => (adding ? `models:\n  ${modelKey(adding.name)}: ${adding.spec_yaml}` : ""), [adding]);
  return (
    <Card data-testid="bedrock-models">
      <CardHeader
        title={
          <span className="inline-flex flex-wrap items-center gap-2">
            Bedrock models ({region}) <FixturesBadge fake={fake} />
          </span>
        }
        description="Text-output foundation models and inference profiles you can call from a lab as a bedrock model. Listing is free; each evaluated row is a billed request."
        actions={
          <div className="w-[min(260px,100%)]">
            <Input label="Search Bedrock models" hideLabel placeholder="Search models…" value={search} onChange={(e) => setSearch(e.target.value)} data-testid="bedrock-search" type="search" />
          </div>
        }
      />
      <div className="max-sm:p-3">
        {res.isError && (
          <div className="p-4">
            <CloudError error={res.error} onRetry={() => void res.refetch()} />
          </div>
        )}
        {res.data?.error && (
          <div className="p-4">
            <Callout tone="warning" title="Bedrock models could not be listed" data-testid="bedrock-error">
              {res.data.error}
            </Callout>
          </div>
        )}
        {!res.isError && !res.data?.error && (
          <TableOrEmpty
            caption={`Bedrock models in ${region}`}
            columns={columns}
            rows={res.data?.items ?? []}
            rowKey={(m) => m.model_id}
            rowTestId={(m) => `bedrock-model-${m.model_id}`}
            loading={res.isLoading}
            defaultSort={{ id: "provider", dir: "asc" }}
            mobile="cards"
            empty={<EmptyState icon={Search} title={q ? `No Bedrock models match “${q}”.` : "No Bedrock models are available here."} body="Try another search or region." />}
          />
        )}
      </div>
      <Dialog
        open={adding != null}
        onOpenChange={(o) => !o && setAdding(null)}
        title={adding ? `Add ${adding.name} to a lab` : "Add to a lab"}
        description="Paste this under models: in a lab file. Credentials come from your AWS profile or environment, never the lab."
        data-testid="bedrock-add-dialog"
        footer={
          <>
            <Button variant="ghost" onClick={() => setAdding(null)}>
              Close
            </Button>
            <Link to="/labs" className="inline-flex h-9 items-center rounded-sm bg-accent px-4 font-medium text-on-accent no-underline hover:bg-accent-hover max-sm:min-h-[44px]">
              Open labs
            </Link>
          </>
        }
      >
        {adding && (
          <div className="flex flex-col gap-3">
            <WrapCode code={snippet} label="Copy snippet" data-testid="bedrock-snippet" />
            <div className="flex items-center gap-2 text-small text-muted">
              <span>Just the spec:</span>
              <code className="min-w-0 truncate font-mono text-mono">{adding.spec_yaml}</code>
              <CopyButton text={adding.spec_yaml} label="Copy model spec" />
            </div>
            <Callout tone="info" title="Billed per request">
              Each row is a billed request to this provider. Studio does not estimate token cost, and runs of labs with a bedrock model ask you to type <code className="font-mono">call paid apis</code>.
            </Callout>
            {adding.status !== "ACTIVE" && (
              <Callout tone="warning">
                <TriangleAlert size={14} aria-hidden className="mr-1 inline" />
                This model is {adding.status.toLowerCase()}; AWS may retire it.
              </Callout>
            )}
          </div>
        )}
      </Dialog>
    </Card>
  );
}
