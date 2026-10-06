// Compute API client (owner: Compute area). Endpoints: API.md section 10.
//
// Cloud reads are never retried by the query client (a 502/504 from a provider would otherwise
// triple the wait); use `cloudQuery` options for them.
import { api } from "@/lib/api";
import type { AwsIdentity, AwsInstance, AwsQuota, BedrockModel, Doctor, Items, SshHost, VastInstance, VastOffer, VastStatus } from "./types";

// ---- response shapes (API.md section 10; fields beyond types.ts are additive) -------------------

export type OwnedInstance = { job_title?: string | null };
export type VastInstanceRow = VastInstance & OwnedInstance;
export type AwsInstanceRow = AwsInstance & OwnedInstance;
export type VastInstances = { fake: boolean; items: VastInstanceRow[]; usd_per_hour: number | null };
export type VastOffers = { fake: boolean; items: VastOffer[]; credit_usd: number | null; gpu_names: string[] };
export type VastOfferQuery = { gpu: string; num_gpus: number; max_price: number; disk_gb: number; min_gpu_ram_gb?: number };
export type VastDestroyResult = { destroyed: boolean; message?: string };

export type AwsProfiles = { fake: boolean; items: string[]; env_profile: string | null; boto3: boolean; default_region?: string; regions?: string[] };
export type AwsIdentityResult = AwsIdentity & { fix?: string | null };
export type AwsQuotas = { fake: boolean; items: AwsQuota[]; error: string | null };
export type AwsInstances = { fake: boolean; items: AwsInstanceRow[]; usd_per_hour: number | null };
export type AwsInstanceType = { type: string; gpu: string | null; vcpus: number; family: string; usd_per_hour: number };
export type AwsInstanceTypes = { items: AwsInstanceType[]; as_of: string; region_note: string };
export type BedrockModels = { fake: boolean; items: BedrockModel[]; error: string | null };
export type AwsScope = { profile?: string; region?: string };

export type SshHostInput = { name: string; target: string; key_path: string; work_dir: string | null };
export type SshTestResult = { ok: boolean; latency_ms: number | null; gpu: string | null; error: string | null; at: string };

// ---- query keys --------------------------------------------------------------------------------

export const computeKeys = {
  all: ["compute"] as const,
  doctor: ["compute", "doctor"] as const,
  vast: ["compute", "vast"] as const,
  vastStatus: ["compute", "vast", "status"] as const,
  vastInstances: ["compute", "vast", "instances"] as const,
  vastOffers: (q: VastOfferQuery) => ["compute", "vast", "offers", q] as const,
  aws: ["compute", "aws"] as const,
  awsProfiles: ["compute", "aws", "profiles"] as const,
  awsIdentity: (profile?: string, region?: string) => ["compute", "aws", "identity", profile, region] as const,
  awsQuotas: (profile?: string, region?: string) => ["compute", "aws", "quotas", profile, region] as const,
  awsInstances: (profile?: string, region?: string) => ["compute", "aws", "instances", profile, region] as const,
  awsInstanceTypes: ["compute", "aws", "instance-types"] as const,
  bedrockModels: (profile?: string, region?: string, q?: string) => ["compute", "aws", "bedrock", profile, region, q] as const,
  sshHosts: ["compute", "ssh", "hosts"] as const,
};

/** Options for cloud reads: no retries, keep showing the previous data while refetching. */
export const cloudQuery = { retry: false, staleTime: 30_000 } as const;

// ---- doctor ------------------------------------------------------------------------------------

export const getComputeDoctor = () => api.get<Doctor>("/api/compute/doctor");

// ---- vast.ai -----------------------------------------------------------------------------------

export const getVastStatus = () => api.get<VastStatus>("/api/compute/vast/status");
export const listVastInstances = () => api.get<VastInstances>("/api/compute/vast/instances");
export const searchVastOffers = (q: VastOfferQuery) => api.get<VastOffers>("/api/compute/vast/offers", { query: q });
export const destroyVastInstance = (id: number, confirm: string) =>
  api.post<VastDestroyResult>(`/api/compute/vast/instances/${encodeURIComponent(String(id))}/destroy`, { confirm });
export const destroyAllVastInstances = (confirm: string) =>
  api.post<{ results: { id: number; destroyed: boolean }[] }>("/api/compute/vast/instances/destroy-all", { confirm });

// ---- AWS ---------------------------------------------------------------------------------------

export const listAwsProfiles = () => api.get<AwsProfiles>("/api/compute/aws/profiles");
export const getAwsIdentity = (profile?: string, region?: string) =>
  api.get<AwsIdentityResult>("/api/compute/aws/identity", { query: { profile, region } });
export const getAwsQuotas = (profile?: string, region?: string) => api.get<AwsQuotas>("/api/compute/aws/quotas", { query: { profile, region } });
export const listAwsInstances = (profile?: string, region?: string) =>
  api.get<AwsInstances>("/api/compute/aws/instances", { query: { profile, region } });
export const listAwsInstanceTypes = () => api.get<AwsInstanceTypes>("/api/compute/aws/instance-types");
export const listBedrockModels = (profile?: string, region?: string, q?: string) =>
  api.get<BedrockModels>("/api/compute/aws/bedrock-models", { query: { profile, region, q } });
export const terminateAwsInstance = (id: string, confirm: string, scope: AwsScope) =>
  api.post<{ terminating: string[] }>(`/api/compute/aws/instances/${encodeURIComponent(id)}/terminate`, { confirm, ...scope });
export const terminateAllAwsInstances = (confirm: string, scope: AwsScope) =>
  api.post<{ terminating: string[] }>("/api/compute/aws/instances/terminate-all", { confirm, ...scope });

// ---- SSH hosts ---------------------------------------------------------------------------------

export const listSshHosts = () => api.get<Items<SshHost>>("/api/compute/ssh/hosts");
export const addSshHost = (h: SshHostInput) => api.post<SshHost>("/api/compute/ssh/hosts", h);
export const updateSshHost = (id: string, h: SshHostInput) => api.put<SshHost>(`/api/compute/ssh/hosts/${encodeURIComponent(id)}`, h);
export const deleteSshHost = (id: string) => api.del<{ deleted: boolean }>(`/api/compute/ssh/hosts/${encodeURIComponent(id)}`);
export const testSshHost = (id: string) => api.post<SshTestResult>(`/api/compute/ssh/hosts/${encodeURIComponent(id)}/test`);

// ---- helpers -----------------------------------------------------------------------------------

/** "1234••••9012" (DESIGN 4.7: the account id is screen-share sensitive, masked by default). */
export function maskAccount(account: string | null | undefined): string {
  if (!account) return "—";
  return account.length <= 8 ? "••••" : `${account.slice(0, 4)}••••${account.slice(-4)}`;
}

/** A lab-friendly model key from a Bedrock model name: "Nova Pro" -> "nova-pro". */
export function modelKey(name: string): string {
  return (
    name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 40) || "bedrock-model"
  );
}

/** Test-id key for doctor rows ("  image input" -> "image-input"). */
export const doctorKey = (item: string) =>
  item
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9.]+/g, "-")
    .replace(/^-+|-+$/g, "");
