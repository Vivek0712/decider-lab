import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { Play, Plus, X } from "lucide-react";
import { estimateRun, labKeys } from "@/api/labs";
import { jobKeys, startJob } from "@/api/jobs";
import { getEnv, systemKeys } from "@/api/system";
import { listSshHosts, computeKeys } from "@/api/compute";
import type { Backend, Estimate, Lab, RunOptions } from "@/api/types";
import { Button } from "@/components/Button";
import { Callout } from "@/components/Callout";
import { CodeBlock } from "@/components/Code";
import { Checkbox, Input, SegmentedControl, Select } from "@/components/Field";
import { Dialog } from "@/components/Overlay";
import { Skeleton } from "@/components/Skeleton";
import { useToast } from "@/components/Toast";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import { fmtInt, fmtRate, fmtUsd } from "@/lib/format";

const BACKENDS: { value: Backend; label: string }[] = [
  { value: "local", label: "Local" },
  { value: "ssh", label: "SSH" },
  { value: "aws", label: "AWS" },
  { value: "vast", label: "vast.ai" },
];
const VAST_GPUS = ["RTX_4090", "RTX_A6000", "L40S", "A100_SXM4", "A100_PCIE", "H100_SXM", "H100_PCIE"];
const AWS_TYPES = [
  { value: "g6e.xlarge", label: "g6e.xlarge · L40S 48 GB" },
  { value: "g6e.2xlarge", label: "g6e.2xlarge · L40S 48 GB" },
  { value: "g6.xlarge", label: "g6.xlarge · L4 24 GB" },
  { value: "g5.xlarge", label: "g5.xlarge · A10G 24 GB" },
  { value: "g4dn.xlarge", label: "g4dn.xlarge · T4 16 GB" },
  { value: "p4d.24xlarge", label: "p4d.24xlarge · 8x A100 40 GB" },
  { value: "c7i.2xlarge", label: "c7i.2xlarge · CPU only" },
  { value: "m7i.2xlarge", label: "m7i.2xlarge · CPU only" },
];
const AWS_AS_OF = "2026-09-01";

type Fields = Record<string, string>;

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return v;
}

function initialFields(lab: Lab, backend: Backend): Fields {
  const o = (lab.summary?.compute.backend === backend ? lab.summary?.compute.options : {}) ?? {};
  const s = (k: string, d = "") => (o[k] != null ? String(o[k]) : d);
  return {
    gpu: s("gpu", "A100_SXM4"),
    num_gpus: s("num_gpus", "1"),
    max_price: s("max_price", "0.8"),
    disk_gb: s("disk_gb", backend === "aws" ? "150" : "80"),
    offer: s("offer"),
    ssh_key: s("ssh_key", "~/.ssh/id_ed25519"),
    instance_type: s("instance_type", "g6e.xlarge"),
    region: s("region", "us-east-1"),
    profile: s("profile"),
    host: s("host"),
    key: s("key"),
    host_id: "",
  };
}

const NUM = new Set(["num_gpus", "max_price", "disk_gb", "offer"]);
const PER_BACKEND: Record<Backend, string[]> = {
  local: [],
  ssh: ["host", "host_id", "key"],
  aws: ["instance_type", "region", "profile", "disk_gb"],
  vast: ["gpu", "num_gpus", "max_price", "disk_gb", "offer", "ssh_key"],
};

