import { useId, type ReactNode } from "react";
import type { BuiltinSuite, SuiteFile } from "@/api/types";
import type { Suites } from "@/api/data";
import { Checkbox, Input, Select } from "@/components/Field";
import { cn } from "@/lib/cn";
import { fmtInt } from "@/lib/format";

export const KIND_LABEL: Record<string, string> = { noul: "yes/no", choice: "choice", score: "score" };
export const kindLabel = (k: string) => KIND_LABEL[k] ?? k;

/** Slug for test ids from a path or ref ("data/my eval.jsonl" -> "data-my-eval-jsonl"). */
export const slug = (s: string) => s.replace(/[^A-Za-z0-9]+/g, "-").replace(/^-|-$/g, "").toLowerCase();

/** Every suite ref a picker offers: built-ins, refs used by labs, workspace files (valid ones). */
export function suiteOptions(s: Suites | undefined): { ref: string; label: string; detail?: string; disabled?: boolean }[] {
  if (!s) return [];
  const out: { ref: string; label: string; detail?: string; disabled?: boolean }[] = [];
  const seen = new Set<string>();
  const add = (ref: string, label: string, detail?: string, disabled?: boolean) => {
    if (seen.has(ref)) return;
    seen.add(ref);
    out.push({ ref, label, detail, disabled });
  };
  for (const b of s.builtins) add(b.ref, b.name, b.rows_estimate != null ? `${fmtInt(b.rows_estimate)} rows` : undefined, !b.available || b.name === "heldout");
  for (const r of s.used_by_labs) {
    if (r.startsWith("file:")) continue;
    add(r, r, "used by a lab", r.startsWith("heldout"));
  }
  for (const f of s.files) add(f.ref, f.path, f.valid ? `${fmtInt(f.rows)} rows` : "not valid", f.valid !== true);
  return out;
}

export function SuiteChecklist({
  label,
  options,
  value,
  onChange,
  testId,
}: {
  label: string;
  options: { ref: string; label: string; detail?: string; disabled?: boolean }[];
  value: string[];
  onChange: (v: string[]) => void;
  testId?: string;
}) {
  return (
    <fieldset className="min-w-0" data-testid={testId}>
      <legend className="mb-1 text-small font-medium">{label}</legend>
      <div className="flex flex-col gap-2 rounded-sm border border-border bg-surface-2 px-3 py-2">
        {options.length === 0 && <span className="text-small text-subtle">No suites found.</span>}
        {options.map((o) => (
          <Checkbox
            key={o.ref}
            label={
              <span className="flex min-w-0 flex-wrap items-baseline gap-x-2">
                <span className="break-all font-mono text-mono">{o.label}</span>
                {o.detail && <span className="text-caption text-subtle">{o.detail}</span>}
              </span>
            }
            checked={value.includes(o.ref)}
            disabled={o.disabled}
            onChange={(on) => onChange(on ? [...value, o.ref] : value.filter((x) => x !== o.ref))}
            data-testid={testId ? `${testId}-${slug(o.label)}` : undefined}
          />
        ))}
      </div>
    </fieldset>
  );
}

export function FileSelect({
  label,
  files,
  value,
  onChange,
  testId,
}: {
  label: string;
  files: SuiteFile[];
  value: string;
  onChange: (fileId: string) => void;
  testId?: string;
}) {
  return (
    <Select
      label={label}
      value={value}
      onChange={onChange}
      data-testid={testId}
      options={[
        { value: "", label: files.length ? "Choose a JSONL file…" : "No JSONL files in the workspace" },
        ...files.map((f) => ({ value: f.file_id, label: `${f.path}${f.rows != null ? ` (${f.rows} rows)` : ""}${f.valid === false ? " · not valid" : ""}` })),
      ]}
    />
  );
}

export function OutPath({
  value,
  onChange,
  overwrite,
  onOverwrite,
  label = "Output file",
  testId,
  error,
}: {
  value: string;
  onChange: (v: string) => void;
  overwrite: boolean;
  onOverwrite: (v: boolean) => void;
  label?: string;
  testId: string;
  error?: string;
}) {
  return (
    <div className="flex flex-col gap-2">
      <Input label={label} mono value={value} onChange={(e) => onChange(e.target.value)} hint="Inside the workspace, ending in .jsonl" data-testid={`${testId}-out`} error={error} />
      <Checkbox label="Overwrite if the file exists" checked={overwrite} onChange={onOverwrite} data-testid={`${testId}-overwrite`} />
    </div>
  );
}

