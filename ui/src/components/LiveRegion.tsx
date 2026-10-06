import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

// One polite live region for the app (DESIGN.md 9): "Copied", status changes, summaries.
const Ctx = createContext<(msg: string) => void>(() => {});

export function LiveRegionProvider({ children }: { children: ReactNode }) {
  const [msg, setMsg] = useState("");
  const announce = useCallback((m: string) => {
    setMsg("");
    window.setTimeout(() => setMsg(m), 30);
  }, []);
  return (
    <Ctx.Provider value={announce}>
      {children}
      <div aria-live="polite" role="status" className="sr-only" data-testid="live-region">
        {msg}
      </div>
    </Ctx.Provider>
  );
}

export const useAnnounce = () => useContext(Ctx);
