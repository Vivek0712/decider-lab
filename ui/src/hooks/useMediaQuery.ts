import { useEffect, useState } from "react";

export function useMediaQuery(query: string): boolean {
  const [match, setMatch] = useState(() => typeof window !== "undefined" && window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setMatch(mq.matches);
    on();
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [query]);
  return match;
}

/** < 1024 px: the sidebar becomes a drawer. */
export const useIsCompact = () => useMediaQuery("(max-width: 1023.98px)");
/** < 640 px: phone layout. */
export const useIsPhone = () => useMediaQuery("(max-width: 639.98px)");
