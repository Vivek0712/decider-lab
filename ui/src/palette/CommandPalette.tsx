import { useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { CornerDownLeft, Search } from "lucide-react";
import { cn } from "@/lib/cn";
import { useModal } from "@/components/Overlay";
import { Kbd } from "@/components/Kbd";
import { isMac } from "@/lib/platform";
import { searchCommands, useCommands, type Command } from "./registry";

const RECENT_KEY = "dl-palette-recent";

function readRecent(): string[] {
  try {
    return JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]") as string[];
  } catch {
    return [];
  }
}
function pushRecent(id: string) {
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify([id, ...readRecent().filter((x) => x !== id)].slice(0, 5)));
  } catch {
    /* storage blocked */
  }
}

/** Mod+K palette: a combobox over the command registry (DESIGN.md 8.1). `>` commands, `#` jobs, `@` labs. */
export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const commands = useCommands();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const panel = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const id = useId();
  useModal(open, () => onOpenChange(false), panel, input);

  useEffect(() => {
    if (open) {
      setQuery("");
      setActive(0);
    }
  }, [open]);

  const groups = useMemo(() => (open ? searchCommands(commands, query, readRecent()) : []), [commands, query, open]);
  const flat = useMemo(() => groups.flatMap((g) => g.items), [groups]);
  useEffect(() => setActive(0), [query]);
  useEffect(() => {
    list.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const run = (c: Command, newTab = false) => {
    pushRecent(c.id);
    onOpenChange(false);
    if (newTab && c.href) {
      window.open(c.href, "_blank", "noopener");
      return;
    }
    if (c.run) c.run({ navigate, close: () => onOpenChange(false) });
    else if (c.href) navigate(c.href);
  };

  if (!open) return null;
  let index = -1;
  return createPortal(
    <div className="fixed inset-0 z-palette flex items-start justify-center px-4 pt-[12vh]">
      <div className="absolute inset-0 bg-[var(--scrim)] dl-fade-in" aria-hidden onClick={() => onOpenChange(false)} />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        data-testid="command-palette"
        className="dl-fade-in relative flex max-h-[70vh] w-full max-w-[640px] flex-col overflow-hidden rounded-lg border border-border-strong bg-surface-1 shadow-elev-3"
      >
        <div className="flex items-center gap-3 border-b border-border px-4">
          <Search size={18} aria-hidden className="shrink-0 text-muted" />
          <input
            ref={input}
            data-testid="palette-input"
            role="combobox"
            aria-expanded="true"
            aria-controls={`${id}-list`}
            aria-activedescendant={flat[active] ? `${id}-opt-${active}` : undefined}
            aria-autocomplete="list"
            aria-label="Search or run a command"
            placeholder="Search or run a command…  (> commands, # jobs, @ labs)"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setActive((a) => Math.min(flat.length - 1, a + 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setActive((a) => Math.max(0, a - 1));
              } else if (e.key === "Home" && e.ctrlKey) {
                setActive(0);
              } else if (e.key === "Enter") {
                e.preventDefault();
                const c = flat[active];
                if (c) run(c, isMac ? e.metaKey : e.ctrlKey);
              }
            }}
            className="h-14 min-w-0 flex-1 bg-transparent text-[16px] text-text outline-none placeholder:text-subtle"
          />
          <Kbd keys={["Esc"]} className="max-sm:hidden" />
        </div>
        <ul ref={list} id={`${id}-list`} role="listbox" aria-label="Commands" className="scrollbar-thin min-h-0 flex-1 overflow-y-auto py-2">
          {flat.length === 0 && <li className="px-4 py-6 text-center text-small text-muted">No matching commands.</li>}
          {groups.map((g) => (
            <li key={g.section} role="presentation">
              <div className="px-4 pb-1 pt-2 text-caption uppercase tracking-wide text-subtle" aria-hidden>
                {g.section}
              </div>
              <ul role="group" aria-label={g.section}>
                {g.items.map((c) => {
                  index += 1;
                  const i = index;
                  const Icon = c.icon;
                  return (
                    <li
                      key={`${g.section}-${c.id}`}
                      id={`${id}-opt-${i}`}
                      role="option"
                      aria-selected={i === active}
                      data-index={i}
                      data-testid={`palette-item-${c.id}`}
                      onMouseMove={() => setActive(i)}
                      onClick={(e) => run(c, isMac ? e.metaKey : e.ctrlKey)}
                      className={cn(
                        "mx-2 flex cursor-pointer items-center gap-3 rounded-sm px-3 py-2 max-sm:min-h-[44px]",
                        i === active ? "bg-surface-3 text-text" : "text-text",
                      )}
                    >
                      <span className="inline-flex w-5 shrink-0 justify-center text-muted">{Icon && <Icon size={16} aria-hidden />}</span>
                      <span className="min-w-0 flex-1 truncate">
                        {c.label}
                        {c.detail && <span className="ml-2 text-small text-subtle">{c.detail}</span>}
                      </span>
                      {c.shortcut && <Kbd keys={c.shortcut} className="max-sm:hidden" />}
                      {i === active && <CornerDownLeft size={14} aria-hidden className="text-subtle" />}
                    </li>
                  );
                })}
              </ul>
            </li>
          ))}
        </ul>
      </div>
    </div>,
    document.body,
  );
}
