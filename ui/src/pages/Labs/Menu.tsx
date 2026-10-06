import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { MoreHorizontal, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/cn";

export type MenuItem = {
  id: string;
  label: string;
  icon?: LucideIcon;
  onSelect: () => void;
  danger?: boolean;
  disabled?: boolean;
  testId?: string;
};

/**
 * Overflow menu (ARIA menu button): Enter/Space/ArrowDown open it, arrows move, Esc closes and
 * returns focus, a click outside closes. Used by the Labs and Jobs pages.
 */
export function OverflowMenu({
  label,
  items,
  trigger,
  "data-testid": testId,
  align = "right",
}: {
  label: string;
  items: MenuItem[];
  trigger?: ReactNode;
  "data-testid"?: string;
  align?: "left" | "right";
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const btn = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!list.current?.contains(e.target as Node) && !btn.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    const t = window.setTimeout(() => list.current?.querySelector<HTMLElement>("[role=menuitem]:not([disabled])")?.focus(), 0);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      window.clearTimeout(t);
    };
  }, [open]);
  const close = (focus = true) => {
    setOpen(false);
    if (focus) btn.current?.focus();
  };
  const move = (dir: 1 | -1) => {
    const els = Array.from(list.current?.querySelectorAll<HTMLElement>("[role=menuitem]:not([disabled])") ?? []);
    const i = els.indexOf(document.activeElement as HTMLElement);
    els[(i + dir + els.length) % els.length]?.focus();
  };
  return (
    <span className="relative inline-flex" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
      <button
        ref={btn}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        aria-label={label}
        title={label}
        data-testid={testId}
        onClick={() => setOpen(!open)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setOpen(true);
          }
        }}
        className={cn(
          "inline-flex h-8 min-w-8 items-center justify-center gap-1 rounded-sm px-1.5 text-muted hover:bg-surface-3 hover:text-text max-sm:h-11 max-sm:min-w-11",
          trigger ? "border border-border-strong bg-surface-2 px-3 text-text" : "",
        )}
      >
        {trigger ?? <MoreHorizontal size={18} aria-hidden />}
      </button>
      {open && (
        <div
          ref={list}
          id={id}
          role="menu"
          aria-label={label}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              e.preventDefault();
              close();
            } else if (e.key === "ArrowDown") {
              e.preventDefault();
              move(1);
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              move(-1);
            } else if (e.key === "Tab") {
              close(false);
            }
          }}
          className={cn(
            "absolute top-full z-popover mt-1 min-w-[200px] rounded-md border border-border bg-surface-2 py-1 shadow-elev-2",
            align === "right" ? "right-0" : "left-0",
          )}
        >
          {items.map((it) => {
            const Icon = it.icon;
            return (
              <button
                key={it.id}
                type="button"
                role="menuitem"
                disabled={it.disabled}
                data-testid={it.testId}
                onClick={() => {
                  close(false);
                  it.onSelect();
                }}
                className={cn(
                  "flex w-full items-center gap-2 px-3 py-2 text-left text-body hover:bg-surface-3 focus:bg-surface-3 disabled:opacity-50 max-sm:min-h-[44px]",
                  it.danger ? "text-danger" : "text-text",
                )}
              >
                {Icon && <Icon size={16} aria-hidden />}
                {it.label}
              </button>
            );
          })}
        </div>
      )}
    </span>
  );
}
