// Data API client (owner: Data area). Endpoints: API.md section 9.
import { api, seg } from "@/lib/api";
import type { SuiteStats, Suites } from "./types";

export const dataKeys = {
  suites: ["suites"] as const,
  stats: (ref: string) => ["suites", "stats", ref] as const,
};

export const listSuites = () => api.get<Suites>("/api/suites");
export const getSuiteStats = (ref: string) => api.get<SuiteStats>(`/api/suites/${seg(ref)}/stats`);
// TODO(data): rows, export-suite, csv preview/convert (multipart), generate, split, check, leakcheck.
