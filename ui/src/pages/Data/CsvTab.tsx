import { useRef, useState, type DragEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { FileUp, Search, Upload } from "lucide-react";
import { convertCsv, dataKeys, previewCsv, type CsvPreview, type Written } from "@/api/data";
import { Badge } from "@/components/Badge";
import { Button } from "@/components/Button";
import { Callout } from "@/components/Callout";
import { Card, CardBody, CardHeader } from "@/components/Card";
import { Input, Select } from "@/components/Field";
import { DataTable, type Column } from "@/components/Table";
import { useToast } from "@/components/Toast";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import { fmtBytes, fmtInt } from "@/lib/format";
import type { SuiteRow } from "@/api/data";
import { OutPath, kindLabel } from "./shared";

const MAX = 50 * 1024 * 1024;
const DELIMS = [
  { value: ",", label: "Comma ," },
  { value: ";", label: "Semicolon ;" },
  { value: "tab", label: "Tab" },
  { value: "|", label: "Pipe |" },
];

/** Upload CSV -> preview (recognised columns, per-row errors, first 20 rows) -> save as JSONL (data.from_csv). */
export function CsvTab({ onInspect }: { onInspect: (ref: string, title: string) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [task, setTask] = useState("custom");
  const [delimiter, setDelimiter] = useState(",");
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState<"upload" | "convert" | null>(null);
  const [preview, setPreview] = useState<CsvPreview | null>(null);
  const [out, setOut] = useState("data/my.jsonl");
  const [overwrite, setOverwrite] = useState(false);
  const [err, setErr] = useState<Error | null>(null);
  const [done, setDone] = useState<Written | null>(null);

  const upload = async (f: File) => {
    setErr(null);
    setDone(null);
    setPreview(null);
    setFile(f);
    if (f.size > MAX) {
      setErr(new Error(`${f.name} is ${fmtBytes(f.size)}; the limit is 50 MB.`));
      return;
    }
    setBusy("upload");
    try {
      const p = await previewCsv(f, task.trim() || "custom", delimiter);
      setPreview(p);
      setOut(p.suggested_out);
    } catch (e) {
      setErr(e as Error);
    } finally {
      setBusy(null);
    }
  };
  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    const f = e.dataTransfer.files?.[0];
    if (f) void upload(f);
  };
  const blocked = !preview || preview.errors.length > 0 || preview.columns.missing_required.length > 0 || preview.rows_total === 0;

  const columns: Column<SuiteRow>[] = [
    { id: "n", header: "#", cell: (r) => <span className="tnum text-muted">{preview!.preview.indexOf(r) + 1}</span>, width: 40 },
    { id: "kind", header: "Kind", cell: (r) => <Badge>{kindLabel(r.kind)}</Badge> },
    { id: "state", header: "State", cell: (r) => <span className="line-clamp-2 max-w-[28rem] whitespace-normal break-words">{String(r.state)}</span> },
    { id: "q", header: "Question", cell: (r) => <span className="whitespace-normal">{r.instructions}</span>, hideBelow: "sm" },
    { id: "gold", header: "Gold", cell: (r) => <span className="font-mono">{r.options[r.label]?.[1]}</span> },
  ];

  return (
    <div className="flex flex-col gap-5">
      <Card>
        <CardHeader
          title="Upload a CSV"
          description={
            <>
              Columns: <code className="font-mono">state, question, answer</code>, and optionally <code className="font-mono">options</code> (A|B|C),{" "}
              <code className="font-mono">kind</code> and <code className="font-mono">task</code>.
            </>
          }
        />
        <CardBody className="flex flex-col gap-4">
          <div className="grid gap-4 sm:grid-cols-2">
            <Input label="Task name" value={task} onChange={(e) => setTask(e.target.value)} hint="Recorded on every row; reports break down by it" data-testid="csv-task" />
            <Select label="Delimiter" value={delimiter} onChange={setDelimiter} options={DELIMS} data-testid="csv-delimiter" />
          </div>
          <div
            data-testid="csv-drop"
            onDragOver={(e) => {
              e.preventDefault();
              setOver(true);
            }}
            onDragLeave={() => setOver(false)}
            onDrop={onDrop}
            className={cn(
              "flex flex-col items-center justify-center gap-2 rounded-md border-2 border-dashed px-4 py-8 text-center",
              over ? "border-accent bg-tint-accent" : "border-border-strong bg-surface-2",
            )}
          >
            <FileUp size={24} aria-hidden className="text-muted" />
            <p className="text-small text-muted">Drop a .csv here (50 MB at most), or</p>
            <input
              ref={input}
              type="file"
              accept=".csv,text/csv"
              className="sr-only"
              id="csv-file"
              aria-label="Choose a CSV file"
              data-testid="csv-file"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void upload(f);
                e.target.value = "";
              }}
            />
            <Button icon={Upload} loading={busy === "upload"} onClick={() => input.current?.click()} data-testid="csv-choose">
              Choose a file…
            </Button>
            {file && <p className="text-caption text-subtle">{file.name} · {fmtBytes(file.size)}</p>}
          </div>
          {err && !preview && (
            <Callout tone="danger" alert title={err.message} data-testid="csv-error">
              {err instanceof ApiError ? err.hint : null}
            </Callout>
          )}
        </CardBody>
      </Card>

      {preview && (
        <Card data-testid="csv-preview">
          <CardHeader
            title="Preview"
            description={`${preview.filename} · ${fmtInt(preview.rows_total)} rows · showing the first ${Math.min(20, preview.preview.length)} converted`}
          />
          <CardBody className="flex flex-col gap-4">
            <div className="flex flex-wrap items-center gap-2 text-small" data-testid="csv-columns">
              <span className="text-muted">Recognised:</span>
              {preview.columns.found.map((c) => (
                <Badge key={c} tone="success">
                  {c}
                </Badge>
              ))}
              {preview.columns.missing_required.length > 0 && <span className="ml-2 text-muted">Missing:</span>}
              {preview.columns.missing_required.map((c) => (
                <Badge key={c} tone="danger">
                  {c}
                </Badge>
              ))}
              {preview.columns.ignored.length > 0 && <span className="ml-2 text-muted">Ignored:</span>}
              {preview.columns.ignored.map((c) => (
                <Badge key={c}>{c}</Badge>
              ))}
            </div>
            {preview.columns.missing_required.length > 0 && (
              <Callout tone="danger" title="Required columns are missing">
                Add {preview.columns.missing_required.join(", ")} as column headers (check the delimiter too), then upload again.
              </Callout>
            )}
            {preview.errors.length > 0 && (
              <Callout tone="danger" title={`${fmtInt(preview.error_count)} ${preview.error_count === 1 ? "row is" : "rows are"} not valid`} data-testid="csv-errors">
                <ul className="m-0 mt-1 flex list-none flex-col gap-0.5 p-0">
                  {preview.errors.slice(0, 20).map((e, i) => (
                    <li key={i}>
                      <span className="tnum font-mono">line {e.row ?? "?"}</span>: {e.message}
                    </li>
                  ))}
                </ul>
                {preview.errors.length > 20 && <p className="mt-1">and {preview.errors.length - 20} more.</p>}
              </Callout>
            )}
            {preview.preview.length > 0 && (
              <DataTable caption="First converted rows" columns={columns} rows={preview.preview} rowKey={(r) => r.id} mobile="cards" data-testid="csv-preview-table" />
            )}
            <OutPath value={out} onChange={setOut} overwrite={overwrite} onOverwrite={setOverwrite} testId="csv" />
            {err && (
              <Callout tone="danger" alert title={err.message} data-testid="csv-error">
                {err instanceof ApiError ? err.hint : null}
              </Callout>
            )}
            <div className="flex flex-wrap items-center justify-end gap-2">
              {blocked && <span className="text-small text-muted">Fix the problems above to convert.</span>}
              <Button
                variant="primary"
                disabled={blocked}
                loading={busy === "convert"}
                data-testid="csv-convert"
                onClick={async () => {
                  if (!preview) return;
                  setBusy("convert");
                  setErr(null);
                  try {
                    const r = await convertCsv({ upload_id: preview.upload_id, out: out.trim(), task: preview.task, delimiter: preview.delimiter, overwrite });
                    setDone(r);
                    setPreview(null);
                    toast({ title: `${fmtInt(r.rows)} rows → ${r.path}` });
                    void qc.invalidateQueries({ queryKey: dataKeys.suites });
                  } catch (e) {
                    setErr(e as Error);
                  } finally {
                    setBusy(null);
                  }
                }}
              >
                Convert
              </Button>
            </div>
          </CardBody>
        </Card>
      )}

      {done && (
        <Callout
          tone="success"
          title={`${fmtInt(done.rows)} rows → ${done.path}`}
          data-testid="csv-done"
          actions={
            <Button size="sm" icon={Search} onClick={() => onInspect(`file:${done.file_id}`, done.path)} data-testid="csv-done-inspect">
              Inspect
            </Button>
          }
        >
          Use it as a suite in a lab (<code className="font-mono">- {done.path}</code>) or as training rows.
        </Callout>
      )}
    </div>
  );
}