export function RunDialog({ lab, open, onClose, initialOnly }: { lab: Lab; open: boolean; onClose: () => void; initialOnly?: string[] | null }) {
  const nav = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();
  const models = useMemo(() => lab.summary?.models.map((m) => m.name) ?? [], [lab.summary]);
  const [backend, setBackend] = useState<Backend>(lab.summary?.compute.backend ?? "local");
  const [fields, setFields] = useState<Fields>(() => initialFields(lab, lab.summary?.compute.backend ?? "local"));
  const [only, setOnly] = useState<string[]>(initialOnly?.length ? initialOnly : models);
  const [limit, setLimit] = useState("");
  const [maxHours, setMaxHours] = useState(String(lab.summary?.compute.max_hours ?? 2));
  const [env, setEnv] = useState<string[]>([]);
  const [envDraft, setEnvDraft] = useState("");
  const [keep, setKeep] = useState(false);
  const [fast, setFast] = useState(false);
  const [strands, setStrands] = useState("");
  const [rootMode, setRootMode] = useState<"existing" | "new">("existing");
  const today = new Date().toISOString().slice(0, 10);
  const [newRoot, setNewRoot] = useState(`${lab.dir}/runs/${lab.name}-${today}`);
  const [confirm, setConfirm] = useState("");
  const [startError, setStartError] = useState<ApiError | Error | null>(null);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    if (open) {
      setOnly(initialOnly?.length ? initialOnly : models);
      setStartError(null);
      setConfirm("");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const remote = backend === "aws" || backend === "vast";
  const options: RunOptions = useMemo(() => {
    const opts: Record<string, string | number | null> = {};
    for (const k of PER_BACKEND[backend]) {
      const v = (fields[k] ?? "").trim();
      if (!v) continue;
      opts[k] = NUM.has(k) && !Number.isNaN(Number(v)) ? Number(v) : v;
    }
    const mh = Number(maxHours);
    return {
      backend,
      only: only.length === models.length ? null : only,
      limit: limit.trim() && Number(limit) > 0 ? Math.floor(Number(limit)) : null,
      max_hours: Number.isFinite(mh) && mh > 0 ? mh : 2,
      env,
      keep: remote && keep,
      fast_kernels: backend !== "local" && fast,
      strands_decider: backend !== "local" && strands.trim() ? strands.trim() : null,
      out: rootMode === "new" && newRoot.trim() ? newRoot.trim() : null,
      options: opts,
    };
  }, [backend, fields, only, models.length, limit, maxHours, env, keep, fast, strands, rootMode, newRoot, remote]);
  const debounced = useDebounced(options, 300);

  const est = useQuery({
    queryKey: labKeys.estimate(lab.lab_id, debounced),
    queryFn: ({ signal }) => estimateRun(lab.lab_id, debounced, signal),
    enabled: open,
    placeholderData: keepPreviousData,
    staleTime: 0,
  });
  const envStatus = useQuery({ queryKey: systemKeys.env(env), queryFn: () => getEnv(env), enabled: open && env.length > 0 });
  const hosts = useQuery({ queryKey: computeKeys.sshHosts, queryFn: listSshHosts, enabled: open && backend === "ssh", retry: false });

  const e: Estimate | undefined = est.data;
  const stale = est.isFetching || debounced !== options;
  const phrase = e?.confirm_phrase ?? null;
  const confirmOk = !phrase || confirm.trim() === phrase;
  const canStart = !!e && e.can_start && !stale && confirmOk && only.length > 0 && !starting;

  const set = (k: string, v: string) => setFields((f) => ({ ...f, [k]: v }));
  const pickBackend = (b: Backend) => {
    setBackend(b);
    setFields(initialFields(lab, b));
    setConfirm("");
  };
  const addEnv = () => {
    const n = envDraft.trim().toUpperCase();
    if (/^[A-Z_][A-Z0-9_]{0,127}$/.test(n) && !env.includes(n)) setEnv([...env, n]);
    setEnvDraft("");
  };

  const start = async () => {
    if (!canStart) return;
    setStarting(true);
    setStartError(null);
    try {
      const res = await startJob({ kind: "run", lab_id: lab.lab_id, ...options, ...(phrase ? { confirm: confirm.trim() } : {}) });
      await qc.invalidateQueries({ queryKey: jobKeys.all });
      toast({ title: `Started: ${res.title}`, action: { label: "View job", onClick: () => nav(`/jobs/${res.job_id}`) } });
      onClose();
      nav(`/jobs/${res.job_id}`);
    } catch (err) {
      setStartError(err instanceof Error ? err : new Error("Could not start the run."));
    } finally {
      setStarting(false);
    }
  };

  const apiErr = startError instanceof ApiError ? startError : null;
  const field = (name: string, label: string, props: Partial<React.ComponentProps<typeof Input>> = {}) => (
    <Input label={label} value={fields[name] ?? ""} onChange={(ev) => set(name, ev.target.value)} data-testid={`run-field-${name}`} {...props} />
  );

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => !o && onClose()}
      title={`Run ${lab.name}`}
      description="Runs every selected model on every suite, then writes REPORT.md. Rows already answered in the run root are reused."
      size="lg"
      data-testid="run-dialog"
      footer={
        <div className="flex w-full flex-wrap items-center justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" icon={Play} disabled={!canStart} loading={starting} onClick={() => void start()} data-testid="run-start">
            Start run
          </Button>
        </div>
      }
    >
      <form
        className="flex flex-col gap-5"
        onSubmit={(ev) => {
          ev.preventDefault();
          void start();
        }}
        onKeyDown={(ev) => {
          if ((ev.metaKey || ev.ctrlKey) && ev.key === "Enter") {
            ev.preventDefault();
            void start();
          }
        }}
      >
        <section className="flex flex-col gap-3">
          <div className="text-small font-medium">Where</div>
          <SegmentedControl
            label="Where to run"
            value={backend}
            onChange={pickBackend}
            options={BACKENDS.map((b) => ({ value: b.value, label: b.label, testId: `run-backend-${b.value}` }))}
          />
          {backend === "vast" && (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              <Select label="GPU" value={fields.gpu} onChange={(v) => set("gpu", v)} options={VAST_GPUS.map((g) => ({ value: g, label: g }))} data-testid="run-field-gpu" />
              {field("num_gpus", "GPUs", { type: "number", min: 1, inputMode: "numeric" })}
              {field("max_price", "Max $/h", { type: "number", step: "0.01", min: 0, inputMode: "decimal" })}
              {field("disk_gb", "Disk GB", { type: "number", min: 10, inputMode: "numeric" })}
              {field("offer", "Offer id", { placeholder: "cheapest", inputMode: "numeric" })}
              {field("ssh_key", "SSH key path", { mono: true })}
            </div>
          )}
          {backend === "aws" && (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Select label="Instance type" value={fields.instance_type} onChange={(v) => set("instance_type", v)} options={AWS_TYPES} data-testid="run-field-instance_type" />
              {field("region", "Region", { mono: true })}
              {field("profile", "Profile", { placeholder: "default", mono: true })}
              {field("disk_gb", "Disk GB", { type: "number", min: 20, inputMode: "numeric" })}
              <p className="col-span-full text-caption text-subtle">AWS makes its own key pair for each run; no key field is needed.</p>
            </div>
          )}
          {backend === "ssh" && (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              {(hosts.data?.items.length ?? 0) > 0 && (
                <Select
                  label="Saved host"
                  value={fields.host_id}
                  onChange={(v) => set("host_id", v)}
                  options={[{ value: "", label: "Type a host instead" }, ...hosts.data!.items.map((h) => ({ value: h.host_id, label: `${h.name} · ${h.target}` }))]}
                  data-testid="run-field-host_id"
                />
              )}
              {!fields.host_id && field("host", "Host", { placeholder: "ubuntu@10.0.0.5:22", mono: true })}
              {!fields.host_id && field("key", "SSH key path", { placeholder: "~/.ssh/id_ed25519", mono: true })}
            </div>
          )}
        </section>

        <section className="flex flex-col gap-3 border-t border-border pt-4">
          <div className="text-small font-medium">Scope</div>
          <fieldset>
            <legend className="mb-1 text-small text-muted">Models</legend>
            <div className="flex flex-wrap gap-2">
              {models.map((m) => {
                const on = only.includes(m);
                return (
                  <label
                    key={m}
                    className={cn(
                      "inline-flex min-h-8 cursor-pointer items-center gap-2 rounded-sm border px-2.5 text-small max-sm:min-h-[44px]",
                      on ? "border-accent bg-tint-accent text-text" : "border-border-strong text-muted",
                    )}
                  >
                    <input
                      type="checkbox"
                      className="h-4 w-4 accent-[var(--accent)]"
                      checked={on}
                      onChange={(ev) => setOnly(ev.target.checked ? models.filter((x) => x === m || only.includes(x)) : only.filter((x) => x !== m))}
                      data-testid={`run-field-only-${m}`}
                    />
                    {m}
                  </label>
                );
              })}
            </div>
            {only.length === 0 && <p className="mt-1 text-small text-danger">Select at least one model.</p>}
            {only.length > 0 && only.length < models.length && (
              <p className="mt-1 text-caption text-subtle">lab.json and REPORT.md will describe only the selected models' failures; other models' existing results stay in the run root.</p>
            )}
          </fieldset>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <Input label="Limit rows per kind" placeholder="all" value={limit} onChange={(ev) => setLimit(ev.target.value)} type="number" min={1} inputMode="numeric" data-testid="run-field-limit" />
            <Input
              label="Max hours"
              value={maxHours}
              onChange={(ev) => setMaxHours(ev.target.value)}
              type="number"
              step="0.25"
              min={0.25}
              inputMode="decimal"
              disabled={backend === "local"}
              hint={backend === "local" ? "remote runs only" : "hard deadline; the machine is released then"}
              data-testid="run-field-max_hours"
            />
          </div>
          {backend !== "local" && (
            <div className="flex flex-col gap-2">
              <div className="text-small font-medium" id="env-label">
                Pass environment variables (names only)
              </div>
              <div className="flex flex-wrap items-center gap-2" aria-labelledby="env-label">
                {env.map((n) => {
                  const st = envStatus.data?.items.find((x) => x.name === n);
                  return (
                    <span key={n} className="inline-flex h-7 items-center gap-1.5 rounded-xs border border-border-strong bg-surface-2 pl-2 font-mono text-caption" data-testid={`run-env-${n}`}>
                      <span aria-hidden className={st?.set ? "text-success" : "text-muted"}>
                        {st?.set ? "●" : "○"}
                      </span>
                      {n}
                      <span className="sr-only">{st?.set ? "set" : "not set"}</span>
                      <button type="button" aria-label={`Remove ${n}`} onClick={() => setEnv(env.filter((x) => x !== n))} className="inline-flex h-7 w-7 items-center justify-center text-muted hover:text-text">
                        <X size={12} aria-hidden />
                      </button>
                    </span>
                  );
                })}
                <span className="inline-flex items-end gap-1">
                  <Input
                    label="Variable name"
                    hideLabel
                    placeholder="HF_TOKEN"
                    value={envDraft}
                    mono
                    onChange={(ev) => setEnvDraft(ev.target.value)}
                    onKeyDown={(ev) => {
                      if (ev.key === "Enter") {
                        ev.preventDefault();
                        addEnv();
                      }
                    }}
                    className="w-[160px]"
                    data-testid="run-field-env"
                  />
                  <Button icon={Plus} size="sm" onClick={addEnv} data-testid="run-env-add">
                    Add name
                  </Button>
                </span>
              </div>
              <div className="flex flex-wrap gap-x-6 gap-y-1">
                {remote && <Checkbox label="Keep machine (debugging)" checked={keep} onChange={setKeep} data-testid="run-field-keep" />}
                <Checkbox label="Fast kernels" checked={fast} onChange={setFast} data-testid="run-field-fast_kernels" />
              </div>
              <Input
                label="strands-decider pip spec"
                placeholder="default (pinned)"
                value={strands}
                onChange={(ev) => setStrands(ev.target.value)}
                mono
                data-testid="run-field-strands_decider"
              />
            </div>
          )}
          <fieldset className="flex flex-col gap-2">
            <legend className="mb-1 text-small font-medium">Run root</legend>
            <label className="flex items-start gap-2 text-small">
              <input type="radio" name="root" className="mt-1 h-4 w-4 accent-[var(--accent)]" checked={rootMode === "existing"} onChange={() => setRootMode("existing")} data-testid="run-root-existing" />
              <span>
                <code className="font-mono">{e?.resume.root ?? "…"}</code>
                <span className="block text-muted" data-testid="run-resume">
                  {e?.resume.exists
                    ? e.resume.rows_reused != null
                      ? `resume: ${fmtInt(e.resume.rows_reused)} rows already answered are reused`
                      : "exists; remote runs answer every row again"
                    : "new run root"}
                </span>
              </span>
            </label>
            <label className="flex items-start gap-2 text-small">
              <input type="radio" name="root" className="mt-1 h-4 w-4 accent-[var(--accent)]" checked={rootMode === "new"} onChange={() => setRootMode("new")} data-testid="run-root-new" />
              <span className="flex-1">
                new
                {rootMode === "new" && <Input label="New run root" hideLabel value={newRoot} onChange={(ev) => setNewRoot(ev.target.value)} mono className="mt-1" data-testid="run-field-out" />}
              </span>
            </label>
          </fieldset>
        </section>

        <section className="flex flex-col gap-2 border-t border-border pt-4" data-testid="run-estimate" aria-busy={stale || undefined}>
          <div className="text-small font-medium">Estimate</div>
          {!e && est.isLoading && <Skeleton lines={3} />}
          {est.isError && !e && (
            <Callout tone="danger" actions={<Button size="sm" onClick={() => est.refetch()}>Retry</Button>}>
              {est.error instanceof Error ? est.error.message : "Could not estimate this run."}
            </Callout>
          )}
          {e && <EstimateView e={e} backend={backend} keep={keep} />}
        </section>

        {phrase && e?.can_start && (
          <section className="flex flex-col gap-1 border-t border-border pt-4">
            <label htmlFor="run-confirm" className="text-small font-medium">
              Type <code className="rounded-xs bg-surface-2 px-1 font-mono" data-testid="run-confirm-phrase">{phrase}</code> to confirm
            </label>
            <input
              id="run-confirm"
              value={confirm}
              onChange={(ev) => setConfirm(ev.target.value)}
              autoComplete="off"
              spellCheck={false}
              aria-invalid={apiErr?.code === "confirm_mismatch" || undefined}
              data-testid="run-confirm-input"
              className="h-9 w-full rounded-sm border border-border-strong bg-surface-2 px-3 font-mono text-mono text-text max-sm:min-h-[44px]"
            />
            <span aria-live="polite" className="text-caption text-subtle">
              {confirmOk ? "Confirmation matches" : " "}
            </span>
            {apiErr?.code === "confirm_mismatch" && <span className="text-small text-danger">{apiErr.message} The estimate may have changed; type the phrase shown above.</span>}
          </section>
        )}

        {e && (
          <div className="flex flex-col gap-1">
            <div className="text-small font-medium">Command</div>
            <CodeBlock code={e.command} language="bash" data-testid="run-command" />
          </div>
        )}

        {startError && apiErr?.code !== "confirm_mismatch" && (
          <Callout tone="danger" alert title={startError.message}>
            {apiErr?.code === "lab_invalid" ? (
              <Link to={`/labs/${lab.lab_id}?tab=editor`} onClick={onClose}>
                Open the editor to fix it
              </Link>
            ) : (
              apiErr?.hint
            )}
          </Callout>
        )}
      </form>
    </Dialog>
  );
}

