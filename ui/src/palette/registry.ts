// Command palette action registry (DESIGN.md 8.1).
//
//   // in a page: commands live while the page is mounted
//   useRegisterCommands("labs-page", [
//     { id: "lab.save", label: "Save lab", section: "Commands", shortcut: ["mod", "S"], run: () => save() },
//   ], [save]);
//
// A later registration with the same id replaces an earlier one (pages override the shell's
// default navigation commands, e.g. "eval.quick" opening a dialog instead of navigating).
// Commands that open a dialog must never execute side effects directly.
import { useEffect, useSyncExternalStore } from "react";
import type { LucideIcon } from "lucide-react";
import type { NavigateFunction } from "react-router-dom";

export type CommandSection = "Recent" | "Commands" | "Navigation" | "Labs" | "Results" | "Jobs" | "Models";
export const SECTION_ORDER: CommandSection[] = ["Recent", "Commands", "Navigation", "Labs", "Results", "Jobs", "Models"];

export type CommandContext = { navigate: NavigateFunction; close: () => void };
export type Command = {
  id: string;
  label: string;
  section: CommandSection;
  detail?: string;
  keywords?: string[];
  icon?: LucideIcon;
  shortcut?: string[];
  /** Navigation target; enables Mod+Enter to open in a new tab. */
  href?: string;
  run?: (ctx: CommandContext) => void;
};

type Listener = () => void;

class Registry {
  private sources = new Map<string, { seq: number; cmds: Command[] }>();
  private listeners = new Set<Listener>();
  private seq = 0;
  private snapshot: Command[] = [];

  register(source: string, cmds: Command[]): () => void {
    this.sources.set(source, { seq: ++this.seq, cmds });
    this.rebuild();
    return () => {
      const cur = this.sources.get(source);
      if (cur && cur.cmds === cmds) {
        this.sources.delete(source);
        this.rebuild();
      }
    };
  }

  private rebuild(): void {
    const byId = new Map<string, Command>();
    [...this.sources.values()].sort((a, b) => a.seq - b.seq).forEach((s) => s.cmds.forEach((c) => byId.set(c.id, c)));
    this.snapshot = [...byId.values()];
    this.listeners.forEach((l) => l());
  }

  subscribe = (l: Listener): (() => void) => {
    this.listeners.add(l);
    return () => this.listeners.delete(l);
  };

  list = (): Command[] => this.snapshot;

  get(id: string): Command | undefined {
    return this.snapshot.find((c) => c.id === id);
  }
}

export const registry = new Registry();

export function useCommands(): Command[] {
  return useSyncExternalStore(registry.subscribe, registry.list, registry.list);
}

/** Register commands for the lifetime of the calling component. */
export function useRegisterCommands(source: string, cmds: Command[], deps: unknown[] = []): void {
  useEffect(() => registry.register(source, cmds), [source, ...deps]);
}

// ---- search --------------------------------------------------------------------------------------

/** Subsequence fuzzy score (higher is better; null = no match). Word starts and runs score more. */
export function fuzzyScore(query: string, text: string): number | null {
  const q = query.toLowerCase().trim();
  if (!q) return 0;
  const t = text.toLowerCase();
  const direct = t.indexOf(q);
  if (direct >= 0) return 1000 - direct * 2 - (t.length - q.length) * 0.1 + (direct === 0 || /\W/.test(t[direct - 1] ?? " ") ? 200 : 0);
  let score = 0;
  let ti = 0;
  let run = 0;
  for (const ch of q) {
    if (ch === " ") continue;
    const at = t.indexOf(ch, ti);
    if (at < 0) return null;
    run = at === ti ? run + 1 : 0;
    score += 10 + run * 5 + (at === 0 || /\W/.test(t[at - 1]) ? 15 : 0) - (at - ti);
    ti = at + 1;
  }
  return score;
}

export function searchCommands(all: Command[], raw: string, recent: string[]): { section: CommandSection; items: Command[] }[] {
  let query = raw;
  let only: CommandSection | null = null;
  let commandsOnly = false;
  if (query.startsWith(">")) {
    commandsOnly = true;
    query = query.slice(1);
  } else if (query.startsWith("#")) {
    only = "Jobs";
    query = query.slice(1);
  } else if (query.startsWith("@")) {
    only = "Labs";
    query = query.slice(1);
  }
  let pool = all;
  if (only) pool = pool.filter((c) => c.section === only);
  if (commandsOnly) pool = pool.filter((c) => c.section === "Commands");
  const scored = pool
    .map((c) => ({ c, s: fuzzyScore(query, [c.label, c.detail ?? "", ...(c.keywords ?? [])].join(" ")) }))
    .filter((x): x is { c: Command; s: number } => x.s !== null);
  const groups = new Map<CommandSection, Command[]>();
  if (!query.trim() && !only && !commandsOnly) {
    const rec = recent.map((id) => all.find((c) => c.id === id)).filter((c): c is Command => !!c);
    if (rec.length) groups.set("Recent", rec);
  }
  const sorted = query.trim() ? scored.sort((a, b) => b.s - a.s) : scored;
  for (const { c } of sorted) {
    if (groups.get("Recent")?.includes(c)) continue;
    const g = groups.get(c.section) ?? [];
    g.push(c);
    groups.set(c.section, g);
  }
  return SECTION_ORDER.filter((s) => groups.has(s)).map((s) => ({ section: s, items: groups.get(s)!.slice(0, s === "Recent" ? 5 : 50) }));
}
