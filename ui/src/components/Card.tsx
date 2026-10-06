import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/cn";

export function Card({ className, children, ...rest }: HTMLAttributes<HTMLElement> & { "data-testid"?: string }) {
  return (
    <section className={cn("min-w-0 rounded-md border border-border bg-surface-1 shadow-elev-1", className)} {...rest}>
      {children}
    </section>
  );
}

export function CardHeader({
  title,
  description,
  actions,
  className,
  as: H = "h2",
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
  as?: "h2" | "h3";
}) {
  return (
    <header className={cn("flex flex-wrap items-start justify-between gap-3 border-b border-border px-4 py-3 sm:px-5", className)}>
      <div className="min-w-0">
        <H className={H === "h2" ? "text-h2" : "text-h3"}>{title}</H>
        {description && <p className="mt-0.5 text-small text-muted">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </header>
  );
}

export function CardBody({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cn("px-4 py-4 sm:px-5", className)}>{children}</div>;
}
