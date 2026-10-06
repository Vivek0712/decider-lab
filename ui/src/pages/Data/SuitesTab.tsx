import { useState } from "react";
import { FileWarning, Inbox, Search, ShieldCheck } from "lucide-react";
import type { BuiltinSuite, SuiteFile } from "@/api/types";
import type { Suites } from "@/api/data";
import { Badge } from "@/components/Badge";
import { Button } from "@/components/Button";
import { Card, CardBody, CardHeader } from "@/components/Card";
import { EmptyState } from "@/components/EmptyState";
import { Checkbox, Input } from "@/components/Field";
import { Tooltip } from "@/components/Tooltip";
import { fmtBytes, fmtInt, fmtRelative } from "@/lib/format";
import { slug } from "./shared";

/** Built-in suites (with synthetic's parameters) and the workspace's JSONL files (DESIGN.md 4.6). */
export function SuitesTab({
  suites,
  onInspect,
  onLeakcheck,
}: {
  suites: Suites;
  onInspect: (ref: string, title: string) => void;
  onLeakcheck: (fileId: string) => void;
}) {
  return (
    <div className="flex flex-col gap-5">
      <Card>
        <CardHeader title="Built-in suites" description="Named, reproducible sets of rows. Each one is cached by its parameters." />
        <CardBody className="flex flex-col divide-y divide-border p-0 sm:p-0">
          {suites.builtins.map((b) =>
            b.name === "synthetic" ? (
              <SyntheticRow key={b.ref} b={b} onInspect={onInspect} />
            ) : (
              <BuiltinRow key={b.ref} b={b} onInspect={onInspect} />
            ),
          )}
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Workspace files (JSONL)" description="Validity is the CLI's data check; Inspect is data stats." />
        <CardBody className="p-0 sm:p-0">
          {suites.files.length === 0 ? (
            <EmptyState
              icon={Inbox}
              title="No JSONL files in this workspace"
              body="Upload a CSV, generate rows or save a suite as JSONL to make one."
              command="decider-lab data from-csv my.csv --out data/my.jsonl"
            />
          ) : (
            <ul className="m-0 flex list-none flex-col divide-y divide-border p-0" data-testid="suite-files">
              {suites.files.map((f) => (
                <FileRow key={f.file_id} f={f} onInspect={onInspect} onLeakcheck={onLeakcheck} />
              ))}
            </ul>
          )}
        </CardBody>
      </Card>
    </div>
  );
}

function BuiltinRow({ b, onInspect }: { b: BuiltinSuite; onInspect: (ref: string, title: string) => void }) {
  const inspect = (
    <Button size="sm" icon={Search} disabled={!b.available} onClick={() => onInspect(b.ref, b.name)} data-testid={`suite-inspect-${b.name}`}>
      Inspect
    </Button>
  );
  return (
    <div className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5" data-testid={`suite-card-${b.name}`}>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-body font-semibold">{b.name}</span>
          {b.rows_estimate != null && <Badge>{fmtInt(b.rows_estimate)} rows</Badge>}
          {b.cached && <Badge tone="success">cached</Badge>}
          {!b.available && <Badge tone="warning">not installed</Badge>}
        </div>
        <p className="mt-1 text-small text-muted">{b.description}</p>
        {b.reason && <p className="mt-1 text-small text-warning">⚠ {b.reason}</p>}
        {b.name === "heldout" && b.available && !b.cached && (
          <p className="mt-1 text-small text-subtle">Inspect builds it first: this downloads the public datasets and can take minutes.</p>
        )}
      </div>
      <div className="shrink-0">{b.available ? inspect : <Tooltip content={b.reason ?? "unavailable"}>{inspect}</Tooltip>}</div>
    </div>
  );
}

