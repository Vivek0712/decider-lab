# decider-lab Studio: REST + SSE contract

Status: v1 contract. Companion: [DESIGN.md](DESIGN.md). This file is the single source of truth
for the wire format between the Studio server (`src/decider_lab/ui/`) and the SPA (`ui/`).
`ui/src/api/types.ts` is a transcription of the shapes here; backend tests assert them.

Contents

1. Server, auth and transport
2. Conventions (ids, errors, pagination, typed confirmation, secrets, fake cloud)
3. Meta and overview
4. Settings and environment
5. Labs
6. Jobs (REST + SSE)
7. Results (run roots, runs, predictions, charts data, compare, export)
8. Models (cache, inspect, pull)
9. Data and suites
10. Compute (doctor, vast, aws, ssh)
11. Shared types (TypeScript)
12. Error codes
13. Endpoint index

---

## 1. Server, auth and transport

### 1.1 Starting

```
decider-lab ui [--workspace DIR] [--port 8765] [--host 127.0.0.1] [--no-browser] [--token TOKEN]
```

- `--host` accepts only loopback (`127.0.0.1`, `::1`, `localhost`); anything else exits 2 with
  "Studio binds to loopback only". Port 0 picks a free port. If the port is taken, exit 2 with
  the message (no silent fallback).
- Token: `--token`, else env `DECIDER_LAB_UI_TOKEN`, else `secrets.token_urlsafe(32)`. Printed
  once to stdout as `http://127.0.0.1:8765/?token=<token>` and opened in the browser unless
  `--no-browser`. The token is never written to disk or logged elsewhere.
- Needs the extra: `pip install 'decider-lab[ui]'` (fastapi, uvicorn, python-multipart, sse-starlette
  optional). Without it, `decider-lab ui` exits 2 naming the extra.
- State directory: `<workspace>/.decider-lab-studio/` (created mode 700): `settings.json`,
  `ssh_hosts.json`, `jobs/<job_id>/{job.json,log.txt,telemetry.jsonl,progress.json}`. Studio
  adds the directory to the workspace's `.gitignore` if one exists (never creates one), so job
  logs are not committed by accident.
- Shutdown: on SIGINT/SIGTERM with active jobs, the server stops accepting requests, cancels
  every active job exactly as `POST /api/jobs/:id/cancel` does (SIGTERM to the process group; the
  CLI releases remote machines), waits up to 60 s, and prints a line per job. A second SIGINT
  exits at once and prints `WARNING: these machines may still be running: vast 9876543, aws
  i-0abc…` (ids parsed from the job logs). Jobs never outlive the server silently.

### 1.2 Auth

- `GET /?token=<t>`: if `t` matches (constant-time compare), sets cookie
  `dl_session=<session>` (`HttpOnly; SameSite=Strict; Path=/`; no `Secure` since it is http on
  loopback) and redirects 302 to `/` without the query. Session = HMAC of the token, so restarting
  with the same token keeps sessions valid; a new token invalidates them.
- Every `/api/*` route except `GET /api/health` requires either the cookie or
  `Authorization: Bearer <token>` (for scripts and tests). Missing/invalid → `401
  {"error":{"code":"unauthorized",…}}`.
- SSE (`EventSource`) uses the cookie (same-origin).
- Static assets (`/assets/*`, `/index.html`) are served without auth (they contain no data).
- Security headers on every response: `Content-Security-Policy: default-src 'self'; img-src 'self'
  data:; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'`,
  `Referrer-Policy: no-referrer` (so the token in the first URL never leaks through a Referer),
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`.
- The token is always in the redaction set (2.5), whatever its environment variable name.
- CSRF/DNS-rebinding: requests whose `Host` header is not `127.0.0.1:<port>`, `localhost:<port>`
  or `[::1]:<port>` → `421 misdirected`. Non-GET requests must carry `X-Studio: 1` (the SPA
  client always sends it) and, if `Origin` is present, it must equal the server origin; else
  `403 forbidden_origin`. No CORS headers are ever sent.

### 1.3 Transport

- JSON bodies `application/json; charset=utf-8`; uploads `multipart/form-data`.
- Every response has `X-Request-Id` (uuid4 hex); errors repeat it as `error.request_id`.
- Timestamps: ISO 8601 UTC with `Z` (`"2026-10-06T10:42:01.512Z"`). Durations: seconds (float).
- Money: USD floats, field names end in `_usd` or `_usd_per_hour`.
- Caching: `GET` responses have `Cache-Control: no-store` except `/assets/*` (immutable, hashed).
- Server-sent events: `text/event-stream`, a `: ping` comment every 15 s, `id:` on every
  data event; clients reconnect with `Last-Event-ID`.

---

## 2. Conventions

### 2.1 Ids

| id | form | example |
|---|---|---|
| `lab_id` | base64url (no padding) of the workspace-relative lab file path | `bGFicy9maXJzdC9sYWIueWFtbA` (`labs/first/lab.yaml`) |
| `root_id` | base64url of the workspace-relative run root path | `bGFicy9maXJzdC9ydW5zL2ZpcnN0LWxhYg` |
| `file_id` | base64url of a workspace-relative data file path | |
| `job_id` | `j_` + 12 lowercase hex | `j_3f9a1c22be07` |
| `host_id` | `h_` + 8 hex | `h_91ab02cd` |
| `model_key` | the cache directory name under `~/.cache/decider-lab/models/` | `9a1f03be2c7d4e11` |
| suite ref | built-in name, built-in with params `synthetic:per_kind=100,seed=3`, or `file:<file_id>` | |

Ids are opaque to the frontend. Path params containing `+` or `:` (suite names like
`synthetic+cal`) are percent-encoded by the client (`synthetic%2Bcal`). The server rejects any
id that decodes to a path outside the workspace (`400 path_outside_workspace`).

### 2.2 Errors

All non-2xx responses:

```json
{
  "error": {
    "code": "lab_invalid",
    "message": "lab.yaml has 1 problem.",
    "hint": "Open the editor to fix it.",
    "detail": {"problems": [{"line": 14, "column": 11, "severity": "error",
                             "message": "baseline `majorty` is not in models. Did you mean `majority`?",
                             "path": "baseline"}]},
    "request_id": "6c1d2e…"
  }
}
```

- `message`: one human sentence (DESIGN.md section 10), safe to show. `hint`: optional fix.
  `detail`: optional, code-specific object (documented with each code in section 12).
- Validation errors from request parsing: `422 bad_request` with
  `detail.fields: [{"field":"max_hours","message":"must be > 0"}]`.
- Unexpected exceptions: `500 internal` with a generic message; the traceback goes to the server
  log only (redacted).

### 2.3 Pagination and filtering

List endpoints that can be large take `offset` (default 0) and `limit` (default 50, max 500) and
return:

```json
{"items": [...], "total": 1204, "offset": 0, "limit": 50}
```

Small lists (labs, jobs, run roots, hosts, models) return `{"items": [...]}` without paging.

### 2.4 Typed confirmation

Actions that spend money or destroy something require a `confirm` string in the body that equals
a phrase the server defines. The server is the enforcement point; the UI only collects the text.

| action | phrase |
|---|---|
| remote run on `vast` / `aws` | from the estimate: `spend <cap with 2 decimals> on <backend>`, e.g. `spend 1.60 on vast` |
| destroy one vast instance | the instance id, e.g. `9876543` |
| destroy all vast instances | `destroy all` |
| terminate one aws instance | the instance id, e.g. `i-0abc123def4567890` |
| terminate all aws instances | `terminate all` |
| delete a cached model | first 8 chars of its ref (commit or sha256), or of `model_key` when it has neither |
| delete a lab file | the lab name |
| delete a finished job record | `delete` |
| delete a run root's results | `delete <lab name>` (the run root's title for an eval root) |
| run (any backend) whose selected models include a paid API model (`bedrock`, `strands`, or `chat` with a non-loopback `url`) | `call paid apis` (local/ssh); on vast/aws the cloud phrase is used and the estimate lists the paid models |
| remote run on `vast` / `aws` with `keep: true` | `spend <cap> on <backend> and keep the machine` |

Wrong or missing → `409 confirm_mismatch` with `detail.expected_hint` (e.g. "type the instance
id"; the phrase itself is returned for runs only, by the estimate endpoint). Estimates are not
cached server-side: `POST /api/jobs` recomputes the cap from the submitted options and checks the
phrase against it, so changing options after confirming fails safely.

### 2.5 Secrets

- The API never returns a secret value, including values the person typed into their own
  files. Environment variables are reported as `{"name":"HF_TOKEN","set":true}`. AWS credentials
  are referenced by profile name or by the `*_env` variable names in a lab; key material is
  never read by Studio code (boto3 reads it). SSH private keys are referenced by path; Studio
  only checks that the path exists.
- Lab YAML validation flags literal secrets (keys `api_key`, `aws_access_key_id`,
  `aws_secret_access_key`, `aws_session_token`, `token`, `password`, `secret`; values matching
  `AKIA[0-9A-Z]{16}`, `ASIA[0-9A-Z]{16}`, `hf_[A-Za-z0-9]{30,}`, `sk-[A-Za-z0-9]{20,}`) as
  problems with code `secret_literal`.
- Masked secrets in lab YAML: `GET /api/labs/:id`, `PUT` responses and every other response that
  carries lab text replace each secret literal's value with the placeholder `"••••"` (the line
  and column are kept, so problems still point at it) and report `secrets_masked: n`. On `PUT`,
  a `"••••"` value at a key path that held a secret in the file on disk is restored from disk
  before writing; a placeholder at any other path → `422 secret_placeholder_unknown` (nothing is
  written). `POST /api/labs/validate` treats the placeholder as a present secret literal. The
  ETag is computed over the real file bytes. `POST /api/labs/:id/fix-secret` (5) moves a
  literal to an `*_env` reference. Literal values are never logged.
- Redaction (`redact.py`, owned by A, used by every route that returns file content or logs):
  replaces with `••••` (a) the values of every environment variable whose name matches
  `(?i)(token|secret|key|password|credential|session)` and whose value is ≥ 8 chars, (b) the
  patterns above, (c) presigned URL query strings (`X-Amz-Signature=…`, `X-Amz-Credential=…`,
  `X-Amz-Security-Token=…` → value `••••`), (d) `Authorization:` header values, (e) URL user
  info (`https://user:pass@host` → `https://••••@host`, also in `git+https://` pip specs such as
  `strands_decider`), (f) the Studio token. Applied to job logs before they are written to
  `log.txt` and before SSE; to every string field of `job.json` (`title`, `argv`, `command`,
  `options`, `failures`, stage `detail`, `progress.runs[].message`); to
  `run.json`/`source.json`/`lab.json`/`calibration.json` content; to `error` strings from
  `predictions.jsonl` (rows, row detail, predictions, CSV exports); to cached model `source`
  strings (`GET /api/models`, `inspect.normalized`); to doctor details; and to server logs.
- Server-side search (`GET /api/jobs/:id/log?q=`, row search `q`) runs on redacted text only, so
  a query cannot probe whether a secret value occurs in a log.
- Model sources that carry a credential (presigned query strings, URL user info) are refused by
  `inspect` and `pull` with `422 credential_in_source`: they would otherwise be persisted in
  `source.json`, cache metadata and provenance.
- Job `env` lists names only; values pass from the server's environment to the subprocess and
  are never part of a request or response.

### 2.6 Fake cloud

When the server's environment has `DECIDER_LAB_FAKE_CLOUD=1`:

- `GET /api/meta` → `fake_cloud: true`.
- `/api/compute/vast/*` and `/api/compute/aws/*` read and mutate in-memory fixtures loaded from
  `src/decider_lab/ui/fakes/{vast,aws}.json` at start (destroy/terminate remove items until the
  server restarts). No `vastai` or boto3 call is made. The fixtures include one running vast
  instance and one running aws instance that no job owns (`idle: true`), so the idle-machine
  warnings are testable.
- `POST /api/jobs` with `kind:"run"` and `backend` `vast`/`aws` runs a simulator: it emits the
  remote stage log lines with short sleeps (`check` 0.2 s, `acquire` 1 s, `copy` 0.3 s,
  `bootstrap` 1 s), then runs the real `decider-lab run <lab> --on local` subprocess (prefixed
  `  | ` like a remote log), then `fetch` and `release` lines; telemetry is synthetic
  (`source:"simulated"`). Results are real local results. `ssh` runs: the simulator also covers
  ssh (stages acquire→ "ssh ok").
- SSH host test: succeeds with `latency_ms: 42` and `gpu: "1x NVIDIA L40S, 46068 MiB"` for any
  host, except hosts whose address ends in `.invalid` → `ok:false, error:"ssh: connect to host
  … port 22: Operation timed out"`.
- `s3://` model pulls and `inspect` are allowed to return `backend_unavailable` in fake mode;
  tests use `https://127.0.0.1:<port>` fixture servers or local directories instead.
- Fake-mode cost estimates use the fixtures' offers and the static AWS price table.
- Paid API models (`bedrock`, `strands`, remote `chat`) still need the `call paid apis` phrase in
  fake mode (the rule is about the lab, not the backend); e2e tests use a lab whose `bedrock`
  model points at a loopback stub via the SDK's test hooks, or only assert the confirmation
  gate and cancel before any request.

---

## 3. Meta and overview

### `GET /api/health` (no auth)

```json
{"ok": true}
```

### `GET /api/meta`

```json
{
  "studio_version": "0.1.0",
  "decider_lab_version": "0.1.0",
  "python": "3.12.6",
  "platform": "macOS-15.6-arm64",
  "workspace": "/Users/me/labs",
  "state_dir": "/Users/me/labs/.decider-lab-studio",
  "cache_dir": "/Users/me/.cache/decider-lab",
  "fake_cloud": false,
  "features": {"vast_cli": true, "boto3": true, "strands_decider": false, "heldout_extra": false,
               "chess_extra": false, "nvidia_smi": false},
  "server_time": "2026-10-06T10:42:01.512Z"
}
```

### `GET /api/overview`

```json
{
  "kpis": {
    "labs": 4, "labs_invalid": 1,
    "runs_scored": 38, "runs_with_errors": 3,
    "best": {"root_id": "…", "lab": "first-lab", "model": "v19", "suite": "synthetic",
             "intelligence": 61.4, "ci95": [57.9, 64.8]},
    "jobs_active": 2, "jobs_active_remote": 1,
    "cloud": {"instances": 1, "idle_instances": 1, "usd_per_hour": 0.612, "known": true,
              "errors": []}
  },
  "active_jobs": [ /* JobSummary, max 5 */ ],
  "recent_results": [
    {"root_id": "…", "lab": "first-lab", "model": "v19", "suite": "synthetic", "calibrated": false,
     "intelligence": 61.4, "ci95": [57.9, 64.8], "accuracy": 78.2, "errors": 0, "n": 540,
     "finished_at": "2026-10-06T08:40:00Z"}
  ],
  "onboarding": {"doctor_seen": true, "has_lab": true, "has_run": false, "has_results": false,
                 "dismissed": false}
}
```

- `best`: highest `intelligence` among non-calibrated, non-baseline runs **on one suite**: the
  base suite (not `smoke`) of the most recently finished scored run; ties broken by most
  recent; `null` if none. Extra fields: `"tied_with": 2` (other runs on that suite whose CI
  overlaps the top run's CI) and `"suite_rows": 540`. The UI labels it "Top on <suite>", never
  "best" across suites.
- `cloud.known: false` when no cloud backend is usable (then `usd_per_hour: null`). Cloud reads
  are cached 60 s and time out at 5 s so the overview never waits on a cloud. A provider whose
  read failed is listed in `errors` (`["vast: timeout"]`) and makes `usd_per_hour` `null`
  rather than a partial sum. `idle_instances`: running decider-lab instances that no active
  Studio job owns (see 10).
- `recent_results`: 10 most recent model/suite runs by `run.json.finished_utc` (raw only), plus
  failed models from `lab.json.failures` as items with `"status": "failed"`, `"message"` and null
  numbers (every item has `status: "ok" | "failed"`).
- `doctor_seen` becomes true after any `GET /api/compute/doctor`.

---

## 4. Settings and environment

### `GET /api/settings`

```json
{
  "theme": "system",
  "density": "comfortable",
  "reduce_motion": "system",
  "max_concurrent_jobs": 2,
  "require_pinned_default": true,
  "require_sha256": false,
  "default_backend": "local",
  "runs_dir": null,
  "onboarding_dismissed": false
}
```

### `PUT /api/settings`

Body: any subset of the fields above. Response: the full settings. Validation: `theme` in
`system|dark|light`; `density` in `comfortable|compact`; `reduce_motion` in `system|on`;
`max_concurrent_jobs` 1–8; `default_backend` in `local|ssh|aws|vast`; `runs_dir` null or a
workspace-relative path. → `422 bad_request` otherwise.

### `GET /api/settings/env`

Query: `names` (optional, comma list of `^[A-Z_][A-Z0-9_]{0,127}$` names) adds those names to the
result (the Run dialog's "+ add name" chips). Only `set` is reported for them.

```json
{
  "items": [
    {"name": "HF_TOKEN", "set": true, "referenced_by": ["first-lab"], "purpose": "private or gated Hugging Face models"},
    {"name": "OPENAI_API_KEY", "set": false, "referenced_by": ["chat-lab"], "purpose": "referenced by api_key_env in a lab"},
    {"name": "AWS_PROFILE", "set": false, "referenced_by": [], "purpose": "default AWS profile"},
    {"name": "DECIDER_LAB_FAKE_CLOUD", "set": false, "referenced_by": [], "purpose": "cloud fixtures for testing"}
  ]
}
```

Fixed names: `HF_TOKEN`, `HF_HUB_OFFLINE`, `AWS_PROFILE`, `AWS_REGION`, `AWS_DEFAULT_REGION`,
`OPENAI_API_KEY`, `DECIDER_LAB_CACHE`, `STRANDS_DECIDER_PYTHON`, `DECIDER_LAB_FAKE_CLOUD`; plus
every value of a key ending in `_env` in any lab (`api_key_env`, `access_key_id_env`, …), plus
every name in any lab's `compute.env`. Never a value.

### `GET /api/about`

```json
{"decider_lab_version": "0.1.0", "studio_build": "a1b2c3d", "python": "3.12.6",
 "strands_decider": null, "license": "Apache-2.0", "cache_dir": "…", "state_dir": "…",
 "telemetry": "none"}
