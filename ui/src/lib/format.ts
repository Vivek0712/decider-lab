// Number, time and size formatting (DESIGN.md 2.3 "Numbers" and "Times").
import type { CI } from "@/api/types";

export const DASH = "—";

const fixed = (x: number, d: number) => x.toFixed(d).replace(/^-(0\.?0*)$/, "$1");

/** Intelligence: 1 decimal. */
export const fmtIntelligence = (x: number | null | undefined) => (x == null ? DASH : fixed(x, 1));
/** Accuracy: 1 decimal with %. */
export const fmtPct = (x: number | null | undefined, digits = 1) => (x == null ? DASH : `${fixed(x, digits)}%`);
/** NLL / ECE / Brier: 3 decimals. */
export const fmt3 = (x: number | null | undefined) => (x == null ? DASH : fixed(x, 3));
/** Signed difference with a real minus sign: +3.2, −0.018. */
export function fmtSigned(x: number | null | undefined, digits = 1): string {
  if (x == null) return DASH;
  const s = fixed(Math.abs(x), digits);
  return x > 0 ? `+${s}` : x < 0 && Number(s) !== 0 ? `−${s}` : s;
}
/** "57.9–64.8" for a CI. */
export function fmtCI(ci: CI | null | undefined, digits = 1): string {
  if (!ci || ci[0] == null || ci[1] == null) return DASH;
  return `${fixed(ci[0], digits)}–${fixed(ci[1], digits)}`;
}
/** Latency: seconds with 2 decimals, ms below 1 s. */
export function fmtLatency(s: number | null | undefined): string {
  if (s == null) return DASH;
  if (s < 0.001) return "< 1 ms";
  if (s < 1) return `${Math.round(s * 1000)} ms`;
  return `${s.toFixed(2)} s`;
}
/** Money: rates "$0.612/h", totals "$1.60". */
export const fmtRate = (x: number | null | undefined) => (x == null ? DASH : `$${x.toFixed(3)}/h`);
export const fmtUsd = (x: number | null | undefined) => (x == null ? DASH : `$${x.toFixed(2)}`);
/** Sizes: "12.4 GB", "412 MB". */
export function fmtBytes(n: number | null | undefined): string {
  if (n == null) return DASH;
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1000 && i < units.length - 1) {
    v /= 1000;
    i++;
  }
  return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}
export const fmtInt = (n: number | null | undefined) => (n == null ? DASH : n.toLocaleString("en-US"));

/** Durations: "41s", "12m 08s", "3h 04m". */
export function fmtDuration(s: number | null | undefined): string {
  if (s == null) return DASH;
  const t = Math.max(0, Math.round(s));
  if (t < 60) return `${t}s`;
  const m = Math.floor(t / 60);
  if (m < 60) return `${m}m ${String(t % 60).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}

/** Relative time for lists: "just now", "4 min ago", "2 h ago", "3 d ago". */
export function fmtRelative(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return DASH;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return DASH;
  const s = Math.round((now - t) / 1000);
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}
/** Absolute local time for tooltips and provenance. */
export function fmtAbsolute(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? DASH : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium" });
}
export function fmtClock(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString(undefined, { hour12: false });
}

/** Truncate the middle of a long path: "/Users/me/…/eval-q3". */
export function truncateMiddle(s: string, max = 40): string {
  if (s.length <= max) return s;
  const keep = max - 1;
  return `${s.slice(0, Math.ceil(keep / 2))}…${s.slice(s.length - Math.floor(keep / 2))}`;
}