function SyntheticRow({ b, onInspect }: { b: BuiltinSuite; onInspect: (ref: string, title: string) => void }) {
  const schema = b.param_schema;
  const fam = schema.families;
  const [perKind, setPerKind] = useState(String(b.params.per_kind ?? 150));
  const [seed, setSeed] = useState(String(b.params.seed ?? 0));
  const [families, setFamilies] = useState<string[]>((b.params.families as string[]) ?? ["arithmetic", "calendar", "seating"]);
  const pk = Number(perKind);
  const sd = Number(seed);
  const pkErr = !Number.isInteger(pk) || pk < 1 || pk > 5000 ? "1 to 5,000" : undefined;
  const sdErr = !Number.isInteger(sd) ? "a whole number" : undefined;
  const famErr = families.length === 0 ? "Pick at least one family." : undefined;
  const parts: string[] = [];
  if (pk !== 150) parts.push(`per_kind=${pk}`);
  if (sd !== 0) parts.push(`seed=${sd}`);
  const defaultFams = ["arithmetic", "calendar", "seating"];
  if (families.slice().sort().join("+") !== defaultFams.join("+")) parts.push(`families=${families.join("+")}`);
  const ref = parts.length ? `synthetic:${parts.join(",")}` : "synthetic";
  const rows = pkErr || famErr ? null : pk * families.length * 3;
  return (
    <div className="flex flex-col gap-3 px-4 py-3 sm:px-5" data-testid="suite-card-synthetic">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-body font-semibold">synthetic</span>
            {rows != null && <Badge data-testid="synthetic-rows">{fmtInt(rows)} rows</Badge>}
            <code className="font-mono text-caption text-subtle" data-testid="synthetic-ref">
              {ref}
            </code>
          </div>
          <p className="mt-1 text-small text-muted">{b.description}</p>
        </div>
        <Button
          size="sm"
          icon={Search}
          className="shrink-0 self-start"
          disabled={!!(pkErr || sdErr || famErr)}
          onClick={() => onInspect(ref, ref)}
          data-testid="suite-inspect-synthetic"
        >
          Inspect
        </Button>
      </div>
      <div className="grid gap-3 sm:grid-cols-[8rem_8rem_1fr]">
        <Input label="Rows per kind" type="number" min={1} max={5000} value={perKind} onChange={(e) => setPerKind(e.target.value)} error={pkErr} data-testid="synthetic-per-kind" />
        <Input label="Seed" type="number" value={seed} onChange={(e) => setSeed(e.target.value)} error={sdErr} data-testid="synthetic-seed" />
        <fieldset className="min-w-0">
          <legend className="mb-1 text-small font-medium">Families</legend>
          <div className="flex flex-wrap gap-x-4">
            {(fam?.options ?? defaultFams).map((f) => {
              const why = fam?.unavailable?.[f];
              return (
                <Checkbox
                  key={f}
                  label={why ? `${f} (${why})` : f}
                  checked={families.includes(f)}
                  disabled={!!why}
                  onChange={(on) => setFamilies(on ? [...families, f] : families.filter((x) => x !== f))}
                  data-testid={`synthetic-family-${f}`}
                />
              );
            })}
          </div>
          {famErr && <p className="text-small text-danger">{famErr}</p>}
        </fieldset>
      </div>
    </div>
  );
}

function FileRow({ f, onInspect, onLeakcheck }: { f: SuiteFile; onInspect: (ref: string, title: string) => void; onLeakcheck: (fileId: string) => void }) {
  const id = slug(f.path);
  return (
    <li className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-5" data-testid={`suite-file-${id}`}>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="break-all font-mono text-mono font-semibold">{f.path}</span>
          {f.valid === true && (
            <Badge tone="success" icon={ShieldCheck}>
              valid
            </Badge>
          )}
          {f.valid === false && (
            <Badge tone="danger" icon={FileWarning}>
              not valid
            </Badge>
          )}
          {f.valid === null && <Badge tone="warning">not checked</Badge>}
        </div>
        <p className="mt-1 text-small text-muted">
          {f.rows != null ? `${fmtInt(f.rows)} rows · ` : ""}
          {fmtBytes(f.size_bytes)} · modified {fmtRelative(f.modified_at)}
        </p>
        {f.problem && <p className="mt-1 break-words text-small text-danger">✕ {f.problem}</p>}
      </div>
      <div className="flex shrink-0 gap-2">
        <Button size="sm" icon={Search} disabled={f.valid !== true} onClick={() => onInspect(f.ref, f.path)} data-testid={`suite-inspect-${id}`}>
          Inspect
        </Button>
        <Button size="sm" disabled={f.valid !== true} onClick={() => onLeakcheck(f.file_id)} data-testid={`suite-leakcheck-${id}`}>
          Leakcheck…
        </Button>
      </div>
    </li>
  );
}
