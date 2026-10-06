// Wire types transcribed from docs/ui/API.md (the single source of truth for shapes).
// When API.md changes, change this file in the same commit. Area clients live in ./<area>.ts.

// ---- 2. Conventions ------------------------------------------------------------------------------

export type ErrorCode =
  | "unauthorized" | "forbidden_origin" | "misdirected" | "not_found" | "bad_request"
  | "path_outside_workspace" | "not_a_lab" | "lab_invalid" | "dir_not_empty" | "file_exists"
  | "etag_mismatch" | "precondition_required" | "too_large" | "confirm_mismatch" | "run_blocked"
  | "job_active" | "job_not_active" | "too_few_dev_rows" | "source_invalid" | "unpinned_source"
  | "suite_unavailable" | "rows_invalid" | "upload_not_found" | "secret_placeholder_unknown"
  | "credential_in_source" | "no_shared_rows" | "use_jevbench_endpoint" | "unsupported_media"
  | "name_taken" | "backend_unavailable" | "cloud_error" | "cloud_timeout" | "internal"
  | "network" | (string & {});

export type ApiErrorBody = {
  error: { code: ErrorCode; message: string; hint?: string; detail?: Record<string, unknown>; request_id: string };
};
export type Page<T> = { items: T[]; total: number; offset: number; limit: number };
export type Items<T> = { items: T[] };
export type Backend = "local" | "ssh" | "aws" | "vast";
export type CI = [number | null, number | null];
export type Verdict = "better" | "worse" | "unclear";
export type Diff = { diff: number | null; ci95: CI; verdict?: Verdict };

// ---- 3. Meta and overview ------------------------------------------------------------------------

export type Health = { ok: boolean };
export type Meta = {
  studio_version: string;
  decider_lab_version: string;
  studio_build?: string | null;
  python: string;
  platform: string;
  workspace: string;
  state_dir: string;
  cache_dir: string;
  fake_cloud: boolean;
  features: { vast_cli: boolean; boto3: boolean; strands_decider: boolean; heldout_extra: boolean;
              chess_extra: boolean; nvidia_smi: boolean };
  server_time: string;
};

export type RecentResult = {
  root_id: string; lab: string; model: string; suite: string; calibrated: boolean;
  status: "ok" | "failed"; message?: string | null;
  intelligence: number | null; ci95: CI | null; accuracy: number | null; errors: number | null; n: number | null;
  finished_at: string | null;
};
export type Overview = {
  kpis: {
    labs: number; labs_invalid: number; runs_scored: number; runs_with_errors: number;
    best: { root_id: string; lab: string; model: string; suite: string; intelligence: number; ci95: CI;
            tied_with: number; suite_rows: number } | null;
    jobs_active: number; jobs_active_remote: number;
    cloud: { instances: number; idle_instances: number; usd_per_hour: number | null; known: boolean; errors: string[] };
  };
  active_jobs: JobSummary[];
  recent_results: RecentResult[];
  onboarding: { doctor_seen: boolean; has_lab: boolean; has_run: boolean; has_results: boolean; dismissed: boolean };
};

// ---- 4. Settings and environment ------------------------------------------------------------------

export type ThemePref = "system" | "dark" | "light";
export type Settings = {
  theme: ThemePref; density: "comfortable" | "compact"; reduce_motion: "system" | "on";
  max_concurrent_jobs: number; require_pinned_default: boolean; require_sha256: boolean;
  default_backend: Backend; runs_dir: string | null; onboarding_dismissed: boolean;
};
export type EnvVar = { name: string; set: boolean; referenced_by: string[]; purpose: string };
export type About = {
  decider_lab_version: string; studio_build: string | null; python: string; strands_decider: string | null;
  license: string; cache_dir: string; state_dir: string; telemetry: "none";
};

// ---- 5. Labs -----------------------------------------------------------------------------------

