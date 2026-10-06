// Models API client (owner: Models area). Endpoints: API.md section 8. Pulls are jobs (kind "pull").
import { api } from "@/lib/api";
import type { Inspect, ModelsList } from "./types";

export const modelKeys = { all: ["models"] as const };

export const listModels = () => api.get<ModelsList>("/api/models");
export const inspectSource = (body: { source: string; revision?: string | null; sha256?: string | null; require_pinned?: boolean }) =>
  api.post<Inspect>("/api/models/inspect", body);
export const deleteModel = (modelKey: string, confirm: string) => api.del<{ deleted: boolean; freed_gb: number }>(`/api/models/${modelKey}`, { confirm });
