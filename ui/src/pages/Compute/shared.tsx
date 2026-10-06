// Small pieces shared by the Compute tabs.
import type { ComponentProps, ReactNode } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, PlugZap } from "lucide-react";
import { ApiError } from "@/lib/api";
import { Badge, Callout, CodeInline, CopyButton, DataTable, EmptyState, ErrorState, Tooltip } from "@/components";
import { cn } from "@/lib/cn";

export const FAKE_NOTE = "Cloud calls return fixtures. Nothing is created or billed.";
export const IDLE_NOTE =
  "No Studio job is using this machine. It bills until you destroy it. It may belong to a CLI run started outside Studio.";

/** Small `fixtures` badge repeated in every cloud panel header in fake-cloud mode (DESIGN 3.1). */
export function FixturesBadge({ fake }: { fake: boolean | undefined }) {
  if (!fake) return null;
  return (
    <Tooltip content={FAKE_NOTE}>
      <span tabIndex={0} className="inline-flex rounded-xs">
        <Badge tone="warning" data-testid="fixtures-badge">
          fixtures
        </Badge>
      </span>
    </Tooltip>
  );
}

/** "idle · billing" warning badge, or a link to the job that owns the machine. */
export function OwnerCell({ idle, jobId, jobTitle, testId }: { idle: boolean; jobId: string | null; jobTitle?: string | null; testId: string }) {
  if (jobId) {
    return (
      <Link to={`/jobs/${jobId}`} className="text-small text-accent hover:underline">
        {jobTitle ?? jobId}
      </Link>
    );
  }
  if (!idle) return <span className="text-small text-muted">—</span>;
  return (
    <Tooltip content={IDLE_NOTE}>
      <span tabIndex={0} className="inline-flex rounded-xs" aria-label={`idle, billing. ${IDLE_NOTE}`}>
        <Badge tone="warning" icon={AlertTriangle} data-testid={testId}>
          idle · billing
        </Badge>
      </span>
    </Tooltip>
  );
}

/** Warning shown in a destroy/terminate dialog when a running job uses the machine. */
export function OwnerWarning({ jobId, jobTitle }: { jobId: string | null; jobTitle?: string | null }) {
  if (!jobId) return null;
  return (
    <Callout tone="warning" className="mt-3" data-testid="owner-warning">
      Job <Link to={`/jobs/${jobId}`}>{jobTitle ?? jobId}</Link> is using this machine and will fail.
    </Callout>
  );
}

/**
 * A failed cloud read: tooling missing (424) or no credentials become a calm empty state with the
 * install/fix command; anything else is the standard ErrorState with Retry.
 */
export function CloudError({ error, onRetry, title }: { error: unknown; onRetry: () => void; title?: string }) {
  if (ApiError.is(error, "backend_unavailable")) {
    const install = typeof error.detail?.install === "string" ? (error.detail.install as string) : undefined;
    return (
      <EmptyState
        icon={PlugZap}
        title={title ?? error.message}
        body={
          <>
            {error.message} {error.hint && <span>{error.hint}</span>}
          </>
        }
        command={install}
      />
    );
  }
  return <ErrorState error={error} onRetry={onRetry} />;
}

/** One-line "label value" row used in identity/machine cards. */
export function Fact({ label, children, testId, className }: { label: string; children: ReactNode; testId?: string; className?: string }) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-0.5", className)} data-testid={testId}>
      <dt className="text-caption uppercase tracking-wide text-muted">{label}</dt>
      <dd className="min-w-0 break-words text-body">{children}</dd>
    </div>
  );
}

export function CommandHint({ label, command }: { label: ReactNode; command: string }) {
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2 text-small text-muted">
      <span>{label}</span>
      <CodeInline code={command} copy />
    </div>
  );
}

/** A command or snippet that wraps instead of scrolling (no keyboard-trapped scroll region), with Copy. */
export function WrapCode({ code, label = "Copy", className, "data-testid": testId }: { code: string; label?: string; className?: string; "data-testid"?: string }) {
  return (
    <div className={cn("relative min-w-0 rounded-sm border border-border bg-surface-2", className)} data-testid={testId}>
      <code className="block whitespace-pre-wrap break-all p-3 pr-10 font-mono text-mono text-text">{code}</code>
      <CopyButton text={code} label={label} className="absolute right-1.5 top-1.5" />
    </div>
  );
}

/**
 * DataTable, or its empty state on its own when there are no rows (so a card list never holds a
 * non-item child). `data-testid` goes on a wrapper that is always present.
 */
export function TableOrEmpty<T>({
  "data-testid": testId,
  empty,
  ...props
}: Omit<ComponentProps<typeof DataTable<T>>, "empty"> & { empty: ReactNode }) {
  const showEmpty = !props.loading && props.rows.length === 0;
  return <div data-testid={testId}>{showEmpty ? empty : <DataTable<T> {...props} />}</div>;
}
