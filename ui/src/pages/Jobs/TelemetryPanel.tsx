import { useMemo, useState } from "react";
import { Activity } from "lucide-react";
import type { Telemetry } from "@/api/types";
import { TimeSeriesChart, type Series } from "@/charts";
import { Badge } from "@/components/Badge";
import { EmptyState } from "@/components/EmptyState";
import { ErrorState } from "@/components/ErrorState";
import { SegmentedControl } from "@/components/Field";

const SOURCE: Record<Telemetry["source"], string> = {
  "local-nvidia-smi": "local nvidia-smi",
  "remote-ssh": "remote via ssh",
  simulated: "simulated (fake cloud)",
  none: "no GPU telemetry",
};

type Row = { ts: string } & Record<string, number | string | null>;

/** Rows/s and errors (always, from predictions files) and GPU charts when a GPU is sampled (DESIGN.md 7.6). */
export function TelemetryPanel({ data, loading, error, onRetry, only }: { data?: Telemetry; loading?: boolean; error?: unknown; onRetry?: () => void; only?: "rows" }) {
  const [win, setWin] = useState<"15m" | "all">("15m");
  const samples = useMemo(() => {
    const all = data?.samples ?? [];
    if (win === "all" || !all.length) return all;
    const last = Date.parse(all[all.length - 1].ts);
    return all.filter((s) => last - Date.parse(s.ts) <= 15 * 60_000);
  }, [data, win]);
  const gpus = data?.gpus ?? [];
  const rows = (pick: (s: Telemetry["samples"][number], gpu: number) => number | null, perGpu: boolean): Row[] =>
    samples.map((s) => {
      const r: Row = { ts: s.ts };
      if (perGpu) for (const g of gpus) r[`g${g.index}`] = pick(s, g.index);
      else r.v = pick(s, 0);
      return r;
    });
  const gpuSeries: Series[] = gpus.map((g, i) => ({ key: `g${g.index}`, label: `GPU ${g.index}${gpus.length === 1 ? ` (${g.name})` : ""}`, colorIndex: i }));
  const gv = (k: "util_pct" | "mem_used_gb" | "temp_c" | "power_w") => (s: Telemetry["samples"][number], idx: number) =>
    (s.gpus.find((x) => x.index === idx)?.[k] as number | null | undefined) ?? null;
  const memTotal = gpus[0]?.memory_total_gb ?? null;
  const powerLimit = gpus[0]?.power_limit_w ?? null;

  if (error) return <ErrorState error={error} onRetry={onRetry} />;
  const rowsCharts = (
    <>
      <TimeSeriesChart
        title="Rows per second"
        caption="30 s moving rate"
        data={rows((s) => s.rows_per_s, false)}
        series={[{ key: "v", label: "rows/s", colorIndex: 0 }]}
        loading={loading}
        data-testid="telemetry-chart-rows"
      />
      <TimeSeriesChart
        title="Errors (cumulative)"
        caption="rows that failed so far"
        data={rows((s) => s.errors_total, false)}
        series={[{ key: "v", label: "errors", color: "var(--danger)" }]}
        loading={loading}
        data-testid="telemetry-chart-errors"
      />
    </>
  );
  if (only === "rows") return <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">{rowsCharts}</div>;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={data?.source === "none" ? "neutral" : data?.source === "simulated" ? "warning" : "accent"} icon={Activity} data-testid="telemetry-source">
            {data ? SOURCE[data.source] : "…"}
          </Badge>
          {data && <span className="text-small text-muted">every {data.interval_s} s · {data.samples.length} samples</span>}
        </div>
        <SegmentedControl
          label="Time window"
          value={win}
          onChange={setWin}
          options={[
            { value: "15m", label: "Last 15 min", testId: "telemetry-window-15m" },
            { value: "all", label: "All", testId: "telemetry-window-all" },
          ]}
        />
      </div>
      {data && data.source === "none" && (
        <EmptyState
          className="py-4"
          title="No GPU telemetry"
          body={`${data.reason ?? "This job runs on a machine without an NVIDIA GPU (Apple MPS and CPU are not sampled)."} Rows per second and errors come from the predictions files.`}
          data-testid="telemetry-empty"
        />
      )}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 md:grid-cols-3">
        {gpus.length > 0 && (
          <>
            <TimeSeriesChart title="GPU utilization" data={rows(gv("util_pct"), true)} series={gpuSeries} unit="%" domain={[0, 100]} data-testid="telemetry-chart-util" />
            <TimeSeriesChart
              title="GPU memory used"
              data={rows(gv("mem_used_gb"), true)}
              series={gpuSeries}
              unit=" GB"
              domain={[0, memTotal ? Math.ceil(memTotal) : "auto"]}
              reference={memTotal ? { y: memTotal, label: `total ${memTotal} GB` } : undefined}
              data-testid="telemetry-chart-mem"
            />
            <TimeSeriesChart
              title="GPU temperature"
              data={rows(gv("temp_c"), true)}
              series={gpuSeries}
              unit=" °C"
              domain={[0, 100]}
              reference={{ y: 85, label: "85 °C" }}
              data-testid="telemetry-chart-temp"
            />
            <TimeSeriesChart
              title="GPU power"
              data={rows(gv("power_w"), true)}
              series={gpuSeries}
              unit=" W"
              domain={[0, powerLimit ? Math.ceil(powerLimit) : "auto"]}
              reference={powerLimit ? { y: powerLimit, label: `limit ${powerLimit} W` } : undefined}
              data-testid="telemetry-chart-power"
            />
          </>
        )}
        {rowsCharts}
      </div>
    </div>
  );
}
