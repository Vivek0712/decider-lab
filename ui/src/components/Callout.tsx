import type { ReactNode } from "react";
import { AlertTriangle, CircleCheck, Info, OctagonAlert } from "lucide-react";
import { cn } from "@/lib/cn";
import type { Tone } from "./Badge";

const style: Record<Tone, { cls: string; Icon: typeof Info }> = {
  neutral: { cls: "border-border bg-surface-2 text-text", Icon: Info },
  accent: { cls: "border-transparent bg-tint-accent text-text", Icon: Info },
  success: { cls: "border-transparent bg-tint-accent text-text", Icon: CircleCheck },
  info: { cls: "border-transparent bg-tint-info text-text", Icon: Info },
  warning: { cls: "border-transparent bg-tint-warning text-text", Icon: AlertTriangle },
  danger: { cls: "border-transparent bg-tint-danger text-text", Icon: OctagonAlert },
};
const iconColor: Record<Tone, string> = {
  neutral: "text-muted", accent: "text-accent", success: "text-success", info: "text-info", warning: "text-warning", danger: "text-danger",
};

/** Inline message. `alert` (role="alert") only for a danger message shown after an action. */
export function Callout({
  tone = "info",
  title,
  children,
  actions,
  alert,
  className,
  "data-testid": testId,
}: {
  tone?: Tone;
  title?: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  alert?: boolean;
  className?: string;
  "data-testid"?: string;
}) {
  const { cls, Icon } = style[tone];
  return (
    <div role={alert ? "alert" : undefined} data-testid={testId} className={cn("flex gap-3 rounded-sm border px-3 py-2.5", cls, className)}>
      <Icon size={16} aria-hidden className={cn("mt-[3px] shrink-0", iconColor[tone])} />
      <div className="min-w-0 flex-1">
        {title && <div className="font-semibold">{title}</div>}
        {children && <div className={cn("text-small", title ? "mt-0.5 text-muted" : "text-text")}>{children}</div>}
        {actions && <div className="mt-2 flex flex-wrap gap-2">{actions}</div>}
      </div>
    </div>
  );
}
