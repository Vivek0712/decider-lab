import { RotateCw } from "lucide-react";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import { Button } from "./Button";

/** A failed load, in place of the region that failed: message, hint, Retry, and Details. */
export function ErrorState({ error, onRetry, className }: { error: unknown; onRetry?: () => void; className?: string }) {
  const e = error instanceof ApiError ? error : null;
  const message = e?.message ?? (error instanceof Error ? error.message : "Something went wrong.");
  return (
    <div data-testid="error-state" role="alert" className={cn("rounded-md border border-border bg-tint-danger px-4 py-4", className)}>
      <div className="font-semibold text-text">{message}</div>
      {e?.hint && <p className="mt-1 text-small text-muted">{e.hint}</p>}
      <div className="mt-3 flex flex-wrap items-center gap-3">
        {onRetry && (
          <Button size="sm" icon={RotateCw} onClick={onRetry}>
            Retry
          </Button>
        )}
        {e && (
          <details className="text-small text-muted">
            <summary className="cursor-pointer select-none">Details</summary>
            <div className="mt-1 font-mono text-caption">
              code {e.code} · status {e.status}
              {e.requestId && <> · request {e.requestId}</>}
            </div>
          </details>
        )}
      </div>
    </div>
  );
}
