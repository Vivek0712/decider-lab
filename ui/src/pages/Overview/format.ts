// Formatting used by the Overview (re-exports the shared helpers, plus a size helper for GB values).
export { DASH, fmtAbsolute, fmtCI, fmtInt, fmtIntelligence, fmtPct, fmtRate, fmtRelative, fmtUsd } from "@/lib/format";

/** "12.6 GB" from a GB float, or "—". */
export const fmtGbOrDash = (gb: number | null | undefined) => (gb == null ? "—" : `${gb.toFixed(gb >= 100 ? 0 : 2)} GB`);
