// Overview API client (owner: Overview area). Endpoint: API.md section 3, GET /api/overview.
import { api } from "@/lib/api";
import type { Overview } from "./types";

export const overviewKeys = { all: ["overview"] as const };

export const getOverview = () => api.get<Overview>("/api/overview");
