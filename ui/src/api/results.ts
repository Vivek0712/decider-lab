// Results API client (owner: Results area). Endpoints: API.md section 7.
import { api, fileUrl, seg } from "@/lib/api";
import type {
  CI,
  Compare,
  Diff,
  Families,
  Items,
  JevBenchItem,
  LatencyDetail,
  Leaderboard,
  Page,
  Prediction,
  Reliability,
  RowAnswer,
  RowsPage,
  RunDetail,
  RunRootDetail,
  RunRootSummary,
  Verdict,
  VsBaseline,
} from "./types";

// ---- area-local extensions of the API.md shapes (additive fields the server also returns) ---------

export type ScoresMode = "raw" | "cal" | "both";
export type BestOnSuite = { suite: string; model: string; intelligence: number; ci95: CI };
export type RunRootListItem = RunRootSummary & { kind?: "lab" | "eval"; best_by_suite?: BestOnSuite[] };
export type RunRootFull = RunRootDetail & {
  kind?: "lab" | "eval";
  wall_s?: number | null;
  report_json_available?: boolean;
  suites: (RunRootDetail["suites"][number] & { calibrated_models?: string[] })[];
};
export type LeaderboardFull = Leaderboard & { scores?: ScoresMode; rows: (Leaderboard["rows"][number] & { is_baseline?: boolean })[] };
export type RunDetailFull = RunDetail & { lab?: string; is_baseline?: boolean };
export type ReliabilityFull = Reliability & { split?: string; bins_count?: number };
export type RootLatency = {
  suite: string; same_machine: boolean;
  items: { model: string; p50: number | null; p95: number | null; n: number; errors: number; host: string | null; workers: number | null; is_baseline?: boolean }[];
};
export type VsBaselineFull = VsBaseline & { variant?: "raw" | "cal"; bootstrap?: number; message?: string };
export type JevBench = { items: (JevBenchItem & { label?: string })[]; label: string; note: string };
export type CalibrationItem = {
  model: string; temperatures: Record<string, number> | null; fit_split: string | null; fit_rows: number | null;
  score_split: string; before: { intelligence: number | null; nll: number | null; ece: number | null } | null;
  after: { intelligence: number | null; nll: number | null; ece: number | null } | null;
  delta: { n_paired: number; only_a: number; only_b: number; intelligence: Diff; accuracy: Diff; nll: Diff } | null;
  is_baseline?: boolean;
};
export type Calibration = {
  suite: string; items: CalibrationItem[];
  eligible: { model: string; dev_rows_by_kind: Record<string, number> }[];
  ineligible: { model: string; reason: string; dev_rows_by_kind?: Record<string, number> }[];
};
export type RowAnswerFull = RowAnswer & { correct?: boolean; expected_level?: number | null };
export type RowItem = RowsPage["items"][number] & { n_options?: number | null; answers: Record<string, RowAnswerFull | null> };
export type RowsResult = Omit<RowsPage, "items"> & {
  items: RowItem[]; suite: string; split: string; families: string[]; kinds: string[]; splits: string[];
};
export type RowDetail = {
  id: string; suite: string; task: string; kind: "noul" | "choice" | "score"; split: string | null; label: number;
  state: unknown; state_is_json: boolean; instructions: string | null; options: [string, string][] | null;
  images: { src: string | null; alt: string }[]; models: string[];
  answers: Record<string, (RowAnswerFull & { probs: number[] | null; latency_s: number | null }) | null>;
  content_available: boolean; content_reason: string | null; position: number | null;
};
export type ProvenanceItem = {
  model: string; suite: string; suite_sha256: string | null; suite_params: Record<string, unknown> | null; n_rows: number | null;
  answerer: Record<string, unknown> | null; source: Record<string, unknown> | null; host: string | null; platform: string | null;
  python: string | null; gpu: string | null; decider_lab: string | null; finished_utc: string | null; wall_s: number | null;
  workers: number | null; limit: number | null; calibration: Record<string, unknown> | null; calibrated_from: string | null;
  jevbench_commit: string | null; job_id: string | null;
};
export type Provenance = {
  items: ProvenanceItem[]; job_id: string | null;
  same_rows: Record<string, { consistent: boolean; sha256: string | null; mismatched_models: string[] }>;
};
export type RunRefItem = {
  root_id: string; lab: string; model: string; suite: string; calibrated: boolean; n: number | null;
  intelligence: number | null; ci95: CI | null; limit: number | null; has_splits: boolean; finished_at: string | null;
};
export type CompareFull = Compare & { proxy_note?: string };
export type { Verdict };

export type RowsQuery = {
  suite: string; offset?: number; limit?: number; kind?: string; family?: string; split?: string; q?: string;
  show?: "all" | "wrong" | "disagree" | "errors"; wrong_for?: string; models?: string;
};

// ---- keys --------------------------------------------------------------------------------------------