function Row({ label, children, testId }: { label: string; children: React.ReactNode; testId?: string }) {
  return (
    <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-[200px,1fr]" data-testid={testId}>
      <dt className="text-small text-muted">{label}</dt>
      <dd className="text-small">{children}</dd>
    </div>
  );
}

function EstimateView({ e, backend, keep }: { e: Estimate; backend: Backend; keep: boolean }) {
  const c = e.cost;
  return (
    <div className="flex flex-col gap-3">
      <dl className="flex flex-col gap-1.5">
        <Row label="Plan">
          {e.plan.models.length} model{e.plan.models.length === 1 ? "" : "s"} × {e.plan.suites.length} suite{e.plan.suites.length === 1 ? "" : "s"} = {e.plan.runs} runs
          {e.plan.calibrated_runs_max ? ` (+ up to ${e.plan.calibrated_runs_max} calibrated)` : ""}
          {e.plan.requests_estimate != null ? `, ≈ ${fmtInt(e.plan.requests_estimate)} requests` : ""}
        </Row>
        {!c.billable && (
          <Row label="Cost" testId="run-cost">
            No cloud cost.
          </Row>
        )}
        {c.billable && (
          <>
            <Row label={backend === "vast" ? "Cheapest matching offer" : "Cost rate (est.)"} testId="run-rate">
              <span className="tnum">{fmtRate(c.rate_usd_per_hour)}</span> {c.rate_source && <span className="text-muted">({c.rate_source})</span>}
            </Row>
            <Row label="Spend cap" testId="run-cap">
              {c.cap_usd != null ? (
                <span className="tnum">
                  at most {fmtUsd(c.cap_usd)}{" "}
                  <span className="text-muted">
                    ({backend === "vast" ? `max ${fmtRate(c.cap_usd_per_hour)}` : fmtRate(c.rate_usd_per_hour)} × {backend === "aws" ? `(${c.max_hours} h + 15 min)` : `${c.max_hours} h`})
                  </span>
                </span>
              ) : (
                "unknown"
              )}
            </Row>
            {c.credit_usd != null && (
              <Row label="Credit" testId="run-credit">
                <span className="tnum">{fmtUsd(c.credit_usd)}</span> {c.cap_usd != null && c.credit_usd >= c.cap_usd + 0.5 ? "→ enough" : "→ not enough"}
              </Row>
            )}
          </>
        )}
      </dl>
      {c.note && <p className={cn("text-small", keep ? "text-warning" : "text-muted")}>{c.note}</p>}
      {backend === "aws" && <p className="text-caption text-subtle">Approximate on-demand list price (us-east-1, table dated {AWS_AS_OF}). Your bill may differ.</p>}
      {e.paid_models.length > 0 && (
        <Callout tone="warning" title="Paid API models" data-testid="run-paid-models">
          {e.paid_models.map((p) => `${p.model} (${p.kind}, ${p.provider})`).join(", ")}: billed per request
          {e.plan.requests_estimate != null ? `; ≈ ${fmtInt(e.paid_models[0]?.requests_estimate ?? null)} requests per model` : ""}; no dollar estimate.
        </Callout>
      )}
      {e.blockers.length > 0 && (
        <Callout tone="danger" title="This run cannot start" data-testid="run-blocker">
          <ul className="list-disc pl-4">
            {e.blockers.map((b) => (
              <li key={b}>{b}</li>
            ))}
          </ul>
        </Callout>
      )}
      {e.warnings.length > 0 && (
        <ul className="flex flex-col gap-1 text-small text-warning" data-testid="run-warnings">
          {e.warnings.map((w) => (
            <li key={w} className="flex gap-1.5">
              <span aria-hidden>⚠</span>
              <span className="text-text">{w}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
