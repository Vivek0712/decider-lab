import { useRef, type ReactNode } from "react";
import { useSearchParams } from "react-router-dom";
import { cn } from "@/lib/cn";

export type TabDef = { id: string; label: ReactNode; count?: number; disabled?: boolean };

/**
 * Tab strip (ARIA tabs; arrows/Home/End move). Controlled with value/onChange, or synced to the
 * URL query (`?tab=`) with `param` (DESIGN.md: every tab lives in the query string).
 * Render panels with <TabPanel id=... active=...>. Test ids: `${testIdPrefix}-${id}`.
 */
export function Tabs({
  tabs,
  value,
  onChange,
  label,
  testIdPrefix,
  className,
}: {
  tabs: TabDef[];
  value: string;
  onChange: (id: string) => void;
  label: string;
  testIdPrefix?: string;
  className?: string;
}) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  const enabled = tabs.filter((t) => !t.disabled);
  const move = (dir: 1 | -1 | "home" | "end") => {
    const i = enabled.findIndex((t) => t.id === value);
    const next =
      dir === "home" ? enabled[0] : dir === "end" ? enabled[enabled.length - 1] : enabled[(i + dir + enabled.length) % enabled.length];
    if (next) {
      onChange(next.id);
      refs.current[next.id]?.focus();
    }
  };
  return (
    <div
      role="tablist"
      aria-label={label}
      className={cn("scrollbar-thin flex max-w-full gap-1 overflow-x-auto border-b border-border", className)}
      onKeyDown={(e) => {
        const k = { ArrowRight: 1, ArrowLeft: -1, Home: "home", End: "end" }[e.key] as 1 | -1 | "home" | "end" | undefined;
        if (k !== undefined) {
          e.preventDefault();
          move(k);
        }
      }}
    >
      {tabs.map((t) => {
        const on = t.id === value;
        return (
          <button
            key={t.id}
            ref={(el) => {
              refs.current[t.id] = el;
            }}
            role="tab"
            type="button"
            id={`tab-${t.id}`}
            aria-selected={on}
            aria-controls={`panel-${t.id}`}
            tabIndex={on ? 0 : -1}
            disabled={t.disabled}
            data-testid={testIdPrefix ? `${testIdPrefix}-${t.id}` : undefined}
            onClick={() => onChange(t.id)}
            className={cn(
              "focus-inset relative -mb-px inline-flex h-10 shrink-0 items-center gap-2 border-b-2 px-3 text-body font-medium transition-colors duration-fast max-sm:h-11",
              on ? "border-accent text-text" : "border-transparent text-muted hover:text-text",
              t.disabled && "cursor-not-allowed opacity-50",
            )}
          >
            {t.label}
            {t.count != null && (
              <span className="tnum rounded-full bg-surface-3 px-1.5 text-caption text-muted">{t.count}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}

export function TabPanel({ id, active, children, className }: { id: string; active: boolean; children: ReactNode; className?: string }) {
  if (!active) return null;
  return (
    <div role="tabpanel" id={`panel-${id}`} aria-labelledby={`tab-${id}`} tabIndex={0} className={cn("focus-visible:outline-none", className)}>
      {children}
    </div>
  );
}

/** [tab, setTab] synced to `?<param>=` (default "tab"), falling back to `fallback`. */
export function useTabParam(fallback: string, param = "tab"): [string, (id: string) => void] {
  const [sp, setSp] = useSearchParams();
  const tab = sp.get(param) ?? fallback;
  const set = (id: string) =>
    setSp(
      (prev) => {
        const n = new URLSearchParams(prev);
        if (id === fallback) n.delete(param);
        else n.set(param, id);
        return n;
      },
      { replace: true },
    );
  return [tab, set];
}