export type ModelKind = "serve" | "url" | "bedrock" | "strands" | "chat" | "python" | "baseline" | "finetuned" | "unknown";
export type Problem = {
  severity: "error" | "warning"; code: string; message: string;
  line: number | null; column: number | null; path: string | null;
};
export type LabSummary = {
  name: string;
  workers: number;
  models: {
    name: string; kind: ModelKind; spec_text: string;
    source_kind?: "hf" | "s3" | "url" | "local"; pinned?: boolean;
    needs_gpu?: boolean; vision?: boolean; is_baseline: boolean; jevbench: boolean; paid_api: boolean;
    env_refs: string[]; warnings: string[];
  }[];
  suites: { ref: string; label: string; rows_estimate: number | null; has_splits: boolean | null }[];
  finetune: { name: string; from: string | null; base_model: string | null; train: string[]; steps: number | null }[];
  calibrate: boolean;
  baseline: string | null;
  jevbench: string[];
  compute: { backend: Backend; max_hours: number; options: Record<string, unknown> };
  plan: { runs: number; calibrated_runs_max: number; requests_estimate: number | null };
};
export type LabListItem = {
  lab_id: string; name: string; path: string; models: number; finetune: number; suites: number;
  backend: Backend; valid: boolean; errors: number; warnings: number;
  last_run_at: string | null; modified_at: string;
};
export type Template = { id: "eval" | "finetune"; title: string; description: string; lab_yaml: string };
export type Lab = {
  lab_id: string; name: string; path: string; dir: string; yaml: string; secrets_masked: number;
  etag: string; modified_at: string; valid: boolean; problems: Problem[]; summary: LabSummary | null;
  run_root_id: string | null; command: string; warning?: string;
};
export type Validation = { valid: boolean; problems: Problem[]; summary: LabSummary | null };

export type RunOptions = {
  backend: Backend; only: string[] | null; limit: number | null; max_hours: number;
  env: string[]; keep: boolean; fast_kernels: boolean; strands_decider: string | null; out: string | null;
  options: Record<string, string | number | null>;
};
export type Estimate = {
  backend: Backend; can_start: boolean; blockers: string[]; warnings: string[];
  plan: { models: string[]; suites: string[]; runs: number; calibrated_runs_max: number; requests_estimate: number | null };
  cost: { billable: boolean; rate_usd_per_hour: number | null; rate_source: string | null; cap_usd_per_hour: number | null;
          max_hours: number; cap_usd: number | null; credit_usd: number | null; note: string | null };
  resume: { root: string; exists: boolean; rows_reused: number | null };
  paid_models: { model: string; kind: string; provider: string; requests_estimate: number | null }[];
  confirm_phrase: string | null; command: string;
};

// ---- 6. Jobs -------------------------------------------------------------------------------------

export type JobKind = "run" | "eval" | "pull" | "jevbench" | "calibrate" | "suite_build";
export type JobStatus = "queued" | "running" | "cancelling" | "succeeded" | "partial" | "failed" | "cancelled" | "lost";
export const ACTIVE_STATUSES: JobStatus[] = ["queued", "running", "cancelling"];
export type StageStatus = "pending" | "active" | "done" | "failed" | "skipped" | "warning";
export type Stage = { name: string; label: string; status: StageStatus; started_at: string | null;
                      ended_at: string | null; detail: string | null };
