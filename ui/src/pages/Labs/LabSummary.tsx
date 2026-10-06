import { AlertTriangle, Cpu, Star } from "lucide-react";
import type { LabSummary, ModelKind } from "@/api/types";
import { Badge, type Tone } from "@/components/Badge";
import { Card, CardBody, CardHeader } from "@/components/Card";
import { cn } from "@/lib/cn";
import { fmtInt } from "@/lib/format";

const KIND_TONE: Record<ModelKind, Tone> = {
  serve: "accent", url: "info", bedrock: "warning", strands: "warning", chat: "info", python: "neutral",
  baseline: "neutral", finetuned: "accent", unknown: "danger",
};

/** Kind badge for a model (serve, url, bedrock, ...); for serve also the source kind (hf, s3, url, local). */
export function ModelKindBadge({ kind, sourceKind }: { kind: ModelKind; sourceKind?: string }) {
  return (
    <Badge tone={KIND_TONE[kind] ?? "neutral"} data-testid={`kind-${kind}`}>
      {kind}
      {sourceKind ? <span className="opacity-80">· {sourceKind}</span> : null}
    </Badge>
  );
}

const BACKEND_LABEL: Record<string, string> = { local: "this machine", ssh: "SSH", aws: "AWS", vast: "vast.ai" };

/** The visual summary of a lab (DESIGN.md 4.2.2): models, suites, fine-tune, options, plan. */
export function LabSummaryView({ summary, compact = false }: { summary: LabSummary; compact?: boolean }) {
  const s = summary;
  const opts = Object.entries(s.compute.options ?? {});
  return (
    <div className={cn("grid gap-4", compact ? "grid-cols-1" : "grid-cols-1 md:grid-cols-2")} data-testid="lab-summary">
      <Card className={compact ? "" : "md:col-span-2"}>
        <CardHeader title={`Models (${s.models.length})`} as="h3" />
        <ul className="divide-y divide-border">
          {s.models.map((m) => (
            <li key={m.name} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5 sm:px-5" data-testid={`summary-model-${m.name}`}>
              <span className="min-w-[96px] font-semibold">{m.name}</span>
              <ModelKindBadge kind={m.kind} sourceKind={m.kind === "serve" ? m.source_kind : undefined} />
              <code className="min-w-0 max-w-full truncate font-mono text-mono text-muted" title={m.spec_text}>
                {m.spec_text}
              </code>
              <span className="ml-auto flex flex-wrap items-center gap-1.5">
                {m.pinned === true && <Badge tone="success">pinned ✓</Badge>}
                {m.pinned === false && m.source_kind === "hf" && <Badge tone="warning">not pinned</Badge>}
                {m.needs_gpu && (
                  <Badge icon={Cpu} title="Needs a GPU here, Apple MPS, or a remote backend">
                    GPU/MPS
                  </Badge>
                )}
                {m.vision && <Badge>vision</Badge>}
                {m.is_baseline && (
                  <Badge tone="accent" icon={Star}>
                    baseline
                  </Badge>
                )}
                {m.jevbench && <Badge tone="info">JevBench</Badge>}
                {m.paid_api && (
                  <Badge tone="warning" data-testid={`lab-paid-badge-${m.name}`} title="Each row is a billed request to this provider. Studio does not estimate token cost.">
                    paid API
                  </Badge>
                )}
              </span>
              {(m.warnings.length > 0 || m.env_refs.length > 0) && (
                <div className="w-full text-small">
                  {m.env_refs.length > 0 && <div className="text-muted">reads {m.env_refs.join(", ")} from the environment</div>}
                  {m.warnings
                    .filter((w) => !w.includes("billed per request"))
                    .map((w) => (
                      <div key={w} className="flex items-start gap-1 text-warning">
                        <AlertTriangle size={14} aria-hidden className="mt-[3px] shrink-0" />
                        <span className="text-text">{w}</span>
                      </div>
                    ))}
                  {m.paid_api && <div className="text-muted">Each row is a billed request to this provider. Studio does not estimate token cost.</div>}
                </div>
              )}
            </li>
          ))}
          {s.models.length === 0 && <li className="px-4 py-3 text-small text-muted sm:px-5">No models yet.</li>}
        </ul>
      </Card>
      <Card>
        <CardHeader title={`Suites (${s.suites.length})`} as="h3" />
        <ul className="divide-y divide-border">
          {s.suites.map((x) => (
            <li key={x.ref} className="flex items-baseline justify-between gap-3 px-4 py-2.5 sm:px-5" data-testid={`summary-suite-${(x as { name?: string }).name ?? x.ref}`}>
              <span className="min-w-0 truncate font-medium" title={x.ref}>
                {x.label}
              </span>
              <span className="tnum shrink-0 text-small text-muted">
                {x.rows_estimate != null ? `${fmtInt(x.rows_estimate)} rows` : "rows known after build"}
                {x.has_splits ? " · dev/test" : ""}
              </span>
            </li>
          ))}
        </ul>
      </Card>
      <Card>
        <CardHeader title="Options" as="h3" />
        <CardBody className="py-3">
          <dl className="grid grid-cols-[auto,1fr] gap-x-4 gap-y-1.5 text-small">
            <dt className="text-muted">calibrate</dt>
            <dd>{s.calibrate ? "on (needs ≥30 dev rows per kind)" : "off"}</dd>
            <dt className="text-muted">baseline</dt>
            <dd>{s.baseline ?? "—"}</dd>
            <dt className="text-muted">jevbench</dt>
            <dd>{s.jevbench.length ? s.jevbench.join(", ") : "—"}</dd>
            <dt className="text-muted">workers</dt>
            <dd className="tnum">{s.workers}</dd>
            <dt className="text-muted">compute</dt>
            <dd data-testid="summary-compute">
              {BACKEND_LABEL[s.compute.backend] ?? s.compute.backend} · max {s.compute.max_hours} h
              {opts.length > 0 && <span className="block font-mono text-caption text-muted">{opts.map(([k, v]) => `${k}: ${String(v)}`).join(", ")}</span>}
            </dd>
          </dl>
        </CardBody>
      </Card>
      {(s.finetune.length > 0 || !compact) && (
        <Card>
          <CardHeader title={`Fine-tune (${s.finetune.length})`} as="h3" />
          <CardBody className="py-3 text-small">
            {s.finetune.length === 0 ? (
              <span className="text-muted">none</span>
            ) : (
              <ul className="flex flex-col gap-1.5">
                {s.finetune.map((f) => (
                  <li key={f.name}>
                    <span className="font-semibold">{f.name}</span>{" "}
                    <span className="text-muted">
                      from {f.from ?? f.base_model ?? "—"} · train {f.train.join(", ") || "—"}
                      {f.steps ? ` · ${f.steps} steps` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      )}
      <div className={cn("text-small text-muted", compact ? "" : "md:col-span-2")} data-testid="lab-plan">
        Plan: {s.models.length} model{s.models.length === 1 ? "" : "s"} × {s.suites.length} suite{s.suites.length === 1 ? "" : "s"} ={" "}
        {s.plan.runs} run{s.plan.runs === 1 ? "" : "s"}
        {s.plan.calibrated_runs_max ? ` (+ up to ${s.plan.calibrated_runs_max} calibrated)` : ""}
        {s.plan.requests_estimate != null ? `, ≈ ${fmtInt(s.plan.requests_estimate)} requests` : ""}
      </div>
    </div>
  );
}
