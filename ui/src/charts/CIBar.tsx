import type { CI } from "@/api/types";

/**
 * Inline CI bar for tables (DESIGN.md 7.1): whisker from lo to hi with caps, point estimate on
 * top, a tick at 0 when 0 is in the domain. Not focusable: the cell carries the numbers as text.
 */
export function CIBar({
  value,
  ci,
  domain,
  color = "var(--chart-1)",
  width = 120,
}: {
  value: number | null;
  ci: CI | null;
  domain: [number, number];
  color?: string;
  width?: number;
}) {
  const h = 16;
  const [d0, d1] = domain;
  const x = (v: number) => 4 + ((Math.max(d0, Math.min(d1, v)) - d0) / (d1 - d0 || 1)) * (width - 8);
  const hasCI = ci && ci[0] != null && ci[1] != null;
  return (
    <svg width={width} height={h} viewBox={`0 0 ${width} ${h}`} aria-hidden focusable="false" className="inline-block align-middle">
      {d0 < 0 && d1 > 0 && <line x1={x(0)} x2={x(0)} y1={1} y2={h - 1} stroke="var(--text-muted)" strokeWidth={1} />}
      {hasCI && (
        <>
          <line x1={x(ci![0]!)} x2={x(ci![1]!)} y1={h / 2} y2={h / 2} stroke={color} strokeWidth={1.5} />
          <line x1={x(ci![0]!)} x2={x(ci![0]!)} y1={h / 2 - 4} y2={h / 2 + 4} stroke={color} strokeWidth={1.5} />
          <line x1={x(ci![1]!)} x2={x(ci![1]!)} y1={h / 2 - 4} y2={h / 2 + 4} stroke={color} strokeWidth={1.5} />
        </>
      )}
      {value != null && <circle cx={x(value)} cy={h / 2} r={3.5} fill={color} />}
    </svg>
  );
}

/** Shared CIBar domain for a table: [min(lo) − 5, max(hi) + 5] clipped to [−100, 100]. */
export function ciDomain(items: { ci: CI | null; value: number | null }[]): [number, number] {
  const lo = items.flatMap((i) => [i.ci?.[0], i.value]).filter((v): v is number => v != null);
  const hi = items.flatMap((i) => [i.ci?.[1], i.value]).filter((v): v is number => v != null);
  if (!lo.length) return [0, 100];
  return [Math.max(-100, Math.min(...lo) - 5), Math.min(100, Math.max(...hi) + 5)];
}
