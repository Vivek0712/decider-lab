import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Play } from "lucide-react";
import { dataKeys, listSuites } from "@/api/data";
import { overviewKeys, startEval, type EvalBody } from "@/api/overview";
import { Button } from "@/components/Button";
import { Callout } from "@/components/Callout";
import { CodeBlock } from "@/components/Code";
import { Checkbox, Input, SegmentedControl, Select } from "@/components/Field";
import { Dialog } from "@/components/Overlay";
import { useToast } from "@/components/Toast";
import { ApiError } from "@/lib/api";

// command blocks wrap instead of scrolling sideways (no unfocusable scroll region on phones)
const WRAP = "[&_pre]:whitespace-pre-wrap [&_pre]:break-all";

type ModelType = EvalBody["model"]["type"];
const NAME = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/;

function quote(s: string) {
  return /^[A-Za-z0-9_./:@=+,-]+$/.test(s) ? s : `'${s.replace(/'/g, "'\\''")}'`;
}

/** Quick eval (DESIGN.md 4.3.2): one model on one suite with `decider-lab eval`. Never costs money. */
export function QuickEvalDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const navigate = useNavigate();
  const suites = useQuery({ queryKey: dataKeys.suites, queryFn: listSuites, enabled: open });
  const [type, setType] = useState<ModelType>("baseline");
  const [value, setValue] = useState("majority");
  const [name, setName] = useState("majority");
  const [nameTouched, setNameTouched] = useState(false);
  const [suite, setSuite] = useState("smoke");
  const [split, setSplit] = useState("");
  const [limit, setLimit] = useState("");
  const [workers, setWorkers] = useState("4");
  const [vision, setVision] = useState(false);
  const [out, setOut] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<Error | null>(null);
  useEffect(() => {
    if (open) setErr(null);
  }, [open]);
  useEffect(() => {
    if (type === "baseline" && !["uniform", "majority", "random"].includes(value)) setValue("majority");
    if (type !== "baseline" && ["uniform", "majority", "random"].includes(value)) setValue("");
  }, [type]);
  useEffect(() => {
    if (!nameTouched) setName(type === "baseline" ? value || "model" : type === "url" ? "remote" : "model");
  }, [type, value, nameTouched]);

  const suiteOptions = useMemo(() => {
    const opts = [
      { value: "smoke", label: "smoke (90 rows)" },
      { value: "synthetic", label: "synthetic (1,350 rows)" },
    ];
    for (const r of suites.data?.used_by_labs ?? []) if (!r.startsWith("file:") && !r.startsWith("heldout") && !opts.some((o) => o.value === r)) opts.push({ value: r, label: r });
    for (const f of suites.data?.files ?? []) if (f.valid) opts.push({ value: f.ref, label: f.path });
    return opts;
  }, [suites.data]);

  const lim = limit.trim() ? Number(limit) : null;
  const wk = Number(workers);
  const valueErr =
    !value.trim()
      ? "Required."
      : type === "url" && !/^https?:\/\/\S+$/.test(value.trim())
        ? "The http(s) URL of a System One server."
        : type === "python" && !/^[A-Za-z_][\w.]*:[A-Za-z_]\w*$/.test(value.trim())
          ? "module:attr, e.g. my_model:Heuristic"
          : undefined;
  const nameErr = NAME.test(name) ? undefined : "Letters, digits, . _ - (up to 64).";
  const limErr = lim != null && (!Number.isInteger(lim) || lim < 1) ? "A positive whole number, or empty." : undefined;
  const wkErr = !Number.isInteger(wk) || wk < 1 || wk > 64 ? "1 to 64." : undefined;
  const invalid = !!(valueErr || nameErr || limErr || wkErr);
  const suiteArg = suite.startsWith("file:") ? suites.data?.files.find((f) => f.ref === suite)?.path ?? suite : suite;
  const suiteName = suite.startsWith("file:") ? (suiteArg.split("/").pop() ?? "file").split(".")[0] : suite.split(":")[0];
  const outPath = out.trim() || `runs/${name}/${suiteName}`;
  const command = [
    "decider-lab eval",
    type === "serve" ? `--serve ${quote(value.trim())}${vision ? " --vision" : ""}` : `--model ${quote(type === "python" ? `python:${value.trim()}` : value.trim())}`,
    `--suite ${quote(suiteArg)} --name ${quote(name)} --workers ${workers} --out ${quote(outPath)}`,
    lim ? `--limit ${lim}` : "",
    split ? `--split ${split}` : "",
  ]
    .filter(Boolean)
    .join(" ");

  const start = async () => {
    if (invalid || busy) return;
    setBusy(true);
    setErr(null);
    try {
      const r = await startEval({
        kind: "eval",
        model: { type, value: value.trim() },
        name,
        suite,
        split: split || null,
        limit: lim,
        workers: wk,
        vision: type === "serve" && vision,
        out: out.trim() || null,
      });
      toast({ title: `Started: ${r.title}`, tone: "info", action: { label: "View job", onClick: () => navigate(`/jobs/${r.job_id}`) } });
      void qc.invalidateQueries({ queryKey: overviewKeys.all });
      onOpenChange(false);
    } catch (e) {
      setErr(e as Error);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Quick eval"
      description="One model on one suite, here. Never costs money."
      size="lg"
      data-testid="quick-eval-dialog"
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Close
          </Button>
          <Button variant="primary" icon={Play} disabled={invalid} loading={busy} onClick={start} data-testid="quick-eval-start">
            Start eval
          </Button>
        </>
      }
    >
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          void start();
        }}
      >
        <div className="flex flex-col gap-1">
          <span className="text-small font-medium">Model</span>
          <SegmentedControl<ModelType>
            label="Model type"
            value={type}
            onChange={setType}
            options={[
              { value: "url", label: "System One URL", testId: "quick-eval-type-url" },
              { value: "baseline", label: "Baseline", testId: "quick-eval-type-baseline" },
              { value: "serve", label: "Serve a checkpoint", testId: "quick-eval-type-serve" },
              { value: "python", label: "Python", testId: "quick-eval-type-python" },
            ]}
          />
        </div>
        {type === "baseline" ? (
          <Select
            label="Baseline"
            value={value}
            onChange={setValue}
            data-testid="quick-eval-value"
            options={[
              { value: "majority", label: "majority (the most common answer)" },
              { value: "uniform", label: "uniform (equal probabilities)" },
              { value: "random", label: "random" },
            ]}
          />
        ) : (
          <Input
            label={type === "url" ? "Server URL" : type === "serve" ? "Checkpoint source" : "module:attr"}
            mono
            value={value}
            placeholder={type === "url" ? "http://127.0.0.1:8000" : type === "serve" ? "hf://org/repo@commit or a directory" : "my_model:Heuristic"}
            onChange={(e) => setValue(e.target.value)}
            error={value ? valueErr : undefined}
            data-testid="quick-eval-value"
          />
        )}
        {type === "serve" && <Checkbox label="Vision (image rows)" checked={vision} onChange={setVision} data-testid="quick-eval-vision" />}
        <div className="grid gap-4 sm:grid-cols-2">
          <Input
            label="Name"
            value={name}
            onChange={(e) => {
              setNameTouched(true);
              setName(e.target.value);
            }}
            error={nameErr}
            data-testid="quick-eval-name"
          />
          <Select label="Suite" value={suite} onChange={setSuite} options={suiteOptions} data-testid="quick-eval-suite" />
          <Select
            label="Split"
            value={split}
            onChange={setSplit}
            data-testid="quick-eval-split"
            options={[
              { value: "", label: "auto (test when the suite has splits)" },
              { value: "dev", label: "dev" },
              { value: "test", label: "test" },
              { value: "all", label: "all" },
            ]}
          />
          <Input label="Limit per kind" type="number" min={1} value={limit} placeholder="all rows" onChange={(e) => setLimit(e.target.value)} error={limErr} data-testid="quick-eval-limit" />
          <Input label="Workers" type="number" min={1} max={64} value={workers} onChange={(e) => setWorkers(e.target.value)} error={wkErr} data-testid="quick-eval-workers" />
          <Input label="Output directory" mono value={out} placeholder={`runs/${name}/${suiteName}`} onChange={(e) => setOut(e.target.value)} data-testid="quick-eval-out" />
        </div>
        <p className="text-small text-muted">For Bedrock, Strands or OpenAI-compatible models, add them to a lab.</p>
        <div>
          <div className="mb-1 text-small font-medium">Command</div>
          <CodeBlock code={command} language="bash" data-testid="quick-eval-command" className={WRAP} />
        </div>
        {err && (
          <Callout tone="danger" alert title={err.message} data-testid="quick-eval-error">
            {err instanceof ApiError ? err.hint : null}
          </Callout>
        )}
      </form>
    </Dialog>
  );
}
