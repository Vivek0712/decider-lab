// Overview API client (owner: Overview area). Endpoint: API.md section 3, GET /api/overview.
import { api } from "@/lib/api";
import type { CI, JobStarted, Overview as OverviewBase, RecentResult as RecentResultBase } from "./types";

type Provider = { configured: boolean; instances?: number; idle?: number; usd_per_hour?: number | null; error?: string | null };
export type RecentResult = RecentResultBase & { is_baseline?: boolean };
export type RecentRoot = {
  root_id: string; title: string; path: string; kind: "lab" | "eval"; finished_at: string | null; failures: number;
  suites: { suite: string; models: number; n: number; errors: number;
            top: { model: string; intelligence: number; ci95: CI | null; tied_with: number } | null;
            baseline: { model: string; intelligence: number } | null }[];
};
export type LabItem = { lab_id: string; name: string; path: string; valid: boolean; modified_at: string | null };
/** GET /api/overview: the contract's fields plus what the home page also shows. */
export type Overview = Omit<OverviewBase, "kpis" | "recent_results"> & {
  kpis: OverviewBase["kpis"] & {
    run_roots: number; cached_models: number; cached_models_gb: number; jobs_queued: number;
    cloud: OverviewBase["kpis"]["cloud"] & {
      fake: boolean;
      vast: Provider & { credit_usd?: number | null };
      aws: Provider & { account?: string | null; profile?: string | null; region?: string | null };
    };
  };
  recent_results: RecentResult[];
  recent_roots: RecentRoot[];
  labs: LabItem[];
};

export type EvalBody = {
  kind: "eval";
  model: { type: "url" | "baseline" | "serve" | "python"; value: string };
  name: string; suite: string; split: string | null; limit: number | null; workers: number; vision: boolean; out: string | null;
};

export const overviewKeys = { all: ["overview"] as const };

export const getOverview = () => api.get<Overview>("/api/overview");
export const startEval = (body: EvalBody) => api.post<JobStarted>("/api/jobs", body);