export type RunProgress = {
  model: string; suite: string; status: "pending" | "running" | "done" | "failed" | "skipped";
  done: number; total: number | null; errors: number; rows_per_s: number | null;
  intelligence: number | null; ci95: CI | null; accuracy: number | null; message: string | null;
  reused?: number | null;
};
export type JobProgress = {
  fraction: number | null; label: string; runs: RunProgress[];
  bytes: { done: number; total: number | null } | null;
  finetune: { model: string; step: number | null; total_steps: number | null; loss: number | null;
              status: "pending" | "running" | "done" | "failed" }[] | null;
  machine: { provider: "vast" | "aws" | "ssh"; id: string | null; target: string | null; gpu: string | null;
             usd_per_hour: number | null; cost_so_far_usd: number | null } | null;
};
export type JobSummary = {
  job_id: string; kind: JobKind; title: string; status: JobStatus; backend: Backend; lab_id: string | null;
  created_at: string; started_at: string | null; ended_at: string | null; duration_s: number | null;
  progress: { fraction: number | null; label: string }; failure_count: number; root_id: string | null;
  queue_position: number | null; cost_so_far_usd: number | null; machine_id: string | null;
};
export type Job = Omit<JobSummary, "progress"> & {
  argv: string[]; cwd: string; env_names: string[]; exit_code: number | null; stages: Stage[];
  progress: JobProgress; failures: string[]; result: Record<string, unknown> | null;
  log: { lines: number; bytes: number };
  telemetry: { source: "local-nvidia-smi" | "remote-ssh" | "simulated" | "none"; reason: string | null };
  options: Record<string, unknown>; command?: string;
};
export type JobStartBody =
  | ({ kind: "run"; lab_id: string; confirm?: string } & RunOptions)
  | { kind: "eval"; model: { type: "url" | "baseline" | "serve" | "python"; value: string }; name: string; suite: string;
      split: string | null; limit: number | null; workers: number; vision: boolean; out: string | null }
  | { kind: "pull"; source: string; revision: string | null; sha256: string | null; require_pinned: boolean;
      profile: string | null; region: string | null }
  | { kind: "jevbench"; url: string; label: string; out: string | null }
  | { kind: "calibrate"; root_id: string; model: string; suite: string; out: string | null }
  | { kind: "suite_build"; suite: string };
export type JobStarted = { job_id: string; status: JobStatus; title: string; command: string };
export type LogLevel = "info" | "warn" | "error";
export type LogLine = { seq: number; ts: string; text: string; level: LogLevel };
export type LogPage = { lines: LogLine[]; next_offset: number; total: number; truncated: boolean };
export type TelemetrySample = {
  ts: string;
  gpus: { index: number; util_pct: number | null; mem_used_gb: number | null; temp_c: number | null; power_w: number | null }[];
  rows_per_s: number | null; errors_total: number | null; train_loss?: number | null;
};
export type Telemetry = {
  source: Job["telemetry"]["source"]; reason: string | null; interval_s: number;
  gpus: { index: number; name: string; memory_total_gb: number; power_limit_w: number | null }[];
  samples: TelemetrySample[];
};
export type JobEvent =
  | { event: "snapshot"; data: { job: Job } }
  | { event: "log"; data: LogLine }
  | { event: "stage"; data: { stage: Stage } }
  | { event: "progress"; data: { progress: JobProgress } }
  | { event: "telemetry"; data: { sample: TelemetrySample } }
  | { event: "status"; data: { status: JobStatus; exit_code: number | null; ended_at: string | null; result: Record<string, unknown> | null } }
  | { event: "end"; data: Record<string, never> };
export type GlobalEvent =
  | { event: "job.created" | "job.updated" | "job.finished"; data: JobSummary }
  | { event: "labs.changed"; data: { lab_ids: string[] } }
  | { event: "runs.changed"; data: { root_ids: string[] } }
  | { event: "models.changed"; data: Record<string, never> };

// ---- 7. Results ----------------------------------------------------------------------------------

