// Compute API client (owner: Compute area). Endpoints: API.md section 10.
// Doctor: GET /api/system/doctor (see ./system.ts) or /api/compute/doctor once the area adds it.
import { api } from "@/lib/api";
import type { AwsIdentity, Items, SshHost, VastInstance, VastStatus } from "./types";

export const computeKeys = {
  vastStatus: ["compute", "vast", "status"] as const,
  vastInstances: ["compute", "vast", "instances"] as const,
  awsIdentity: (profile?: string, region?: string) => ["compute", "aws", "identity", profile, region] as const,
  sshHosts: ["compute", "ssh", "hosts"] as const,
};

export const getVastStatus = () => api.get<VastStatus>("/api/compute/vast/status");
export const listVastInstances = () => api.get<Items<VastInstance> & { fake: boolean }>("/api/compute/vast/instances");
export const getAwsIdentity = (profile?: string, region?: string) =>
  api.get<AwsIdentity>("/api/compute/aws/identity", { query: { profile, region } });
export const listSshHosts = () => api.get<Items<SshHost>>("/api/compute/ssh/hosts");
// TODO(compute): offers, destroy/terminate (typed confirm), quotas, instance types, bedrock models, ssh CRUD/test.
