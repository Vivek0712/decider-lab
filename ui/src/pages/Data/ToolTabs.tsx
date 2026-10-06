import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleCheck, OctagonAlert, Play, Search, ShieldCheck, Split } from "lucide-react";
import type { SuiteStats } from "@/api/types";
import {
  dataKeys,
  generateRows,
  getSuiteStats,
  leakcheck,
  refLabel,
  splitFile,
  type GenerateResult,
  type LeakcheckResult,
  type SplitResult,
  type Suites,
} from "@/api/data";
import { Badge } from "@/components/Badge";
import { Button } from "@/components/Button";
import { Callout } from "@/components/Callout";
import { Card, CardBody, CardHeader } from "@/components/Card";
import { CodeBlock } from "@/components/Code";
import { Checkbox, Input } from "@/components/Field";
import { useToast } from "@/components/Toast";
import { ApiError } from "@/lib/api";
import { fmtInt } from "@/lib/format";
import { JobCard } from "../Models/JobCard";
import { FileSelect, OutPath, SuiteChecklist, kindLabel, suiteOptions } from "./shared";

// command blocks wrap instead of scrolling sideways (no unfocusable scroll region on phones)
const WRAP = "[&_pre]:whitespace-pre-wrap [&_pre]:break-all";

function ActionError({ err, testId }: { err: Error | null; testId: string }) {
  if (!err) return null;
  return (
    <Callout tone="danger" alert title={err.message} data-testid={testId}>
      {err instanceof ApiError ? err.hint : null}
    </Callout>
  );
}

const int = (s: string) => (s.trim() === "" ? NaN : Number(s));

// ---- Generate ----------------------------------------------------------------------------------

