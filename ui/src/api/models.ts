// Models API client (owner: Models area). Endpoints: API.md section 8. Pulls are jobs (kind "pull").
import { api } from "@/lib/api";
import type { CachedModel as CachedModelBase, Inspect, Job, JobStarted, ModelsList as ModelsListBase } from "./types";

/** Fields the server adds beside the contract's (HF cache entries are listed, never deleted). */
export type CachedModel = CachedModelBase & { location: "decider-lab" | "hf"; deletable: boolean; hf_refs?: string[] };
export type ModelsList = Omit<ModelsListBase, "items"> & { items: CachedModel[]; hf_cache_dir?: string };

export type PullBody = {
  kind: "pull";
  source: string;
  revision: string | null;
  sha256: string | null;
  require_pinned: boolean;
  profile: string | null;
  region: string | null;
};

export const modelKeys = {
  all: ["models"] as const,
  inspect: (b: object) => ["models", "inspect", b] as const,
  job: (id: string) => ["models", "job", id] as const,
};

export const listModels = () => api.get<ModelsList>("/api/models");
export const inspectSource = (body: { source: string; revision?: string | null; sha256?: string | null; require_pinned?: boolean; profile?: string | null; region?: string | null }) =>
  api.post<Inspect>("/api/models/inspect", body);
export const deleteModel = (modelKey: string, confirm: string) => api.del<{ deleted: boolean; freed_gb: number }>(`/api/models/${modelKey}`, { confirm });
export const startPull = (body: PullBody) => api.post<JobStarted>("/api/jobs", body);
export const getPullJob = (jobId: string) => api.get<Job>(`/api/jobs/${jobId}`);

/** First 8 characters of the ref (commit or sha256), or of the key: the delete confirmation phrase. */
export const ref8 = (m: Pick<CachedModel, "ref" | "model_key">) => (m.ref ?? m.model_key).slice(0, 8);

/** The `serve:` snippet a lab needs for this model. */
export function labSnippet(m: Pick<CachedModel, "source" | "kind" | "ref" | "ref_kind">, name = "my-model"): string {
  const lines = [`  ${name}:`, `    serve: ${m.source}`];
  if ((m.kind === "url" || m.kind === "s3") && m.ref_kind === "sha256" && m.ref) lines.push(`    sha256: ${m.ref}`);
  if (m.kind === "hf" && m.ref_kind === "commit" && m.ref && !m.source.includes("@")) lines.push(`    revision: ${m.ref}`);
  if (m.kind === "hf") lines.push("    require_pinned: true");
  return `models:\n${lines.join("\n")}\n`;
}
