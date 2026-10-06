import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { AXIS, chartColor } from "./palette";
import { ChartFrame } from "./ChartFrame";

/** Simple categorical bars (counts, label balance, latency histograms). */
export function BarSeriesChart({
  title,
  caption,
  data,
  unit = "",
  height = 200,
  colorIndex = 0,
  horizontal,
  "data-testid": testId,
}: {
  title: string;
  caption?: string;
  data: { label: string; value: number | null; color?: string }[];
  unit?: string;
  height?: number;
  colorIndex?: number;
  horizontal?: boolean;
  "data-testid"?: string;
}) {
  const aria = `${title}: ` + data.map((d) => `${d.label} ${d.value ?? "no value"}${unit}`).join(", ");
  const reduce = typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  return (
    <ChartFrame
      title={title}
      caption={caption}
      ariaLabel={aria}
      height={height}
      table={{ columns: ["label", `value${unit ? ` (${unit})` : ""}`], rows: data.map((d) => [d.label, d.value]) }}
      empty={data.length === 0}
      data-testid={testId}
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout={horizontal ? "vertical" : "horizontal"} margin={{ top: 6, right: 8, bottom: 0, left: horizontal ? 8 : -12 }}>
          <CartesianGrid vertical={!!horizontal} horizontal={!horizontal} stroke={AXIS.grid} strokeDasharray="2 3" />
          {horizontal ? (
            <>
              <XAxis type="number" tick={{ fill: AXIS.tick, fontSize: 12 }} stroke={AXIS.line} />
              <YAxis type="category" dataKey="label" tick={{ fill: AXIS.tick, fontSize: 12 }} stroke={AXIS.line} width={120} />
            </>
          ) : (
            <>
              <XAxis dataKey="label" tick={{ fill: AXIS.tick, fontSize: 12 }} stroke={AXIS.line} />
              <YAxis tick={{ fill: AXIS.tick, fontSize: 12 }} stroke={AXIS.line} width={44} />
            </>
          )}
          <Tooltip
            formatter={(v) => [`${v ?? "—"}${unit}`, title]}
            contentStyle={{ background: "var(--surface-2)", border: "1px solid var(--border-strong)", borderRadius: 6, color: "var(--text)" }}
            cursor={{ fill: "var(--surface-3)" }}
          />
          <Bar dataKey="value" isAnimationActive={!reduce} radius={[3, 3, 0, 0]}>
            {data.map((d, i) => (
              <Cell key={i} fill={d.color ?? chartColor(colorIndex)} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}
