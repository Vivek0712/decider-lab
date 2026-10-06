// Labs API client (owner: Labs area). Endpoints: API.md section 5.
import { api } from "@/lib/api";
import type { Estimate, Items, Lab, LabListItem, RunOptions, Template, Validation } from "./types";

export const labKeys = {
  all: ["labs"] as const,
  list: (q?: { q?: string; invalid?: boolean }) => ["labs", "list", q ?? {}] as const,
  one: (labId: string) => ["labs", "one", labId] as const,
  templates: ["labs", "templates"] as const,
  estimate: (labId: string, options: RunOptions) => ["labs", "estimate", labId, options] as const,
};

export type CreatedLab = { lab_id: string; name: string; path: string; files: string[] };

export const listLabs = (q?: { q?: string; invalid?: boolean }) =>
  api.get<Items<LabListItem>>("/api/labs", { query: { q: q?.q, invalid: q?.invalid ? 1 : undefined } });
export const getLab = (labId: string) => api.get<Lab>(`/api/labs/${labId}`);
export const listTemplates = () => api.get<Items<Template>>("/api/templates");
export const validateLab = (yaml: string, labId?: string, signal?: AbortSignal) =>
  api.post<Validation>("/api/labs/validate", { yaml, lab_id: labId }, { signal });
export const saveLab = (labId: string, yaml: string, etag: string) => api.put<Lab>(`/api/labs/${labId}`, { yaml }, { ifMatch: etag });
export const estimateRun = (labId: string, options: RunOptions, signal?: AbortSignal) =>
  api.post<Estimate>(`/api/labs/${labId}/estimate`, options, { signal });
export const createLab = (body: { template: "eval" | "finetune"; name: string; dir: string }) => api.post<CreatedLab>("/api/labs", body);
export const importLab = (path: string) => api.post<LabListItem>("/api/labs/import", { path });
export const duplicateLab = (labId: string, body: { name: string; path: string }) =>
  api.post<LabListItem>(`/api/labs/${labId}/duplicate`, body);
export const deleteLab = (labId: string, confirm: string) => api.del<{ deleted: boolean }>(`/api/labs/${labId}`, { confirm });
export const fixSecret = (labId: string, path: string, envName: string, etag: string) =>
  api.post<Lab>(`/api/labs/${labId}/fix-secret`, { path, env_name: envName }, { ifMatch: etag });
