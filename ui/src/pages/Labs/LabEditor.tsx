import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useBlocker } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { EditorView } from "@codemirror/view";
import { AlertTriangle, ChevronDown, CircleX, EyeOff, KeyRound, RotateCcw, Save } from "lucide-react";
import { fixSecret, getLab, labKeys, saveLab, validateLab } from "@/api/labs";
import type { Lab, Problem, Validation } from "@/api/types";
import { Button } from "@/components/Button";
import { Callout } from "@/components/Callout";
import { Card, CardHeader } from "@/components/Card";
import { YamlEditor } from "@/components/CodeEditor";
import { Input } from "@/components/Field";
import { Dialog } from "@/components/Overlay";
import { useToast } from "@/components/Toast";
import { useHotkeys } from "@/hooks/useHotkeys";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import { fmtRelative } from "@/lib/format";
import { useRegisterCommands } from "@/palette/registry";
import { LabSummaryView } from "./LabSummary";
import { OverflowMenu } from "./Menu";
import { SNIPPETS, insertSnippet } from "./snippets";

export function LabEditor({ lab, onSaved, onDirtyChange }: { lab: Lab; onSaved: (lab: Lab) => void; onDirtyChange?: (d: boolean) => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [text, setText] = useState(lab.yaml);
  const [base, setBase] = useState<{ yaml: string; etag: string; modified_at: string }>({ yaml: lab.yaml, etag: lab.etag, modified_at: lab.modified_at });
  const [validation, setValidation] = useState<Validation>({ valid: lab.valid, problems: lab.problems, summary: lab.summary });
  const [validating, setValidating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<ApiError | null>(null);
  const [conflict, setConflict] = useState(false);
  const [fix, setFix] = useState<Problem | null>(null);
  const holder = useRef<HTMLDivElement>(null);
  const dirty = text !== base.yaml;

  useEffect(() => onDirtyChange?.(dirty), [dirty, onDirtyChange]);

  // the lab changed underneath (another tab saved, fix-secret): follow it when there are no local edits
  useEffect(() => {
    if (lab.etag !== base.etag && !dirty) {
      setText(lab.yaml);
      setBase({ yaml: lab.yaml, etag: lab.etag, modified_at: lab.modified_at });
      setValidation({ valid: lab.valid, problems: lab.problems, summary: lab.summary });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lab.etag]);

  // validate on change, debounced 400 ms (no write)
  useEffect(() => {
    const ctl = new AbortController();
    const t = window.setTimeout(() => {
      setValidating(true);
      validateLab(text, lab.lab_id, ctl.signal)
        .then((v) => setValidation(v))
        .catch(() => undefined)
        .finally(() => !ctl.signal.aborted && setValidating(false));
    }, 400);
    return () => {
      ctl.abort();
      window.clearTimeout(t);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, lab.lab_id]);

  // unsaved changes: browser unload and in-app navigation (not tab switches within this lab)
  useEffect(() => {
    if (!dirty) return;
    const fn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", fn);
    return () => window.removeEventListener("beforeunload", fn);
  }, [dirty]);
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname);

  const save = useCallback(
    async (overwrite = false) => {
      if (saving) return;
      setSaving(true);
      setSaveError(null);
      try {
        const saved = await saveLab(lab.lab_id, text, overwrite ? "*" : base.etag);
        setBase({ yaml: saved.yaml, etag: saved.etag, modified_at: saved.modified_at });
        setText(saved.yaml);
        setValidation({ valid: saved.valid, problems: saved.problems, summary: saved.summary });
        setConflict(false);
        qc.setQueryData(labKeys.one(lab.lab_id), saved);
        void qc.invalidateQueries({ queryKey: ["labs", "list"] });
        onSaved(saved);
        toast({
          title: saved.valid ? "Saved lab.yaml" : `Saved lab.yaml with ${saved.problems.filter((p) => p.severity === "error").length} error(s)`,
          description: saved.warning,
        });
      } catch (e) {
        if (e instanceof ApiError && e.code === "etag_mismatch") setConflict(true);
        else setSaveError(e instanceof ApiError ? e : null);
      } finally {
        setSaving(false);
      }
    },
    [saving, lab.lab_id, text, base.etag, qc, onSaved, toast],
  );

  const loadDisk = async () => {
    const fresh = await getLab(lab.lab_id);
    qc.setQueryData(labKeys.one(lab.lab_id), fresh);
    setText(fresh.yaml);
    setBase({ yaml: fresh.yaml, etag: fresh.etag, modified_at: fresh.modified_at });
    setValidation({ valid: fresh.valid, problems: fresh.problems, summary: fresh.summary });
    setConflict(false);
    onSaved(fresh);
  };

  useHotkeys([{ keys: "mod+s", allowInInput: true, handler: () => dirty && void save() }]);
  useRegisterCommands("lab-editor", [{ id: "lab.save", label: "Save lab", section: "Commands", shortcut: ["mod", "S"], run: () => void save() }], [save]);

  const jump = (p: Problem) => {
    const dom = holder.current?.querySelector<HTMLElement>(".cm-editor");
    if (!dom || !p.line) return;
    const view = EditorView.findFromDOM(dom);
    if (!view) return;
    const line = view.state.doc.line(Math.min(Math.max(1, p.line), view.state.doc.lines));
    const pos = Math.min(line.to, line.from + Math.max(0, (p.column ?? 1) - 1));
    view.dispatch({ selection: { anchor: pos }, effects: EditorView.scrollIntoView(pos, { y: "center" }) });
    view.focus();
  };

  const problems = validation.problems;
  const errors = problems.filter((p) => p.severity === "error").length;
  const masked = (text.match(/••••/g) ?? []).length;
  const groups = useMemo(() => ["Model", "Suite", "Option", "Compute"] as const, []);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3 text-small text-muted" aria-live="polite">
          {dirty ? (
            <span className="inline-flex items-center gap-1.5 text-warning" data-testid="lab-dirty">
              <span aria-hidden>●</span> unsaved changes
            </span>
          ) : (
            <span>saved {fmtRelative(base.modified_at)}</span>
          )}
          {validating && <span>checking…</span>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <OverflowMenu
            label="Insert a snippet"
            data-testid="lab-insert"
            align="right"
            trigger={
              <span className="inline-flex items-center gap-1 text-small">
                Insert <ChevronDown size={14} aria-hidden />
              </span>
            }
            items={groups.flatMap((g) =>
              SNIPPETS.filter((s) => s.group === g).map((s) => ({
                id: s.id,
                label: `${g}: ${s.label}`,
                testId: `lab-insert-${s.id}`,
                onSelect: () => setText((t) => insertSnippet(t, s)),
              })),
            )}
          />
          <Button icon={RotateCcw} disabled={!dirty} onClick={() => setText(base.yaml)} data-testid="lab-revert">
            Revert
          </Button>
          <Button variant="primary" icon={Save} disabled={!dirty} loading={saving} onClick={() => void save()} data-testid="lab-save" kbd={["mod", "S"]}>
            Save
          </Button>
        </div>
      </div>
      {conflict && (
        <Callout
          tone="warning"
          title="lab.yaml changed on disk since you opened it."
          data-testid="lab-conflict"
          actions={
            <>
              <Button size="sm" onClick={() => void loadDisk()} data-testid="lab-load-disk">
                Load disk version
              </Button>
              <Button size="sm" variant="danger" onClick={() => void save(true)} data-testid="lab-overwrite">
                Overwrite
              </Button>
            </>
          }
        >
          Loading the disk version discards your edits here. Overwriting replaces the file with your version.
        </Callout>
      )}
      {saveError && (
        <Callout tone="danger" alert title={saveError.message}>
          {saveError.hint}
        </Callout>
      )}
      {masked > 0 && (
        <Callout tone="info" data-testid="lab-secret-mask" title={`${masked} secret value${masked === 1 ? " is" : "s are"} hidden as ••••`}>
          <span className="inline-flex items-center gap-1">
            <EyeOff size={14} aria-hidden /> Hidden: Studio never shows secret values. Saving keeps the value on disk as long as •••• stays at the same key.
          </span>
        </Callout>
      )}
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr),minmax(300px,380px)]">
        <div ref={holder} className="min-w-0">
          <YamlEditor value={text} onChange={setText} problems={problems} height="560px" />
        </div>
        <aside className="flex min-w-0 flex-col gap-4" aria-label="Problems and summary">
          <Card data-testid="lab-problems">
            <CardHeader
              as="h3"
              title={`Problems (${problems.length})`}
              description={problems.length === 0 ? "No problems found." : `${errors} error${errors === 1 ? "" : "s"}, ${problems.length - errors} warning${problems.length - errors === 1 ? "" : "s"}`}
            />
            {problems.length > 0 && (
              <ul className="max-h-[320px] divide-y divide-border overflow-y-auto">
                {problems.map((p, i) => (
                  <li key={`${p.code}-${p.path}-${i}`} className="px-4 py-2" data-testid={`lab-problem-${p.code}`}>
                    <button
                      type="button"
                      onClick={() => jump(p)}
                      className="flex w-full items-start gap-2 text-left text-small hover:underline"
                      aria-label={`${p.severity}${p.line ? ` on line ${p.line}` : ""}: ${p.message}`}
                    >
                      {p.severity === "error" ? (
                        <CircleX size={16} aria-hidden className="mt-0.5 shrink-0 text-danger" />
                      ) : (
                        <AlertTriangle size={16} aria-hidden className="mt-0.5 shrink-0 text-warning" />
                      )}
                      <span className="min-w-0">
                        {p.line ? <span className="tnum mr-1 text-muted">line {p.line}:</span> : null}
                        <span className={cn(p.severity === "error" ? "text-text" : "text-text")}>{p.message}</span>
                      </span>
                    </button>
                    {p.code === "secret_literal" && p.path && /(^|\.)(api_key|aws_access_key_id|aws_secret_access_key|aws_session_token|token|password|secret)$/i.test(p.path) && (
                      <Button size="sm" icon={KeyRound} className="ml-6 mt-1.5" onClick={() => setFix(p)} data-testid="lab-fix-secret">
                        Move to environment variable…
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <div>
            <h3 className="mb-2 text-h3">Visual summary</h3>
            {validation.summary ? (
              <LabSummaryView summary={validation.summary} compact />
            ) : (
              <p className="text-small text-muted">The summary appears when the YAML parses into a mapping.</p>
            )}
          </div>
        </aside>
      </div>
      <FixSecretDialog
        problem={fix}
        dirty={dirty}
        onClose={() => setFix(null)}
        onFixed={(fresh) => {
          setText(fresh.yaml);
          setBase({ yaml: fresh.yaml, etag: fresh.etag, modified_at: fresh.modified_at });
          setValidation({ valid: fresh.valid, problems: fresh.problems, summary: fresh.summary });
          qc.setQueryData(labKeys.one(lab.lab_id), fresh);
          onSaved(fresh);
        }}
        labId={lab.lab_id}
        etag={base.etag}
      />
      <Dialog
        open={blocker.state === "blocked"}
        onOpenChange={(o) => !o && blocker.reset?.()}
        title="Discard unsaved changes?"
        description="lab.yaml has edits that are not saved."
        size="sm"
        data-testid="lab-leave-dialog"
        footer={
          <>
            <Button variant="ghost" onClick={() => blocker.reset?.()}>
              Keep editing
            </Button>
            <Button variant="danger" onClick={() => blocker.proceed?.()} data-testid="lab-leave-discard">
              Discard changes
            </Button>
          </>
        }
      />
    </div>
  );
}

function FixSecretDialog({
  problem,
  dirty,
  onClose,
  onFixed,
  labId,
  etag,
}: {
  problem: Problem | null;
  dirty: boolean;
  onClose: () => void;
  onFixed: (lab: Lab) => void;
  labId: string;
  etag: string;
}) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [seed, setSeed] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (problem && seed !== problem.path) {
    setSeed(problem.path);
    const parts = (problem.path ?? "").split(".");
    const model = parts.length >= 2 && parts[0] === "models" ? parts[1] : parts[0];
    const key = parts[parts.length - 1] ?? "api_key";
    setName(`${model}_${key}`.toUpperCase().replace(/[^A-Z0-9_]/g, "_"));
    setError(null);
  }
  const ok = /^[A-Z_][A-Z0-9_]{0,127}$/.test(name);
  const submit = async () => {
    if (!problem?.path || !ok) return;
    setBusy(true);
    setError(null);
    try {
      const fresh = await fixSecret(labId, problem.path, name, etag);
      onFixed(fresh);
      toast({ title: `Moved to ${name}`, description: `Set ${name} in the shell you start decider-lab ui from.` });
      setSeed(null);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not move the value.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog
      open={!!problem}
      onOpenChange={(o) => {
        if (!o) {
          setSeed(null);
          onClose();
        }
      }}
      title="Move to environment variable"
      size="md"
      data-testid="fix-secret-dialog"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" loading={busy} disabled={!ok || dirty} onClick={() => void submit()} data-testid="fix-secret-submit">
            Move value
          </Button>
        </>
      }
    >
      <form
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <p className="text-body text-muted">
          Rewrites <code className="font-mono">{problem?.path}</code> to <code className="font-mono">{(problem?.path ?? "").split(".").pop()}_env: {name || "NAME"}</code>. The value is removed
          from this file. Set {name || "NAME"} in the shell you start <code className="font-mono">decider-lab ui</code> from, and rotate the key if this file was ever shared or committed.
        </p>
        <Input label="Variable name" value={name} onChange={(e) => setName(e.target.value.toUpperCase())} mono error={name && !ok ? "Use A-Z, 0-9 and _." : undefined} data-testid="fix-secret-name" />
        {dirty && <Callout tone="warning">Save or revert your edits first; this rewrites the file on disk.</Callout>}
        {error && (
          <Callout tone="danger" alert>
            {error}
          </Callout>
        )}
      </form>
    </Dialog>
  );
}
