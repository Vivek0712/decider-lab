import { useEffect, useRef } from "react";
import { isMac, isTypingTarget } from "@/lib/platform";

export type Hotkey = {
  /** "mod+k", "mod+\\", "?", "t", "g r" (a chord: second key within 1 s), "escape" */
  keys: string;
  handler: (e: KeyboardEvent) => void;
  /** Also fire while typing in an input (Mod combos and Esc default to true). */
  allowInInput?: boolean;
};

function matches(combo: string, e: KeyboardEvent): boolean {
  const parts = combo.toLowerCase().split("+");
  const key = parts.pop()!;
  const mod = parts.includes("mod");
  const shift = parts.includes("shift");
  const alt = parts.includes("alt");
  const modDown = isMac ? e.metaKey : e.ctrlKey;
  if (mod !== modDown || alt !== e.altKey) return false;
  if (shift && !e.shiftKey) return false;
  if (!mod && !isMac && e.ctrlKey) return false;
  if (!mod && isMac && e.metaKey) return false;
  const k = e.key.toLowerCase();
  return k === key || (key === "escape" && k === "esc");
}

/** Global keyboard shortcuts with `g x` chords (DESIGN.md 8.2). */
export function useHotkeys(hotkeys: Hotkey[]): void {
  const ref = useRef(hotkeys);
  ref.current = hotkeys;
  useEffect(() => {
    let pending: { first: string; at: number } | null = null;
    const onKey = (e: KeyboardEvent) => {
      if (e.defaultPrevented || e.isComposing) return;
      const typing = isTypingTarget(e.target);
      const k = e.key.toLowerCase();
      if (pending && Date.now() - pending.at < 1000 && !typing) {
        const chord = `${pending.first} ${k}`;
        pending = null;
        const hit = ref.current.find((h) => h.keys === chord);
        if (hit) {
          e.preventDefault();
          hit.handler(e);
          return;
        }
      }
      for (const h of ref.current) {
        if (h.keys.includes(" ")) continue;
        const isCombo = h.keys.includes("mod+") || h.keys === "escape";
        if (typing && !(h.allowInInput ?? isCombo)) continue;
        if (matches(h.keys, e)) {
          e.preventDefault();
          h.handler(e);
          return;
        }
      }
      if (!typing && !e.metaKey && !e.ctrlKey && !e.altKey && ref.current.some((h) => h.keys.startsWith(`${k} `))) {
        pending = { first: k, at: Date.now() };
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}