export function GenerateTab({ suites, onInspect, onLeakcheck }: { suites: Suites; onInspect: (ref: string, title: string) => void; onLeakcheck: (fileId: string, against: string[]) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const synth = suites.builtins.find((b) => b.name === "synthetic");
  const famOptions = synth?.param_schema.families?.options ?? ["arithmetic", "calendar", "seating", "chess"];
  const unavailable = synth?.param_schema.families?.unavailable ?? {};
  const options = suiteOptions(suites);
  const [families, setFamilies] = useState<string[]>(["arithmetic", "calendar", "seating"]);
  const [perKind, setPerKind] = useState("500");
  const [seed, setSeed] = useState("1");
  const [exclude, setExclude] = useState<string[]>(() => suites.used_by_labs.filter((r) => !r.startsWith("heldout")));
  const [out, setOut] = useState("data/train.jsonl");
  const [overwrite, setOverwrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Error | null>(null);
  const [result, setResult] = useState<GenerateResult | null>(null);
  const [job, setJob] = useState<string | null>(null);
  const pk = int(perKind);
  const sd = int(seed);
  const pkErr = !Number.isInteger(pk) || pk < 1 || pk > 5000 ? "A whole number from 1 to 5,000." : undefined;
  const sdErr = !Number.isInteger(sd) ? "A whole number." : undefined;
  const total = pkErr ? null : pk * families.length * 3;

  const run = async () => {
    setBusy(true);
    setErr(null);
    setResult(null);
    setJob(null);
    try {
      const r = await generateRows({ out: out.trim(), families, per_kind: pk, seed: sd, exclude_suites: exclude, overwrite });
      if ("job_id" in r) {
        setJob(r.job_id);
        toast({ title: "Started: generate rows", tone: "info" });
      } else {
        setResult(r);
        toast({ title: `${fmtInt(r.rows)} rows → ${r.path}` });
      }
      void qc.invalidateQueries({ queryKey: dataKeys.suites });
    } catch (e) {
      setErr(e as Error);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-5">
      <Card>
        <CardHeader title="Generate training rows" description="Program-computed labels for the generated families (CLI: decider-lab data generate)." />
        <CardBody>
          <form
            className="flex flex-col gap-4"
            data-testid="generate-form"
            onSubmit={(e) => {
              e.preventDefault();
              void run();
            }}
          >
            <fieldset>
              <legend className="mb-1 text-small font-medium">Families</legend>
              <div className="flex flex-wrap gap-x-4">
                {famOptions.map((f) => (
                  <Checkbox
                    key={f}
                    label={unavailable[f] ? `${f} (${unavailable[f]})` : f}
                    checked={families.includes(f)}
                    disabled={!!unavailable[f]}
                    onChange={(on) => setFamilies(on ? [...families, f] : families.filter((x) => x !== f))}
                    data-testid={`generate-family-${f}`}
                  />
                ))}
              </div>
              {families.length === 0 && <p className="text-small text-danger">Pick at least one family.</p>}
            </fieldset>
            <div className="grid gap-4 sm:grid-cols-3">
              <Input label="Rows per kind and family" type="number" min={1} max={5000} value={perKind} onChange={(e) => setPerKind(e.target.value)} error={pkErr} data-testid="generate-per-kind" />
              <Input label="Seed" type="number" value={seed} onChange={(e) => setSeed(e.target.value)} error={sdErr} data-testid="generate-seed" />
              <div className="flex flex-col justify-end pb-1 text-small text-muted" data-testid="generate-total">
                {total != null ? `${fmtInt(total)} rows before exclusions` : ""}
                {total != null && total > 30000 && <span className="text-caption">Runs as a job (more than 30,000 rows).</span>}
              </div>
            </div>
            <SuiteChecklist label="Exclude rows that are in these suites" options={options} value={exclude} onChange={setExclude} testId="generate-exclude" />
            <p className="-mt-2 text-caption text-subtle">
              Rows that also appear in these suites are dropped, so you do not train on what you evaluate on. Defaults to every suite a lab here uses.
            </p>
            <OutPath value={out} onChange={setOut} overwrite={overwrite} onOverwrite={setOverwrite} testId="generate" />
            <ActionError err={err} testId="generate-error" />
            <div className="flex justify-end">
              <Button type="submit" variant="primary" icon={Play} loading={busy} disabled={!!(pkErr || sdErr) || families.length === 0 || !out.trim()} data-testid="generate-run">
                Generate
              </Button>
            </div>
          </form>
        </CardBody>
      </Card>
      {job && <JobCard jobId={job} onDone={() => void qc.invalidateQueries({ queryKey: dataKeys.suites })} />}
      {result && (
        <Card data-testid="generate-result">
          <CardHeader title={`${fmtInt(result.rows)} rows → ${result.path}`} description={`${fmtInt(result.dropped_overlapping)} dropped as overlapping with the excluded suites`} />
          <CardBody className="flex flex-col gap-3">
            <div className="flex flex-wrap gap-2">
              {Object.entries(result.stats.by_kind).map(([k, v]) => (
                <Badge key={k}>
                  {kindLabel(k)} {fmtInt(v)}
                </Badge>
              ))}
            </div>
            <CodeBlock code={result.command} language="bash" className={WRAP} />
            <div className="flex flex-wrap gap-2">
              <Button size="sm" icon={Search} onClick={() => onInspect(`file:${result.file_id}`, result.path)} data-testid="generate-inspect">
                Inspect
              </Button>
              <Button size="sm" icon={ShieldCheck} onClick={() => onLeakcheck(result.file_id, exclude.length ? exclude : ["smoke"])} data-testid="generate-leakcheck">
                Leakcheck against…
              </Button>
            </div>
          </CardBody>
        </Card>
      )}
    </div>
  );
}

// ---- Split -------------------------------------------------------------------------------------

export function SplitTab({ suites, onInspect }: { suites: Suites; onInspect: (ref: string, title: string) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const files = suites.files.filter((f) => f.valid === true);
  const [fileId, setFileId] = useState("");
  const [frac, setFrac] = useState("0.4");
  const [seed, setSeed] = useState("0");
  const [out, setOut] = useState("");
  const [overwrite, setOverwrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Error | null>(null);
  const [result, setResult] = useState<SplitResult | null>(null);
  const file = files.find((f) => f.file_id === fileId);
  useEffect(() => {
    if (file) setOut(file.path.replace(/\.jsonl(\.gz)?$/, "-split.jsonl"));
  }, [file]);
  const stats = useQuery({ queryKey: dataKeys.stats(`file:${fileId}`), queryFn: () => getSuiteStats(`file:${fileId}`), enabled: !!fileId });
  const s = stats.data && !("job_id" in stats.data) ? (stats.data as SuiteStats) : null;
  const f = Number(frac);
  const fracErr = !(f > 0 && f < 1) ? "Between 0 and 1, e.g. 0.4." : undefined;
  const sd = int(seed);
  const projected = useMemo(() => (s && !fracErr ? Object.entries(s.by_kind).map(([k, n]) => [k, Math.round(n * f)] as const) : []), [s, f, fracErr]);
  const low = projected.filter(([, n]) => n < 30);

  return (
    <div className="flex flex-col gap-5">
      <Card>
        <CardHeader title="Split into dev and test" description="Within each family and kind, a share of rows becomes dev (for calibration) and the rest test (CLI: decider-lab data split)." />
        <CardBody>
          <form
            className="flex flex-col gap-4"
            data-testid="split-form"
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              setErr(null);
              setResult(null);
              try {
                const r = await splitFile({ file_id: fileId, out: out.trim(), dev_fraction: f, seed: sd, overwrite });
                setResult(r);
                toast({ title: `Split ${fmtInt(r.rows)} rows → ${r.path}` });
                void qc.invalidateQueries({ queryKey: dataKeys.suites });
              } catch (e2) {
                setErr(e2 as Error);
              } finally {
                setBusy(false);
              }
            }}
          >
            <FileSelect label="Input file" files={files} value={fileId} onChange={setFileId} testId="split-file" />
            <div className="grid gap-4 sm:grid-cols-2">
              <Input label="Dev fraction" type="number" step={0.05} min={0.05} max={0.95} value={frac} onChange={(e) => setFrac(e.target.value)} error={fracErr} data-testid="split-fraction" />
              <Input label="Seed" type="number" value={seed} onChange={(e) => setSeed(e.target.value)} error={Number.isInteger(sd) ? undefined : "A whole number."} data-testid="split-seed" />
            </div>
            {projected.length > 0 && (
              <div className="text-small" data-testid="split-projected">
                <span className="text-muted">Projected dev rows: </span>
                {projected.map(([k, n]) => (
                  <Badge key={k} tone={n < 30 ? "warning" : "neutral"} className="mr-1">
                    {kindLabel(k)} {fmtInt(n)}
                  </Badge>
                ))}
                {low.length > 0 && <p className="mt-1 text-warning">⚠ Calibration needs at least 30 dev rows per kind; {low.map(([k]) => kindLabel(k)).join(", ")} would have fewer.</p>}
              </div>
            )}
            <OutPath value={out} onChange={setOut} overwrite={overwrite} onOverwrite={setOverwrite} testId="split" />
            <ActionError err={err} testId="split-error" />
            <div className="flex justify-end">
              <Button type="submit" variant="primary" icon={Split} loading={busy} disabled={!fileId || !!fracErr || !Number.isInteger(sd) || !out.trim()} data-testid="split-run">
                Split
              </Button>
            </div>
          </form>
        </CardBody>
      </Card>
      {result && (
        <Card data-testid="split-result">
          <CardHeader title={`${fmtInt(result.rows)} rows → ${result.path}`} />
          <CardBody className="flex flex-col gap-3">
            <div className="flex flex-wrap gap-2">
              {Object.entries(result.by_split).map(([k, v]) => (
                <Badge key={k} tone="info">
                  {k} {fmtInt(v)}
                </Badge>
              ))}
            </div>
            <div>
              <div className="mb-1 text-small font-medium">Use it in a lab</div>
              <CodeBlock code={result.snippet} language="yaml" className={WRAP} />
            </div>
            <div>
              <Button size="sm" icon={Search} onClick={() => onInspect(`file:${result.file_id}`, result.path)} data-testid="split-inspect">
                Inspect
              </Button>
            </div>
          </CardBody>
        </Card>
      )}
    </div>
  );
}

// ---- Leakcheck ---------------------------------------------------------------------------------

export function LeakcheckTab({ suites, initialTrain, initialAgainst }: { suites: Suites; initialTrain?: string | null; initialAgainst?: string[] | null }) {
  const qc = useQueryClient();
  const files = suites.files.filter((f) => f.valid === true);
  const options = suiteOptions(suites);
  const [train, setTrain] = useState(initialTrain ?? "");
  const [against, setAgainst] = useState<string[]>(() => {
    if (initialAgainst?.length) return initialAgainst;
    const used = suites.used_by_labs.filter((r) => !r.startsWith("heldout") && !r.startsWith("file:")).slice(0, 4);
    return used.length ? used : ["smoke"];
  });
  const [dropTo, setDropTo] = useState("");
  const [overwrite, setOverwrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Error | null>(null);
  const [result, setResult] = useState<LeakcheckResult | null>(null);
  useEffect(() => {
    if (initialTrain) setTrain(initialTrain);
  }, [initialTrain]);
  useEffect(() => {
    if (initialAgainst?.length) setAgainst(initialAgainst);
  }, [initialAgainst]);
  const trainFile = files.find((f) => f.file_id === train);
  const againstClean = against.filter((r) => !train || r !== `file:${train}`);

  return (
    <div className="flex flex-col gap-5">
      <Card>
        <CardHeader
          title="Leakcheck"
          description="Training rows whose input also appears in an evaluation suite make that suite's score meaningless for the model. Run this before every fine-tune."
        />
        <CardBody>
          <form
            className="flex flex-col gap-4"
            data-testid="leakcheck-form"
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              setErr(null);
              setResult(null);
              try {
                const r = await leakcheck({ train_file_id: train, against: againstClean, drop_to: dropTo.trim() || null, overwrite });
                setResult(r);
                if (r.clean) void qc.invalidateQueries({ queryKey: dataKeys.suites });
              } catch (e2) {
                setErr(e2 as Error);
              } finally {
                setBusy(false);
              }
            }}
          >
            <FileSelect label="Training file" files={files} value={train} onChange={setTrain} testId="leakcheck-train" />
            <SuiteChecklist label="Check against" options={options.filter((o) => o.ref !== `file:${train}`)} value={againstClean} onChange={setAgainst} testId="leakcheck-against" />
            <Input
              label="Write a clean copy to (optional)"
              mono
              value={dropTo}
              placeholder={trainFile ? trainFile.path.replace(/\.jsonl(\.gz)?$/, "-clean.jsonl") : "data/train-clean.jsonl"}
              onChange={(e) => setDropTo(e.target.value)}
              hint="The training rows without the overlapping ones"
              data-testid="leakcheck-drop-to"
            />
            {dropTo.trim() && <Checkbox label="Overwrite if the file exists" checked={overwrite} onChange={setOverwrite} data-testid="leakcheck-overwrite" />}
            <ActionError err={err} testId="leakcheck-error" />
            <div className="flex justify-end">
              <Button type="submit" variant="primary" icon={ShieldCheck} loading={busy} disabled={!train || againstClean.length === 0} data-testid="leakcheck-run">
                Run leakcheck
              </Button>
            </div>
          </form>
        </CardBody>
      </Card>
      {result && <LeakResult r={result} trainPath={trainFile?.path ?? "the training file"} files={suites.files} />}
    </div>
  );
}

function LeakResult({ r, trainPath, files }: { r: LeakcheckResult; trainPath: string; files: { ref: string; path: string }[] }) {
  const against = r.against.map((x) => refLabel(x, files)).join(", ");
  return (
    <Card data-testid="leakcheck-result" data-passed={r.passed ? "true" : "false"}>
      <CardBody className="flex flex-col gap-4">
        {r.passed ? (
          <Callout tone="success" title="Pass: no overlap" data-testid="leakcheck-pass">
            No training row shares an input with these suites ({against}).
          </Callout>
        ) : (
          <Callout tone="danger" title={`Fail: ${fmtInt(r.overlapping)} of ${fmtInt(r.train_rows)} training rows overlap`} data-testid="leakcheck-fail">
            These rows share an input with {against}. Training on them makes those suites' Intelligence (local proxy) meaningless for the model. Write a clean
            copy and train on that instead.
          </Callout>
        )}
        <dl className="m-0 grid grid-cols-3 gap-3 text-small">
          <div>
            <dt className="text-muted">Training rows</dt>
            <dd className="tnum m-0 text-h2">{fmtInt(r.train_rows)}</dd>
          </div>
          <div>
            <dt className="text-muted">Evaluation rows</dt>
            <dd className="tnum m-0 text-h2">{fmtInt(r.eval_rows)}</dd>
          </div>
          <div>
            <dt className="text-muted">Overlapping</dt>
            <dd className={`tnum m-0 text-h2 ${r.overlapping > 0 ? "text-danger" : "text-success"}`} data-testid="leakcheck-overlapping">
              {r.overlapping > 0 ? <OctagonAlert size={18} className="mr-1 inline" aria-hidden /> : <CircleCheck size={18} className="mr-1 inline" aria-hidden />}
              {fmtInt(r.overlapping)}
            </dd>
          </div>
        </dl>
        {r.examples.length > 0 && (
          <div>
            <h3 className="mb-1 text-h3">Examples</h3>
            <ul className="m-0 flex list-none flex-col gap-1 p-0 text-small">
              {r.examples.map((x) => (
                <li key={x.train_row} className="rounded-sm bg-surface-2 px-2 py-1">
                  <span className="tnum font-mono text-muted">
                    {trainPath} row {x.train_row}
                  </span>{" "}
                  <span className="font-mono text-caption text-subtle">{x.task}</span>
                  <div className="break-words">{x.state}</div>
                </li>
              ))}
            </ul>
          </div>
        )}
        {r.clean && (
          <Callout tone="info" data-testid="leakcheck-clean">
            Clean copy: {fmtInt(r.clean.rows)} rows → <code className="font-mono">{r.clean.path}</code>
          </Callout>
        )}
      </CardBody>
    </Card>
  );
}