```

---

## 5. Labs

### 5.1 Types

```ts
type ModelKind = "serve" | "url" | "bedrock" | "strands" | "chat" | "python" | "baseline" | "finetuned" | "unknown";
type Problem = { severity: "error" | "warning"; code: string; message: string;
                 line: number | null; column: number | null; path: string | null };

type LabSummary = {
  name: string;
  workers: number;
  models: { name: string; kind: ModelKind; spec_text: string;          // e.g. "hf://StrandsAgents/…@bb282d7", "majority"
            source_kind?: "hf" | "s3" | "url" | "local"; pinned?: boolean;
            needs_gpu?: boolean; vision?: boolean; is_baseline: boolean; jevbench: boolean;
            paid_api: boolean;                // bedrock, strands, or chat with a non-loopback url: billed per request
            env_refs: string[]; warnings: string[] }[];
  suites: { ref: string; label: string; rows_estimate: number | null; has_splits: boolean | null }[];
  finetune: { name: string; from: string | null; base_model: string | null; train: string[]; steps: number | null }[];
  calibrate: boolean;
  baseline: string | null;
  jevbench: string[];
  compute: { backend: "local" | "ssh" | "aws" | "vast"; max_hours: number; options: Record<string, unknown> };
  plan: { runs: number; calibrated_runs_max: number; requests_estimate: number | null };
};
```

`spec_text` and `compute.options` have secret-looking values replaced by `"••••"`.
`rows_estimate`: smoke 90; synthetic `per_kind × families × 3` (default families 3, per_kind
150); heldout `null` unless already built and cached (then its count); files: line count.

### `GET /api/labs`

Query: `q` (substring on name/path), `invalid` (`1` → only invalid).

```json
{
  "items": [
    {"lab_id": "bGFicy9maXJzdC9sYWIueWFtbA", "name": "first-lab", "path": "labs/first/lab.yaml",
     "models": 3, "finetune": 0, "suites": 2, "backend": "local",
     "valid": true, "errors": 0, "warnings": 1,
     "last_run_at": "2026-10-06T08:40:00Z", "modified_at": "2026-10-06T08:01:12Z"}
  ]
}
```

`last_run_at`: newest `lab.json` mtime under the lab's run root, else null.

### `GET /api/templates`

```json
{"items": [
  {"id": "eval", "title": "Evaluate", "description": "models on suites, compared with a baseline", "lab_yaml": "…"},
  {"id": "finetune", "title": "Fine-tune", "description": "train on your rows, then evaluate", "lab_yaml": "…"}
]}
```

### `POST /api/labs` — create from template (CLI `init`)

Body: `{"template": "eval", "name": "first-lab", "dir": "labs/first-lab"}`

- `name`: `^[a-z0-9][a-z0-9_-]{0,63}$`; written into `lab.yaml`'s `name:`.
- `dir`: workspace-relative; must not exist or be empty.
- Copies the template exactly as `cmd_init` does, then sets `name:`.

`201`:
```json
{"lab_id": "…", "name": "first-lab", "path": "labs/first-lab/lab.yaml", "files": ["lab.yaml", "my_model.py", "README.md"]}
```
Errors: `409 dir_not_empty`, `400 path_outside_workspace`, `422 bad_request`.

### `POST /api/labs/import` — register an existing file

Body: `{"path": "experiments/q3.yaml"}` (workspace-relative). Returns the list item. Errors:
`404 not_found`, `422 not_a_lab` ("no `models:` key").

### `GET /api/labs/:lab_id`

```json
{
  "lab_id": "…", "name": "first-lab", "path": "labs/first/lab.yaml", "dir": "labs/first",
  "yaml": "name: first-lab\nworkers: 8\n…",     // secret literals masked as "••••" (2.5)
  "secrets_masked": 0,
  "etag": "\"sha256:5b1c…\"",
  "modified_at": "2026-10-06T08:01:12Z",
  "valid": true,
  "problems": [ /* Problem */ ],
  "summary": { /* LabSummary, null when the YAML does not parse */ },
  "run_root_id": "…",          // null if never run
  "command": "decider-lab run labs/first/lab.yaml"
}
```
Response header `ETag` equals `etag`.

### `POST /api/labs/validate`

Body: `{"yaml": "…", "lab_id": "…"}` (`lab_id` optional; gives relative paths a base dir).
Never writes. `200` always when the request is well-formed (problems are data, not an error):

```json
{"valid": false, "problems": [{"severity": "error", "code": "unknown_model_ref", "message": "baseline `majorty` is not in models. Did you mean `majority`?", "line": 14, "column": 11, "path": "baseline"}],
 "summary": { /* LabSummary or null */ }}
