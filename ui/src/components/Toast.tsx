import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { CircleCheck, Info, X } from "lucide-react";
import { cn } from "@/lib/cn";

// Toasts are for success and info only (errors sit where the action was). 4 s, role="status".
export type ToastInput = {
  title: string;
  description?: string;
  tone?: "success" | "info";
  action?: { label: string; onClick: () => void };
  durationMs?: number;
};
type ToastItem = ToastInput & { id: number };

const Ctx = createContext<(t: ToastInput) => void>(() => {});
let seq = 0;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const dismiss = useCallback((id: number) => setItems((xs) => xs.filter((x) => x.id !== id)), []);
  const push = useCallback(
    (t: ToastInput) => {
      const id = ++seq;
      setItems((xs) => [...xs.slice(-3), { ...t, id }]);
      window.setTimeout(() => dismiss(id), t.durationMs ?? 4000);
    },
    [dismiss],
  );
  return (
    <Ctx.Provider value={push}>
      {children}
      {createPortal(
        <div className="pointer-events-none fixed bottom-4 right-4 z-toast flex w-[min(380px,calc(100vw-32px))] flex-col gap-2 max-sm:bottom-20">
          {items.map((t) => (
            <div
              key={t.id}
              role="status"
              data-testid="toast"
              className="dl-fade-in pointer-events-auto flex gap-3 rounded-md border border-border bg-surface-2 px-4 py-3 shadow-elev-2"
            >
              {t.tone === "info" ? (
                <Info size={18} aria-hidden className="mt-0.5 shrink-0 text-info" />
              ) : (
                <CircleCheck size={18} aria-hidden className="mt-0.5 shrink-0 text-success" />
              )}
              <div className="min-w-0 flex-1">
                <div className="font-semibold">{t.title}</div>
                {t.description && <div className="text-small text-muted">{t.description}</div>}
                {t.action && (
                  <button
                    type="button"
                    className="mt-1 text-small font-semibold text-accent hover:underline"
                    onClick={() => {
                      t.action!.onClick();
                      dismiss(t.id);
                    }}
                  >
                    {t.action.label}
                  </button>
                )}
              </div>
              <button
                type="button"
                aria-label="Dismiss"
                onClick={() => dismiss(t.id)}
                className={cn("inline-flex h-6 w-6 items-center justify-center rounded-sm text-muted hover:text-text")}
              >
                <X size={14} aria-hidden />
              </button>
            </div>
          ))}
        </div>,
        document.body,
      )}
    </Ctx.Provider>
  );
}

export const useToast = () => useContext(Ctx);
