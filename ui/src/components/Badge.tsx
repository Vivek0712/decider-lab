import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";
import { cn } from "@/lib/cn";

export type Tone = "neutral" | "accent" | "success" | "warning" | "danger" | "info";

const tones: Record<Tone, string> = {
  neutral: "bg-surface-3 text-muted border-border",
  accent: "bg-tint-accent text-accent border-transparent",
  success: "bg-tint-accent text-success border-transparent",
  warning: "bg-tint-warning text-warning border-transparent",
  danger: "bg-tint-danger text-danger border-transparent",
  info: "bg-tint-info text-info border-transparent",
};

export function Badge({
  tone = "neutral",
  icon: Icon,
  children,
  className,
  title,
  "data-testid": testId,
}: {
  tone?: Tone;
  icon?: LucideIcon;
  children: ReactNode;
  className?: string;
  title?: string;
  "data-testid"?: string;
}) {
  return (
    <span
      title={title}
      data-testid={testId}
      className={cn(
        "inline-flex h-6 items-center gap-1 whitespace-nowrap rounded-xs border px-2 text-caption",
        tones[tone],
        className,
      )}
    >
      {Icon && <Icon size={12} aria-hidden />}
      {children}
    </span>
  );
}