export type QuestionKind = "noul" | "choice" | "score";
export type Scores = {
  n: number; errors: number; intelligence: number | null; intelligence_ci95?: CI;
  accuracy: number | null; nll: number | null; ece: number | null; scored_split: string;
  by_kind: Record<QuestionKind, { n: number; competence: number | null; accuracy: number | null; nll: number | null;
                                  brier: number | null; ece: number | null; in_band?: number | null; rps?: number | null }>;
  by_family: Record<string, { n: number; intelligence: number | null; accuracy: number | null }>;
  latency_s?: { median: number; p95: number };
};
export type RunRef = { root_id: string; model: string; suite: string };
export type RunRootSummary = {
  root_id: string; lab: string; lab_id: string | null; path: string; models: string[]; suites: string[];
  has_calibrated: boolean; has_jevbench: boolean; baseline: string | null; failures: string[];
  wall_s: number | null; finished_at: string | null;
  best: { suite: string; model: string; intelligence: number; ci95: CI } | null;
};
export type RunRootDetail = {
  root_id: string; lab: string; lab_id: string | null; path: string; title: string; baseline: string | null;
  lab_json: Record<string, unknown> | null;
  models: { name: string; color_index: number | null; is_baseline: boolean; kind: ModelKind }[];
  suites: { name: string; has_calibrated: boolean; scored_split: string; models: string[] }[];
  jevbench_models: string[]; failures: string[]; report_md_available: boolean; finished_at: string | null;
};
export type LeaderboardRow = {
  model: string; variant: "raw" | "cal"; status: "ok" | "failed" | "missing";
  intelligence: number | null; ci95: CI | null; accuracy: number | null; nll: number | null; ece: number | null;
  noul_in_band: number | null; errors: number | null; n: number | null;
  latency_s: { median: number; p95: number } | null; tie_group: number | null;
  temperatures: Record<QuestionKind, number> | null; message: string | null;
};
export type Leaderboard = {
  suite: string; scored_split: string; baseline: string | null; proxy_note: string; rows: LeaderboardRow[];
  domain: [number, number];
  same_rows: { consistent: boolean; n_by_model: Record<string, number>; sha256_by_model: Record<string, string>; limited: string[] };
};
export type RunDetail = {
  ref: RunRef; scores: Scores; run: Record<string, unknown>; source: Record<string, unknown> | null;
  calibration: Record<string, unknown> | null; has_calibrated: boolean;
  errors_sample: { id: string; error: string }[];
  files: { predictions: string; scores: string; run: string };
};
export type Prediction = {
  id: string; task: string; kind: QuestionKind; split: string | null; label: number; n: number;
  probs: number[] | null; latency_s: number | null; error: string | null;
  top: number | null; correct: boolean | null; proxy_right: boolean | null; in_band: boolean | null;
  expected_level: number | null;
};
export type ReliabilityBin = { lo: number; hi: number; n: number; mean_conf: number; accuracy: number };
export type Reliability = {
  kind: "all" | QuestionKind; bins: ReliabilityBin[]; ece: number; n: number;
  calibrated: { bins: ReliabilityBin[]; ece: number; n: number } | null;
};
export type LatencyDetail = { p50: number; p95: number; n: number; errors: number;
                              histogram: { lo: number; hi: number; ok: number; failed: number }[] };
export type VsBaseline = {
  suite: string; baseline: string | null; split?: string;
  items: { model: string; n_paired: number; only_a: number; only_b: number;
           intelligence: Diff; accuracy: Diff; nll: Diff;
           verdict: { intelligence: Verdict; accuracy: Verdict; nll: Verdict } }[];
};
export type Families = { suite: string; families: string[]; models: string[];
                         cells: { family: string; model: string; n: number; intelligence: number | null; accuracy: number | null }[] };
export type JevBenchItem = {
  model: string; intelligence_proxy: number; n_correct: number; tasks: number;
  competence_by_type: Record<QuestionKind, number>; yes_no_in_band: number; yes_no: number;
  jevbench_commit: string; note: string;
};
export type RowAnswer = { top: number | null; top_name: string | null; p_top: number | null; p_gold: number | null;
                          proxy_right: boolean | null; in_band: boolean | null; error: string | null };
export type RowsPage = Page<{
  id: string; task: string; kind: QuestionKind; split: string | null; label: number; gold_name: string | null;
  state_excerpt: string | null; instructions_excerpt: string | null; answers: Record<string, RowAnswer | null>;
}> & { models: string[]; content_available?: boolean; content_reason?: string };
export type Compare = {
  a: RunRef & { label: string }; b: RunRef & { label: string }; split: string; same_suite_sha256: boolean;
  n_paired: number; only_a: number; only_b: number; intelligence: Diff; accuracy: Diff; nll: Diff;
  bootstrap: number; has_splits: boolean; notes: string[];
};

