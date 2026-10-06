import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fmtClock } from "@/lib/format";
import { AXIS, chartColor } from "./palette";
import { ChartFrame } from "./ChartFrame";

/** Axis ticks that fit the 44 px axis: 1500 -> 1.5k, 2000000 -> 2M. */
const compact = (v: number) =>
  Math.abs(v) >= 1e6 ? `${+(v / 1e6).toFixed(1)}M` : Math.abs(v) >= 1e3 ? `${+(v / 1e3).toFixed(1)}k` : `${+v.toFixed(2)}`;

export type Series = { key: string; label: string; colorIndex?: number; color?: string; dashed?: boolean };

/**
 * Time series (telemetry: GPU util/mem/temp/power, rows/s, errors). `data` rows have `ts` (ISO) and
 * one numeric field per series; null makes a gap. Fixed y domains per DESIGN.md 7.6 via `domain`;
 * `reference` draws a dashed limit line (e.g. memory total, 85 °C).
 */
export function TimeSeriesChart({
  title,
  caption,
  data,
  series,
  unit = "",
  domain = [0, "auto"],
  reference,
  height = 140,
  syncId = "telemetry",
  loading,
  "data-testid": testId,
}: {
  title: string;
  caption?: string;
  data: Array<{ ts: string } & Record<string, number | string | null>>;
  series: Series[];
  unit?: string;
  domain?: [number | "auto", number | "auto"];
  reference?: { y: number; label: string };
  height?: number;
  syncId?: string;
  loading?: boolean;
  "data-testid"?: string;
}) {
  const last = data[data.length - 1];
  const aria =
    `${title}: ` +
    (last ? series.map((s) => `${s.label} ${last[s.key] ?? "no value"}${unit}`).join(", ") + ` at ${fmtClock(last.ts)}` : "no samples yet");
  const table = {
    columns: ["time", ...series.map((s) => `${s.label}${unit ? ` (${unit})` : ""}`)],
    rows: data.map((d) => [fmtClock(d.ts), ...series.map((s) => (d[s.key] as number | null) ?? null)]),
  };
  const reduce = typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  return (
    <ChartFrame title={title} caption={caption} ariaLabel={aria} table={table} height={height} loading={loading} empty={!loading && data.length === 0} data-testid={testId}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} syncId={syncId} margin={{ top: 6, right: 8, bottom: 0, left: -12 }}>
          <CartesianGrid vertical={false} stroke={AXIS.grid} strokeDasharray="2 3" />
          <XAxis dataKey="ts" tickFormatter={fmtClock} tick={{ fill: AXIS.tick, fontSize: 12 }} stroke={AXIS.line} minTickGap={40} />
          <YAxis domain={domain} tick={{ fill: AXIS.tick, fontSize: 12 }} stroke={AXIS.line} width={44} allowDecimals tickFormatter={compact} />
          <Tooltip
            labelFormatter={(v) => fmtClock(String(v))}
            formatter={(v, name) => [`${v ?? "—"}${unit}`, name]}
            contentStyle={{ background: "var(--surface-2)", border: "1px solid var(--border-strong)", borderRadius: 6, color: "var(--text)" }}
            labelStyle={{ color: "var(--text-muted)" }}
          />
          {reference && <ReferenceLine y={reference.y} stroke={AXIS.ref} strokeDasharray="4 3" label={{ value: reference.label, fill: AXIS.tick, fontSize: 12, position: "insideTopRight" }} />}
          {series.map((s) => (
            <Line
              key={s.key}
              type="monotone"
              dataKey={s.key}
              name={s.label}
              stroke={s.color ?? chartColor(s.colorIndex ?? 0)}
              strokeWidth={1.75}
              strokeDasharray={s.dashed ? "4 2" : undefined}
              dot={false}
              connectNulls={false}
              isAnimationActive={!reduce}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </ChartFrame>
  );
}
