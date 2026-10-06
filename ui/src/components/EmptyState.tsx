import type { ReactNode } from "react";
import { Inbox } from "lucide-react";
import { cn } from "@/lib/cn";
import { CodeInline } from "./Code";

/** One sentence of what this is, one primary action, and the CLI equivalent when there is one. */
export function EmptyState({
  title,
  body,
  action,
  command,
  icon: Icon = Inbox,
  className,
  "data-testid": testId = "empty-state",
}: {
  title: ReactNode;
  body?: ReactNode;
  action?: ReactNode;
  command?: string;
  icon?: typeof Inbox;
  className?: string;
  "data-testid"?: string;
}) {
  return (
    <div data-testid={testId} className={cn("flex flex-col items-center px-4 py-10 text-center", className)}>
      <span className="mb-3 inline-flex h-10 w-10 items-center justify-center rounded-md bg-surface-2 text-muted">
        <Icon size={20} aria-hidden />
      </span>
      <div className="text-h3">{title}</div>
      {body && <p className="mt-1 max-w-[56ch] text-small text-muted">{body}</p>}
      {action && <div className="mt-4">{action}</div>}
      {command && (
        <div className="mt-3 max-w-full text-small text-subtle">
          From a terminal: <CodeInline code={command} copy />
        </div>
      )}
    </div>
  );
}
