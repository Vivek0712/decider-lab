import { useId, useState, type ReactElement, type ReactNode } from "react";
import { cloneElement } from "react";
import { cn } from "@/lib/cn";

/** Hover and focus tooltip. Never the only place for essential information. */
export function Tooltip({
  content,
  side = "top",
  children,
}: {
  content: ReactNode;
  side?: "top" | "bottom";
  children: ReactElement;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const trigger = cloneElement(children, {
    "aria-describedby": open ? id : undefined,
    onMouseEnter: () => setOpen(true),
    onMouseLeave: () => setOpen(false),
    onFocus: () => setOpen(true),
    onBlur: () => setOpen(false),
    onKeyDown: (e: KeyboardEvent) => e.key === "Escape" && setOpen(false),
  } as Record<string, unknown>);
  return (
    <span className="relative inline-flex">
      {trigger}
      {open && (
        <span
          role="tooltip"
          id={id}
          className={cn(
            "pointer-events-none absolute left-1/2 z-popover w-max max-w-[280px] -translate-x-1/2 rounded-sm border border-border bg-surface-2 px-2 py-1 text-small text-text shadow-elev-2",
            side === "top" ? "bottom-full mb-1.5" : "top-full mt-1.5",
          )}
        >
          {content}
        </span>
      )}
    </span>
  );
}