```

Validation = YAML parse (with line/column) + the rules of `lab.load_lab` (no models, baseline /
jevbench not in models) + `make_adapter`-level spec checks without constructing network clients
(unknown keys per kind, unknown baseline name, `serve` source `kind_of`, unpinned hf → warning
`unpinned_source`, s3/https archive without sha256 → warning `unverified_source` or error when
setting `require_sha256`), suites resolvable (`load_suite` names and file existence, not built),
`finetune` entries have `train`, `compute.backend` valid and its options known
(`FLAG_OPTIONS`), `*_env` names not set → warning `env_not_set`, literal secrets → error
`secret_literal`, `calibrate` true with suites lacking splits → warning `calibrate_no_splits`.
Problem codes are listed in section 12.2.

### `PUT /api/labs/:lab_id`

Headers: `If-Match: <etag>` (required; `*` to overwrite). Body: `{"yaml": "…"}`. Saves even if
invalid (DESIGN 4.2.2). Writes atomically (temp file + rename), preserving the file's mode.

`200`: same shape as `GET /api/labs/:lab_id` with the new `etag`.
Errors: `409 etag_mismatch` with `detail: {"current_etag": "…", "modified_at": "…"}`;
`428 precondition_required` without `If-Match`; `413 too_large` above 1 MB;
`422 secret_placeholder_unknown` with `detail.paths` (2.5). A `409 job_active` is not raised:
saving while the lab runs is allowed (the running job already read the file); the response
adds `"warning": "A run of this lab is in progress; it uses the version it started with."`.

### `POST /api/labs/:lab_id/fix-secret`

Body: `{"path": "models.nova.api_key", "env_name": "NOVA_API_KEY"}` + `If-Match`. Replaces the
literal at `path` with `<key>_env: NAME` (`api_key` → `api_key_env`), writes atomically, and
returns the lab (as `GET`). The secret value is dropped from the file and never returned.
Errors: `404 not_found` (no secret literal at `path`), `422 bad_request` (env name pattern
`^[A-Z_][A-Z0-9_]{0,127}$`), `409 etag_mismatch`.

### `DELETE /api/labs/:lab_id`

Body: `{"confirm": "first-lab"}`. Deletes the YAML file only (never runs, data or other files).
`200 {"deleted": true}`. Errors: `409 confirm_mismatch`, `409 job_active` (a job is running this lab).

### `POST /api/labs/:lab_id/duplicate`

Body: `{"name": "first-lab-copy", "path": "labs/first/lab-copy.yaml"}`. Copies the file with the new
`name:`. `201` list item. Errors: `409 file_exists`.

### `POST /api/labs/:lab_id/estimate` — run plan and cost

Body (`RunOptions`, same as the `run` job minus `confirm`):

```json
{
  "backend": "vast",
  "only": ["v19", "majority"],
  "limit": null,
  "max_hours": 2.0,
  "env": ["HF_TOKEN"],
  "keep": false,
  "fast_kernels": false,
  "strands_decider": null,
  "out": null,
  "options": {"gpu": "A100_SXM4", "num_gpus": 1, "max_price": 0.8, "disk_gb": 80, "offer": null, "ssh_key": "~/.ssh/id_ed25519"}
}
```

`options` keys per backend (mirroring `cli.FLAG_OPTIONS` and the providers):
- `local`: none.
- `ssh`: `host_id` (from SSH hosts) or `host` (`user@addr[:port]`), `key`.
- `aws`: `instance_type`, `region`, `profile`, `disk_gb`.
- `vast`: `gpu`, `num_gpus`, `max_price`, `disk_gb`, `offer`, `ssh_key`.
Missing options fall back to the lab's `compute.<backend>` section, then provider defaults.
`out`: workspace-relative run root (CLI `--out`); null = the lab's default
(`<lab dir>/runs/<name>`, or under Settings `runs_dir`). `strands_decider` is redacted (2.5) in
every response because pip specs can carry URL credentials.

`200`:
```json
{
  "backend": "vast",
  "can_start": true,
  "blockers": [],
  "warnings": ["v19 runs with `serve` and needs a GPU: the A100_SXM4 has 80 GB"],
  "plan": {"models": ["v19", "majority"], "suites": ["smoke", "synthetic"], "runs": 4, "calibrated_runs_max": 2, "requests_estimate": 1980},
  "resume": {"root": "labs/first/runs/first-lab", "exists": true, "rows_reused": 412},
  "paid_models": [],                       // e.g. [{"model": "nova", "kind": "bedrock", "provider": "AWS Bedrock us-east-1", "requests_estimate": 990}]
  "cost": {
    "billable": true,
    "rate_usd_per_hour": 0.612,
    "rate_source": "cheapest matching vast.ai offer 1234567 (1x A100_SXM4 80 GB, CA)",
    "cap_usd_per_hour": 0.8,
    "max_hours": 2.0,
    "cap_usd": 1.6,
    "credit_usd": 25.4,
    "note": "This rents a machine that bills until it is destroyed. decider-lab destroys it when the run ends, fails, is cancelled, or reaches max hours."
  },
  "confirm_phrase": "spend 1.60 on vast",
  "command": "decider-lab run labs/first/lab.yaml --on vast --only v19 majority --gpu A100_SXM4 --max-price 0.8 --num-gpus 1 --disk 80 --max-hours 2.0 --env HF_TOKEN"
}
```

Rules:
- `local`, `ssh`: `cost.billable: false`, all money fields `null`, `confirm_phrase: null`.
- `vast`: `cap_usd = max_price × max_hours` (`num_gpus` is already in vast's `dph_total`).
  Blockers: no matching offer; `credit_usd < cap_usd + 0.5` (the provider's own check); vastai CLI
  missing or no API key.
- `aws`: `rate_usd_per_hour` from the static table `ui/prices.py: AWS_ON_DEMAND_USD_PER_HOUR`
  (us-east-1 list prices, with `AS_OF` date; other regions use the same table and add a warning
  "price table is for us-east-1"); an unknown type gives `rate_usd_per_hour: null`, `cap_usd: null`,
  `can_start: false` and the blocker "no price known for <type>; pick a listed type" (a spend
  cap must be known before money is spent). `cap_usd = rate × (max_hours + 0.25)` (the instance's own shutdown timer is
  max_hours + 15 min). Blockers: no credentials for the profile, vCPU quota for the family
  smaller than the type's vCPUs (names the quota code), boto3 missing.
- Always: lab invalid → blocker "lab.yaml has n errors"; `only` names unknown models → blocker;
  `env` names that are not set → warning "HF_TOKEN is not set; it will not be passed".
- `confirm_phrase` uses `f"spend {cap_usd:.2f} on {backend}"`, plus `" and keep the machine"`
  when `keep` is true. With `keep`, `cost.cap_usd` still states the run's cap but `cost.note`
  says the machine bills past it, and a warning is added.
- `paid_models` non-empty on `local`/`ssh` → `confirm_phrase: "call paid apis"` (cost stays
  `billable: false` for compute, but the phrase is required). On aws/vast the cloud phrase covers
  both and `paid_models` is listed in the estimate.
- `resume.rows_reused`: lines without `error` in existing `predictions.jsonl` files of the
  selected models under the target root (local backend only; remote runs copy no results up,
  so `null`).

Errors: `404 not_found`. Cloud lookups that fail become blockers, not HTTP errors.

---

## 6. Jobs

A job is one `decider-lab` CLI subprocess (or a fake-cloud simulator wrapping one), started by
the server, tracked in `<state_dir>/jobs/<job_id>/`. Jobs survive page reloads; on server
restart, jobs whose process is gone become `lost` (their logs and results remain; `machine_id`
and `failures: ["Studio stopped while this job ran; a remote machine may still be running"]`
are kept so the UI can point at Compute).

### 6.1 Types

```ts
type JobKind = "run" | "eval" | "pull" | "jevbench" | "calibrate" | "suite_build";
type JobStatus = "queued" | "running" | "cancelling" | "succeeded" | "partial" | "failed" | "cancelled" | "lost";
type Stage = { name: string; label: string; status: "pending" | "active" | "done" | "failed" | "skipped" | "warning";
               started_at: string | null; ended_at: string | null; detail: string | null };

type RunProgress = { model: string; suite: string;               // suite may end in "+cal" or be "jevbench"
                     status: "pending" | "running" | "done" | "failed" | "skipped";
                     done: number; total: number | null; errors: number;
                     rows_per_s: number | null;                  // over the last 30 s
                     intelligence: number | null; ci95: [number | null, number | null] | null;
                     accuracy: number | null; message: string | null;
                     reused: number | null };                    // rows resumed from an earlier call

type JobProgress = { fraction: number | null;                     // 0..1, null when unknown
                     label: string;                                // "4/6 runs", "412 MB / 4.1 GB", "90/90 rows"
                     runs: RunProgress[];                          // run/eval/jevbench/calibrate
                     bytes: { done: number; total: number | null } | null;   // pull
                     finetune: { model: string; step: number | null; total_steps: number | null;
                                 loss: number | null; status: "pending" | "running" | "done" | "failed" }[] | null;
                     machine: { provider: "vast" | "aws" | "ssh"; id: string | null; target: string | null;
                                gpu: string | null; usd_per_hour: number | null; cost_so_far_usd: number | null } | null };

type JobSummary = { job_id: string; kind: JobKind; title: string; status: JobStatus;
                    backend: "local" | "ssh" | "aws" | "vast"; lab_id: string | null;
                    created_at: string; started_at: string | null; ended_at: string | null;
                    duration_s: number | null; progress: { fraction: number | null; label: string };
                    failure_count: number; root_id: string | null;
                    queue_position: number | null;          // 1-based while queued, else null
                    cost_so_far_usd: number | null;         // remote runs only; an estimate
                    machine_id: string | null };            // vast/aws id once parsed (also kept for lost jobs)

type Job = JobSummary & {
  argv: string[];                 // exactly what was executed (redacted)
  cwd: string;
  env_names: string[];            // names passed through; never values
  exit_code: number | null;
  stages: Stage[];
  progress: JobProgress;
  failures: string[];             // from lab.json, or the last error line (JobSummary has only failure_count)
  result: Record<string, unknown> | null;   // kind-specific, see 6.3
  log: { lines: number; bytes: number };
  telemetry: { source: "local-nvidia-smi" | "remote-ssh" | "simulated" | "none"; reason: string | null };
  options: Record<string, unknown>;          // the submitted body minus confirm, for "Re-run"
};
```

Stage lists per kind (names are stable; `label` is display text):

| kind / backend | stages |
|---|---|
| run / local | `prepare`, `run`, `report` (`prepare`, `finetune`, `run`, `report` when the lab has `finetune:` entries) |
| run / ssh, aws, vast | `check`, `acquire`, `copy`, `bootstrap`, `run`, `fetch`, `release` |
| eval | `prepare`, `run` |
| pull | `resolve`, `download`, `verify`, `extract`, `done` |
| jevbench | `harness`, `run`, `score` |
| calibrate | `fit`, `score` |
| suite_build | `build` |

Log-line → state rules (`jobs.py` parser; lines are matched after stripping a leading `  | `):

| pattern | effect |
|---|---|
| `[<provider>] renting …` / `[aws] launching …` / `instance <id>` | `acquire` active; `machine.id`, `usd_per_hour` |
| `[<provider>] ssh ok: <target>:<port>, work dir …` | `acquire` done, `copy` active; `machine.target` |
| `[remote] … presigned …` | `copy` detail |
| `[<provider>] bootstrap:` / `bootstrap ok` | `bootstrap` active / done |
| first line prefixed `  | ` | `run` active |
| `[decider-lab] <model> / <suite>: <done>/<total>` | `runs[model,suite]` running, counts |
| `[decider-lab] <model> / <suite>: Intelligence <x> (95% CI [<lo>, <hi>]), accuracy <a>%, errors <e>` | that run done with numbers |
| `[decider-lab] <model> / <suite>+cal: T=… Intelligence <b> -> <a>` | `<suite>+cal` done |
| `… too few dev rows …; no +cal run` | `<suite>+cal` skipped with message |
| `[decider-lab] <model> / jevbench-public: proxy Intelligence <x> (<n>/231 right)` | `jevbench` done |
| `[decider-lab] FAILED <model>: …` | that model's pending/running runs failed with message |
| `[decider-lab] report: <path>` | `report` (local) done; `root_id` set |
| `[<provider>] results copied to …` | `fetch` done |
| `destroyed` / `terminated` / `--keep: the machine is left running` | `release` done / done / warning |
| `WARNING: instance … may still be running` | `release` failed (danger) |

Row counts are also refreshed every 2 s by counting lines of
`<root>/<model>/<suite>/predictions.jsonl` (local jobs only; it is append-only during a run) and
the number with `"error": non-null`, which gives `rows_per_s` and `errors` between the CLI's
every-50-rows progress lines. Totals for runs not yet started come from the lab summary's
`rows_estimate` (or `null`).

Fine-tune progress (local runs): the training output goes to
`<root>/_finetune/<name>/train.log`, not stdout, so the server tails that file every 2 s,
appends its lines to the job log prefixed `[train <name>] ` (redacted), and fills
`progress.finetune` from lines that carry a step and a loss (best-effort; unknown formats leave
`step`/`loss` null). Telemetry samples add `train_loss` when known.

Exit status mapping: exit 0 → `succeeded`; non-zero with `lab.json.failures` non-empty and at
least one scored run → `partial`; other non-zero → `failed`; terminated by cancel → `cancelled`.

### `GET /api/jobs`

Query: `status` (`active` = queued+running+cancelling, `finished`, or a single status), `kind`,
`lab_id`, `q`, `limit` (default 100).
`200 {"items": [JobSummary, …]}` newest first.

### `POST /api/jobs` — start a job

Body by kind:

```jsonc
// run (CLI: decider-lab run)
{"kind": "run", "lab_id": "…", /* RunOptions from 5 */ "confirm": "spend 1.60 on vast"}

