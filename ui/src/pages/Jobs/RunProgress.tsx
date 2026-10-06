import type { JobProgress, RunProgress } from "@/api/types";
import { ProgressBar } from "@/components/ProgressBar";
import { Tooltip } from "@/components/Tooltip";
import { cn } from "@/lib/cn";
import { fmtCI, fmtInt, fmtIntelligence } from "@/lib/format";

const RUN_STATUS: Record<RunProgress["status"], { icon: string; label: string; cls: string }> = {
  pending: { icon: "○", label: "pending", cls: "text-muted" },
  running: { icon: "●", label: "running", cls: "text-accent" },
  done: { icon: "✓", label: "done", cls: "text-success" },
  failed: { icon: "✕", label: "failed", cls: "text-danger" },
  skipped: { icon: "–", label: "skipped", cls: "text-muted" },
};

export const PROXY_NOTICE =
  "Intelligence (local proxy): JevBench v1.5 rules applied to this suite. It ranks these runs against each other; it is not a JevBench board score.";

const slug = (s: string) => s.replace(/[^A-Za-z0-9_+.-]/g, "-");

/** Per model/suite progress (DESIGN.md RunProgressTable). Intelligence only for finished runs, always with its CI. */
export function RunProgressTable({ progress }: { progress: JobProgress }) {
  const runs = progress.runs;
  if (!runs.length) return null;
  return (
    <div className="scrollbar-thin max-w-full overflow-x-auto">
      <table className="w-full border-collapse text-left text-body" data-testid="job-runs">
        <caption className="caption-bottom pt-2 text-left text-caption text-subtle" data-testid="proxy-notice">
          {PROXY_NOTICE}
        </caption>
        <thead className="bg-surface-2">
          <tr className="text-caption uppercase tracking-wide text-muted">
            <th scope="col" className="h-9 px-3 font-semibold">
              Run
            </th>
            <th scope="col" className="px-3 font-semibold">
              Status
            </th>
            <th scope="col" className="px-3 font-semibold">
              Rows
            </th>
            <th scope="col" className="px-3 text-right font-semibold max-sm:hidden">
              Rows/s
            </th>
            <th scope="col" className="px-3 text-right font-semibold">
              Errors
            </th>
            <th scope="col" className="px-3 text-right font-semibold">
              <Tooltip content="95% bootstrap confidence interval over rows (1,000 resamples)">
                <span tabIndex={0}>Intelligence (local proxy) · 95% CI</span>
              </Tooltip>
            </th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => {
            const st = RUN_STATUS[r.status];
            const frac = r.total ? Math.min(1, r.done / r.total) : null;
            return (
              <tr key={`${r.model}/${r.suite}`} className="border-t border-border align-top" data-testid={`job-progress-${slug(r.model)}-${slug(r.suite)}`} data-status={r.status}>
                <td className="px-3 py-2">
                  <span className="font-medium">{r.model}</span> <span className="text-muted">/ {r.suite}</span>
                  {r.message && <div className={cn("text-small", r.status === "failed" ? "text-danger" : "text-muted")}>{r.message}</div>}
                </td>
                <td className={cn("whitespace-nowrap px-3 py-2 text-small font-medium", st.cls)}>
                  <span aria-hidden className={cn("mr-1", r.status === "running" && "dl-pulse")}>
                    {st.icon}
                  </span>
                  {st.label}
                </td>
                <td className="px-3 py-2">
                  <div className="flex min-w-[150px] items-center gap-2">
                    <span className="tnum whitespace-nowrap text-small" data-testid="run-rows">
                      {fmtInt(r.done)}/{r.total != null ? fmtInt(r.total) : "?"}
                    </span>
                    {r.status !== "skipped" && (
                      <ProgressBar
                        value={r.status === "done" ? 1 : frac}
                        indeterminate={r.status === "running" && frac == null}
                        label={`${r.model} / ${r.suite}: ${r.done} of ${r.total ?? "unknown"} rows`}
                        tone={r.status === "failed" ? "danger" : "accent"}
                        className="w-[90px] flex-none"
                      />
                    )}
                  </div>
                  {r.reused ? <div className="text-caption text-subtle">reused {fmtInt(r.reused)} rows</div> : null}
                </td>
                <td className="tnum px-3 py-2 text-right text-small max-sm:hidden">{r.rows_per_s != null ? r.rows_per_s.toFixed(1) : "—"}</td>
                <td className={cn("tnum px-3 py-2 text-right text-small", r.errors > 0 && "text-warning")}>
                  {r.errors}
                  {r.errors > 0 && <span aria-label="rows failed"> ⚠</span>}
                </td>
                <td className="tnum px-3 py-2 text-right text-small">
                  {r.status === "done" && r.intelligence != null ? (
                    <>
                      <span className="font-semibold">{fmtIntelligence(r.intelligence)}</span>{" "}
                      <span className="text-muted">{r.ci95 ? fmtCI(r.ci95) : "CI n/a"}</span>
                    </>
                  ) : (
                    <span className="text-muted" title={r.status === "running" ? "when done" : undefined}>
                      {r.status === "running" ? "(when done)" : "—"}
                    </span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
