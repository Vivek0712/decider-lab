import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { cn } from "@/lib/cn";

const FOCUSABLE =
  'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"]),.cm-content';

/** Focus trap + Esc + focus restore + scroll lock, shared by Dialog, Drawer and the palette. */
export function useModal(open: boolean, onClose: () => void, panel: React.RefObject<HTMLElement | null>, initialFocus?: React.RefObject<HTMLElement | null>) {
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const t = window.setTimeout(() => {
      const el = initialFocus?.current ?? panel.current?.querySelector<HTMLElement>("[data-autofocus]") ?? panel.current?.querySelector<HTMLElement>(FOCUSABLE) ?? panel.current;
      el?.focus();
    }, 0);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        closeRef.current();
      } else if (e.key === "Tab" && panel.current) {
        const items = Array.from(panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((x) => x.offsetParent !== null);
        if (!items.length) return;
        const first = items[0];
        const last = items[items.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      window.clearTimeout(t);
      document.removeEventListener("keydown", onKey, true);
      document.body.style.overflow = overflow;
      prev?.focus?.();
    };
  }, [open, panel, initialFocus]);
}

const sizes = { sm: "max-w-[420px]", md: "max-w-[560px]", lg: "max-w-[800px]", full: "max-w-[1200px]" };

export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  size = "md",
  children,
  footer,
  "data-testid": testId,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  description?: ReactNode;
  size?: keyof typeof sizes;
  children?: ReactNode;
  footer?: ReactNode;
  "data-testid"?: string;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const id = useId();
  useModal(open, () => onOpenChange(false), panel);
  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-dialog flex items-end justify-center sm:items-center sm:p-6">
      <div className="absolute inset-0 bg-[var(--scrim)] dl-fade-in" onClick={() => onOpenChange(false)} aria-hidden />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-t`}
        aria-describedby={description ? `${id}-d` : undefined}
        tabIndex={-1}
        data-testid={testId}
        className={cn(
          "dl-fade-in relative flex max-h-[92vh] w-full flex-col rounded-t-lg border border-border bg-surface-1 shadow-elev-3 sm:rounded-lg",
          sizes[size],
        )}
      >
        <header className="flex items-start gap-3 border-b border-border px-5 py-4">
          <div className="min-w-0 flex-1">
            <h2 id={`${id}-t`} className="text-h2">
              {title}
            </h2>
            {description && (
              <p id={`${id}-d`} className="mt-1 text-small text-muted">
                {description}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            aria-label="Close"
            className="inline-flex h-8 w-8 items-center justify-center rounded-sm text-muted hover:bg-surface-3 hover:text-text max-sm:h-11 max-sm:w-11"
          >
            <X size={18} aria-hidden />
          </button>
        </header>
        <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer && <footer className="flex flex-wrap justify-end gap-2 border-t border-border px-5 py-3">{footer}</footer>}
      </div>
    </div>,
    document.body,
  );
}

export function Drawer({
  open,
  onOpenChange,
  title,
  side = "right",
  width = 480,
  children,
  "data-testid": testId,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  side?: "left" | "right";
  width?: number;
  children: ReactNode;
  "data-testid"?: string;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const id = useId();
  useModal(open, () => onOpenChange(false), panel);
  if (!open) return null;
  return createPortal(
    <div className="fixed inset-0 z-drawer">
      <div className="absolute inset-0 bg-[var(--scrim)] dl-fade-in" onClick={() => onOpenChange(false)} aria-hidden />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-t`}
        tabIndex={-1}
        data-testid={testId}
        style={{ width: `min(${width}px, 100vw)` }}
        className={cn(
          "absolute inset-y-0 flex flex-col border-border bg-surface-1 shadow-elev-3",
          side === "right" ? "right-0 border-l" : "left-0 border-r",
        )}
      >
        <header className="flex items-center gap-3 border-b border-border px-4 py-3">
          <h2 id={`${id}-t`} className="min-w-0 flex-1 truncate text-h3">
            {title}
          </h2>
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            aria-label="Close"
            className="inline-flex h-8 w-8 items-center justify-center rounded-sm text-muted hover:bg-surface-3 hover:text-text max-sm:h-11 max-sm:w-11"
          >
            <X size={18} aria-hidden />
          </button>
        </header>
        <div className="scrollbar-thin min-h-0 flex-1 overflow-y-auto">{children}</div>
      </div>
    </div>,
    document.body,
  );
}