// eval (CLI: decider-lab eval)
{"kind": "eval",
 "model": {"type": "url", "value": "http://127.0.0.1:8000"},   // type: url | baseline | serve | python
 "name": "model", "suite": "smoke", "split": null, "limit": null, "workers": 4, "vision": false,
 "out": null}                                                     // default runs/<name>/<suite> under the workspace

// pull (CLI: decider-lab pull)
{"kind": "pull", "source": "hf://StrandsAgents/strands-decider-2B-hobson-v19@bb282d786bc251fd4e3068de3ada9ddbb38127cd",
 "revision": null, "sha256": null, "require_pinned": true, "profile": null, "region": null}

// jevbench (CLI: decider-lab jevbench)
{"kind": "jevbench", "url": "http://127.0.0.1:8000", "label": "v19", "out": null}  // default runs/jevbench/<label>-<utc stamp>

// calibrate (CLI: decider-lab calibrate)
{"kind": "calibrate", "root_id": "…", "model": "v19", "suite": "synthetic", "out": null}  // default <root>/<model>/<suite>+cal

// suite_build (builds/caches a built-in suite, e.g. heldout)
{"kind": "suite_build", "suite": "heldout"}
```

`202`:
```json
{"job_id": "j_3f9a1c22be07", "status": "queued", "title": "run first-lab on vast.ai",
 "command": "decider-lab run labs/first/lab.yaml --on vast --gpu A100_SXM4 …"}
```

Rules: `run` with `vast`/`aws` requires `confirm` (2.4) and re-runs the estimate checks; any
blocker → `409 run_blocked` with `detail.blockers`. `run` refuses an invalid lab
(`422 lab_invalid` with problems). A second active `run` of the same lab → `409 job_active`
(detail `job_id`). Jobs beyond `max_concurrent_jobs` are `queued` (FIFO). `pull` validates the
source like `/api/models/inspect` and fails fast with `422 source_invalid` /
`422 unpinned_source` (when `require_pinned`). `eval` with `serve` uses `--serve`; `python` uses
`--model python:<module:attr>`. `calibrate`: `409 too_few_dev_rows` if no kind has ≥ 30 dev
rows (checked before starting), `404 not_found` for an unknown run.

The server builds argv itself from validated fields (never a shell string; no user text is
interpolated into a shell). Subprocess: `sys.executable -m decider_lab <args>`, cwd = the lab's
directory (run) or the workspace, its own process group, stdout+stderr merged, environment =
server environment (so `env` names resolve) plus `PYTHONUNBUFFERED=1`.

### `GET /api/jobs/:job_id`

`200 Job`. Example (running remote run):

```json
{
  "job_id": "j_3f9a1c22be07", "kind": "run", "title": "run first-lab on vast.ai", "status": "running",
  "backend": "vast", "lab_id": "bGFicy9maXJzdC9sYWIueWFtbA",
  "created_at": "2026-10-06T10:42:00Z", "started_at": "2026-10-06T10:42:00Z", "ended_at": null,
  "duration_s": 728.1, "failure_count": 0, "root_id": null,
  "queue_position": null, "cost_so_far_usd": 0.12, "machine_id": "9876543",
  "progress": {
    "fraction": 0.58, "label": "4/6 runs",
    "runs": [
      {"model": "v19", "suite": "smoke", "status": "done", "done": 90, "total": 90, "errors": 0, "rows_per_s": null,
       "intelligence": 58.3, "ci95": [51.2, 65.0], "accuracy": 74.4, "message": null},
      {"model": "v19", "suite": "synthetic", "status": "running", "done": 410, "total": 900, "errors": 2, "rows_per_s": 9.8,
       "intelligence": null, "ci95": null, "accuracy": null, "message": null}
    ],
    "bytes": null,
    "machine": {"provider": "vast", "id": "9876543", "target": "root@ssh4.vast.ai:22311", "gpu": "1x A100_SXM4",
                "usd_per_hour": 0.612, "cost_so_far_usd": 0.12}
  },
  "argv": ["decider-lab", "run", "labs/first/lab.yaml", "--on", "vast", "--gpu", "A100_SXM4", "--max-price", "0.8", "--max-hours", "2.0", "--env", "HF_TOKEN"],
  "cwd": "/Users/me/labs",
  "env_names": ["HF_TOKEN"],
  "exit_code": null,
  "stages": [
    {"name": "check", "label": "Check", "status": "done", "started_at": "2026-10-06T10:42:00Z", "ended_at": "2026-10-06T10:42:02Z", "detail": "credit $25.40"},
    {"name": "acquire", "label": "Acquire machine", "status": "done", "started_at": "…", "ended_at": "…", "detail": "offer 1234567"},
    {"name": "copy", "label": "Copy lab", "status": "done", "started_at": "…", "ended_at": "…", "detail": null},
    {"name": "bootstrap", "label": "Bootstrap", "status": "active", "started_at": "…", "ended_at": null, "detail": null},
    {"name": "run", "label": "Run", "status": "pending", "started_at": null, "ended_at": null, "detail": null},
    {"name": "fetch", "label": "Fetch results", "status": "pending", "started_at": null, "ended_at": null, "detail": null},
    {"name": "release", "label": "Release machine", "status": "pending", "started_at": null, "ended_at": null, "detail": null}
  ],
  "failures": [],
  "result": null,
  "log": {"lines": 412, "bytes": 38211},
  "telemetry": {"source": "remote-ssh", "reason": null},
  "options": {"backend": "vast", "only": null, "max_hours": 2.0, "env": ["HF_TOKEN"], "options": {"gpu": "A100_SXM4"}}
}
```

`cost_so_far_usd = usd_per_hour × hours since acquire`, labelled an estimate in the UI.
`argv[0]` is shown as `decider-lab` for readability (the real executable is in server logs).

### 6.3 `result` by kind (set when the job ends)

| kind | result |
|---|---|
| run | `{"root_id": "…", "report_md": "labs/first/runs/first-lab/REPORT.md", "failures": ["nova: ThrottlingException: …"]}` |
| eval | `{"run": {"root_id": "…", "model": "model", "suite": "smoke"}, "scores": {"n": 90, "errors": 0, "intelligence": 12.0, "intelligence_ci95": [8.1, 15.9], "accuracy": 55.0, "nll": 0.69, "ece": 0.012}}` (the eval output directory's parent is treated as a run root) |
| pull | `{"path": "/Users/me/.cache/decider-lab/models/9a1f…/ckpt", "model_key": "9a1f03be2c7d4e11", "info": {"source": "hf://…", "kind": "hf", "resolved_commit": "bb282d78…", "pinned": true}}` |
| jevbench | `{"out": "runs/jevbench/v19-20261006T104200Z", "scores": {"intelligence_proxy": 41.2, "n_correct": 151, "tasks": 231, "competence_by_type": {"noul": 45.1, "choice": 40.3, "score": 38.2}, "yes_no_in_band": 12, "yes_no": 77, "jevbench_commit": "1bcc55eb…", "note": "local proxy of the JevBench v1.5 rules on the 231 public v1 tasks; not a board score"}}` |
| calibrate | `{"run": {"root_id": "…", "model": "v19", "suite": "synthetic+cal"}, "temperatures": {"noul": 1.42, "choice": 1.1, "score": 0.93}, "before": {"intelligence": 61.4, "nll": 0.512, "ece": 0.041}, "after": {"intelligence": 61.9, "nll": 0.488, "ece": 0.019}, "fit_rows": 360}` |
| suite_build | `{"suite": "heldout", "rows": 3312, "sha256": "…"}` |

### `POST /api/jobs/:job_id/cancel`

No body. `202 {"status": "cancelling"}`. Sends SIGTERM to the process group (the CLI's
`run_on` releases the machine on SIGTERM); after 60 s SIGKILL; for remote backends a SIGKILL
adds failure "the machine may still be running: check Compute" and the `release` stage becomes
`failed`. Queued jobs become `cancelled` immediately (`200 {"status":"cancelled"}`).
Errors: `409 job_not_active`.

### `DELETE /api/jobs/:job_id`

Body `{"confirm": "delete"}`. Finished jobs only; removes the job record and log, never results.
`200 {"deleted": true}`. Errors: `409 job_active`, `409 confirm_mismatch`.

### `GET /api/jobs/:job_id/log`

Query: `offset` (line index, default 0), `limit` (default 5000, max 20000), `q` (optional
server-side substring filter, case-insensitive), `level` (`all|warn|error`).

```json
{"lines": [{"seq": 0, "ts": "2026-10-06T10:42:01.112Z", "text": "[vast] renting 1x A100_SXM4 at $0.612/h (offer 1234567)", "level": "info"}],
 "next_offset": 1, "total": 412, "truncated": false}
```

`level` classification: `error` if matches `FAILED|Traceback|Error|failed|WARNING: instance`,
`warn` if `WARNING|WARN|too few`, else `info`. `seq` is the 0-based line number and equals the
SSE event id for `log` events.

### `GET /api/jobs/:job_id/log.txt`

Plain text download (`Content-Disposition: attachment; filename="<job_id>.log"`), redacted.

### `GET /api/jobs/:job_id/telemetry`

Query: `since` (ISO time, optional), `limit` (default 900 samples).

```json
{
  "source": "local-nvidia-smi",
  "reason": null,
  "interval_s": 2,
  "gpus": [{"index": 0, "name": "NVIDIA L40S", "memory_total_gb": 45.0, "power_limit_w": 350}],
  "samples": [
    {"ts": "2026-10-06T10:52:00Z",
     "gpus": [{"index": 0, "util_pct": 87, "mem_used_gb": 31.2, "temp_c": 64, "power_w": 241.5}],
     "rows_per_s": 9.8, "errors_total": 2}
  ]
}
```

`source: "none"` with `reason` (e.g. "no NVIDIA GPU on this machine (Apple MPS and CPU are not
sampled)"); `rows_per_s`/`errors_total` are still present for run/eval jobs. Sampler: local
jobs run `nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit
--format=csv,noheader,nounits` every 2 s while running; remote jobs on ssh/vast/aws run the
same command over ssh (using the job's `machine.target` and the key from options; aws uses the
per-run key in `~/.cache/decider-lab/keys`) every 10 s, `source:"remote-ssh"`; failures to
sample are silent gaps. Fake cloud: deterministic sine-based values, `source:"simulated"`.

### `GET /api/jobs/:job_id/events` (SSE)

Replays from `Last-Event-ID` (or from line 0 if `?from=0`; default: only new events after the
current state is sent once as a `snapshot`). Events:

```
event: snapshot
id: s-0
data: {"job": <Job>}

event: log
id: 413
data: {"seq": 413, "ts": "…", "text": "[decider-lab] v19 / synthetic: 450/900", "level": "info"}

event: stage
id: st-12
data: {"stage": <Stage>}

event: progress
id: p-88
data: {"progress": <JobProgress>}

event: telemetry
id: t-301
data: {"sample": <TelemetrySample>}

event: status
id: x-5
data: {"status": "succeeded", "exit_code": 0, "ended_at": "…", "result": {…}}