// ---- 8. Models -----------------------------------------------------------------------------------

export type CachedModel = {
  model_key: string; source: string; kind: "hf" | "s3" | "url" | "local"; ref: string | null;
  ref_kind: "commit" | "sha256" | "etags" | "none"; pinned: boolean; size_gb: number; dir: string;
  pulled_at: string; used_by_labs: string[]; in_use_by_job: string | null;
};
export type ModelsList = { cache_dir: string; total_gb: number; items: CachedModel[]; hf_cache_note: string };
export type Inspect = {
  kind: "hf" | "s3" | "url" | "local"; normalized: string; pinned: boolean; cached: boolean; model_key: string | null;
  problems: Problem[]; needs: { env: string[]; profile: boolean }; command: string;
  cached_info?: { model_key: string; pulled_at: string; ref: string | null };
};

// ---- 9. Data and suites --------------------------------------------------------------------------

export type BuiltinSuite = {
  ref: string; name: string; description: string; params: Record<string, unknown>;
  param_schema: Record<string, { type: "int" | "multi"; min?: number; max?: number; options?: string[];
                                 unavailable?: Record<string, string> }>;
  available: boolean; reason: string | null; rows_estimate: number | null; cached: boolean;
};
export type SuiteFile = { file_id: string; ref: string; path: string; rows: number | null; valid: boolean | null;
                          problem: string | null; modified_at: string; size_bytes: number };
export type Suites = { builtins: BuiltinSuite[]; files: SuiteFile[] };
export type SuiteStats = {
  ref: string; name: string; params: Record<string, unknown>; sha256: string; rows: number;
  by_kind: Record<string, number>; by_task: Record<string, number>; by_split: Record<string, number>;
  label_balance: Record<string, Record<string, number>>; with_images: number;
};

// ---- 10. Compute ---------------------------------------------------------------------------------

export type DoctorCheck = { status: "ok" | "warn" | "info"; item: string; detail: string };
export type Doctor = {
  checks: DoctorCheck[];
  machine: { os: string; python: string; cpus: number | null; memory_gb: number | null; accelerator: string;
             gpus: { index: number; name: string; memory_total_gb: number }[] };
  counts: { ok: number; warn: number; info: number };
  fake?: boolean;
};
export type VastStatus = { fake: boolean; cli: boolean; api_key: boolean; credit_usd: number | null; as_of: string | null; error: string | null };
export type VastOffer = { id: number; gpu_name: string; num_gpus: number; dph_total: number; gpu_ram_gb: number;
                          cuda_max_good: number; reliability: number; geolocation: string; inet_down_mbps: number;
                          disk_space_gb: number };
export type VastInstance = { id: number; label: string; status: string; gpu: string; dph_total: number; started_at: string;
                             uptime_s: number; ssh: string | null; job_id: string | null; idle: boolean; cost_so_far_usd: number };
export type AwsIdentity = { fake: boolean; profile: string; region: string; account: string | null; arn: string | null;
                            ok: boolean; error: string | null };
export type AwsQuota = { family: string; code: string; name: string; limit_vcpus: number | null; used_vcpus: number | null; error?: string };
export type AwsInstance = { id: string; type: string; state: string; lab: string | null; launched_at: string; uptime_s: number;
                            usd_per_hour: number | null; public_ip_masked: string | null; job_id: string | null; idle: boolean };
export type BedrockModel = { model_id: string; invoke_id: string; name: string; provider: string;
                             input_modalities: string[]; output_modalities: string[]; status: string; spec_yaml: string };
export type SshHost = {
  host_id: string; name: string; target: string; user: string; address: string; port: number; key_path: string;
  key_exists: boolean; work_dir: string | null;
  last_test: { at: string; ok: boolean; latency_ms: number | null; gpu: string | null; error: string | null } | null;
};
