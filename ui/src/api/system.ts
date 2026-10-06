// System API client (owner: foundation). Endpoints: API.md sections 3, 4, 10 (doctor).
import { api } from "@/lib/api";
import type { About, Doctor, EnvVar, Health, Items, Meta, Settings } from "./types";

export const systemKeys = {
  meta: ["system", "meta"] as const,
  doctor: ["system", "doctor"] as const,
  about: ["system", "about"] as const,
  settings: ["system", "settings"] as const,
  env: (names?: string[]) => ["system", "env", names ?? []] as const,
};

export const getHealth = () => api.get<Health>("/api/health");
export const getMeta = () => api.get<Meta>("/api/meta");
export const getDoctor = () => api.get<Doctor>("/api/system/doctor");
export const getAbout = () => api.get<About>("/api/about");
export const getSettings = () => api.get<Settings>("/api/settings");
export const putSettings = (patch: Partial<Settings>) => api.put<Settings>("/api/settings", patch);
export const getEnv = (names?: string[]) => api.get<Items<EnvVar>>("/api/settings/env", { query: { names } });