event: end
id: e-0
data: {}
```

`log` ids are the line `seq` (integers); other events use prefixed ids and are not replayed
(the client re-reads `GET /api/jobs/:id` after reconnecting). `progress` is coalesced to at most
2 per second. The stream ends with `end` after a terminal status.

### `GET /api/events` (SSE, global)

For the jobs badge, Jobs list and JobChips.

```
event: job.created   data: <JobSummary>
event: job.updated   data: <JobSummary>      (status or progress label change, ≤ 1/s per job)
event: job.finished  data: <JobSummary>
event: labs.changed  data: {"lab_ids": ["…"]}      (file watcher: created/modified/deleted, polled every 3 s)
event: runs.changed  data: {"root_ids": ["…"]}     (a report.json or scores.json written)
event: models.changed data: {}
```

No replay; the client refetches lists on reconnect.

---

## 7. Results

### 7.1 Types

```ts
type CI = [number | null, number | null];
type Scores = {                                  // scores.json, as the SDK writes it
  n: number; errors: number; intelligence: number | null; intelligence_ci95?: CI;
  accuracy: number | null; nll: number | null; ece: number | null; scored_split: string;
  by_kind: Record<"noul" | "choice" | "score", { n: number; competence: number | null; accuracy: number | null;
            nll: number | null; brier: number | null; ece: number | null; in_band?: number | null; rps?: number | null }>;
  by_family: Record<string, { n: number; intelligence: number | null; accuracy: number | null }>;
  latency_s?: { median: number; p95: number };
};
type RunRef = { root_id: string; model: string; suite: string };   // suite may end with "+cal"
```

### `GET /api/runs`

Query: `lab_id`, `q`.

```json
{
  "items": [
    {"root_id": "…", "lab": "first-lab", "lab_id": "…", "path": "labs/first/runs/first-lab",
     "models": ["v19", "heuristic", "majority"], "suites": ["smoke", "synthetic"],
     "has_calibrated": true, "has_jevbench": false, "baseline": "majority",
     "failures": [], "wall_s": 412.7, "finished_at": "2026-10-06T08:40:00Z",
     "best": {"suite": "synthetic", "model": "v19", "intelligence": 61.4, "ci95": [57.9, 64.8]}}
  ]
}
```

Run roots are found by scanning the workspace (depth ≤ 6) for directories that contain
`lab.json` or `report.json`, or that contain `<model>/<suite>/scores.json` two levels down (an
`eval` output). `suites` lists base suites (no `+cal`, no `jevbench`).

### `GET /api/runs/:root_id`

```json
{
  "root_id": "…", "lab": "first-lab", "lab_id": "…", "path": "labs/first/runs/first-lab",
  "title": "first-lab", "baseline": "majority",
  "lab_json": {"name": "first-lab", "models": {"v19": {"serve": "hf://…"}}, "suites": ["smoke"], "wall_s": 412.7, "failures": []},
  "models": [{"name": "v19", "color_index": 0, "is_baseline": false, "kind": "serve"},
             {"name": "majority", "color_index": null, "is_baseline": true, "kind": "baseline"}],
  "suites": [{"name": "synthetic", "has_calibrated": true, "scored_split": "test", "models": ["v19", "heuristic", "majority"]}],
  "jevbench_models": [],
  "failures": [],
  "report_md_available": true,
  "finished_at": "2026-10-06T08:40:00Z"
}
```

`lab_json` is redacted. `color_index` is the position among non-baseline models in `lab.json`
model order (fallback: directory order); the frontend maps it to `--chart-<1+i%6>`.

### `GET /api/runs/:root_id/leaderboard`

Query: `suite` (required, base name), `scores` (`raw|cal|both`, default `raw`).

```json
{
  "suite": "synthetic", "scored_split": "test", "baseline": "majority",
  "proxy_note": "Intelligence is a local proxy computed with the JevBench v1.5 rules on this suite; it is not a JevBench board score.",
  "rows": [
    {"model": "v19", "variant": "raw", "status": "ok",
     "intelligence": 61.4, "ci95": [57.9, 64.8], "accuracy": 78.2, "nll": 0.512, "ece": 0.041,
     "noul_in_band": 6.0, "errors": 0, "n": 540, "latency_s": {"median": 0.18, "p95": 0.41},
     "tie_group": 0, "temperatures": null, "message": null},
    {"model": "v19", "variant": "cal", "status": "ok", "intelligence": 61.9, "ci95": [58.3, 65.1], "accuracy": 78.2,
     "nll": 0.488, "ece": 0.019, "noul_in_band": 7.1, "errors": 0, "n": 540, "latency_s": {"median": 0.18, "p95": 0.41},
     "tie_group": 0, "temperatures": {"noul": 1.42, "choice": 1.1, "score": 0.93}, "message": null},
    {"model": "nova", "variant": "raw", "status": "failed", "intelligence": null, "ci95": null, "accuracy": null,
     "nll": null, "ece": null, "noul_in_band": null, "errors": null, "n": null, "latency_s": null,
     "tie_group": null, "temperatures": null, "message": "nova: ThrottlingException: Rate exceeded"}
  ],
  "domain": [-5.0, 69.8],
  "same_rows": {"consistent": true, "n_by_model": {"v19": 540, "heuristic": 540, "majority": 540},
                "sha256_by_model": {"v19": "3be1…"}, "limited": []}
}
```

- Rows sorted by `intelligence` desc within variant; failed rows last.
- `tie_group`: consecutive (in sort order) models whose CIs overlap share a group number; the UI
  shows "≈" for groups with > 1 member.
- `status: "failed"` rows come from `lab.json.failures` for models with no `scores.json` for
  this suite. `status: "missing"` when the model is in the lab but never ran this suite.
- `domain`: the shared CIBar axis (DESIGN 7.1).
- `same_rows.consistent` is false when the ok rows' `n` differ, their `run.json.suite_sha256`
  differ, or any was run with `--limit` (listed in `limited`, from `lab.json`/`run.json`). The UI
  then shows the "not same rows" warning (DESIGN 4.4.1); ranks and `tie_group` are still
  computed but carry no pairing.
- Only `intelligence` carries a CI here (the SDK bootstraps only it per run); paired CIs for
  accuracy and NLL come from `vs-baseline` and `compare`.
- For `suite=jevbench` use `GET …/jevbench` instead (`400 use_jevbench_endpoint`).

### `GET /api/runs/:root_id/runs/:model/:suite`

Single run detail (suite may be `synthetic%2Bcal`).

```json
{
  "ref": {"root_id": "…", "model": "v19", "suite": "synthetic"},
  "scores": { /* Scores */ },
  "run": {"model": "v19", "suite": "synthetic", "suite_params": {"per_kind": 100}, "suite_sha256": "3be1…",
          "n_rows": 900, "answered_this_call": 900, "wall_s": 96.2,
          "answerer": {"adapter": "systemone", "url": "http://127.0.0.1:52011", "health": {"model": "strands-decider-2B-hobson-v19"},
                       "source": {"source": "hf://…", "kind": "hf", "resolved_commit": "bb282d78…"}},
          "decider_lab": "0.1.0", "host": "gpu-box", "platform": "Linux-…", "python": "3.11.9",
          "gpu": "NVIDIA L40S, 550.54", "finished_utc": "2026-10-06T08:39:12Z"},
  "source": {"source": "hf://…", "kind": "hf", "resolved_commit": "bb282d78…", "path": "…"},
  "calibration": null,
  "has_calibrated": true,
  "errors_sample": [{"id": "7f3a…", "error": "TimeoutError: …"}],
  "files": {"predictions": "labs/first/runs/first-lab/v19/synthetic/predictions.jsonl", "scores": "…", "run": "…"}
}
```

`run` = redacted `run.json`; `source` = redacted `<root>/<model>/source.json` or null;
`calibration` = `calibration.json` for `+cal` runs (`{temperatures, fit_split, fit_rows,
score_split, before, after}`) else null; `errors_sample` up to 5.

### `GET /api/runs/:root_id/runs/:model/:suite/predictions`

Query: `offset`, `limit` (≤ 500), `kind`, `family`, `split`, `correct` (`1|0`), `error` (`1`).
Paged list of prediction records exactly as in `predictions.jsonl` plus derived fields:

```json
{"items": [{"id": "7f3a…", "task": "synthetic/calendar", "kind": "noul", "split": "test", "label": 1, "n": 2,
            "probs": [0.09, 0.91], "latency_s": 0.181, "error": null,
            "top": 1, "correct": true, "proxy_right": true, "in_band": false, "expected_level": null}],
 "total": 540, "offset": 0, "limit": 50}
```

`proxy_right`: right under the v1.5 rules (noul: decisive and on the gold side; choice: top =
gold; score: `null`, graded jointly). `expected_level` for score rows.

### `GET /api/runs/:root_id/runs/:model/:suite/predictions.csv`

CSV download of the predictions with derived fields (`probs` as `;`-joined). Redaction not
needed (no secrets in predictions) but `error` strings are still passed through `redact()`.

### `GET /api/runs/:root_id/runs/:model/:suite/reliability`

Query: `bins` (5–20, default 10), `kind` (`all|noul|choice|score`), `split` (default the run's
`scored_split`).

```json
{"kind": "all", "bins": [{"lo": 0.5, "hi": 0.6, "n": 61, "mean_conf": 0.552, "accuracy": 0.541}],
 "ece": 0.041, "n": 540,
 "calibrated": {"bins": [ … ], "ece": 0.019, "n": 540}}
```

Computed with `metrics.row_stats` (`conf` = top-option probability, `correct`), same binning as
`metrics.ece`. `calibrated` present when a `+cal` sibling exists (else null). Empty bins are
omitted.

### `GET /api/runs/:root_id/runs/:model/:suite/latency`

```json
{"p50": 0.18, "p95": 0.41, "n": 540, "errors": 0,
 "histogram": [{"lo": 0.1, "hi": 0.126, "ok": 41, "failed": 0}]}
```

30 log-spaced bins between min and max latency (linear if max/min < 3). Uses all predictions of
the scored split.

### `GET /api/runs/:root_id/latency`

Query: `suite`. `{"items": [{"model": "v19", "p50": 0.18, "p95": 0.41, "n": 540, "errors": 0}]}`.

### `GET /api/runs/:root_id/vs-baseline`

Query: `suite` (required), `variant` (`raw|cal`, default raw).

```json
{"suite": "synthetic", "baseline": "majority", "split": "test",
 "items": [{"model": "v19", "n_paired": 540, "only_a": 0, "only_b": 0,
            "intelligence": {"diff": 61.4, "ci95": [57.2, 65.3]},
            "accuracy": {"diff": 27.8, "ci95": [24.1, 31.4]},
            "nll": {"diff": -0.181, "ci95": [-0.205, -0.158]},
            "verdict": {"intelligence": "better", "accuracy": "better", "nll": "better"}}]}
```

From `report.json.vs_baseline` for `raw` (no recomputation); for `cal`, computed with
`metrics.compare` on the `+cal` runs and cached by file mtimes. `verdict`: `better` /
`worse` / `unclear` (CI includes 0; for nll lower is better). No baseline →
`200 {"baseline": null, "items": []}`.

### `GET /api/runs/:root_id/families`

Query: `suite`, `variant`.

```json
{"suite": "synthetic", "families": ["synthetic/arithmetic", "synthetic/calendar", "synthetic/seating"],
 "models": ["v19", "heuristic", "majority"],
 "cells": [{"family": "synthetic/arithmetic", "model": "v19", "n": 180, "intelligence": 70.1, "accuracy": 84.0}]}
```

### `GET /api/runs/:root_id/jevbench`

```json
{"items": [{"model": "v19", "intelligence_proxy": 41.2, "n_correct": 151, "tasks": 231,
            "competence_by_type": {"noul": 45.1, "choice": 40.3, "score": 38.2},
            "yes_no_in_band": 12, "yes_no": 77, "jevbench_commit": "1bcc55eb6c8cffde2306b3db03ede39b61c6152a",
            "note": "local proxy of the JevBench v1.5 rules on the 231 public v1 tasks; not a board score"}]}