/** DESIGN.md 7.8: per kind, a 100% stacked bar of label counts with the uniform share as ticks. */
export function LabelBalanceBars({ balance, className }: { balance: Record<string, Record<string, number>>; className?: string }) {
  const kinds = Object.keys(balance).sort();
  const shades = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)", "var(--chart-5)", "var(--chart-6)"];
  return (
    <figure className={cn("m-0", className)} data-testid="label-balance">
      <h3 className="text-h3">Label balance</h3>
      <figcaption className="mb-2 text-small text-muted">Share of each gold label per kind; ticks mark an even split.</figcaption>
      <div className="flex flex-col gap-3">
        {kinds.map((k) => {
          const counts = balance[k];
          const labels = Object.keys(counts).sort((a, b) => Number(a) - Number(b));
          const total = labels.reduce((s, l) => s + counts[l], 0);
          const n = labels.length;
          const aria = `${kindLabel(k)}: ` + labels.map((l) => `label ${l} ${counts[l]} of ${total}`).join(", ");
          return (
            <div key={k} data-testid={`label-balance-${k}`}>
              <div className="mb-1 flex justify-between text-small">
                <span className="font-medium">{kindLabel(k)}</span>
                <span className="tnum text-muted">{fmtInt(total)} rows</span>
              </div>
              <div role="img" aria-label={aria} className="relative flex h-6 overflow-hidden rounded-xs border border-border">
                {labels.map((l, i) => {
                  const pct = total ? (counts[l] / total) * 100 : 0;
                  return (
                    <div
                      key={l}
                      title={`label ${l}: ${counts[l]} (${pct.toFixed(1)}%)`}
                      className="flex h-full items-center justify-center overflow-hidden text-caption text-[var(--on-accent)]"
                      style={{ width: `${pct}%`, background: shades[i % shades.length], borderRight: i < n - 1 ? "1px solid var(--surface-1)" : undefined }}
                    >
                      {pct >= 8 ? counts[l] : ""}
                    </div>
                  );
                })}
                {n > 1 &&
                  Array.from({ length: n - 1 }, (_, i) => (
                    <span key={i} aria-hidden className="absolute inset-y-0 w-px bg-[var(--text)] opacity-60" style={{ left: `${((i + 1) / n) * 100}%` }} />
                  ))}
              </div>
              <div className="mt-1 flex flex-wrap gap-x-3 text-caption text-muted">
                {labels.map((l, i) => (
                  <span key={l} className="inline-flex items-center gap-1">
                    <span aria-hidden className="inline-block h-2 w-2 rounded-full" style={{ background: shades[i % shades.length] }} />
                    {k === "noul" ? (l === "1" ? "yes" : "no") : `label ${l}`} {counts[l]}
                  </span>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </figure>
  );
}

/** Horizontal bars for a count breakdown (by family/task, by kind, by split), with the numbers as text. */
export function BreakdownBars({ title, caption, data, testId }: { title: string; caption?: string; data: Record<string, number>; testId?: string }) {
  const entries = Object.entries(data).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, v]) => v));
  const id = useId();
  return (
    <figure className="m-0 min-w-0" data-testid={testId} aria-labelledby={id}>
      <h3 id={id} className="text-h3">
        {title}
      </h3>
      {caption && <figcaption className="mb-2 text-small text-muted">{caption}</figcaption>}
      <dl className="m-0 flex flex-col gap-1.5">
        {entries.map(([k, v]) => (
          <div key={k} className="grid grid-cols-[minmax(0,10rem)_1fr_auto] items-center gap-2 text-small">
            <dt className="truncate font-mono text-mono" title={k}>
              {k}
            </dt>
            <dd className="m-0 h-2.5 overflow-hidden rounded-full bg-surface-3">
              <span className="block h-full rounded-full bg-accent" style={{ width: `${(v / max) * 100}%` }} aria-hidden />
            </dd>
            <dd className="tnum m-0 text-right text-muted">{fmtInt(v)}</dd>
          </div>
        ))}
      </dl>
    </figure>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span className="text-small font-medium">{label}</span>
      {children}
    </div>
  );
}

export type { BuiltinSuite };
