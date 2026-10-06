import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { ThemePref } from "@/api/types";

// Theme preference lives in localStorage ("dl-theme") so it applies before the API answers
// (public/theme-init.js reads the same key before first paint). Settings > Appearance may also
// save it server-side with PUT /api/settings; call setPref() so the shell follows.

const KEY = "dl-theme";
const ORDER: ThemePref[] = ["system", "dark", "light"];

type ThemeCtx = {
  pref: ThemePref;
  resolved: "dark" | "light";
  setPref: (p: ThemePref) => void;
  /** system -> dark -> light -> system */
  cycle: () => void;
  next: ThemePref;
};

const Ctx = createContext<ThemeCtx | null>(null);

function readPref(): ThemePref {
  try {
    const v = localStorage.getItem(KEY);
    return v === "dark" || v === "light" || v === "system" ? v : "system";
  } catch {
    return "system";
  }
}

const systemDark = () => !window.matchMedia("(prefers-color-scheme: light)").matches; // dark when no preference

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [pref, setPrefState] = useState<ThemePref>(readPref);
  const [sysDark, setSysDark] = useState(systemDark);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: light)");
    const on = () => setSysDark(systemDark());
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);

  const resolved: "dark" | "light" = pref === "system" ? (sysDark ? "dark" : "light") : pref;

  useEffect(() => {
    const root = document.documentElement;
    // switch without animating every color (and without mid-transition contrast)
    root.setAttribute("data-theme-switching", "");
    root.setAttribute("data-theme", resolved);
    root.setAttribute("data-theme-pref", pref);
    void root.offsetHeight;
    const t = window.setTimeout(() => root.removeAttribute("data-theme-switching"), 50);
    return () => window.clearTimeout(t);
  }, [resolved, pref]);

  const setPref = useCallback((p: ThemePref) => {
    setPrefState(p);
    try {
      localStorage.setItem(KEY, p);
    } catch {
      /* storage blocked */
    }
  }, []);

  const value = useMemo<ThemeCtx>(() => {
    const next = ORDER[(ORDER.indexOf(pref) + 1) % ORDER.length];
    return { pref, resolved, setPref, cycle: () => setPref(next), next };
  }, [pref, resolved, setPref]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useTheme(): ThemeCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useTheme outside ThemeProvider");
  return v;
}
