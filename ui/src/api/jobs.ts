// Jobs API client (owner: Jobs area). Endpoints: API.md section 6.
import { api, openEventSource } from "@/lib/api";
import type { Items, Job, JobStartBody, JobStarted, JobSummary, LogPage, Telemetry } from "./types";

export type JobListQuery = { status?: "active" | "finished" | string; kind?: string; lab_id?: string; q?: string; limit?: number };

export const jobKeys = {
  all: ["jobs"] as const,
  list: (q?: JobListQuery) => ["jobs", "list", q ?? {}] as const,
  one: (jobId: string) => ["jobs", "one", jobId] as const,
  log: (jobId: string) => ["jobs", "log", jobId] as const,
  telemetry: (jobId: string) => ["jobs", "telemetry", jobId] as const,
};

export const listJobs = (q?: JobListQuery) => api.get<Items<JobSummary>>("/api/jobs", { query: q });
export const getJob = (jobId: string) => api.get<Job>(`/api/jobs/${jobId}`);
export const startJob = (body: JobStartBody) => api.post<JobStarted>("/api/jobs", body);
export const cancelJob = (jobId: string) => api.post<{ status: string }>(`/api/jobs/${jobId}/cancel`);
export const getJobLog = (jobId: string, q?: { offset?: number; limit?: number; q?: string; level?: string }) =>
  api.get<LogPage>(`/api/jobs/${jobId}/log`, { query: q });
export const getTelemetry = (jobId: string) => api.get<Telemetry>(`/api/jobs/${jobId}/telemetry`);
/** SSE: snapshot, log, stage, progress, telemetry, status, end (see JobEvent in types.ts). */
export const jobEvents = (jobId: string, q?: { from?: 0; last_event_id?: number }) =>
  openEventSource(`/api/jobs/${jobId}/events`, q);
/** SSE: job.created/updated/finished, labs.changed, runs.changed, models.changed. */
export const globalEvents = () => openEventSource("/api/events");
// TODO(jobs): delete (typed `delete`), log.txt download link via fileUrl().
