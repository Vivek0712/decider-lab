import { cn } from "@/lib/cn";

/** 80×20 sparkline with the last value as text beside it (DESIGN.md 7.6). Gaps for nulls. */
export function Sparkline({
  values,
  width = 80,
  height = 20,
  color = "var(--accent)",
  label,
  format = (v: number) => String(v),
  className,
}: {
  values: (number | null)[];
  width?: number;
  height?: number;
  color?: string;
  label: string;
  format?: (v: number) => string;
  className?: string;
}) {
  const nums = values.filter((v): v is number => v != null);
  const max = Math.max(1e-9, ...nums);
  const min = Math.min(0, ...nums);
  const step = values.length > 1 ? width / (values.length - 1) : width;
  let d = "";
  let pen = false;
  values.forEach((v, i) => {
    if (v == null) {
      pen = false;
      return;
    }
    const x = i * step;
    const y = height - 1 - ((v - min) / (max - min || 1)) * (height - 2);
    d += `${pen ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`;
    pen = true;
  });
  const last = [...values].reverse().find((v) => v != null);
  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${label}: last ${last != null ? format(last) : "no value"}`}>
        <path d={d} fill="none" stroke={color} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
      </svg>
      <span className="tnum text-small text-muted">{last != null ? format(last) : "—"}</span>
    </span>
  );
}