export const resultKeys = {
  all: ["runs"] as const,
  list: (q?: { lab_id?: string; q?: string }) => ["runs", "list", q ?? {}] as const,
  refs: () => ["runs", "refs"] as const,
  root: (rootId: string) => ["runs", "root", rootId] as const,
  leaderboard: (rootId: string, suite: string, scores: string) => ["runs", "leaderboard", rootId, suite, scores] as const,
  vsBaseline: (rootId: string, suite: string, variant: string) => ["runs", "vs-baseline", rootId, suite, variant] as const,
  families: (rootId: string, suite: string, variant: string) => ["runs", "families", rootId, suite, variant] as const,
  calibration: (rootId: string, suite: string) => ["runs", "calibration", rootId, suite] as const,
  latency: (rootId: string, suite: string) => ["runs", "latency", rootId, suite] as const,
  runLatency: (rootId: string, model: string, suite: string) => ["runs", "run-latency", rootId, model, suite] as const,
  jevbench: (rootId: string) => ["runs", "jevbench", rootId] as const,
  rows: (rootId: string, q: RowsQuery) => ["runs", "rows", rootId, q] as const,
  row: (rootId: string, suite: string, rowId: string) => ["runs", "row", rootId, suite, rowId] as const,
  provenance: (rootId: string) => ["runs", "provenance", rootId] as const,
  run: (rootId: string, model: string, suite: string) => ["runs", "run", rootId, model, suite] as const,
  predictions: (rootId: string, model: string, suite: string, q: Record<string, unknown>) => ["runs", "predictions", rootId, model, suite, q] as const,
  reliability: (rootId: string, model: string, suite: string, kind: string, bins: number) =>
    ["runs", "reliability", rootId, model, suite, kind, bins] as const,
  compare: (a: string, b: string, split: string) => ["runs", "compare", a, b, split] as const,
};

// ---- calls -------------------------------------------------------------------------------------------

const root = (rootId: string) => `/api/runs/${seg(rootId)}`;
const run = (rootId: string, model: string, suite: string) => `${root(rootId)}/runs/${seg(model)}/${seg(suite)}`;

export const listRunRoots = (q?: { lab_id?: string; q?: string }) => api.get<Items<RunRootListItem>>("/api/runs", { query: q });
export const listRunRefs = () => api.get<Items<RunRefItem>>("/api/runs/refs");
export const getRunRoot = (rootId: string) => api.get<RunRootFull>(root(rootId));
export const getLeaderboard = (rootId: string, suite: string, scores: ScoresMode = "raw") =>
  api.get<LeaderboardFull>(`${root(rootId)}/leaderboard`, { query: { suite, scores } });
export const getVsBaseline = (rootId: string, suite: string, variant: "raw" | "cal" = "raw") =>
  api.get<VsBaselineFull>(`${root(rootId)}/vs-baseline`, { query: { suite, variant } });
export const getFamilies = (rootId: string, suite: string, variant: "raw" | "cal" = "raw") =>
  api.get<Families>(`${root(rootId)}/families`, { query: { suite, variant } });
export const getCalibration = (rootId: string, suite: string) => api.get<Calibration>(`${root(rootId)}/calibration`, { query: { suite } });
export const getRootLatency = (rootId: string, suite: string) => api.get<RootLatency>(`${root(rootId)}/latency`, { query: { suite } });
export const getJevBench = (rootId: string) => api.get<JevBench>(`${root(rootId)}/jevbench`);
export const getRows = (rootId: string, q: RowsQuery) => api.get<RowsResult>(`${root(rootId)}/rows`, { query: q });
export const getRow = (rootId: string, suite: string, rowId: string) =>
  api.get<RowDetail>(`${root(rootId)}/rows/${seg(rowId)}`, { query: { suite } });
export const getProvenance = (rootId: string) => api.get<Provenance>(`${root(rootId)}/provenance`);
export const rebuildReport = (rootId: string, baseline?: string | null) =>
  api.post<{ report_md: string; report_json: string }>(`${root(rootId)}/report`, baseline === undefined ? {} : { baseline });
export const deleteRunRoot = (rootId: string, confirm: string) => api.del<{ deleted: boolean; freed_mb: number }>(root(rootId), { confirm });

export const getRun = (rootId: string, model: string, suite: string) => api.get<RunDetailFull>(run(rootId, model, suite));
export const getPredictions = (rootId: string, model: string, suite: string, q: Record<string, string | number | undefined>) =>
  api.get<Page<Prediction>>(`${run(rootId, model, suite)}/predictions`, { query: q });
export const getReliability = (rootId: string, model: string, suite: string, kind = "all", bins = 10) =>
  api.get<ReliabilityFull>(`${run(rootId, model, suite)}/reliability`, { query: { kind, bins } });
export const getRunLatency = (rootId: string, model: string, suite: string) => api.get<LatencyDetail>(`${run(rootId, model, suite)}/latency`);

/** `<root_id>:<model>:<suite>` with each part percent-encoded (API.md GET /api/compare). */
export const refString = (r: { root_id: string; model: string; suite: string }) =>
  [r.root_id, r.model, r.suite].map((x) => encodeURIComponent(x)).join(":");
export const parseRef = (s: string | null): { root_id: string; model: string; suite: string } | null => {
  if (!s) return null;
  const parts = s.split(":");
  if (parts.length !== 3) return null;
  const [root_id, model, suite] = parts.map((p) => {
    try {
      return decodeURIComponent(p);
    } catch {
      return p;
    }
  });
  return { root_id, model, suite };
};
export const getCompare = (a: string, b: string, split: string) => api.get<CompareFull>("/api/compare", { query: { a, b, split } });

// ---- download links (plain <a href download>; the dev token is added when there is one) --------------

export const exportUrls = (rootId: string, suite: string, scores: ScoresMode) => ({
  reportMd: fileUrl(`${root(rootId)}/report.md`),
  reportJson: fileUrl(`${root(rootId)}/report.json`),
  leaderboardCsv: fileUrl(`${root(rootId)}/leaderboard.csv`, { suite, scores }),
  leaderboardMd: `${root(rootId)}/leaderboard.md`,
});
export const predictionsCsvUrl = (rootId: string, model: string, suite: string) => fileUrl(`${run(rootId, model, suite)}/predictions.csv`);
export const getLeaderboardMarkdown = (rootId: string, suite: string, scores: ScoresMode) =>
  api.get<string>(`${root(rootId)}/leaderboard.md`, { query: { suite, scores } });