```

Also used for standalone jevbench outputs (an `out` dir with `scores.json` that has
`intelligence_proxy` is listed as a run root with `has_jevbench: true`).

### `GET /api/runs/:root_id/rows` — row explorer across models

Query: `suite` (required; base or `+cal`), `offset`, `limit` (≤ 200), `kind`, `family`, `split`
(default scored split), `q` (substring of state/instructions), `show`
(`all|wrong|disagree|errors`), `wrong_for` (model, with `show=wrong`), `models` (comma list,
default all).

```json
{
  "models": ["v19", "heuristic", "majority"],
  "items": [{
    "id": "7f3a…", "task": "synthetic/calendar", "kind": "noul", "split": "test",
    "label": 1, "gold_name": "true",
    "state_excerpt": "Today is 2024-03-01. In 45 days it will be…",
    "instructions_excerpt": "Is it April?",
    "answers": {
      "v19": {"top": 1, "top_name": "true", "p_top": 0.91, "p_gold": 0.91, "proxy_right": true, "in_band": false, "error": null},
      "heuristic": {"top": 1, "top_name": "true", "p_top": 0.5, "p_gold": 0.5, "proxy_right": false, "in_band": true, "error": null},
      "majority": null
    }
  }],
  "total": 540, "offset": 0, "limit": 50
}
```

Row content comes from the suite's rows (rebuilt from the suite cache via `load_suite` and the
lab's suite spec, matched by `id`); when the suite cannot be rebuilt (missing file/extra),
`state_excerpt`/`instructions_excerpt`/`gold_name` are `null` and the response has
`"content_available": false` with `content_reason`. `answers[model]` is null when that model has
no prediction for the row. `disagree` = models' top options differ.

### `GET /api/runs/:root_id/rows/:row_id`

Query: `suite`.

```json
{"id": "7f3a…", "task": "synthetic/calendar", "kind": "noul", "split": "test",
 "state": "Today is …", "state_is_json": false, "instructions": "Is it April?",
 "options": [["false", "no"], ["true", "yes"]], "label": 1,
 "images": [{"src": "/api/files/raw?file_id=…", "alt": "image 1 of row 7f3a"}],
 "answers": {"v19": {"probs": [0.09, 0.91], "top": 1, "proxy_right": true, "in_band": false, "latency_s": 0.181, "error": null}}}
```

Images: workspace files are served through `GET /api/files/raw?file_id=` (images only:
png/jpg/jpeg/gif/webp, ≤ 20 MB, inside the workspace); `data:` URIs are passed through; anything
else is listed as `{"src": null, "alt": "image outside the workspace: not shown"}`.

### `GET /api/runs/:root_id/provenance`

```json
{"items": [{"model": "v19", "suite": "synthetic", "suite_sha256": "3be1…", "answerer": {…}, "source": {…},
            "host": "gpu-box", "platform": "…", "python": "3.11.9", "gpu": "…", "decider_lab": "0.1.0",
            "finished_utc": "…", "wall_s": 96.2, "workers": 8, "limit": null, "calibration": null,
            "job_id": "j_3f9a1c22be07"}],
 "same_rows": {"synthetic": {"consistent": true, "sha256": "3be1…", "mismatched_models": []}}}
```

All redacted. `job_id`: the Studio job whose run root this is, when a job record still exists
(else null).

### `GET /api/compare`

Query: `a` and `b` as `<root_id>:<model>:<suite>` (each part percent-encoded), `split`
(`test|dev|all`, default `test`).

```json
{"a": {"root_id": "…", "model": "v19", "suite": "synthetic", "label": "first-lab / v19 / synthetic"},
 "b": {"root_id": "…", "model": "majority", "suite": "synthetic", "label": "first-lab / majority / synthetic"},
 "split": "test", "same_suite_sha256": true,
 "n_paired": 540, "only_a": 0, "only_b": 0,
 "intelligence": {"diff": 61.4, "ci95": [57.2, 65.3], "verdict": "better"},
 "accuracy": {"diff": 27.8, "ci95": [24.1, 31.4], "verdict": "better"},
 "nll": {"diff": -0.181, "ci95": [-0.205, -0.158], "verdict": "better"},
 "bootstrap": 2000, "has_splits": true, "notes": []}
```

Same filtering as `cmd_compare` (rows with no split are kept for `test` and `dev`). `has_splits`
false → `notes: ["These runs have no dev/test split: all rows are compared."]`; differing
`suite_sha256` with overlapping ids → a note "Only the n shared rows are compared."; a `--limit`
run → a note naming it. Errors: `422 no_shared_rows`, `404 not_found`.

### `GET /api/runs/refs`

All model/suite runs for the RunPicker: `{"items": [{"root_id","lab","model","suite","calibrated","n","intelligence","ci95","limit","has_splits","finished_at"}]}`.
Route order: `/api/runs/refs` is registered before `/api/runs/:root_id` (a root id never equals
`refs`, since ids are base64url of paths containing `/` or longer than 4 chars, but the router
must not depend on that).

### `GET /api/runs/:root_id/calibration`

Query: `suite` (base name). For each model with a `+cal` run:

```json
{"suite": "synthetic", "items": [{"model": "v19", "temperatures": {"noul": 1.42, "choice": 1.1, "score": 0.93},
  "fit_split": "dev", "fit_rows": 360, "score_split": "test",
  "before": {"intelligence": 61.4, "nll": 0.512, "ece": 0.041}, "after": {"intelligence": 61.9, "nll": 0.488, "ece": 0.019},
  "delta": {"n_paired": 540, "intelligence": {"diff": 0.5, "ci95": [-0.8, 1.9], "verdict": "unclear"},
            "nll": {"diff": -0.024, "ci95": [-0.031, -0.017], "verdict": "better"}}}],
 "eligible": [{"model": "heuristic", "dev_rows_by_kind": {"noul": 120, "choice": 120, "score": 120}}],
 "ineligible": [{"model": "majority", "reason": "too few dev rows (12 of noul; needs 30)"}]}
```

`delta` is `metrics.compare(+cal, raw)` on the scored split (paired; 2,000 resamples), cached by
file mtimes. `eligible` lists runs that can be calibrated now (the UI's Calibrate buttons).

### `DELETE /api/runs/:root_id`

Body `{"confirm": "delete first-lab"}`. Removes the run root directory (only if it is inside the
workspace and contains `lab.json`, `report.json` or `<model>/<suite>/scores.json`; never a lab
file, data file or cache). `200 {"deleted": true, "freed_mb": 41.2}`. Errors: `409
confirm_mismatch`, `409 job_active` (a job's run root is this directory), `404 not_found`.

### `POST /api/runs/:root_id/report` — rebuild REPORT.md (CLI `report`)

Body: `{"baseline": "majority"}` (optional; default from `lab.json`). Runs `report.write`
synchronously (it is fast). `200 {"report_md": "…/REPORT.md", "report_json": "…/report.json"}`.

### Exports

| endpoint | returns |
|---|---|
| `GET /api/runs/:root_id/report.md` | `text/markdown` REPORT.md (attachment) |
| `GET /api/runs/:root_id/report.json` | `application/json` report.json (attachment) |
| `GET /api/runs/:root_id/leaderboard.csv?suite=&scores=raw\|cal\|both` | CSV: `model,variant,intelligence_local_proxy,ci95_lo,ci95_hi,accuracy,nll,ece,noul_in_band,errors,n,latency_p50,latency_p95,status,message` (failed rows included; the column name carries the proxy caveat because CSV has no room for a note) |
| `GET /api/runs/:root_id/leaderboard.md?suite=&scores=` | Markdown table with the same columns plus the `proxy_note` line and "95% CI" in the header (the "Copy as Markdown" action) |
| `GET /api/runs/:root_id/runs/:model/:suite/predictions.csv` | see above |

`404 not_found` if the file does not exist (e.g. no report yet).

---

## 8. Models

### `GET /api/models`

```json
{"cache_dir": "/Users/me/.cache/decider-lab/models", "total_gb": 12.6,
 "items": [{"model_key": "9a1f03be2c7d4e11", "source": "hf://StrandsAgents/strands-decider-2B-hobson-v19",
            "kind": "hf", "ref": "bb282d786bc251fd4e3068de3ada9ddbb38127cd", "ref_kind": "commit",
            "pinned": true, "size_gb": 4.12, "dir": "…/models/9a1f03be2c7d4e11", "pulled_at": "2026-10-04T09:00:00Z",
            "used_by_labs": ["first-lab"], "in_use_by_job": null}],
 "hf_cache_note": "Hugging Face snapshots live in the HF cache."}
```

From `sources.cached()`; `ref` = `resolved_commit` or `sha256` or `etags_sha256`; `ref_kind`
`commit|sha256|etags|none`; `pinned` true for a full commit requested at pull time or a verified
sha256; `pulled_at` is the cache directory mtime; `used_by_labs` by matching `serve:` specs.

### `POST /api/models/inspect`

Body: `{"source": "hf://StrandsAgents/strands-decider-2B-hobson-v19@main", "revision": null, "sha256": null, "require_pinned": true}`

```json
{"kind": "hf", "normalized": "hf://StrandsAgents/strands-decider-2B-hobson-v19@main",
 "pinned": false, "cached": false, "model_key": null,
 "problems": [{"severity": "error", "code": "unpinned_source",
               "message": "Pinned revisions are required: use a full 40-character commit.",
               "line": null, "column": null, "path": "source"}],
 "needs": {"env": ["HF_TOKEN?"], "profile": false},
 "command": "decider-lab pull hf://StrandsAgents/strands-decider-2B-hobson-v19@main --require-pinned"}
```

No network. Classification uses `sources.kind_of`. `needs.profile` true for s3. Local paths
must be inside the workspace or the home directory and exist. A source with URL user info or
presigned query parameters → problem `credential_in_source` (error), and `normalized` is
redacted. `cached: true` adds `"cached_info": {"model_key", "pulled_at", "ref"}`.

### Pull

`POST /api/jobs {"kind": "pull", …}` (section 6). Progress `bytes` parsed from the CLI log
when it reports sizes; otherwise indeterminate.

### `DELETE /api/models/:model_key`

Body: `{"confirm": "bb282d78"}`. Removes that cache directory only (never the HF cache).
`200 {"deleted": true, "freed_gb": 4.12}`. Errors: `409 confirm_mismatch`, `409 job_active` (a
running job uses it), `404 not_found`.

---

## 9. Data and suites

### `GET /api/suites`

```json
{"builtins": [
   {"ref": "smoke", "name": "smoke", "description": "90 generated rows (arithmetic, calendar, seating x 3 kinds x 10)…",
    "params": {}, "param_schema": {}, "available": true, "reason": null, "rows_estimate": 90, "cached": true},
   {"ref": "synthetic", "name": "synthetic", "description": "…",
    "params": {"per_kind": 150, "seed": 1, "families": ["arithmetic", "calendar", "seating"]},
    "param_schema": {"per_kind": {"type": "int", "min": 1, "max": 5000},
                     "seed": {"type": "int"},
                     "families": {"type": "multi", "options": ["arithmetic", "calendar", "seating", "chess"],
                                  "unavailable": {"chess": "needs the chess extra"}}},
    "available": true, "reason": null, "rows_estimate": 1350, "cached": false},
   {"ref": "heldout", "name": "heldout", "available": false, "reason": "needs the heldout extra: pip install 'decider-lab[heldout]'", "rows_estimate": null, "cached": false, "params": {}, "param_schema": {}, "description": "…"}],
 "files": [
   {"file_id": "…", "ref": "file:…", "path": "data/my_eval.jsonl", "rows": 1204, "valid": true,
    "problem": null, "modified_at": "…", "size_bytes": 812331}]}
```

`files`: `*.jsonl`/`*.jsonl.gz` in the workspace (depth ≤ 5, excluding `runs/`, state dir,
`.git`, `.venv`); `valid`/`problem` from `rows.check` (first problem only, e.g. `"row 17: label 4
out of range for 3 options"`), cached by mtime. Files > 200 MB are listed with `valid: null`.

### `GET /api/suites/:ref/stats`

