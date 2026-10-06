// Results API client (owner: Results area). Endpoints: API.md section 7.
import { api, seg } from "@/lib/api";
import type { Items, Leaderboard, RunDetail, RunRootDetail, RunRootSummary } from "./types";

export const resultKeys = {
  all: ["runs"] as const,
  list: (q?: { lab_id?: string; q?: string }) => ["runs", "list", q ?? {}] as const,
  root: (rootId: string) => ["runs", "root", rootId] as const,
  leaderboard: (rootId: string, suite: string, scores: string) => ["runs", "leaderboard", rootId, suite, scores] as const,
};

export const listRunRoots = (q?: { lab_id?: string; q?: string }) => api.get<Items<RunRootSummary>>("/api/runs", { query: q });
export const getRunRoot = (rootId: string) => api.get<RunRootDetail>(`/api/runs/${rootId}`);
export const getLeaderboard = (rootId: string, suite: string, scores: "raw" | "cal" | "both" = "raw") =>
  api.get<Leaderboard>(`/api/runs/${rootId}/leaderboard`, { query: { suite, scores } });
export const getRun = (rootId: string, model: string, suite: string) =>
  api.get<RunDetail>(`/api/runs/${rootId}/runs/${seg(model)}/${seg(suite)}`);
// TODO(results): vs-baseline, families, latency, jevbench, rows, provenance, calibration, compare, exports.
