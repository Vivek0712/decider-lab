// Results charts (DESIGN.md 7.2-7.5, 7.7): hand-written SVG on d3-scale, every one wrapped in
// ChartFrame (title, caption, role="img" + generated aria-label, "View as table").
// Area-local for now; the integrator may move them to src/charts/.
import { useEffect, useRef, useState, type ReactNode } from "react";
import { scaleLinear, scaleLog } from "d3-scale";
import type { CI, Diff, ReliabilityBin } from "@/api/types";
import { AXIS, ChartFrame } from "@/charts";
import { fmt3, fmtCI, fmtIntelligence, fmtLatency, fmtSigned } from "@/lib/format";
import { cn } from "@/lib/cn";

// ---- utilities -----------------------------------------------------------------------------------

/** Width of a container, tracked with ResizeObserver (charts are responsive to their card). */
export function useWidth<T extends HTMLElement>(fallback = 640): [React.RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const set = () => setW(Math.max(200, Math.floor(el.getBoundingClientRect().width)));
    set();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(set);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

// ticks inside [lo, hi] only: a "nice" domain extends past the data, and the clamped scale would
// pin those extra ticks to the chart edges on top of their neighbours
const ticks = (lo: number, hi: number, n = 6) =>
  scaleLinear().domain([lo, hi]).ticks(n).filter((t) => t >= lo - 1e-9 && t <= hi + 1e-9);
const hasCI = (ci: CI | null | undefined): ci is [number, number] => !!ci && ci[0] != null && ci[1] != null;

function Whisker({ x0, x1, y, color, cap = 8 }: { x0: number; x1: number; y: number; color: string; cap?: number }) {
  return (
    <g stroke={color} strokeWidth={1.5}>
      <line x1={x0} x2={x1} y1={y} y2={y} />
      <line x1={x0} x2={x0} y1={y - cap / 2} y2={y + cap / 2} />
      <line x1={x1} x2={x1} y1={y - cap / 2} y2={y + cap / 2} />
    </g>
  );
}

/** Small floating tooltip positioned inside the chart box (hover and keyboard focus). */
function ChartTip({ x, y, width, children }: { x: number; y: number; width: number; children: ReactNode }) {
  const left = Math.min(Math.max(8, x + 12), Math.max(8, width - 240));
  return (
    <div
      aria-hidden
      className="pointer-events-none absolute z-popover w-max max-w-[240px] rounded-sm border border-border bg-surface-2 px-2 py-1 text-small text-text shadow-elev-2"
      style={{ left, top: Math.max(0, y - 8) }}
    >
      {children}
    </div>
  );
}

// ---- Intelligence bars with CI whiskers (leaderboard) ---------------------------------------------

export type IntelBar = { key: string; label: string; value: number | null; ci: CI | null; color: string; note?: string; baseline?: boolean };

/** Horizontal bars from 0 to the point estimate, with the 95% CI whisker on top. */
export function IntelligenceBars({ items, domain, title, caption }: { items: IntelBar[]; domain: [number, number]; title: string; caption: string }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const rowH = 30;
  const left = Math.min(160, Math.max(90, width * 0.22));
  const right = 16;
  const height = Math.max(1, items.length) * rowH + 30;
  const lo = Math.min(0, domain[0]);
  const hi = Math.max(0, domain[1]);
  const x = scaleLinear().domain([lo, hi]).range([left, width - right]).clamp(true);
  const aria = items.map((i) => `${i.label} ${fmtIntelligence(i.value)}${hasCI(i.ci) ? `, 95% CI ${i.ci[0]} to ${i.ci[1]}` : ""}`).join("; ");
  return (
    <ChartFrame
      grow
      title={title}
      caption={caption}
      ariaLabel={`Intelligence (local proxy) with 95% CI: ${aria || "no scored models"}`}
      table={{ columns: ["model", "Intelligence (local proxy)", "95% CI"], rows: items.map((i) => [i.label, i.value, fmtCI(i.ci)]) }}
      height={height}
      empty={items.length === 0}
      data-testid="leaderboard-chart"
    >
      <div ref={ref} className="relative h-full w-full">
        <svg width={width} height={height} className="block">
          {ticks(lo, hi).map((t) => (
            <g key={t}>
              <line x1={x(t)} x2={x(t)} y1={4} y2={height - 24} stroke={AXIS.grid} strokeDasharray="2 3" />
              <text x={x(t)} y={height - 8} textAnchor="middle" fontSize={11} fill={AXIS.tick}>
                {t}
              </text>
            </g>
          ))}
          <line x1={x(0)} x2={x(0)} y1={4} y2={height - 24} stroke={AXIS.ref} />
          {items.map((it, i) => {
            const y = 6 + i * rowH + rowH / 2;
            return (
              <g
                key={it.key}
                onMouseEnter={() => setHover(i)}
                onMouseLeave={() => setHover(null)}
                onFocus={() => setHover(i)}
                onBlur={() => setHover(null)}
                className="outline-none focus-visible:[&>rect.hit]:stroke-[var(--focus)]"
              >
                <rect className="hit" x={0} y={y - rowH / 2} width={width} height={rowH} fill="transparent" strokeWidth={2} />
                <text x={left - 8} y={y + 4} textAnchor="end" fontSize={12} fill="var(--text)">
                  {it.label.length > 18 ? `${it.label.slice(0, 17)}…` : it.label}
                </text>
                {it.value != null && (
                  <rect
                    x={Math.min(x(0), x(it.value))}
                    y={y - 7}
                    width={Math.max(1, Math.abs(x(it.value) - x(0)))}
                    height={14}
                    rx={2}
                    fill={it.color}
                    opacity={0.35}
                  />
                )}
                {hasCI(it.ci) && <Whisker x0={x(it.ci[0])} x1={x(it.ci[1])} y={y} color={it.color} />}
                {it.value != null && <circle cx={x(it.value)} cy={y} r={3.5} fill={it.color} />}
                {it.value == null && (
                  <text x={x(0) + 6} y={y + 4} fontSize={12} fill="var(--text-muted)">
                    {it.note ?? "—"}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
        {hover != null && items[hover] && (
          <ChartTip x={x(items[hover].value ?? 0)} y={6 + hover * rowH} width={width}>
            <div className="font-semibold">{items[hover].label}</div>
            <div className="tnum">
              Intelligence {fmtIntelligence(items[hover].value)} · 95% CI {fmtCI(items[hover].ci)}
            </div>
            {!hasCI(items[hover].ci) && items[hover].value != null && <div className="text-muted">no CI (bootstrap needs scored rows)</div>}
          </ChartTip>
        )}
      </div>
    </ChartFrame>
  );
}

// ---- Forest plot (paired vs baseline) --------------------------------------------------------------

export type ForestItem = { model: string; diff: Diff; n_paired: number };
export type ForestMetric = "intelligence" | "accuracy" | "nll";

export const verdictOf = (d: Diff, lowerIsBetter: boolean): "better" | "worse" | "unclear" => {
  if (d.verdict) return d.verdict;
  if (!hasCI(d.ci95)) return "unclear";
  const [lo, hi] = d.ci95;
  if (lowerIsBetter) return hi < 0 ? "better" : lo > 0 ? "worse" : "unclear";
  return lo > 0 ? "better" : hi < 0 ? "worse" : "unclear";
};
const VERDICT_COLOR = { better: "var(--success)", worse: "var(--danger)", unclear: "var(--text-subtle)" } as const;

export function verdictSentence(v: "better" | "worse" | "unclear", baseline: string, lowerIsBetter = false): string {
  if (v === "unclear") return "No clear difference: the 95% CI includes 0.";
  return `${v === "better" ? "Better" : "Worse"} than ${baseline}${lowerIsBetter ? " (lower is better)" : ""}: the 95% CI excludes 0.`;
}

export function ForestPlot({ items, metric, baseline }: { items: ForestItem[]; metric: ForestMetric; baseline: string }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const lower = metric === "nll";
  const digits = metric === "nll" ? 3 : 1;
  const unit = metric === "intelligence" ? "points" : metric === "accuracy" ? "percentage points" : "nats";
  const rowH = 32;
  const narrow = width < 560;
  const left = narrow ? 84 : 130;
  const textW = narrow ? 0 : 230;
  const top = 22;
  const height = top + Math.max(1, items.length) * rowH + 26;
  const ext = Math.max(
    metric === "nll" ? 0.05 : 5,
    ...items.flatMap((i) => [i.diff.ci95?.[0], i.diff.ci95?.[1], i.diff.diff]).filter((v): v is number => v != null).map((v) => Math.abs(v) * 1.1),
  );
  const x = scaleLinear().domain([-ext, ext]).range([left, width - textW - 12]).nice();
  const tk = x.ticks(narrow ? 4 : 6);
  const aria = items
    .map((i) => `${i.model} ${fmtSigned(i.diff.diff, digits)} (95% CI ${fmtSigned(i.diff.ci95?.[0], digits)} to ${fmtSigned(i.diff.ci95?.[1], digits)}), ${verdictOf(i.diff, lower)}`)
    .join("; ");
  return (
    <ChartFrame
      grow
      title={`Δ ${metric === "intelligence" ? "Intelligence (local proxy)" : metric === "accuracy" ? "accuracy" : "NLL"} vs ${baseline}`}
      caption={`Paired on the same rows, 95% bootstrap CI (2,000 resamples). Green: CI entirely ${lower ? "below" : "above"} 0 (better); red: worse; grey: the CI includes 0.`}
      ariaLabel={`Paired difference against ${baseline} in ${unit}: ${aria || "no models"}`}
      table={{
        columns: ["model", `Δ ${metric}`, "95% CI", "rows paired", "verdict"],
        rows: items.map((i) => [i.model, i.diff.diff, `${fmtSigned(i.diff.ci95?.[0], digits)} to ${fmtSigned(i.diff.ci95?.[1], digits)}`, i.n_paired, verdictOf(i.diff, lower)]),
      }}
      height={height}
      empty={items.length === 0}
      data-testid="forest-plot"
    >
      <div ref={ref} className="relative h-full w-full">
        <svg width={width} height={height} className="block">
          {tk.map((t) => (
            <g key={t}>
              <line x1={x(t)} x2={x(t)} y1={top} y2={height - 24} stroke={AXIS.grid} strokeDasharray="2 3" />
              <text x={x(t)} y={height - 8} textAnchor="middle" fontSize={11} fill={AXIS.tick}>
                {fmtSigned(t, metric === "nll" ? 2 : 0)}
              </text>
            </g>
          ))}
          <line x1={x(0)} x2={x(0)} y1={top - 6} y2={height - 24} stroke={AXIS.ref} />
          <text x={x(0)} y={12} textAnchor="middle" fontSize={11} fill="var(--text-muted)">
            baseline: {baseline}
          </text>
          {items.map((it, i) => {
            const y = top + i * rowH + rowH / 2;
            const v = verdictOf(it.diff, lower);
            const c = VERDICT_COLOR[v];
            const ci = it.diff.ci95;
            return (
              <g
                key={it.model}
                data-testid={`forest-row-${it.model}`}
                data-verdict={v}
                onMouseEnter={() => setHover(i)}
                onMouseLeave={() => setHover(null)}
                onFocus={() => setHover(i)}
                onBlur={() => setHover(null)}
                className="outline-none"
              >
                <rect x={0} y={y - rowH / 2} width={width} height={rowH} fill={hover === i ? "var(--surface-3)" : "transparent"} />
                <text x={left - 8} y={y + 4} textAnchor="end" fontSize={12} fill="var(--text)">
                  {it.model.length > (narrow ? 10 : 16) ? `${it.model.slice(0, narrow ? 9 : 15)}…` : it.model}
                </text>
                {hasCI(ci) ? (
                  <>
                    <Whisker x0={x(ci[0])} x1={x(ci[1])} y={y} color={c} />
                    {it.diff.diff != null && <circle cx={x(it.diff.diff)} cy={y} r={3.5} fill={c} />}
                  </>
                ) : (
                  it.diff.diff != null && <circle cx={x(it.diff.diff)} cy={y} r={3.5} fill="none" stroke={c} strokeWidth={1.5} />
                )}
                {!narrow && (
                  <text x={width - textW} y={y + 4} fontSize={12} fill="var(--text)" className="tnum">
                    {fmtSigned(it.diff.diff, digits)}{" "}
                    {hasCI(ci) ? `(${fmtSigned(ci[0], digits)} to ${fmtSigned(ci[1], digits)})` : `no CI: ${it.n_paired} paired rows`} · {it.n_paired} rows · {v}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
        {hover != null && items[hover] && (
          <ChartTip x={x(items[hover].diff.diff ?? 0)} y={top + hover * rowH} width={width}>
            <div className="font-semibold">{items[hover].model}</div>
            <div className="tnum">
              Δ {fmtSigned(items[hover].diff.diff, digits)} · 95% CI {fmtSigned(items[hover].diff.ci95?.[0], digits)} to {fmtSigned(items[hover].diff.ci95?.[1], digits)}
            </div>
            <div className="tnum text-muted">{items[hover].n_paired} rows paired</div>
            <div>{verdictSentence(verdictOf(items[hover].diff, lower), baseline, lower)}</div>
          </ChartTip>
        )}
      </div>
      {narrow && (
        <ul className="mt-2 flex flex-col gap-1 text-small">
          {items.map((it) => {
            const v = verdictOf(it.diff, lower);
            return (
              <li key={it.model} className="tnum">
                <span className="font-medium">{it.model}</span> {fmtSigned(it.diff.diff, digits)} ({fmtSigned(it.diff.ci95?.[0], digits)} to {fmtSigned(it.diff.ci95?.[1], digits)}) · {it.n_paired} rows · {v}
              </li>
            );
          })}
        </ul>
      )}
    </ChartFrame>
  );
}

// ---- Family heatmap --------------------------------------------------------------------------------

export type HeatCell = { family: string; model: string; n: number; intelligence: number | null; accuracy: number | null };

function heatColor(v: number | null, metric: "intelligence" | "accuracy"): string {
  if (v == null) return "var(--surface-2)";
  if (metric === "accuracy") {
    const p = Math.round(Math.max(0, Math.min(100, v)) * 0.55);
    return `color-mix(in srgb, var(--accent) ${p}%, var(--surface-2))`;
  }
  const p = Math.round(Math.min(100, Math.abs(v)) * 0.55);
  return `color-mix(in srgb, ${v >= 0 ? "var(--success)" : "var(--danger)"} ${p}%, var(--surface-2))`;
}

export function FamilyHeatmap({ families, models, cells, metric }: { families: string[]; models: string[]; cells: HeatCell[]; metric: "intelligence" | "accuracy" }) {
  const get = (f: string, m: string) => cells.find((c) => c.family === f && c.model === m);
  const [focus, setFocus] = useState<string | null>(null);
  const aria = `${metric === "intelligence" ? "Intelligence (local proxy)" : "accuracy"} by family and model: ` +
    families.map((f) => `${f}: ${models.map((m) => `${m} ${fmtIntelligence(get(f, m)?.[metric] ?? null)}`).join(", ")}`).join("; ");
  return (
    <ChartFrame
      grow
      title={metric === "intelligence" ? "Intelligence (local proxy) by family" : "Accuracy % by family"}
      caption="Per-family values have no CI and rest on fewer rows than the suite total (n in each cell). Use them to find where a model fails, not to rank models."
      ariaLabel={aria}
      table={{ columns: ["family", ...models.map((m) => `${m} (n)`)], rows: families.map((f) => [f, ...models.map((m) => {
        const c = get(f, m);
        return c ? `${fmtIntelligence(c[metric])} (${c.n})` : null;
      })]) }}
      height={Math.max(80, families.length * 36 + 64)}
      empty={families.length === 0}
      data-testid="family-heatmap"
    >
      <div className="scrollbar-thin h-full overflow-x-auto">
        <table className="border-separate border-spacing-1 text-small">
          <thead>
            <tr>
              <th scope="col" className="sticky left-0 z-[1] bg-surface-1 px-2 text-left text-caption font-semibold uppercase text-muted">
                family
              </th>
              {models.map((m) => (
                <th key={m} scope="col" className="px-1 text-center text-caption font-semibold text-muted">
                  {m}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {families.map((f) => (
              <tr key={f}>
                <th scope="row" className="sticky left-0 z-[1] max-w-[200px] truncate bg-surface-1 px-2 text-left font-medium" title={f}>
                  {f}
                </th>
                {models.map((m) => {
                  const c = get(f, m);
                  const v = c ? c[metric] : null;
                  const id = `${f}|${m}`;
                  return (
                    <td key={m} className="p-0">
                      <div
                        data-testid={`heat-cell-${m}-${f}`}
                        onMouseEnter={() => setFocus(id)}
                        onMouseLeave={() => setFocus(null)}
                        onFocus={() => setFocus(id)}
                        onBlur={() => setFocus(null)}
                        className={cn(
                          "relative flex h-8 min-w-[44px] items-center justify-center rounded-xs px-2 tnum text-text max-sm:min-w-[36px]",
                          c && c.n < 20 && "outline-dotted outline-1 outline-[var(--text-muted)]",
                        )}
                        style={{ background: heatColor(v, metric) }}
                      >
                        {v == null ? "—" : Math.round(v)}
                        {focus === id && c && (
                          <span aria-hidden className="pointer-events-none absolute bottom-full left-1/2 z-popover mb-1 w-max -translate-x-1/2 rounded-sm border border-border bg-surface-2 px-2 py-1 text-left text-small shadow-elev-2">
                            <span className="block font-semibold">{f} · {m}</span>
                            <span className="block">Intelligence {fmtIntelligence(c.intelligence)} · accuracy {fmtIntelligence(c.accuracy)}%</span>
                            <span className="block text-muted">{c.n} rows{c.n < 20 ? " · few rows: wide uncertainty" : ""}</span>
                          </span>
                        )}
                      </div>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
        <div className="mt-2 flex items-center gap-2 text-caption text-muted" aria-hidden>
          {metric === "intelligence" ? (
            <>
              <span>worse than chance</span>
              <span className="h-2 w-28 rounded-full" style={{ background: "linear-gradient(90deg, color-mix(in srgb, var(--danger) 55%, var(--surface-2)), var(--surface-2), color-mix(in srgb, var(--success) 55%, var(--surface-2)))" }} />
              <span>better · chance = 0</span>
            </>
          ) : (
            <>
              <span>0%</span>
              <span className="h-2 w-28 rounded-full" style={{ background: "linear-gradient(90deg, var(--surface-2), color-mix(in srgb, var(--accent) 55%, var(--surface-2)))" }} />
              <span>100%</span>
            </>
          )}
        </div>
      </div>
    </ChartFrame>
  );
}

// ---- Reliability diagram ---------------------------------------------------------------------------

export function ReliabilityDiagram({
  model,
  color,
  raw,
  cal,
  kind,
  testId,
}: {
  model: string;
  color: string;
  raw: { bins: ReliabilityBin[]; ece: number | null; n: number };
  cal: { bins: ReliabilityBin[]; ece: number | null; n: number } | null;
  kind: string;
  testId?: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>(300);
  const [hover, setHover] = useState<{ b: ReliabilityBin; cal: boolean } | null>(null);
  const size = Math.min(width, 320);
  const pad = 32;
  const plot = size - pad - 8;
  const histH = 32;
  const x = scaleLinear().domain([0, 1]).range([pad, pad + plot]);
  const y = scaleLinear().domain([0, 1]).range([8 + plot, 8]);
  const maxN = Math.max(1, ...raw.bins.map((b) => b.n));
  const r = (n: number) => 3 + 6 * Math.sqrt(n / maxN);
  const path = (bins: ReliabilityBin[]) => bins.map((b, i) => `${i ? "L" : "M"}${x(b.mean_conf)},${y(b.accuracy)}`).join("");
  const ece = (v: number | null) => (v == null ? "—" : fmt3(v));
  const caption = `ECE ${ece(raw.ece)}${cal ? ` → ${ece(cal.ece)} calibrated` : ""} · ${raw.n} rows`;
  const height = 8 + plot + 20 + histH + 8;
  return (
    <ChartFrame
      grow
      title={model}
      caption={caption}
      ariaLabel={`Reliability of ${model} (${kind}): ECE ${ece(raw.ece)}${cal ? `, calibrated ${ece(cal.ece)}` : ""}; ` +
        raw.bins.map((b) => `confidence ${b.mean_conf.toFixed(2)} accuracy ${b.accuracy.toFixed(2)} (${b.n} rows)`).join("; ")}
      table={{
        columns: ["bin", "rows", "mean confidence", "accuracy", "gap", ...(cal ? ["cal rows", "cal confidence", "cal accuracy"] : [])],
        rows: raw.bins.map((b) => {
          const c = cal?.bins.find((x) => x.lo === b.lo);
          return [`${b.lo.toFixed(1)}–${b.hi.toFixed(1)}`, b.n, b.mean_conf, b.accuracy, Number((b.accuracy - b.mean_conf).toFixed(3)),
            ...(cal ? [c?.n ?? null, c?.mean_conf ?? null, c?.accuracy ?? null] : [])];
        }),
      }}
      height={height}
      empty={raw.n === 0}
      data-testid={testId}
    >
      <div ref={ref} className="relative h-full w-full">
        <svg width={size} height={height} className="block">
          {kind === "noul" && (
            <g>
              <rect x={x(0.5)} y={8} width={x(0.8) - x(0.5)} height={plot} fill="var(--tint-warning)" />
              <text x={x(0.65)} y={20} textAnchor="middle" fontSize={10} fill="var(--text-muted)">abstention band</text>
            </g>
          )}
          {[0, 0.2, 0.4, 0.6, 0.8, 1].map((t) => (
            <g key={t}>
              <line x1={x(t)} x2={x(t)} y1={8} y2={8 + plot} stroke={AXIS.grid} strokeDasharray="2 3" />
              <line x1={pad} x2={pad + plot} y1={y(t)} y2={y(t)} stroke={AXIS.grid} strokeDasharray="2 3" />
              <text x={x(t)} y={8 + plot + 14} textAnchor="middle" fontSize={10} fill={AXIS.tick}>{t}</text>
              <text x={pad - 4} y={y(t) + 3} textAnchor="end" fontSize={10} fill={AXIS.tick}>{t}</text>
            </g>
          ))}
          <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(1)} stroke="var(--text-subtle)" strokeDasharray="4 3" />
          <text x={x(0.98)} y={y(0.98) + 14} textAnchor="end" fontSize={10} fill="var(--text-subtle)">perfectly calibrated</text>
          <path d={path(raw.bins)} fill="none" stroke={color} strokeWidth={1.5} />
          {raw.bins.map((b) => (
            <circle key={`r${b.lo}`} cx={x(b.mean_conf)} cy={y(b.accuracy)} r={r(b.n)} fill={color}
              onMouseEnter={() => setHover({ b, cal: false })} onMouseLeave={() => setHover(null)} onFocus={() => setHover({ b, cal: false })} onBlur={() => setHover(null)} />
          ))}
          {cal && (
            <>
              <path d={path(cal.bins)} fill="none" stroke={color} strokeWidth={1.5} strokeDasharray="4 3" />
              {cal.bins.map((b) => (
                <circle key={`c${b.lo}`} cx={x(b.mean_conf)} cy={y(b.accuracy)} r={r(b.n)} fill="var(--surface-1)" stroke={color} strokeWidth={1.5}
                  onMouseEnter={() => setHover({ b, cal: true })} onMouseLeave={() => setHover(null)} />
              ))}
            </>
          )}
          <text x={pad + plot / 2} y={8 + plot + 28} textAnchor="middle" fontSize={10} fill={AXIS.tick} />
          {raw.bins.map((b) => {
            const h = (b.n / maxN) * histH;
            return <rect key={`h${b.lo}`} x={x(b.lo) + 1} width={Math.max(1, x(b.hi) - x(b.lo) - 2)} y={8 + plot + 20 + histH - h} height={h} fill={color} opacity={0.45} />;
          })}
        </svg>
        {hover && (
          <ChartTip x={x(hover.b.mean_conf)} y={y(hover.b.accuracy)} width={size}>
            <div className="font-semibold">{hover.cal ? "+cal " : ""}bin {hover.b.lo.toFixed(1)}–{hover.b.hi.toFixed(1)}</div>
            <div className="tnum">{hover.b.n} rows · confidence {hover.b.mean_conf.toFixed(3)} · accuracy {hover.b.accuracy.toFixed(3)}</div>
            <div className="tnum text-muted">gap {fmtSigned(hover.b.accuracy - hover.b.mean_conf, 3)}</div>
          </ChartTip>
        )}
        <div className="mt-1 flex flex-wrap gap-3 text-caption text-muted" aria-hidden>
          <span className="inline-flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-full" style={{ background: color }} /> raw</span>
          {cal && <span className="inline-flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-full border" style={{ borderColor: color }} /> +cal (dashed)</span>}
          <span>x: mean confidence · y: accuracy · bars: rows per bin</span>
        </div>
      </div>
    </ChartFrame>
  );
}

// ---- Latency ---------------------------------------------------------------------------------------

export type LatencyItem = { model: string; p50: number | null; p95: number | null; n: number; errors: number; color: string; host?: string | null };

export function LatencyDotRange({ items }: { items: LatencyItem[] }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const vals = items.flatMap((i) => [i.p50, i.p95]).filter((v): v is number => v != null && v > 0);
  const lo = vals.length ? Math.min(...vals) : 0.001;
  const hi = vals.length ? Math.max(...vals) : 1;
  const log = hi / lo > 20;
  const left = 110;
  const rowH = 30;
  const height = Math.max(1, items.length) * rowH + 30;
  const x = log
    ? scaleLog().domain([lo * 0.8, hi * 1.25]).range([left, width - 16]).clamp(true)
    : scaleLinear().domain([0, hi * 1.1 || 1]).range([left, width - 16]).nice();
  const tk = (x.ticks(5) as number[]).slice(0, 7);
  return (
    <ChartFrame
      grow
      title="Latency per request: p50 to p95"
      caption="Wall-clock per request from this machine, including network and queueing at the lab's workers concurrency."
      ariaLabel={`Latency: ${items.map((i) => `${i.model} p50 ${fmtLatency(i.p50)}, p95 ${fmtLatency(i.p95)}`).join("; ")}`}
      table={{ columns: ["model", "p50", "p95", "rows", "errors"], rows: items.map((i) => [i.model, fmtLatency(i.p50), fmtLatency(i.p95), i.n, i.errors]) }}
      height={height}
      empty={items.length === 0}
      data-testid="latency-chart"
    >
      <div ref={ref} className="relative h-full w-full">
        <svg width={width} height={height} className="block">
          {tk.map((t) => (
            <g key={t}>
              <line x1={x(t)} x2={x(t)} y1={4} y2={height - 24} stroke={AXIS.grid} strokeDasharray="2 3" />
              <text x={x(t)} y={height - 8} textAnchor="middle" fontSize={11} fill={AXIS.tick}>{fmtLatency(t)}</text>
            </g>
          ))}
          {items.map((it, i) => {
            const y = 6 + i * rowH + rowH / 2;
            const tiny = it.p95 != null && it.p95 < 0.001;
            return (
              <g key={it.model}
                onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)} onFocus={() => setHover(i)} onBlur={() => setHover(null)} className="outline-none">
                <text x={left - 8} y={y + 4} textAnchor="end" fontSize={12} fill="var(--text)">{it.model.length > 14 ? `${it.model.slice(0, 13)}…` : it.model}</text>
                {tiny || it.p50 == null || it.p95 == null ? (
                  <text x={left + 4} y={y + 4} fontSize={12} fill="var(--text-muted)">{tiny ? "< 1 ms" : "—"}</text>
                ) : (
                  <>
                    <line x1={x(it.p50)} x2={x(it.p95)} y1={y} y2={y} stroke={it.color} strokeWidth={2} />
                    <circle cx={x(it.p50)} cy={y} r={4} fill={it.color} />
                    <circle cx={x(it.p95)} cy={y} r={4} fill="var(--surface-1)" stroke={it.color} strokeWidth={1.5} />
                  </>
                )}
              </g>
            );
          })}
        </svg>
        {hover != null && items[hover] && (
          <ChartTip x={x(items[hover].p50 ?? lo)} y={6 + hover * rowH} width={width}>
            <div className="font-semibold">{items[hover].model}</div>
            <div className="tnum">p50 {fmtLatency(items[hover].p50)} · p95 {fmtLatency(items[hover].p95)}</div>
            <div className="tnum text-muted">{items[hover].n} rows · {items[hover].errors} errors</div>
          </ChartTip>
        )}
      </div>
    </ChartFrame>
  );
}

export function LatencyHistogram({ model, bins, color }: { model: string; bins: { lo: number; hi: number; ok: number; failed: number }[]; color: string }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const height = 180;
  const max = Math.max(1, ...bins.map((b) => b.ok + b.failed));
  const left = 36;
  const bw = (width - left - 8) / Math.max(1, bins.length);
  const yy = scaleLinear().domain([0, max]).range([height - 24, 8]).nice();
  const failed = bins.reduce((s, b) => s + b.failed, 0);
  return (
    <ChartFrame
      grow
      title={`Latency histogram · ${model}`}
      caption={`Rows per latency bin${bins.length === 30 ? " (log-spaced when the range is wide)" : ""}; failed rows stacked in red on top.`}
      ariaLabel={`Latency histogram for ${model}: ${bins.reduce((s, b) => s + b.ok, 0)} answered rows, ${failed} failed, from ${fmtLatency(bins[0]?.lo)} to ${fmtLatency(bins[bins.length - 1]?.hi)}`}
      table={{ columns: ["from", "to", "ok", "failed"], rows: bins.map((b) => [fmtLatency(b.lo), fmtLatency(b.hi), b.ok, b.failed]) }}
      height={height}
      empty={bins.length === 0}
      data-testid="latency-histogram"
    >
      <div ref={ref} className="h-full w-full">
        <svg width={width} height={height} className="block">
          {yy.ticks(4).map((t) => (
            <g key={t}>
              <line x1={left} x2={width - 8} y1={yy(t)} y2={yy(t)} stroke={AXIS.grid} strokeDasharray="2 3" />
              <text x={left - 4} y={yy(t) + 3} textAnchor="end" fontSize={10} fill={AXIS.tick}>{t}</text>
            </g>
          ))}
          {bins.map((b, i) => {
            const hOk = yy(0) - yy(b.ok);
            const hF = yy(0) - yy(b.failed);
            return (
              <g key={i}>
                <rect x={left + i * bw + 1} width={Math.max(1, bw - 2)} y={yy(0) - hOk} height={hOk} fill={color} opacity={0.7} />
                {b.failed > 0 && <rect x={left + i * bw + 1} width={Math.max(1, bw - 2)} y={yy(0) - hOk - hF} height={hF} fill="var(--danger)" />}
              </g>
            );
          })}
          {bins.length > 0 && (
            <>
              <text x={left} y={height - 6} fontSize={10} fill={AXIS.tick}>{fmtLatency(bins[0].lo)}</text>
              <text x={width - 8} y={height - 6} textAnchor="end" fontSize={10} fill={AXIS.tick}>{fmtLatency(bins[bins.length - 1].hi)}</text>
            </>
          )}
        </svg>
      </div>
    </ChartFrame>
  );
}

// ---- Probability bars (row detail) -----------------------------------------------------------------

export function ProbBars({ options, probs, gold, kind, top }: { options: [string, string][]; probs: number[] | null; gold: number; kind: string; top: number | null }) {
  const label = (name: string) => (kind === "noul" ? (name === "true" ? "yes" : name === "false" ? "no" : name) : name);
  return (
    <div className="flex flex-col gap-1">
      {options.map(([name, desc], i) => {
        const p = probs ? probs[i] ?? 0 : null;
        return (
          <div key={i} className="grid grid-cols-[minmax(0,7rem)_1fr_3.5rem] items-center gap-2 text-small">
            <span className="truncate" title={desc}>
              {i === gold && <><span className="text-accent" aria-hidden>★ </span><span className="sr-only">gold answer: </span></>}
              {label(name)}
            </span>
            <span className="relative h-3 overflow-hidden rounded-xs bg-surface-3" aria-hidden>
              {kind === "noul" && i === 1 && <span className="absolute inset-y-0 bg-tint-warning" style={{ left: "20%", width: "60%" }} />}
              {p != null && (
                <span className={cn("absolute inset-y-0 left-0 rounded-xs", i === top ? "bg-accent" : "bg-[var(--chart-baseline)]")} style={{ width: `${Math.max(1, p * 100)}%` }} />
              )}
            </span>
            <span className="tnum text-right">{p == null ? "—" : p.toFixed(2)}</span>
          </div>
        );
      })}
      {kind === "noul" && <span className="text-caption text-subtle">Shaded: the 0.2–0.8 abstention band for P(yes); an answer inside it counts as wrong.</span>}
    </div>
  );
}

// ---- JevBench competence bars ----------------------------------------------------------------------

export function CompetenceBars({ values }: { values: Record<string, number> }) {
  const names: Record<string, string> = { noul: "yes/no", choice: "choice", score: "score" };
  return (
    <div role="group" className="flex flex-col gap-1.5" aria-label="Competence by question type, chance-corrected (−100 to 100)">
      {(["noul", "choice", "score"] as const).filter((k) => values[k] != null).map((k) => {
        const v = Math.max(-100, Math.min(100, values[k]));
        return (
          <div key={k} className="grid grid-cols-[4rem_1fr_3rem] items-center gap-2 text-small">
            <span className="text-muted">{names[k]}</span>
            <span className="relative h-3 rounded-xs bg-surface-3" aria-hidden>
              <span className="absolute inset-y-0 left-1/2 w-px bg-[var(--text-muted)]" />
              <span
                className={cn("absolute inset-y-0 rounded-xs", v >= 0 ? "bg-accent" : "bg-danger")}
                style={v >= 0 ? { left: "50%", width: `${v / 2}%` } : { right: "50%", width: `${-v / 2}%` }}
              />
            </span>
            <span className="tnum text-right">{fmtIntelligence(values[k])}</span>
          </div>
        );
      })}
    </div>
  );
}