`ref` percent-encoded (`synthetic%3Aper_kind%3D100`, `file%3A<file_id>`).
If the suite is cached or cheap (smoke, synthetic, files): `200`:

```json
{"ref": "smoke", "name": "smoke", "params": {}, "sha256": "3be1…",
 "rows": 90, "by_kind": {"noul": 30, "choice": 30, "score": 30},
 "by_task": {"synthetic/arithmetic": 30}, "by_split": {"None": 90},
 "label_balance": {"noul": {"0": 15, "1": 15}}, "with_images": 0}
```

(`data.stats` plus `sha256` = `rows_sha256`.) If building would be slow (heldout not cached):
`202 {"job_id": "j_…"}` (a `suite_build` job), then the client retries after the job finishes.
Errors: `422 suite_unavailable` (missing extra), `422 rows_invalid` with `detail.problem`.

### `GET /api/suites/:ref/rows`

Query `offset`, `limit` (≤ 200), `kind`, `split`, `task`, `q`. Items are rows as stored (state
truncated to 2,000 chars with `"state_truncated": true`).

### `POST /api/data/export-suite` (CLI `suites --build --out`)

Body `{"ref": "synthetic:per_kind=100", "out": "data/synthetic-100.jsonl", "overwrite": false}`.
`200 {"path": "data/synthetic-100.jsonl", "rows": 900, "file_id": "…"}`. Errors: `409 file_exists`.

### `POST /api/data/csv/preview` (multipart)

Fields: `file` (≤ 50 MB), `task` (default `custom`), `delimiter` (default `,`).
The upload is stored under `<state_dir>/uploads/<upload_id>.csv` (deleted after 24 h or on convert).

```json
{"upload_id": "u_5c2e91aa", "filename": "my.csv", "rows_total": 1204,
 "columns": {"found": ["state", "question", "answer", "options", "kind"], "missing_required": [], "ignored": ["notes"]},
 "preview": [ /* first 20 converted rows */ ],
 "errors": [{"row": 17, "message": "answer `maybe` is not one of the options"}],
 "stats": { /* data.stats of all converted rows, when no errors */ }}
```

Conversion uses `data.from_csv`; errors are collected per row (up to 100) instead of stopping at
the first.

### `POST /api/data/csv/convert` (CLI `data from-csv`)

Body `{"upload_id": "u_5c2e91aa", "out": "data/my.jsonl", "task": "custom", "delimiter": ",", "overwrite": false}`.
`200 {"path": "data/my.jsonl", "rows": 1204, "file_id": "…"}`. Errors: `422 rows_invalid`,
`409 file_exists`, `404 upload_not_found`.

### `POST /api/data/generate` (CLI `data generate`)

Body `{"out": "data/train.jsonl", "families": ["arithmetic", "calendar", "seating"], "per_kind": 500, "seed": 1, "exclude_suites": ["smoke", "synthetic:per_kind=100"], "overwrite": false}`.
`200 {"path": "data/train.jsonl", "rows": 4471, "dropped_overlapping": 29, "file_id": "…", "stats": {…}}`.
Runs synchronously when `per_kind × families × 3 ≤ 30,000`, else `202 {"job_id"}`.

### `POST /api/data/split` (CLI `data split`)

Body `{"file_id": "…", "out": "data/my-split.jsonl", "dev_fraction": 0.4, "seed": 0, "overwrite": false}`.
`200 {"path": "…", "by_split": {"dev": 482, "test": 722}, "file_id": "…"}`.
Validation: `0 < dev_fraction < 1`.

### `POST /api/data/check` (CLI `data check` / `data stats`)

Body `{"file_id": "…"}`. `200 {"valid": true, "problem": null, "stats": {…}}` or
`200 {"valid": false, "problem": "row 17: …", "stats": null}`.

### `POST /api/data/leakcheck` (CLI `data leakcheck`)

Body `{"train_file_id": "…", "against": ["smoke", "file:…"], "drop_to": "data/train-clean.jsonl", "overwrite": false}` (`drop_to` optional).

```json
{"train_rows": 4500, "eval_rows": 990, "overlapping": 29,
 "examples": [{"train_row": 12, "task": "train/calendar", "state": "Today is 2024-03-01. In 45 days…"}],
 "clean": {"path": "data/train-clean.jsonl", "rows": 4471, "file_id": "…"}}
```

`clean` null when `drop_to` is absent. (`overlapping_indices` is not returned.)

### `GET /api/files/raw`

Query `file_id`. Images only (see 7, rows): png, jpg/jpeg, gif, webp, sniffed by magic bytes,
served with the sniffed `Content-Type`; SVG and HTML are never served (script risk). Other types
→ `415 unsupported_media`.

---

## 10. Compute

All cloud endpoints accept and return data only for resources decider-lab created (vast label
prefix `decider-lab`, aws tag `decider-lab`), except identity, quotas, offers and Bedrock models.
Every response includes `"fake": true|false`. Cloud reads time out at 15 s →
`504 cloud_timeout`; missing tooling → `424 backend_unavailable` with the install hint.

### `GET /api/compute/doctor`

```json
{"checks": [{"status": "ok", "item": "decider-lab", "detail": "0.1.0 on Python 3.12.6"},
            {"status": "info", "item": "torch", "detail": "not installed (only needed to serve or train a model here)"},
            {"status": "warn", "item": "vast.ai", "detail": "CLI present, but: …"}],
 "machine": {"os": "macOS 15.6", "python": "3.12.6", "cpus": 12, "memory_gb": 36.0,
             "accelerator": "Apple MPS", "gpus": []},
 "counts": {"ok": 6, "warn": 1, "info": 4}}
```

`checks` = `doctor.checks()` (redacted). In fake mode the vast and aws rows are replaced by
fixture rows (no CLI/boto3 calls).

### vast.ai

#### `GET /api/compute/vast/status`

`{"fake": false, "cli": true, "api_key": true, "credit_usd": 25.4, "as_of": "2026-10-06T10:41:00Z", "error": null}` —
`api_key` is whether `vastai show user` works; the key itself is never read.

#### `GET /api/compute/vast/offers`

Query `gpu` (default `RTX_4090`), `num_gpus` (1), `max_price` (0.8), `disk_gb` (80),
`min_gpu_ram_gb` (0).

```json
{"fake": false, "items": [{"id": 1234567, "gpu_name": "RTX_4090", "num_gpus": 1, "dph_total": 0.342,
                           "gpu_ram_gb": 24, "cuda_max_good": 12.8, "reliability": 0.995,
                           "geolocation": "CA", "inet_down_mbps": 812, "disk_space_gb": 120}],
 "credit_usd": 25.4, "gpu_names": ["RTX_4090", "RTX_A6000", "L40S", "A100_SXM4", "A100_PCIE", "H100_SXM", "H100_PCIE"]}
```

Same query as `vast.offers()`; sorted by `dph_total`; max 50 items.

#### `GET /api/compute/vast/instances`

```json
{"fake": false, "items": [{"id": 9876543, "label": "decider-lab:first-lab", "status": "running",
                           "gpu": "1x A100_SXM4", "dph_total": 0.612, "started_at": "2026-10-06T10:42:30Z",
                           "uptime_s": 840, "ssh": "root@ssh4.vast.ai:22311", "job_id": "j_3f9a1c22be07",
                           "idle": false, "cost_so_far_usd": 0.14}]}
```

