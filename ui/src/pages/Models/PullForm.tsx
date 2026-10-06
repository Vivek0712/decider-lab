import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Download, Info } from "lucide-react";
import type { Inspect, Job, Problem } from "@/api/types";
import { inspectSource, modelKeys, startPull } from "@/api/models";
import { getEnv, getSettings, systemKeys } from "@/api/system";
import { Badge } from "@/components/Badge";
import { Button } from "@/components/Button";
import { Callout } from "@/components/Callout";
import { Card, CardBody, CardHeader } from "@/components/Card";
import { CodeBlock } from "@/components/Code";
import { Checkbox, Input } from "@/components/Field";
import { useToast } from "@/components/Toast";
import { ApiError } from "@/lib/api";
import { fmtRelative } from "@/lib/format";
import { JobCard } from "./JobCard";

// command blocks wrap instead of scrolling sideways (no unfocusable scroll region on phones)
const WRAP = "[&_pre]:whitespace-pre-wrap [&_pre]:break-all";

const KIND_LABEL: Record<Inspect["kind"], string> = { hf: "Hugging Face", s3: "Amazon S3", url: "URL", local: "Local directory" };

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** Pull a model (DESIGN.md 4.5 Pull): classify as you type, then start a `decider-lab pull` job. */
export function PullForm({ initialSource, onShowCache }: { initialSource?: string | null; onShowCache: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const settings = useQuery({ queryKey: systemKeys.settings, queryFn: getSettings });
  const env = useQuery({ queryKey: systemKeys.env(["HF_TOKEN"]), queryFn: () => getEnv(["HF_TOKEN"]) });
  const [source, setSource] = useState(initialSource ?? "");
  const [revision, setRevision] = useState("");
  const [sha256, setSha256] = useState("");
  const [requirePinned, setRequirePinned] = useState<boolean | null>(null);
  const [profile, setProfile] = useState("");
  const [region, setRegion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [jobs, setJobs] = useState<string[]>([]);
  const pinned = requirePinned ?? settings.data?.require_pinned_default ?? true;

  const body = useMemo(
    () => ({ source: source.trim(), revision: revision.trim() || null, sha256: sha256.trim() || null, require_pinned: pinned,
             profile: profile.trim() || null, region: region.trim() || null }),
    [source, revision, sha256, pinned, profile, region],
  );
  const debounced = useDebounced(body, 250);
  const inspect = useQuery({
    queryKey: modelKeys.inspect(debounced),
    queryFn: () => inspectSource(debounced),
    enabled: debounced.source.length > 0,
    staleTime: 5_000,
  });
  const stale = debounced !== body;
  const res = debounced.source ? inspect.data : undefined;
  const errors = res?.problems.filter((p) => p.severity === "error") ?? [];
  const warnings = res?.problems.filter((p) => p.severity === "warning") ?? [];
  const kind = res?.kind;
  const canPull = !!source.trim() && !!res && !stale && !inspect.isFetching && errors.length === 0 && !busy;
  const hfToken = env.data?.items.find((e) => e.name === "HF_TOKEN");

  const pull = async () => {
    if (!canPull) return;
    setBusy(true);
    setError(null);
    try {
      const started = await startPull({ kind: "pull", ...body });
      setJobs((xs) => [started.job_id, ...xs]);
      toast({ title: `Started: ${started.title}`, tone: "info" });
    } catch (e) {
      setError(e as Error);
    } finally {
      setBusy(false);
    }
  };

  const onDone = (job: Job) => {
    void qc.invalidateQueries({ queryKey: modelKeys.all });
    if (job.status === "succeeded") {
      const info = (job.result?.info ?? {}) as Record<string, unknown>;
      const verified = typeof info.sha256 === "string" && body.sha256 ? " · sha256 verified" : "";
      toast({ title: `Pulled${verified}`, description: job.title });
    }
  };

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
      <Card>
        <CardHeader title="Pull a model" description="Fetch a checkpoint now, verify it, and keep it in the cache for labs." />
        <CardBody>
          <form
            className="flex flex-col gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              void pull();
            }}
            data-testid="pull-form"
          >
            <Input
              label="Source"
              mono
              placeholder="hf://StrandsAgents/strands-decider-2B-hobson-v19@<commit>"
              value={source}
              onChange={(e) => setSource(e.target.value)}
              autoComplete="off"
              spellCheck={false}
              data-testid="pull-source"
              hint="hf://org/repo@commit, s3://bucket/key.tar, https://…/model.tar.gz, or a directory"
            />
            <div data-testid="pull-inspect" aria-live="polite" className="-mt-2 min-h-[22px] text-small">
              {!body.source ? null : stale || inspect.isFetching ? (
                <span className="text-subtle">Checking…</span>
              ) : inspect.error ? (
                <span className="text-danger">{(inspect.error as Error).message}</span>
              ) : res ? (
                <span className="flex flex-wrap items-center gap-2">
                  {errors.length === 0 ? <CheckCircle2 size={14} className="text-success" aria-hidden /> : null}
                  <span className="font-medium">{KIND_LABEL[res.kind]}</span>
                  {res.kind === "hf" && (res.pinned ? <Badge tone="success">pinned to a full commit</Badge> : <Badge tone="warning">not pinned</Badge>)}
                  {(res.kind === "url" || res.kind === "s3") && (res.pinned ? <Badge tone="success">sha256 given</Badge> : <Badge tone="warning">not verified</Badge>)}
                  {res.cached && <Badge tone="info">in the cache</Badge>}
                </span>
              ) : null}
            </div>
            <ProblemList problems={[...errors, ...warnings]} />
            {res?.cached && res.cached_info && (
              <Callout
                tone="info"
                data-testid="pull-cached"
                actions={
                  <Button size="sm" onClick={onShowCache}>
                    Show in cache
                  </Button>
                }
              >
                Already in the cache (pulled {fmtRelative(res.cached_info.pulled_at)}
                {res.cached_info.ref ? `, ${res.cached_info.ref.slice(0, 8)}` : ""}). Pulling again re-uses it.
              </Callout>
            )}
            <div className="grid gap-4 sm:grid-cols-2">
              <Input
                label="Revision"
                mono
                value={revision}
                onChange={(e) => setRevision(e.target.value)}
                disabled={!!kind && kind !== "hf"}
                hint="Hugging Face only; or use @ in the source"
                data-testid="pull-revision"
              />
              <Input
                label="sha256"
                mono
                value={sha256}
                onChange={(e) => setSha256(e.target.value)}
                disabled={kind === "hf" || kind === "local"}
                hint={settings.data?.require_sha256 ? "Required for archives and files (Settings)" : "Verifies an archive or file from s3 or https"}
                data-testid="pull-sha256"
              />
            </div>
            <Checkbox
              label="Require a pinned revision (refuse branches and tags)"
              checked={pinned}
              onChange={setRequirePinned}
              data-testid="pull-require-pinned"
            />
            {kind === "s3" && (
              <div className="grid gap-4 sm:grid-cols-2">
                <Input label="AWS profile" value={profile} onChange={(e) => setProfile(e.target.value)} placeholder="default" data-testid="pull-profile" />
                <Input label="Region" value={region} onChange={(e) => setRegion(e.target.value)} placeholder="us-east-1" data-testid="pull-region" />
              </div>
            )}
            {kind === "hf" && (
              <p className="flex items-center gap-2 text-small text-muted" data-testid="pull-token">
                <Info size={14} aria-hidden /> Token <code className="font-mono">HF_TOKEN</code>
                {hfToken?.set ? <Badge tone="success">set</Badge> : <Badge>not set</Badge>}
                <span className="text-subtle">read from the environment; never shown</span>
              </p>
            )}
            {res?.command && (
              <div>
                <div className="mb-1 text-small font-medium">Command</div>
                <CodeBlock code={res.command} language="bash" data-testid="pull-command" className={WRAP} />
              </div>
            )}
            {error && (
              <Callout tone="danger" alert title={error.message} data-testid="pull-error">
                {error instanceof ApiError ? error.hint : null}
              </Callout>
            )}
            <div className="flex justify-end gap-2">
              <Button type="submit" variant="primary" icon={Download} disabled={!canPull} loading={busy} data-testid="pull-start">
                Pull
              </Button>
            </div>
          </form>
        </CardBody>
      </Card>
      <div className="flex min-w-0 flex-col gap-3" data-testid="pull-jobs">
        <h2 className="text-h3">Pulls started here</h2>
        {jobs.length === 0 ? (
          <p className="text-small text-muted">Each pull runs as a job; its progress shows here and on the Jobs page.</p>
        ) : (
          jobs.map((id) => <JobCard key={id} jobId={id} onDone={onDone} />)
        )}
      </div>
    </div>
  );
}

function ProblemList({ problems }: { problems: Problem[] }) {
  if (!problems.length) return null;
  return (
    <ul className="-mt-2 flex list-none flex-col gap-1 p-0" data-testid="pull-problems">
      {problems.map((p, i) => (
        <li key={i} data-severity={p.severity} className={p.severity === "error" ? "text-small text-danger" : "text-small text-warning"}>
          {p.severity === "error" ? "✕ " : "⚠ "}
          {p.message}
        </li>
      ))}
    </ul>
  );
}
