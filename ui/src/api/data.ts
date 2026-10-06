// Data API client (owner: Data area). Endpoints: API.md section 9.
import { api, seg } from "@/lib/api";
import type { Page, SuiteStats, Suites as SuitesBase } from "./types";

export type Suites = SuitesBase & { used_by_labs: string[] };
export type DataStats = {
  rows: number; by_kind: Record<string, number>; by_task: Record<string, number>; by_split: Record<string, number>;
  label_balance: Record<string, Record<string, number>>; with_images: number;
};
export type SuiteRow = {
  index: number; id: string; kind: "noul" | "choice" | "score"; state: string | unknown; state_truncated?: boolean;
  instructions: string; options: [string, string][]; label: number; task?: string; split?: string | null;
  images?: { path: string; file_id: string | null }[];
};
export type RowsPage = Page<SuiteRow> & { facets: { kinds: string[]; splits: string[]; tasks: string[] } };
export type RowsQuery = { offset?: number; limit?: number; kind?: string; split?: string; task?: string; q?: string };
export type Written = { path: string; rows: number; file_id: string };
export type CsvPreview = {
  upload_id: string; filename: string; suggested_out: string; task: string; delimiter: string; rows_total: number;
  columns: { found: string[]; missing_required: string[]; ignored: string[] };
  preview: SuiteRow[]; errors: { row: number | null; message: string }[]; error_count: number; stats: DataStats | null;
};
export type GenerateBody = { out: string; families: string[]; per_kind: number; seed: number; exclude_suites: string[]; overwrite: boolean };
export type GenerateResult = Written & { dropped_overlapping: number; stats: DataStats; command: string };
export type SplitBody = { file_id: string; out: string; dev_fraction: number; seed: number; overwrite: boolean };
export type SplitResult = Written & { by_split: Record<string, number>; dev_by_kind: Record<string, number>; snippet: string };
export type LeakcheckBody = { train_file_id: string; against: string[]; drop_to: string | null; overwrite: boolean };
export type LeakcheckResult = {
  train_rows: number; eval_rows: number; overlapping: number; passed: boolean; against: string[];
  examples: { train_row: number; task: string | null; state: string }[]; clean: Written | null;
};
export type CheckResult = { valid: boolean; problem: string | null; stats: DataStats | null };

export const dataKeys = {
  suites: ["suites"] as const,
  stats: (ref: string) => ["suites", "stats", ref] as const,
  rows: (ref: string, q: RowsQuery) => ["suites", "rows", ref, q] as const,
};

export const listSuites = () => api.get<Suites>("/api/suites");
/** 200 stats, or `{job_id}` (202) when a large suite is being built first. */
export const getSuiteStats = (ref: string) => api.get<SuiteStats | { job_id: string }>(`/api/suites/${seg(ref)}/stats`);
export const getSuiteRows = (ref: string, q: RowsQuery) => api.get<RowsPage>(`/api/suites/${seg(ref)}/rows`, { query: q });
export const exportSuite = (body: { ref: string; out: string; overwrite: boolean }) => api.post<Written>("/api/data/export-suite", body);
export function previewCsv(file: File, task: string, delimiter: string) {
  const fd = new FormData();
  fd.append("file", file);
  fd.append("task", task);
  fd.append("delimiter", delimiter);
  return api.post<CsvPreview>("/api/data/csv/preview", fd);
}
export const convertCsv = (body: { upload_id: string; out: string; task: string; delimiter: string; overwrite: boolean }) =>
  api.post<Written & { stats: DataStats }>("/api/data/csv/convert", body);
export const generateRows = (body: GenerateBody) => api.post<GenerateResult | { job_id: string }>("/api/data/generate", body);
export const splitFile = (body: SplitBody) => api.post<SplitResult>("/api/data/split", body);
export const checkFile = (fileId: string) => api.post<CheckResult>("/api/data/check", { file_id: fileId });
export const leakcheck = (body: LeakcheckBody) => api.post<LeakcheckResult>("/api/data/leakcheck", body);

/** Human label for a suite ref: built-ins as written, files by path. */
export function refLabel(ref: string, files: { ref: string; path: string }[]): string {
  if (ref.startsWith("file:")) return files.find((f) => f.ref === ref)?.path ?? "file";
  return ref;
}