`job_id` when an active Studio job acquired it (matched by id from the job's log). `idle`: true
when no active Studio job owns it (it may belong to a CLI run outside Studio, or be left over by
`--keep`, a `lost` job or a failed release). `cost_so_far_usd = dph_total × uptime` (estimate).

#### `POST /api/compute/vast/instances/:id/destroy`

Body `{"confirm": "9876543"}`. Calls `vast.destroy(id)` (with verify). `200 {"destroyed": true}`
or `200 {"destroyed": false, "message": "Instance 9876543 is still listed. Check `vastai show instances`."}`.
Errors: `409 confirm_mismatch`, `404 not_found` (not a decider-lab instance).
If a running job owns it, the job's log gets the line and the job will fail; the UI warns before.

#### `POST /api/compute/vast/instances/destroy-all`

Body `{"confirm": "destroy all"}`. `200 {"results": [{"id": 9876543, "destroyed": true}]}`.

### AWS

All take `profile` (optional; default `AWS_PROFILE` or boto3 default chain) and `region`
(default `us-east-1`) as query params (body fields for POST).

#### `GET /api/compute/aws/profiles`

`{"fake": false, "items": ["default", "heisenberg"], "env_profile": null, "boto3": true}` — names
parsed from `~/.aws/config` and `~/.aws/credentials` section headers only (no values read).

#### `GET /api/compute/aws/identity`

`{"fake": false, "profile": "heisenberg", "region": "us-east-1", "account": "123456789012", "arn": "arn:aws:sts::123456789012:assumed-role/Admin/session", "ok": true, "error": null}`
(The account id is not a secret and is returned in full; the UI masks it by default for screen
sharing. No access key id, secret or session token is ever read or returned.)
On failure: `200` with `ok:false`, `error: "ExpiredToken: the SSO session has expired; run aws sso login --profile heisenberg"`.

#### `GET /api/compute/aws/quotas`

```json
{"fake": false, "items": [
  {"family": "g", "code": "L-DB2E81BA", "name": "Running On-Demand G and VT instances", "limit_vcpus": 8, "used_vcpus": 4},
  {"family": "p", "code": "L-417A185B", "name": "Running On-Demand P instances", "limit_vcpus": 0, "used_vcpus": 0},
  {"family": "standard", "code": "L-1216C47A", "name": "Running On-Demand Standard (A, C, D, H, I, M, R, T, Z) instances", "limit_vcpus": 64, "used_vcpus": 2}],
 "error": null}
```

`used_vcpus` from `DescribeInstances` (running/pending, vCPUs by type) or `null` if not
computable. Per-item `error` when a quota read is denied (names the IAM action
`servicequotas:GetServiceQuota`).

#### `GET /api/compute/aws/instance-types`

`{"items": [{"type": "g6e.xlarge", "gpu": "1x L40S 48 GB", "vcpus": 4, "family": "g", "usd_per_hour": 1.861}], "as_of": "2026-09-01", "region_note": "us-east-1 on-demand list prices; approximate"}`
from the static table (no AWS call).

#### `GET /api/compute/aws/instances`

`{"fake": false, "items": [{"id": "i-0abc123def4567890", "type": "g6e.xlarge", "state": "running", "lab": "first-lab", "launched_at": "…", "uptime_s": 840, "usd_per_hour": 1.861, "public_ip_masked": "3.91.x.x", "job_id": null, "idle": true}]}`
(`aws.tagged_instances`; the server masks the last two octets of the public IP and never
returns the full address; `usd_per_hour` is the static list price, `null` for unknown types;
`idle` as for vast.)

#### `POST /api/compute/aws/instances/:id/terminate`

Body `{"confirm": "i-0abc123def4567890", "profile": "heisenberg", "region": "us-east-1"}`.
Only instances tagged `decider-lab` (`404 not_found` otherwise). `200 {"terminating": ["i-0abc…"]}`.

#### `POST /api/compute/aws/instances/terminate-all`

Body `{"confirm": "terminate all", "profile", "region"}`. `200 {"terminating": [...]}`.

#### `GET /api/compute/aws/bedrock-models`

Query `profile`, `region`, `q`.
`{"fake": false, "items": [{"model_id": "amazon.nova-pro-v1:0", "invoke_id": "us.amazon.nova-pro-v1:0", "name": "Nova Pro", "provider": "Amazon", "input_modalities": ["TEXT", "IMAGE"], "output_modalities": ["TEXT"], "status": "ACTIVE", "spec_yaml": "{bedrock: us.amazon.nova-pro-v1:0, region: us-east-1}"}], "error": null}`
From `ListFoundationModels` (text output only) joined with `ListInferenceProfiles` (system
profiles) to give the `invoke_id` the SDK expects. Read-only.

### SSH hosts

Stored in `<state_dir>/ssh_hosts.json` (no key material; key **path** only).

#### `GET /api/compute/ssh/hosts`

`{"items": [{"host_id": "h_91ab02cd", "name": "gpu-box", "target": "ubuntu@10.0.0.5:22", "user": "ubuntu", "address": "10.0.0.5", "port": 22, "key_path": "~/.ssh/id_ed25519", "key_exists": true, "work_dir": null, "last_test": {"at": "…", "ok": true, "latency_ms": 42, "gpu": "1x NVIDIA L40S, 46068 MiB", "error": null}}]}`

#### `POST /api/compute/ssh/hosts`

Body `{"name": "gpu-box", "target": "ubuntu@10.0.0.5:22", "key_path": "~/.ssh/id_ed25519", "work_dir": null}`.
Validation: name `^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$` unique; target `user@host[:port]` with no
whitespace or shell metacharacters; key_path a path string (existence reported, not required).
`201` the host. Errors: `409 name_taken`, `422 bad_request`.

#### `PUT /api/compute/ssh/hosts/:host_id`, `DELETE /api/compute/ssh/hosts/:host_id`

PUT body as POST (full replace) → `200` host. DELETE → `200 {"deleted": true}` (no typed
confirm; bookmarks only).

#### `POST /api/compute/ssh/hosts/:host_id/test`

Runs `compute.base.Host(...).ssh("nvidia-smi --query-gpu=name,memory.total --format=csv,noheader || echo no-gpu", timeout=20)`
(BatchMode, so it never prompts). `200 {"ok": true, "latency_ms": 42, "gpu": "1x NVIDIA L40S, 46068 MiB", "error": null, "at": "…"}`
or `200 {"ok": false, "latency_ms": null, "gpu": null, "error": "Permission denied (publickey).", "at": "…"}`.
The result is stored as `last_test`.

---

## 11. Shared types (TypeScript, for `ui/src/api/types.ts`)

```ts
export type ApiError = { error: { code: ErrorCode; message: string; hint?: string; detail?: Record<string, unknown>; request_id: string } };
export type Page<T> = { items: T[]; total: number; offset: number; limit: number };
export type Backend = "local" | "ssh" | "aws" | "vast";

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
export type TelemetrySample = { ts: string; gpus: { index: number; util_pct: number | null; mem_used_gb: number | null;
                                temp_c: number | null; power_w: number | null }[]; rows_per_s: number | null; errors_total: number | null;
                                train_loss?: number | null };
export type DoctorCheck = { status: "ok" | "warn" | "info"; item: string; detail: string };
export type Verdict = "better" | "worse" | "unclear";
export type Diff = { diff: number | null; ci95: CI; verdict?: Verdict };
```

(Plus every type defined inline in sections 5–10.)

---

## 12. Error codes

### 12.1 HTTP error codes

| code | status | when | detail |
|---|---|---|---|
| `unauthorized` | 401 | no/invalid session or bearer | — |
| `forbidden_origin` | 403 | missing `X-Studio` or foreign `Origin` | — |
| `misdirected` | 421 | Host header not loopback:port | — |
| `not_found` | 404 | unknown id/path/model/instance | `{"what": "lab"}` |
| `bad_request` | 422 | body/query validation | `{"fields": [{field, message}]}` |
| `path_outside_workspace` | 400 | id or path resolves outside workspace | — |
| `not_a_lab` | 422 | YAML without `models:` | — |
| `lab_invalid` | 422 | run on a lab with errors | `{"problems": Problem[]}` |
| `dir_not_empty` | 409 | create lab into a non-empty dir | `{"dir"}` |
| `file_exists` | 409 | write target exists and `overwrite` false | `{"path"}` |
| `etag_mismatch` | 409 | lab changed on disk | `{"current_etag", "modified_at"}` |
| `precondition_required` | 428 | PUT lab without If-Match | — |
| `too_large` | 413 | body/upload over limit | `{"limit_bytes"}` |
| `confirm_mismatch` | 409 | typed confirmation wrong/missing | `{"expected_hint"}` |
| `run_blocked` | 409 | estimate blockers at start | `{"blockers": string[]}` |
| `job_active` | 409 | conflicting active job | `{"job_id"}` |
| `job_not_active` | 409 | cancel a finished job | — |
| `too_few_dev_rows` | 409 | calibrate without ≥ 30 dev rows of any kind | `{"dev_rows_by_kind"}` |
| `source_invalid` | 422 | pull source not parseable | `{"problems"}` |
| `unpinned_source` | 422 | require_pinned and not a full commit | — |
| `sha256_mismatch` | — | (job failure reason, not HTTP) | — |
| `suite_unavailable` | 422 | suite needs a missing extra | `{"extra"}` |
| `rows_invalid` | 422 | JSONL/CSV rows fail `rows.check` | `{"problem"}` or `{"errors"}` |
| `upload_not_found` | 404 | unknown/expired upload_id | — |
| `secret_placeholder_unknown` | 422 | PUT lab with `••••` at a path that held no secret on disk | `{"paths"}` |
| `credential_in_source` | 422 | model source with URL user info or presigned query | — |
| `no_shared_rows` | 422 | compare runs with no common ids | — |
| `use_jevbench_endpoint` | 400 | leaderboard with suite=jevbench | — |
| `unsupported_media` | 415 | raw file not an image | — |
| `name_taken` | 409 | ssh host name exists | — |
| `backend_unavailable` | 424 | vastai/boto3/extra missing or no credentials | `{"install": "pip install 'decider-lab[aws]'"}` |
| `cloud_error` | 502 | cloud API error | `{"provider", "aws_code"?}` |
| `cloud_timeout` | 504 | cloud read > 15 s | — |
| `internal` | 500 | unexpected | — |

### 12.2 Lab problem codes (`Problem.code`)

`yaml_syntax`, `not_a_mapping`, `no_models`, `unknown_model_ref` (baseline/jevbench/only),
`unknown_model_kind`, `unknown_key` (warning), `unknown_baseline`, `bad_source`,
`unpinned_source` (warning; error under `require_pinned_default` only at run time for `serve`),
`unverified_source` (warning; error with `require_sha256`), `local_path_missing`,
`unknown_suite`, `suite_unavailable` (warning: missing extra), `suite_file_missing`,
`finetune_no_train`, `finetune_train_missing`, `bad_compute_backend`, `unknown_compute_option`
(warning), `env_not_set` (warning), `secret_literal`, `calibrate_no_splits` (warning),
`jevbench_needs_systemone` (error when a jevbench model is not `serve`/`url`/`finetuned`),
`workers_invalid`, `paid_api_model` (warning on each `bedrock`/`strands`/remote
`chat` model: "billed per request"), `credential_in_source` (error: a `serve`/`url`/`chat`
source with URL user info or a presigned query).

---

## 13. Endpoint index

| method | path | section |
|---|---|---|
| GET | `/?token=` | 1.2 |
| GET | `/api/health` | 3 |
| GET | `/api/meta` | 3 |
| GET | `/api/overview` | 3 |
| GET, PUT | `/api/settings` | 4 |
| GET | `/api/settings/env` | 4 |
| GET | `/api/about` | 4 |
| GET, POST | `/api/labs` | 5 |
| POST | `/api/labs/import` | 5 |
| GET | `/api/templates` | 5 |
| GET, PUT, DELETE | `/api/labs/:lab_id` | 5 |
| POST | `/api/labs/validate` | 5 |
| POST | `/api/labs/:lab_id/duplicate` | 5 |
| POST | `/api/labs/:lab_id/fix-secret` | 5 |
| POST | `/api/labs/:lab_id/estimate` | 5 |
| GET, POST | `/api/jobs` | 6 |
| GET, DELETE | `/api/jobs/:job_id` | 6 |
| POST | `/api/jobs/:job_id/cancel` | 6 |
| GET | `/api/jobs/:job_id/log` | 6 |
| GET | `/api/jobs/:job_id/log.txt` | 6 |
| GET | `/api/jobs/:job_id/telemetry` | 6 |
| GET (SSE) | `/api/jobs/:job_id/events` | 6 |
| GET (SSE) | `/api/events` | 6 |
| GET | `/api/runs` | 7 |
| GET | `/api/runs/refs` | 7 |
| GET, DELETE | `/api/runs/:root_id` | 7 |
| GET | `/api/runs/:root_id/calibration` | 7 |
| GET | `/api/runs/:root_id/leaderboard.md` | 7 |
| GET | `/api/runs/:root_id/leaderboard` | 7 |
| GET | `/api/runs/:root_id/leaderboard.csv` | 7 |
| GET | `/api/runs/:root_id/vs-baseline` | 7 |
| GET | `/api/runs/:root_id/families` | 7 |
| GET | `/api/runs/:root_id/latency` | 7 |
| GET | `/api/runs/:root_id/jevbench` | 7 |
| GET | `/api/runs/:root_id/rows` | 7 |
| GET | `/api/runs/:root_id/rows/:row_id` | 7 |
| GET | `/api/runs/:root_id/provenance` | 7 |
| POST | `/api/runs/:root_id/report` | 7 |
| GET | `/api/runs/:root_id/report.md`, `/report.json` | 7 |
| GET | `/api/runs/:root_id/runs/:model/:suite` | 7 |
| GET | `/api/runs/:root_id/runs/:model/:suite/predictions` | 7 |
| GET | `/api/runs/:root_id/runs/:model/:suite/predictions.csv` | 7 |
| GET | `/api/runs/:root_id/runs/:model/:suite/reliability` | 7 |
| GET | `/api/runs/:root_id/runs/:model/:suite/latency` | 7 |
| GET | `/api/compare` | 7 |
| GET | `/api/models` | 8 |
| POST | `/api/models/inspect` | 8 |
| DELETE | `/api/models/:model_key` | 8 |
| GET | `/api/suites` | 9 |
| GET | `/api/suites/:ref/stats` | 9 |
| GET | `/api/suites/:ref/rows` | 9 |
| POST | `/api/data/export-suite` | 9 |
| POST | `/api/data/csv/preview` | 9 |
| POST | `/api/data/csv/convert` | 9 |
| POST | `/api/data/generate` | 9 |
| POST | `/api/data/split` | 9 |
| POST | `/api/data/check` | 9 |
| POST | `/api/data/leakcheck` | 9 |
| GET | `/api/files/raw` | 9 |
| GET | `/api/compute/doctor` | 10 |
| GET | `/api/compute/vast/status` | 10 |
| GET | `/api/compute/vast/offers` | 10 |
| GET | `/api/compute/vast/instances` | 10 |
| POST | `/api/compute/vast/instances/:id/destroy` | 10 |
| POST | `/api/compute/vast/instances/destroy-all` | 10 |
| GET | `/api/compute/aws/profiles` | 10 |
| GET | `/api/compute/aws/identity` | 10 |
| GET | `/api/compute/aws/quotas` | 10 |
| GET | `/api/compute/aws/instance-types` | 10 |
| GET | `/api/compute/aws/instances` | 10 |
| POST | `/api/compute/aws/instances/:id/terminate` | 10 |
| POST | `/api/compute/aws/instances/terminate-all` | 10 |
| GET | `/api/compute/aws/bedrock-models` | 10 |
| GET, POST | `/api/compute/ssh/hosts` | 10 |
| PUT, DELETE | `/api/compute/ssh/hosts/:host_id` | 10 |
| POST | `/api/compute/ssh/hosts/:host_id/test` | 10 |

Backend test expectations (each row of this index has at least one test in `tests/ui/`, with
`DECIDER_LAB_FAKE_CLOUD=1`, a temp workspace and `DECIDER_LAB_CACHE` pointed at a temp dir):
auth (401 without token, 421 bad Host, 403 without `X-Studio` on POST), security headers present,
every typed-confirmation endpoint rejects a wrong phrase (including `call paid apis` and the
`keep` suffix), no response body in the whole suite contains the value of a secret env var set
by the test fixture or a secret literal written into a fixture lab (`sk-test…` in `api_key:`,
`https://user:pw@…` in a `url:` model, an `X-Amz-Signature` in a log line), `PUT` with masked
placeholders preserves the on-disk secret, log `q` search for a secret value returns no match,
and no test touches the network except loopback.
