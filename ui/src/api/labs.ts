// Labs API client (owner: Labs area). Endpoints: API.md section 5.
import { api } from "@/lib/api";
import type { Estimate, Items, Lab, LabListItem, RunOptions, Template, Validation } from "./types";

export const labKeys = {
  all: ["labs"] as const,
  list: (q?: { q?: string; invalid?: boolean }) => ["labs", "list", q ?? {}] as const,
  one: (labId: string) => ["labs", "one", labId] as const,
  templates: ["labs", "templates"] as const,
};

export const listLabs = (q?: { q?: string; invalid?: boolean }) =>
  api.get<Items<LabListItem>>("/api/labs", { query: { q: q?.q, invalid: q?.invalid ? 1 : undefined } });
export const getLab = (labId: string) => api.get<Lab>(`/api/labs/${labId}`);
export const listTemplates = () => api.get<Items<Template>>("/api/templates");
export const validateLab = (yaml: string, labId?: string) => api.post<Validation>("/api/labs/validate", { yaml, lab_id: labId });
export const saveLab = (labId: string, yaml: string, etag: string) => api.put<Lab>(`/api/labs/${labId}`, { yaml }, { ifMatch: etag });
export const estimateRun = (labId: string, options: RunOptions) => api.post<Estimate>(`/api/labs/${labId}/estimate`, options);
// TODO(labs): create, import, duplicate, delete, fix-secret (API.md section 5).
