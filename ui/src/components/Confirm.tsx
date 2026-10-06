import { useEffect, useId, useState, type ReactNode } from "react";
import { ApiError } from "@/lib/api";
import { Button } from "./Button";
import { Callout } from "./Callout";
import { Dialog } from "./Overlay";

/** "Type `<phrase>` to confirm" input; matches exactly (DESIGN.md TypedConfirm). */
export function TypedConfirm({
  phrase,
  value,
  onChange,
  label,
}: {
  phrase: string;
  value: string;
  onChange: (v: string) => void;
  label?: ReactNode;
}) {
  const id = useId();
  const ok = value === phrase;
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-small font-medium">
        {label ?? (
          <>
            Type <code className="rounded-xs bg-surface-2 px-1 font-mono" data-testid="confirm-phrase">{phrase}</code> to confirm
          </>
        )}
      </label>
      <input
        id={id}
        data-testid="confirm-input"
        value={value}
        autoComplete="off"
        spellCheck={false}
        onChange={(e) => onChange(e.target.value)}
        className="h-9 w-full rounded-sm border border-border-strong bg-surface-2 px-3 font-mono text-mono text-text max-sm:min-h-[44px]"
      />
      <span aria-live="polite" className="text-caption text-subtle">
        {ok ? "Confirmation matches" : " "}
      </span>
    </div>
  );
}

/**
 * Confirmation dialog. With `phrase`, the person must type it; `onConfirm(typed)` gets the typed
 * text to send as `confirm` (the server enforces it). Errors from onConfirm show inline.
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  body,
  confirmLabel,
  tone = "default",
  phrase,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  title: ReactNode;
  body?: ReactNode;
  confirmLabel: string;
  tone?: "danger" | "default";
  phrase?: string;
  onConfirm: (typed: string) => Promise<unknown> | unknown;
}) {
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (open) {
      setTyped("");
      setError(null);
    }
  }, [open]);
  const ready = !phrase || typed === phrase;
  const submit = async () => {
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    try {
      await onConfirm(typed);
      onOpenChange(false);
    } catch (e) {
      setError(e instanceof ApiError || e instanceof Error ? e.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={title}
      size="sm"
      data-testid="confirm-dialog"
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Keep it
          </Button>
          <Button variant={tone === "danger" ? "danger" : "primary"} disabled={!ready} loading={busy} onClick={submit} data-testid="confirm-submit">
            {confirmLabel}
          </Button>
        </>
      }
    >
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        {body && <div className="text-body text-muted">{body}</div>}
        {phrase && <TypedConfirm phrase={phrase} value={typed} onChange={setTyped} />}
        {error && (
          <Callout tone="danger" alert>
            {error}
          </Callout>
        )}
      </form>
    </Dialog>
  );
}
