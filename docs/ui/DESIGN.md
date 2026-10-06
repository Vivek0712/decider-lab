# decider-lab Studio: design

Status: v1 contract. Companion: [API.md](API.md) (every endpoint named here is defined there).
Audience: the four engineers building Studio in parallel, and whoever reviews them. When this
file and API.md disagree on a wire shape, API.md wins; on anything a person sees, this file wins.

Studio is a local web portal for decider-lab, started with `decider-lab ui`. It lets a person
do everything the CLI does, visually, and see everything that is happening while it happens.
It never replaces the CLI: long operations run as the real `decider-lab` command in a tracked
job, and every job shows the exact command, so the UI teaches the CLI instead of hiding it.

Contents

1. Principles and non-goals
2. Architecture, ownership and conventions
3. Information architecture and navigation
4. Pages (wireframes, actions, states)
5. Design system (tokens, type, spacing, radii, elevation, motion)
6. Component inventory (props)
7. Charts
8. Command palette and keyboard shortcuts
9. Accessibility
10. Copy guidelines
11. CLI parity map
12. Test ids and e2e coverage map

---

## 1. Principles and non-goals

| principle | what it means in the product |
|---|---|
| Local-first | Server binds 127.0.0.1 only. Token auth like Jupyter (URL token once, then an HttpOnly cookie). No analytics, no fonts or scripts from a CDN at runtime (all assets bundled), no request leaves the machine except the ones the person asked for (a pull, a cloud call, a remote run). |
| Honest numbers | "Intelligence" is always labelled **local proxy** (JevBench v1.5 rules on this suite) and never called a score, a rank or a board number. Every comparison shows its 95% CI. Every row that failed is visible and counted; nothing is filtered out silently. Calibrated runs are labelled `+cal` and are never mixed into a raw column. |
| Safe by default | Anything that spends money (remote run on vast or aws) or destroys something (destroy instance, terminate instance, delete a cached model, delete a lab file, delete a job record with its logs) needs a typed confirmation. The server enforces it (API.md, "Typed confirmation"), not just the dialog. Secrets are never displayed, logged or returned. AWS and other credentials are referenced by environment-variable name or profile name only. |
| Fast and calm | Dark-first, with a light theme. Keyboard-first (cmd/ctrl+K palette, `g` chords). No spinners longer than needed: skeletons for loads, optimistic UI only for reversible actions. No toasts for errors that need action: errors sit where the action was. |

Non-goals for v1: multi-user, remote access, editing predictions, scheduling, a hosted
version, authoring adapters in the browser (Python adapters are referenced, not written).

---

## 2. Architecture, ownership and conventions

### 2.1 Shape

```
browser (React SPA)  --REST/JSON + SSE-->  Studio server (Python, in-process with the SDK)
                                             |-- reads: imports decider_lab (lab, report, metrics, data, sources, doctor)
                                             |-- long work: spawns `decider-lab ...` subprocesses as jobs
                                             |-- state: <workspace>/.decider-lab-studio/ (jobs, settings, ssh hosts)
                                             '-- cloud reads: vast CLI / boto3, or fixtures when DECIDER_LAB_FAKE_CLOUD=1
```

- Server: FastAPI + uvicorn, shipped as the optional extra `decider-lab[ui]`, code in
  `src/decider_lab/ui/` (`server.py`, `jobs.py`, `routes/*.py`, `fakes/`, `static/`).
- Frontend: React 18 + TypeScript + Vite in `ui/` at the repo root; `npm run build` writes into
  `src/decider_lab/ui/static/` which the wheel ships. Routing: react-router (browser history,
  server falls back to `index.html` for unknown non-`/api` paths). Data: TanStack Query.
  Charts: hand-written SVG components using `d3-scale`, `d3-array`, `d3-shape` only (no chart
  library: CI whiskers, a11y tables and theming must be exact). YAML editor: CodeMirror 6 with
  `@codemirror/lang-yaml`. Fonts bundled (`@fontsource/inter`, `@fontsource/jetbrains-mono`).
- Tests: backend `tests/ui/test_*.py` (pytest, FastAPI TestClient, `DECIDER_LAB_FAKE_CLOUD=1`);
  e2e `ui/e2e/*.spec.ts` (Playwright, chromium, viewport 1440x900 and 390x844).

### 2.2 Who builds what (no two people edit the same file)

| engineer | owns | files |
|---|---|---|
| A: platform backend | `decider-lab ui` command, auth, settings, workspace scan, labs CRUD + validation, jobs (spawn, parse, cancel, SSE, telemetry sampler, redaction), meta/overview | `src/decider_lab/ui/{server,auth,jobs,redact,telemetry}.py`, `routes/{meta,labs,jobs,settings,overview}.py`, `tests/ui/test_{auth,labs,jobs,settings}.py` |
| B: data backend | results, compare, export, models, data tools, suites, compute (doctor, vast, aws, ssh) and all fakes/fixtures | `routes/{runs,compare,models,data,suites,compute}.py`, `ui/fakes/*`, `tests/ui/test_{runs,models,data,compute}.py` |
| C: frontend shell | app shell, design tokens, all shared components, palette, shortcuts, Overview, Labs, Jobs, Settings pages | `ui/src/{app,theme,components,palette,hooks}/**`, `ui/src/pages/{Overview,Labs,Jobs,Settings}/**`, `ui/e2e/{shell,overview,labs,jobs,settings}.spec.ts` |
| D: frontend analysis | charts, Results, Models, Data, Compute pages | `ui/src/charts/**`, `ui/src/pages/{Results,Models,Data,Compute}/**`, `ui/e2e/{results,models,data,compute}.spec.ts` |

Shared, written first by C in a single commit before anything else: `ui/src/api/types.ts` (the
TypeScript types transcribed from API.md), `ui/src/api/client.ts`, `ui/src/theme/tokens.css`.
B and D start from fixture runs created by `tests/ui/conftest.py::seeded_workspace` (A owns
that fixture; it runs a real lab with baselines on `smoke` and `synthetic:per_kind=40` so that
every page has data with no cloud and no GPU).

### 2.3 Conventions

- Workspace: the directory Studio was started in (`--workspace`, default cwd). Labs are the
  `*.yaml`/`*.yml` files under it (depth <= 4, skipping `.git`, `.venv`, `node_modules`, `runs`)
  whose top level has a `models:` key. Results are run roots: any directory holding
  `report.json` or `lab.json`, normally `<lab dir>/runs/<lab name>/`.
- Ids are opaque strings issued by the server (base64url of the workspace-relative path for
  labs, run roots and data files). The frontend never builds an id from a path.
- Numbers: Intelligence 1 decimal, accuracy 1 decimal with `%`, NLL/ECE/Brier 3 decimals,
  latency 2 decimals with `s` (ms below 1 s: `840 ms`), money `$0.000/h` for rates and `$0.00`
  for totals, sizes `12.4 GB`. Tabular numerals everywhere numbers align. A missing value is an
  em dash `—` with a tooltip saying why ("no dev rows", "not run", "failed").
- Times: relative in lists ("4 min ago"), absolute ISO local time in tooltips and provenance.
- URLs are state: every filter, tab, selected suite, toggle and comparison lives in the query
  string so a view can be reloaded and pasted.

---

## 3. Information architecture and navigation

```
Studio
├── Overview                 /
├── Labs                     /labs
│   ├── New lab              /labs/new
│   └── Lab                  /labs/:labId            tabs: Summary | Editor | Runs
│       └── Run dialog       /labs/:labId?run=1
├── Jobs                     /jobs
│   └── Job                  /jobs/:jobId            tabs: Progress | Logs | Telemetry | Command
├── Results                  /results
│   ├── Run root             /results/:rootId        tabs: Leaderboard | Vs baseline | Families |
│   │                                                      Calibration | Latency | JevBench | Rows | Provenance
│   ├── Single run           /results/:rootId/:model/:suite
│   └── Compare two runs     /results/compare?a=&b=&split=
├── Models                   /models                 tabs: Cache | Pull
├── Data                     /data                   tabs: Suites | Files | Upload CSV | Generate | Split | Leakcheck
├── Compute                  /compute                tabs: Local | vast.ai | AWS | SSH hosts
└── Settings                 /settings               tabs: Workspace | Appearance | Environment | About
```

### 3.1 App shell

```
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ [◆ decider-lab Studio]  workspace: ~/labs/eval-q3  ▾      [⌘K Search or run a command]  ●2 jobs  ◐  │  topbar 56px
├───────────────┬──────────────────────────────────────────────────────────────────────────┤
│ ▣ Overview    │  Page title                                   [secondary] [Primary action]│
│ ▤ Labs        │  one-line description of the page                                        │
│ ▶ Jobs     2  │ ─────────────────────────────────────────────────────────────────────────│
│ ▥ Results     │                                                                          │
│ ◫ Models      │  content (max-width 1440, 24px gutters; 16px below 640px)                │
│ ≡ Data        │                                                                          │
│ ⚙ Compute     │                                                                          │
│               │                                                                          │
│ ─────────     │                                                                          │
│ Settings      │                                                                          │
│ FAKE CLOUD    │  ← amber pill, only when DECIDER_LAB_FAKE_CLOUD=1                        │
│ v0.1.0        │                                                                          │
└───────────────┴──────────────────────────────────────────────────────────────────────────┘
  sidebar 232px (collapsible to 64px icon rail with `[`; at < 1024px it becomes a drawer)
```

- Topbar: logo (link to Overview), workspace path (truncate middle; menu: copy path, open
  Settings > Workspace), palette trigger (also the global search), active-jobs badge (count of
  `running`+`queued`; click opens a popover listing them with progress bars and a link to Jobs),
  theme toggle (cycles system → dark → light; `aria-label` says the next state).
- At < 640px (down to 390px): topbar shows logo, palette icon button, jobs badge, menu button;
  sidebar is a left drawer (focus-trapped, closes on Esc and route change); page actions move
  into a sticky bottom action bar (max two buttons, primary on the right); tables become cards
  (see `DataTable` `mobile="cards"`); tabs become a horizontally scrollable tab strip with
  scroll shadows; charts keep their aspect ratio and get a "View as table" toggle.
- Fake cloud: when the server reports `fake_cloud: true`, the sidebar shows an amber
  `FAKE CLOUD` pill (tooltip: "Cloud calls return fixtures. Nothing is created or billed.") and
  every cloud panel header repeats a smaller `fixtures` badge.
- Connection banner: if `/api/meta` fails or SSE drops for > 5 s, a non-modal banner under the
  topbar: "Lost connection to the Studio server. Retrying…" with a Retry button. If the server
  answers 401: full-page "Session expired" with the instruction to reopen the URL printed by
  `decider-lab ui` (the token is never shown in the UI).

---

## 4. Pages

Every action below lists its states. Shared rules, so they are not repeated per page:

- **Loading**: skeleton shapes of the final layout (`Skeleton`), never a centered spinner for
  page loads. Button actions show an inline spinner inside the button and keep its width.
- **Error (load)**: `ErrorState` in place of the failed region only (a failed chart does not
  blank the page): title from `error.message`, the `hint` if present, a Retry button, and a
  "Details" disclosure with `code` and `request_id`.
- **Error (action)**: inline `Callout tone="danger"` directly above the action's buttons in the
  dialog or form; field errors under the field (`aria-describedby`). Toasts only for success.
- **Empty**: `EmptyState` with one sentence of what this is, one primary action, and, when
  there is a CLI equivalent, the command in a copyable `CodeInline`.
- **Running**: anything that becomes a job immediately navigates to nothing; it shows a toast
  "Started: <job title>" with a "View job" link and the active-jobs badge increments. The
  originating control shows a `JobChip` (status dot + progress) until the job ends.
- **Success**: toast for 4 s (`role="status"`), and the affected query is invalidated.

### 4.1 Overview `/`

```
┌ Overview ───────────────────────────────────────────────────────── [Quick eval] [New lab] ┐
│ ┌ KPI ──────────┐ ┌ KPI ──────────┐ ┌ KPI ──────────┐ ┌ KPI ──────────┐ ┌ KPI ─────────┐ │
│ │ Labs          │ │ Runs scored   │ │ Best proxy    │ │ Active jobs   │ │ Cloud spend   │ │
│ │ 4             │ │ 38            │ │ 61.4 v19/syn  │ │ 2 (1 remote)  │ │ $0.82/h now   │ │
│ │ 1 invalid     │ │ 3 with errors │ │ CI 57.9–64.8  │ │               │ │ 1 instance    │ │
│ └───────────────┘ └───────────────┘ └───────────────┘ └───────────────┘ └───────────────┘ │
│ ┌ Active jobs ─────────────────────────────────┐ ┌ Quick actions ─────────────────────────┐│
│ │ ● run first-lab on vast  bootstrap  ▓▓▓░░ 3/7│ │ ▸ Run a lab…          ▸ Pull a model…   ││
│ │ ● pull hf://Strands…/v19  412 MB/4.1 GB      │ │ ▸ Quick eval…         ▸ Upload CSV…     ││
│ │                                   View all → │ │ ▸ Compare two runs…   ▸ Check machine   ││
│ └──────────────────────────────────────────────┘ └────────────────────────────────────────┘│
│ ┌ Recent results ──────────────────────────────────────────────────────────────────────────┐│
│ │ lab        model     suite        Intelligence (proxy) 95% CI     acc    errors  when     ││
│ │ first-lab  v19       synthetic    61.4  ├──●──┤ 57.9–64.8         78.2%  0       2 h ago  ││
│ │ first-lab  majority  synthetic     0.0  ├●┤     0.0–0.0           50.4%  0       2 h ago  ││
│ │ nova-lab   nova      smoke        22.1  ├───●───┤ 9.3–34.0        61.1%  4 ⚠     1 d ago  ││
│ └──────────────────────────────────────────────────────────────────────────────────────────┘│
│ Intelligence is a local proxy computed with the JevBench v1.5 rules on these suites. It is   │
│ not a JevBench board score.                                                                   │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
```

Data: `GET /api/overview`. KPI cards link to the filtered page (Labs with `?invalid=1`, Results,
the best run, Jobs `?status=active`, Compute). "Cloud spend" shows the sum of `dph` of running
instances the Studio knows about (vast + aws fixtures or reads); if no cloud backend is
configured it shows `—` with "No cloud backend configured".

Onboarding (shown instead of KPIs when the workspace has no labs and no runs; dismissible,
remembered in settings `onboarding_dismissed`):

```
┌ Welcome to decider-lab Studio ───────────────────────────────────────────────────────────┐
│ ① Check this machine          OK 6 · warn 1 · info 4             [Open doctor]  ✓ done   │
│ ② Create a lab                from the eval or finetune template [New lab]               │
│ ③ Run it                      smoke takes seconds, no GPU needed  [Run]  (disabled until ②)│
│ ④ Read the results            leaderboard, CIs, vs baseline       [Open results]         │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

Each step's done state is derived from the server (`overview.onboarding`), not clicks.

| action | states |
|---|---|
| Quick eval (dialog, see 4.3.5) | idle, validating, error (inline), started (toast + JobChip) |
| New lab | navigates to `/labs/new` |
| KPI click | navigation |
| Recent result row click | `/results/:rootId/:model/:suite` |
| Onboarding step buttons | as their target actions |

Empty (has labs, no runs): Recent results shows `EmptyState` "No scored runs yet. Run a lab to
see results here." [Run a lab…] plus `decider-lab run lab.yaml`.

### 4.2 Labs `/labs`

```
┌ Labs ──────────────────────────────────────────────── [Import file…] [New lab] ┐
│ [Search labs…              ]  [Status: All ▾]                                     │
│ name          file                      models  suites  compute   last run  status│
│ first-lab     first/lab.yaml            3       2       local     2 h ago   ✓ valid│
│ nova-lab      nova/lab.yaml             2       1       local     1 d ago   ✓ valid│
│ tune-v19      tune/lab.yaml             2 (+1 ft) 1     vast      never     ✕ 1 error│
└──────────────────────────────────────────────────────────────────────────────────┘
```

Row click → `/labs/:labId`. Row overflow menu: Open, Run…, Duplicate…, Reveal path (copies),
Delete file… (typed confirm with the lab name; runs are kept and the dialog says so).

Empty: "No labs in this workspace. A lab is one YAML file that says which models answer which
suites." [New lab] and `decider-lab init my-lab`.

#### 4.2.1 New lab `/labs/new`

```
┌ New lab ──────────────────────────────────────────────────────────────────────┐
│ Template   (●) Evaluate   models on suites, compare with a baseline          │
│            ( ) Fine-tune  train on your rows, then evaluate                   │
│ Name       [first-lab        ]  lowercase, digits, - and _                     │
│ Directory  [labs/first-lab   ]  inside the workspace; must not exist or be empty│
│ Preview    ┌ lab.yaml (read-only CodeMirror) ──────────────────────────────┐  │
│            └───────────────────────────────────────────────────────────────┘  │
│                                                        [Cancel] [Create lab]   │
└────────────────────────────────────────────────────────────────────────────────┘
```

`POST /api/labs`. States: idle; invalid name/dir (field errors, Create disabled);
creating (button spinner); error `dir_not_empty` (field error on Directory); success →
navigate to the new lab's Editor tab with toast "Created first-lab".

#### 4.2.2 Lab `/labs/:labId`

```
┌ first-lab  ✓ valid   labs/first/lab.yaml                       [Run…] [⋯] ┐
│ [Summary] [Editor] [Runs]                                                    │
├──────────────────────────────────────────────────────────────────────────────┤
│ SUMMARY                                                                      │
│ ┌ Models (3) ────────────────────────────────────────────────────────────┐  │
│ │ v19        serve  hf://StrandsAgents/…-v19@bb282d7  pinned ✓   GPU/MPS │  │
│ │ heuristic  python my_model:Heuristic                                    │  │
│ │ majority   baseline majority                       ★ baseline          │  │
│ └─────────────────────────────────────────────────────────────────────────┘  │
│ ┌ Suites (2) ───────────────┐ ┌ Options ───────────────────────────────────┐ │
│ │ smoke        90 rows      │ │ calibrate  on (needs ≥30 dev rows per kind) │ │
│ │ synthetic    per_kind 100 │ │ baseline   majority                         │ │
│ │              900 rows     │ │ jevbench   —                                │ │
│ └───────────────────────────┘ │ workers    8                                │ │
│ ┌ Fine-tune (0) ────────────┐ │ compute    local · max 2 h                  │ │
│ │ none                      │ └─────────────────────────────────────────────┘ │
│ └───────────────────────────┘                                               │
│ Plan: 3 models × 2 suites = 6 runs (+ up to 6 calibrated), ≈ 2,970 requests │
└──────────────────────────────────────────────────────────────────────────────┘
```

Summary is computed by the server (`GET /api/labs/:id` → `summary`). Model kind badges use the
kinds in API.md (`serve`, `url`, `bedrock`, `strands`, `chat`, `python`, `baseline`,
`finetuned`). Warnings render inline on the item they concern (e.g. "hf:// source not pinned to
a commit: results may not be reproducible", "chat model reads its key from OPENAI_API_KEY (not
set)", "baseline names a model not in models"). Secret-looking literal values in the YAML
(`api_key:`, `aws_secret_access_key:`, strings matching key patterns) are a validation
**error** with the fix: "Put the key in an environment variable and reference it with
`api_key_env: NAME`."

Editor tab:

```
┌ Editor ─────────────────────────────────────── saved 2 min ago · [Revert] [Save ⌘S] ┐
│ ┌ lab.yaml ─────────────────────────────────────┐ ┌ Problems (1) ─────────────────┐ │
│ │  1 name: first-lab                              │ │ ✕ line 14: baseline `majorty` │ │
│ │  2 workers: 8                                   │ │   is not in models. Did you   │ │
│ │ …                                               │ │   mean `majority`?            │ │
│ │ 14 baseline: majorty  ~~~~~~~                   │ ├ Visual summary ───────────────┤ │
│ │                                                 │ │ (same cards as Summary, live) │ │
│ └─────────────────────────────────────────────────┘ └───────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

- Validation runs on change (debounced 400 ms) with `POST /api/labs/validate` (no write);
  problems show as CodeMirror lint markers and in the Problems list (click jumps to line).
- Save: `PUT /api/labs/:id` with `If-Match: <etag>`. States: clean (Save disabled), dirty (dot
  in tab title, Save enabled, `beforeunload` guard and in-app navigation guard dialog), saving,
  saved (toast), invalid-but-saved (allowed: a person may save a work in progress; the status
  pill turns to "✕ n errors" and Run is disabled), conflict 409 (`Callout`: "lab.yaml changed on
  disk since you opened it." [Load disk version] [Overwrite]; Overwrite re-sends with
  `If-Match: *`).
- At < 640px the Problems/summary panel stacks below the editor.

Runs tab: run roots of this lab (from `GET /api/runs?lab_id=:id`) as `RunRootCard`s plus the
lab's jobs (`GET /api/jobs?lab_id=:id`). Empty: "This lab has not been run yet." [Run…].

#### 4.2.3 Run dialog `?run=1`

```
┌ Run first-lab ──────────────────────────────────────────────────────────────────┐
│ Where   [ Local ] [ SSH ] [ AWS ] [ vast.ai ]   (segmented; default = lab compute)│
│ ── vast.ai ─────────────────────────────────────────────────────────────────────│
│ GPU [A100_SXM4 ▾]  GPUs [1]  Max $/h [0.80]  Disk GB [80]  Offer [cheapest ▾]  │
│ SSH key path [~/.ssh/id_ed25519]                                                │
│ ── Scope ───────────────────────────────────────────────────────────────────────│
│ Models  [✓ v19] [✓ heuristic] [✓ majority]     Limit rows per kind [   ] (all)  │
│ Max hours [2.0]   Pass env vars [HF_TOKEN ×] [+ add name]   ☐ Keep machine (debug)│
│ ☐ Fast kernels   strands-decider spec [default (pinned) ▾]                        │
│ ── Estimate ────────────────────────────────────────────────────────────────────│
│ Cheapest matching offer   $0.612/h  (offer 1234567, 1× A100_SXM4 80 GB, Quebec)  │
│ Spend cap                 at most $1.60  (max $0.80/h × 2.0 h)                   │
│ Credit                    $25.40 → enough                                        │
│ This rents a machine that bills until it is destroyed. decider-lab destroys it   │
│ when the run ends, fails, is cancelled, or reaches max hours.                    │
│ ── Confirm ─────────────────────────────────────────────────────────────────────│
│ Type  spend 1.60 on vast  to confirm  [                         ]               │
│ Command  decider-lab run labs/first/lab.yaml --on vast --gpu A100_SXM4 …  [copy] │
│                                                       [Cancel]  [Start run]      │
└──────────────────────────────────────────────────────────────────────────────────┘
```

- Fields per backend mirror `FLAG_OPTIONS` in `cli.py`: local (none), ssh (host select from
  Compute > SSH hosts or free text `user@host[:port]`, key path), aws (profile select, region,
  instance type select with GPU memory and list price, disk GB), vast (gpu, num_gpus,
  max_price, disk, offer, ssh key). Defaults come from the lab's `compute:` section.
- Env vars: chips of **names** only. Each chip shows ● set / ○ not set (from
  `GET /api/settings/env`). The value is never requested or shown.
- Estimate: `POST /api/labs/:id/estimate` on open and on every field change (debounced 300 ms).
  Local and ssh: "No cloud cost." and no typed confirmation. aws/vast: estimate + the server's
  `confirm_phrase`; Start is disabled until the input equals it exactly (case-sensitive,
  trimmed). Estimate states: loading (skeleton lines), ok, blocked (`can_start: false` with
  `blockers[]`, e.g. "credit $1.20 does not cover the $1.60 cap (+$0.50 margin)", "G-instance
  vCPU quota is 0 in us-east-1: request L-DB2E81BA", "lab has 1 error"), error (retry).
- Start: `POST /api/jobs {kind:"run", …, confirm}`. Success → navigate to `/jobs/:jobId`.
  Errors: `confirm_mismatch` (field error), `lab_invalid` (callout with link to Editor),
  `backend_unavailable` (callout with the hint).
- Mobile: full-screen sheet; Estimate and Confirm stay pinned above the action bar.

### 4.3 Jobs `/jobs`

```
┌ Jobs ────────────────────────────────────────────────────────────────────────────┐
│ [Active] [Finished] [All]   [Kind: All ▾]  [Lab: All ▾]  [Search…]                │
│ status       title                         kind   where   progress        started  dur │
│ ● running    run first-lab                 run    vast    ▓▓▓▓░░ 4/6 runs  10:42   12m │
│ ● running    pull hf://…/v19@bb282d7       pull   local   ▓▓░░░░ 38%       10:50   4m  │
│ ✓ succeeded  eval http://127.0.0.1:8000    eval   local   90/90            09:12   41s │
│ ◐ partial    run nova-lab                  run    local   5/6 · 1 failed   yday    18m │
│ ✕ failed     jevbench v19                  jevb.  local   —                yday    2m  │
│ ■ cancelled  run tune-v19                  run    aws     2/7 stages       Mon     3m  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

Live via `GET /api/events` (SSE, `job.*` events). Status vocabulary and colors (always icon +
text, never color alone): queued ○ muted, running ● accent (pulsing dot; static when
`prefers-reduced-motion`), cancelling ◌ warning, succeeded ✓ success, partial ◐ warning
("finished; some models failed"), failed ✕ danger, cancelled ■ muted, lost ? warning (server
restarted while the job ran; process gone).

Empty: "No jobs yet. Runs, pulls and evals you start appear here with live logs."

#### 4.3.1 Job `/jobs/:jobId`

```
┌ ● run first-lab on vast.ai               started 10:42 · 12m 08s  [Cancel run] [⋯] ┐
│ [Progress] [Logs] [Telemetry] [Command]                                             │
├─────────────────────────────────────────────────────────────────────────────────────┤
│ Stages                                                                              │
│ ✓ check ─ ✓ acquire ─ ✓ copy ─ ● bootstrap ─ ○ run ─ ○ fetch ─ ○ release            │
│   0:02     3:41        0:22     6:03…                                                │
│ machine  vast instance 9876543 · 1× A100_SXM4 · $0.612/h · cost so far ≈ $0.12      │
│                                                                                     │
│ Runs                                     rows            rows/s  errors  Intelligence│
│ v19 / smoke          ✓ done             90/90   ▓▓▓▓▓▓   14.2    0       58.3        │
│ v19 / synthetic      ● running          410/900 ▓▓▓░░░    9.8    2 ⚠     —           │
│ v19 / synthetic+cal  ○ pending                                                      │
│ heuristic / smoke    ○ pending  …                                                   │
│ ┌ rows/s (last 10 min) ─────────────┐ ┌ errors (cumulative) ───────────────────────┐│
│ └───────────────────────────────────┘ └─────────────────────────────────────────────┘│
│ Result  (when finished) [Open results →]  REPORT.md · report.json                    │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

- Stages: remote runs `check, acquire, copy, bootstrap, run, fetch, release`; local runs
  `prepare, run, report`; pull `resolve, download, verify, extract, done`; eval
  `prepare, run`; jevbench `harness, run, score`; calibrate `fit, score`. Each stage:
  pending ○, active ● (with elapsed), done ✓ (duration), failed ✕ (the failing line is
  shown under it), skipped – . The release stage of a remote run is never "skipped" silently:
  if `--keep` was used it shows ⚠ "machine left running: release it in Compute".
- Per model/suite progress comes from the server's `job.progress` (parsed from the CLI log and
  from `predictions.jsonl` line counts; see API.md). A model that failed shows ✕ with its
  error line, and the remaining models keep going (that is how `run_lab` behaves).
- Cancel (running/queued): confirm dialog (not typed; cancelling is safe) "Cancel this run?
  A remote machine is released before the job stops." → `POST /api/jobs/:id/cancel`. States:
  cancelling (button disabled, label "Cancelling… releasing machine"), cancelled.
  If the process does not exit in 60 s the server escalates; for remote jobs the job page then
  shows a danger callout "The machine may still be running" with a link to Compute.
- Overflow menu: Re-run with same settings (opens the originating dialog prefilled; never
  starts directly), Copy command, Download log, Delete record… (typed `delete`, finished jobs
  only).

Logs tab:

```
┌ [Search logs…        ] [⇅ 3/17]  [Level: all ▾]  [☑ Follow]  [Wrap]  [Download]   │
│ 10:42:01  [vast] renting 1x A100_SXM4 at $0.612/h (offer 1234567)                  │
│ 10:45:43  [vast] ssh ok: root@ssh4.vast.ai:22311, work dir /root/decider-lab-work  │
│ 10:46:05  [vast] bootstrap: torch for this machine, strands-decider[vision,cuda]…  │
│ 10:52:08    | [decider-lab] v19 / smoke: Intelligence 58.3 (95% CI [51.2, 65.0])…  │
│ 10:58:31    | [decider-lab] FAILED heuristic: ModuleNotFoundError: my_model        │
└────────────────────────────────────────────────────────────────────────────────────┘
```

- `LogViewer`: virtualized, monospace, SSE `log` events appended; on open fetch history with
  `GET /api/jobs/:id/log?offset=0` then subscribe with `Last-Event-ID`. Follow is on by
  default and turns off when the person scrolls up (a "Jump to latest ↓ (n new)" pill appears).
- Search: client-side over loaded lines, highlights matches, `Enter`/`Shift+Enter` next/prev,
  match counter. Level filter: all / warnings (`WARNING`, `WARN`, `⚠`) / errors (`FAILED`,
  `Error`, `Traceback`, `failed`). Lines matching error patterns get a danger left border.
- The server redacts before storing and before streaming (API.md, "Secrets"); the UI adds no
  redaction of its own and must not try to "unredact".

Telemetry tab: see 7.6. Shows a source badge: `local nvidia-smi`, `remote via ssh`,
`simulated (fake cloud)` or the empty state "No GPU telemetry: this job runs on a machine
without an NVIDIA GPU (Apple MPS and CPU are not sampled)." rows/s and errors are always
available for run/eval jobs because they come from predictions files.

Command tab: the argv as a copyable block, working directory, environment variable **names**
passed (values never), exit code, start/end timestamps, the job's files (log path, run root).

#### 4.3.5 Quick eval dialog (CLI `eval`)

Fields: Model (segmented: URL of a System One server | baseline uniform/majority/random |
serve a checkpoint (source field with the same validation as Models > Pull) | python
`module:attr`), Name, Suite (`SuitePicker`), Split (auto/dev/test/all), Limit per kind,
Workers (default 4), Vision toggle (serve only). Shows the command. `POST /api/jobs
{kind:"eval"}`. Never costs money; no typed confirm.

### 4.4 Results `/results`

List of run roots:

```
┌ Results ──────────────────────────────────────────── [Compare two runs…] ┐
│ [Search…]  [Lab: All ▾]                                                     │
│ ┌ first-lab ─────────────────────────────────────────────── 2 h ago ─────┐ │
│ │ 3 models · 2 suites · baseline majority · calibrated · 0 failures       │ │
│ │ best on synthetic: v19 61.4 (57.9–64.8)   [Open]                        │ │
│ └─────────────────────────────────────────────────────────────────────────┘ │
│ ┌ nova-lab ──────────────────────────────────────────────── 1 d ago ─────┐ │
│ │ 2 models · 1 suite · ⚠ 1 failure: nova: ThrottlingException …           │ │
│ └─────────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────────┘
```

Empty: "No results yet. A lab run writes runs/<lab>/ with a report; it appears here." [Run a lab…].

#### 4.4.1 Run root `/results/:rootId`

Header: lab name, run root path (copy), finished time, wall time, failures callout (danger, one
line per `lab.json.failures` entry, never collapsed by default when non-empty), actions:
[Export ▾] (REPORT.md, report.json, leaderboard CSV, predictions CSV for the selected suite),
[Rebuild report] (`POST /api/runs/:rootId/report`), [Calibrate…] (per run, see below).

Suite selector (`SuitePicker` chips: `smoke`, `synthetic`, `jevbench`; `+cal` is NOT a separate
chip: it is the Raw/Calibrated toggle) and a split note "Scored on: test (dev rows were used to
fit temperatures)".

The proxy notice is pinned at the top of every Results view (component `ProxyNotice`):
"Intelligence (local proxy): JevBench v1.5 rules applied to this suite. Ranks these runs against
each other; not a JevBench board score." It is not dismissible.

Leaderboard tab:

```
Suite [smoke] [synthetic●] [jevbench]      Scores: (●) Raw ( ) Calibrated ( ) Both
┌──────────────────────────────────────────────────────────────────────────────────────────────┐
│ #  model      Intelligence (proxy) · 95% CI          acc %  NLL    ECE    yes/no in band  err  lat p50│
│ 1  v19        61.4  ├────●────┤ 57.9 – 64.8           78.2   0.512  0.041  6.0 %           0    0.18 s│
│ 2  heuristic  12.0  ├──●──┤ 8.1 – 15.9                55.0   0.690  0.012  0.0 %           0    1 ms  │
│ 3  majority    0.0  ●  0.0 – 0.0                      50.4   0.693  0.004  100.0 %         0    1 ms  │
│ ─  nova       —  failed: ThrottlingException (see job)                                       ⚠ 540   │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
 The CI bar uses one shared axis per table (−100 to 100 clipped to the data range ± 5).
 Ranks are shown only as order; two models whose CIs overlap get the same "≈" marker in #.
```

- Columns sortable (click or Enter on header; `aria-sort`). Default sort Intelligence desc.
- "Both" shows raw and calibrated in adjacent sub-rows with a `+cal` tag and the fitted
  temperatures in a tooltip.
- A model with `errors > 0` shows `n ⚠` in danger color with a tooltip "n of N rows failed and
  were scored as uniform answers" and links to Rows filtered by `error=1`.
- A model with no run for this suite (failed before writing) is still listed at the bottom with
  `—` and the failure text from `lab.json`.
- Row click opens `/results/:rootId/:model/:suite`.
- Mobile: cards: model name, Intelligence + `CIBar`, then a 2×3 grid of the other metrics.

Vs baseline tab: `ForestPlot` (7.2) of `vs_baseline` for the selected suite (ΔIntelligence,
toggle Δaccuracy, ΔNLL), with n paired. Empty: "No baseline set. Add `baseline: <model>` to the
lab to compare every model with it row by row." [Open editor].

Families tab: `FamilyHeatmap` (7.3), models × families, Intelligence (toggle accuracy).
Empty (≤ 1 family): "This suite has one family; nothing to break down."

Calibration tab: per model `ReliabilityDiagram` (7.4), small multiples (3 per row desktop, 1 on
mobile), raw vs calibrated overlaid when a `+cal` run exists, and a table: kind, T, NLL before →
after, ECE before → after, Intelligence before → after. Explains: "Temperature per question kind,
fitted on dev rows by minimising NLL, scored on test rows. It changes confidence, not which option
is on top, except where a yes/no answer moves across the 0.2–0.8 band." Calibrate action
(`POST /api/jobs {kind:"calibrate"}`) is offered per model/suite run that has dev rows and no
`+cal`: states idle / running (JobChip) / done (refetch) / error `too_few_dev_rows` ("needs at
least 30 dev rows of a kind; this run has 12").

Latency tab: `LatencyChart` (7.5): p50 and p95 per model, plus a histogram for the selected
model. Note: "Wall-clock per request from this machine, including network and queueing at
`workers` concurrency. Compare only runs made on the same machine."

JevBench tab: `JevBenchPanel` (7.7) for every model that has a `jevbench/scores.json`. Empty:
"JevBench public tasks were not run for this lab. Add `jevbench: [model]` (System One models
only), or run it against a URL." [Run JevBench…] (dialog: URL, label → `POST /api/jobs
{kind:"jevbench"}`; it needs network to clone the harness at a pinned commit; the dialog says so).

Rows tab (row explorer across models):

```
┌ Filters: kind [all ▾] family [all ▾] split [test ▾] show [all | wrong for: v19 ▾ | disagreements | errors] [Search state…] ┐
│ id        family              kind    gold        v19            heuristic     majority                    │
│ 7f3a…     synthetic/calendar  noul    yes         ▇ 0.91 ✓       ▃ 0.50 ✕band   ▅ 0.55 ✕band               │
│ 1c09…     synthetic/seating   choice  C (of 4)    ▇ C 0.72 ✓     ▅ A 0.41 ✕     ▅ A 0.25 ✕                │
│ 9d21…     synthetic/arith     score   3 (0–4)     E=2.8 ✓≈       E=2.0          E=2.0                      │
│ ...                                                       page 1 of 18  [‹] [›]  50 per page               │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

Click a row → side panel (`RowDetail`): state (pretty-printed; JSON states as a collapsible tree;
images as thumbnails when they are files/data URIs inside the workspace), instructions, options
with gold marked, and per model a `ProbBars` with the top option, correctness under the proxy
rules ("wrong: yes/no inside the 0.2–0.8 band counts as wrong"), latency and the error text if
any. `[` / `]` move to previous/next row. Data from `GET /api/runs/:rootId/rows`.

Provenance tab: per model × suite, from `run.json` and `source.json`: answerer (adapter
description, server /health), model source (kind, resolved commit, sha256, cached dir),
suite sha256 with a "same rows" check across models (✓ all models scored suite rows with sha
`3be1…`, or ⚠ "heuristic scored a different suite fingerprint"), host, platform, python, GPU,
decider-lab version, finished time, wall time, workers, calibration temperatures and
`calibrated_from`. Every value copyable. No secret ever appears because the SDK never writes one;
the server still runs `redact()` on these files before returning them.

#### 4.4.2 Single run `/results/:rootId/:model/:suite`

Header: model, suite, raw/cal toggle (when `+cal` exists), [Compare with…], [Export CSV].
Sections: KPI strip (Intelligence + CI, accuracy, NLL, ECE, errors, n, median/p95 latency),
per-kind table (n, competence, accuracy, NLL, Brier, ECE, in band for noul, RPS for score),
per-family table, reliability diagram, the Rows explorer scoped to this model, provenance.

#### 4.4.3 Compare two runs `/results/compare?a=&b=&split=test`

```
┌ Compare two runs ────────────────────────────────────────────────────────────────────┐
│ A [first-lab / v19 / synthetic      ▾]   minus   B [first-lab / majority / synthetic ▾] │
│ Split (●) test ( ) dev ( ) all                                         [Swap A↔B]     │
│ ┌ Paired on 540 rows (0 only in A, 0 only in B) ─────────────────────────────────────┐ │
│ │ Δ Intelligence   +61.4   ├──────────●──────────┤  +57.2 to +65.3   A is better      │ │
│ │ Δ accuracy %     +27.8   ├─────●─────┤          +24.1 to +31.4     A is better      │ │
│ │ Δ NLL            −0.181  ├──●──┤               −0.205 to −0.158   A is better (lower)│ │
│ └────────────────────────────────────────────────────────────────────────────────────┘ │
│ Verdict words: "A is better" only when the CI excludes 0; otherwise "No clear difference │
│ (CI includes 0)". For NLL lower is better and the sentence says so.                     │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

`GET /api/compare?a=&b=&split=`. Run pickers list every model/suite run in the workspace
(grouped by run root; `+cal` runs listed with their tag). Errors: `no_shared_rows` ("These two
runs share no rows: they scored different suites."), shown as an `ErrorState` in the result card.
If `suite_sha256` differs but ids overlap, a warning callout says only the shared rows are
compared.

### 4.5 Models `/models`

Cache tab:

```
┌ Models ────────────────────────────────────────────────────────── [Pull a model…] ┐
│ Cache: ~/.cache/decider-lab/models · 3 models · 12.6 GB   (HF snapshots live in the HF cache) │
│ source                                     kind   ref              size     pulled     │
│ hf://StrandsAgents/strands-decider-2B…     hf     bb282d78 pinned  4.12 GB  2 d ago  ⋯  │
│ s3://my-bucket/weights/mine.tar            s3     sha256 9a1f…     4.10 GB  5 d ago  ⋯  │
│ https://example.com/ckpt.tgz               url    sha256 03be…     4.38 GB  1 w ago  ⋯  │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

Row menu: Copy source, Copy path, Use in a lab… (opens a lab picker, then the Editor with a
snippet in the clipboard and a toast explaining where to paste; Studio does not rewrite YAML
structurally), Delete from cache… (typed: the first 8 characters of the ref, e.g. `bb282d78`).
Ref column: `pinned` badge (success) for full commits or sha256-verified files; `unpinned`
badge (warning) with tooltip "resolved to <commit> at pull time; the branch may move".

Pull tab / dialog:

```
┌ Pull a model ────────────────────────────────────────────────────────────────────┐
│ Source   [hf://StrandsAgents/strands-decider-2B-hobson-v19@bb282d7…           ]  │
│          ✓ Hugging Face · pinned to a full commit                                 │
│ Revision [            ] (hf only; or use @ in the source)                          │
│ sha256   [            ] (required for archives and files from s3/https by policy)  │
│ ☑ Require a pinned revision (refuse branches and tags)                            │
│ AWS profile [heisenberg ▾]  region [us-east-1]   (s3 only)                          │
│ Token     HF_TOKEN ● set  (read from the environment; never shown)                  │
│ Command   decider-lab pull hf://… --require-pinned                         [copy]  │
│                                                            [Cancel] [Pull]         │
└────────────────────────────────────────────────────────────────────────────────────┘
```

- Source is classified as you type with `POST /api/models/inspect` (kind, pinned, warnings,
  errors). Revision policy (Settings > Workspace `require_pinned_default`, default on):
  unpinned hf sources show a warning and, when "Require pinned" is checked, an error that
  disables Pull. s3/https archives without sha256: warning "cannot be verified" unless
  `require_sha256` setting is on, then error.
- Pull → `POST /api/jobs {kind:"pull"}`; progress shows bytes/total when known (parsed),
  else indeterminate. Success: toast "Pulled · sha256 verified" and the Cache tab refreshes.
  Failure `sha256_mismatch` is shown as a danger callout naming both hashes.

Empty cache: "No models pulled yet. Labs pull their `serve:` sources on first run; pull one now
to check it." [Pull a model…], `decider-lab pull hf://org/repo@commit`.

### 4.6 Data `/data`

Suites tab:

```
┌ Built-in suites ───────────────────────────────────────────────────────────────────┐
│ smoke       90 rows · 3 families × 3 kinds × 10 · seconds        [Inspect]          │
│ synthetic   per_kind [150] families [arith ✓ cal ✓ seat ✓ chess ☐] seed [1] [Inspect]│
│ heldout     ~3,300 rows · needs the heldout extra ⚠ not installed [Inspect] (disabled)│
│ ┌ Workspace files (JSONL) ──────────────────────────────────────────────────────┐   │
│ │ data/my_eval.jsonl   1,204 rows · valid ✓          [Inspect] [Leakcheck…]      │   │
│ │ data/bad.jsonl       ✕ row 17: label 4 out of range for 3 options [Inspect]   │   │
│ └───────────────────────────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

Inspect opens a `SuiteInspector` drawer: stats from `GET /api/suites/:ref/stats` (rows, by kind,
by split, by task top 30, label balance per kind as small bar groups, rows with images, sha256)
and a paged row browser (`GET /api/suites/:ref/rows`). Building a large suite (heldout) can take
minutes: the server returns 202 with a job id and the drawer shows the JobChip until done.
Export: "Save as JSONL…" (path inside the workspace) → `POST /api/data/export-suite`.

Upload CSV tab: drop zone (`FileDrop`, `.csv`, max 50 MB) → `POST /api/data/csv/preview`
(multipart) returns detected columns and the first 20 converted rows plus per-row errors →
mapping is fixed by the SDK (`state, question, answer[, options, kind, task]`; the UI shows which
columns were recognised and which are missing) → Task name, delimiter, output path
(`data/<name>.jsonl`) → [Convert] `POST /api/data/csv/convert`. States: empty drop zone,
uploading (progress), preview ok, preview with errors (table with row number + message; Convert
disabled if any error), converting, done ("1,204 rows → data/my.jsonl" with [Inspect]).

Generate tab: families checkboxes, per kind, seed, exclude suites (`SuitePicker multi`), output
path → `POST /api/data/generate`. Result card: rows written, rows dropped as overlapping.

Split tab: input file, dev fraction (0.05–0.95, default 0.4), seed, output path →
`POST /api/data/split`. Result: by-split counts.

Leakcheck tab: training file, against (suites/files, multi), optional "write clean copy to" →
`POST /api/data/leakcheck`. Result: train rows, eval rows, overlapping (danger if > 0), up to 5
examples (row index, task, state excerpt), and the clean file path if written. Zero overlap shows
success "No training row shares an input with these suites."

All write actions refuse to overwrite an existing file unless "Overwrite" is checked (server
error `file_exists`).

### 4.7 Compute `/compute`

Local tab: doctor (`GET /api/compute/doctor`) as a checklist: status icon (ok ✓ success, warn ⚠
warning, info – muted), item, detail. [Re-check]. Below: "This machine" card: OS, Python, CPU
count, memory, GPU(s) via nvidia-smi or "Apple MPS" or "CPU only".

vast.ai tab:

```
┌ vast.ai  [fixtures]                                          credit $25.40  [Refresh] ┐
│ ┌ Instances started by decider-lab ────────────────────────────────────────────────┐ │
│ │ id        label                      status    GPU            $/h     up    ⋯     │ │
│ │ 9876543   decider-lab:first-lab      running   1× A100_SXM4   0.612   14m  [Destroy…]│
│ └──────────────────────────────────────────────────────────────────────────────────┘ │
│ ┌ Offers ───── GPU [RTX_4090 ▾] GPUs [1] max $/h [0.80] disk [80] [Search] ─────────┐ │
│ │ offer      GPU              $/h     VRAM   CUDA   reliability  location            │ │
│ │ 1234567    1× RTX_4090      0.342   24 GB  12.8   0.995        CA                  │ │
│ │ …                                    [Use in run dialog…]                          │ │
│ └──────────────────────────────────────────────────────────────────────────────────────┘ │
```

- Not configured state: "The vastai CLI is not installed or has no API key." with
  `pip install 'decider-lab[vast]'` and `vastai set api-key <key>` (the key is entered in a
  terminal, never in Studio).
- Destroy…: typed confirmation of the instance id. States: destroying (row shows ◌), destroyed
  (row removed, toast), still listed (danger callout: "Instance 9876543 is still listed. Check
  `vastai show instances`.").
- "Destroy all decider-lab instances…": typed `destroy all`.

AWS tab:

```
┌ AWS  [fixtures]   Profile [heisenberg ▾]  Region [us-east-1 ▾]               [Refresh] ┐
│ Identity  account 1234••••9012 · arn …:assumed-role/…/session  ✓ credentials valid      │
│ ┌ vCPU quotas (On-Demand) ─────────────────────────────────────────────────────────────┐ │
│ │ G and VT   8 / 8 used 4    ▓▓▓▓░░░░  L-DB2E81BA                                       │ │
│ │ P          0               ⚠ request L-417A185B to use p4d/p5                         │ │
│ │ Standard   64 used 2                                                                   │ │
│ └───────────────────────────────────────────────────────────────────────────────────────┘ │
│ ┌ Instances tagged decider-lab ───────────────────────────────────────────────────────┐ │
│ │ i-0abc…  g6e.xlarge  running  first-lab  14m  ≈ $1.86/h  [Terminate…]                │ │
│ └───────────────────────────────────────────────────────────────────────────────────────┘ │
│ ┌ Bedrock models (us-east-1) ─────────────── [Search…] ───────────────────────────────┐ │
│ │ us.amazon.nova-pro-v1:0  Amazon Nova Pro  text,image  ACTIVE   [Copy model spec]     │ │
│ └───────────────────────────────────────────────────────────────────────────────────────┘ │
```

- Profiles come from the AWS config/credentials files (names only) and `AWS_PROFILE`.
  Studio never reads or shows key material. The account id is masked by default (middle digits)
  with a reveal toggle (it is not a secret, but it is screen-share-sensitive).
- Every panel loads independently; a permissions error in one (e.g. `AccessDenied` for Service
  Quotas) shows in that panel only, with the missing IAM action named.
- Terminate…: typed confirmation of the instance id; "Terminate all…" typed `terminate all`.
- "Copy model spec" copies `{bedrock: us.amazon.nova-pro-v1:0, region: us-east-1}`.
- Real AWS calls are read-only (`sts:GetCallerIdentity`, `service-quotas:GetServiceQuota`,
  `ec2:DescribeInstances`, `bedrock:ListFoundationModels`, `bedrock:ListInferenceProfiles`)
  except Terminate, which requires typed confirmation.

SSH hosts tab:

```
┌ SSH hosts ──────────────────────────────────────────────────────────── [Add host] ┐
│ name       target                     key                      last test            │
│ gpu-box    ubuntu@10.0.0.5:22         ~/.ssh/id_ed25519        ✓ 42 ms · 1× L40S  [Test] [⋯] │
│ old-rig    root@rig.invalid           ~/.ssh/rig               ✕ timed out          [Test] [⋯] │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

Add/edit dialog: name, target `user@host[:port]`, key path (a path, with "exists ✓/✕"
indicator; Studio never reads the key), remote work dir (optional). Test → `POST
/api/compute/ssh/hosts/:id/test`: states testing (row spinner), ok (latency, GPU line from
`nvidia-smi` or "no NVIDIA GPU"), failed (error message from ssh, e.g. "Permission denied
(publickey)"). Delete: simple confirm (not typed; it deletes a bookmark only).

### 4.8 Settings `/settings`

- Workspace: path (read-only; "restart with `decider-lab ui --workspace DIR` to change"),
  runs directory override, `max_concurrent_jobs` (1–8, default 2), `require_pinned_default`,
  `require_sha256`, default compute backend for new labs.
- Appearance: theme (System / Dark / Light), density (Comfortable / Compact: row height 44 vs
  36), reduce motion (System / On), number of CI resamples display note (read-only: 1000 for
  scores, 2000 for comparisons, as the SDK does).
- Environment: the variables Studio knows to be relevant (`HF_TOKEN`, `AWS_PROFILE`,
  `AWS_REGION`, `AWS_DEFAULT_REGION`, `OPENAI_API_KEY`, `DECIDER_LAB_CACHE`,
  `STRANDS_DECIDER_PYTHON`, `HF_HUB_OFFLINE`, `DECIDER_LAB_FAKE_CLOUD`, plus every `*_env`
  name referenced by a lab) with ● set / ○ not set and which labs reference them. Values are
  never shown. Copy: "Set these in the shell you start `decider-lab ui` from."
- About: versions (decider-lab, Python, strands-decider if importable, Studio build hash), cache
  dir, workspace state dir, license, links to docs (local docs/ files rendered as text, no
  network), "Studio sends no telemetry."

Save: `PUT /api/settings` per section with inline success check (no page-level save button).

---

## 5. Design system

All values are CSS custom properties in `ui/src/theme/tokens.css`. Components use semantic
tokens only (never raw hex). Theme is set by `data-theme="dark|light"` on `<html>`; "System"
follows `prefers-color-scheme`. Dark is the default when the system has no preference.

### 5.1 Color

Brand anchors: deep green `#06140E`, brand `#0B7A45`, accent `#2FDC85`, light `#F5F8F4`.
Contrast ratios below were computed with the WCAG 2.x formula against the stated background.

Dark theme

| token | value | use | contrast |
|---|---|---|---|
| `--bg` | `#06140E` | page | — |
| `--surface-1` | `#0B1F16` | cards, sidebar | — |
| `--surface-2` | `#10291D` | raised: popovers, table header, inputs | — |
| `--surface-3` | `#163424` | hover rows, selected nav | — |
| `--border` | `#1F4030` | dividers, card borders (decorative) | — |
| `--border-strong` | `#3E6E55` | input borders, focus-adjacent UI | 3.2:1 on bg |
| `--text` | `#E8F2EC` | body | 16.5:1 bg, 13.5:1 surface-2 |
| `--text-muted` | `#A3BFB0` | secondary | 9.6:1 bg, 7.8:1 surface-2 |
| `--text-subtle` | `#7F9C8C` | captions, placeholders | 6.3:1 bg, 5.8:1 surface-1 |
| `--accent` | `#2FDC85` | primary buttons, links, active nav, focus ring, running | 10.5:1 bg |
| `--accent-hover` | `#5BE79F` | hover on accent | — |
| `--on-accent` | `#06140E` | text on accent fills | 10.5:1 |
| `--brand` | `#0B7A45` | brand mark, selected chip fill (with `--on-brand`) | — |
| `--on-brand` | `#FFFFFF` | text on brand fills | 5.4:1 |
| `--success` | `#2FDC85` | ✓ states (with icon) | 10.5:1 |
| `--warning` | `#F5C451` | ⚠ partial, unpinned, fake cloud | 11.6:1 |
| `--danger` | `#FF7A7A` | ✕ failed, errors, destructive | 7.5:1 bg, 6.1:1 surface-2 |
| `--info` | `#7CC4FF` | info callouts | 10.1:1 |
| `--danger-fill` | `#5A1E1E` | destructive button bg (text `--text`) | 11.2:1 |
| `--tint-accent` | `rgba(47,220,133,.12)` | accent backgrounds (chips, selected rows) | — |
| `--tint-warning` | `rgba(245,196,81,.12)` | warning callout bg | — |
| `--tint-danger` | `rgba(255,122,122,.12)` | danger callout bg | — |
| `--focus` | `#2FDC85` | focus ring | 10.5:1 |

Light theme

| token | value | contrast |
|---|---|---|
| `--bg` | `#F5F8F4` | — |
| `--surface-1` | `#FFFFFF` | — |
| `--surface-2` | `#ECF2EC` | — |
| `--surface-3` | `#E1EBE3` | — |
| `--border` | `#D3DFD6` | decorative |
| `--border-strong` | `#7A9585` | 3.0:1 on bg, 3.3:1 on white |
| `--text` | `#06140E` | 17.6:1 |
| `--text-muted` | `#3D5747` | 7.4:1 |
| `--text-subtle` | `#56705F` | 5.1:1 bg, 5.4:1 white |
| `--accent` | `#0B7A45` | 5.1:1 bg, 5.4:1 white (links, primary fill) |
| `--accent-hover` | `#096239` | — |
| `--on-accent` | `#FFFFFF` | 5.4:1 |
| `--accent-bright` | `#2FDC85` | decorative only (status dot fill with a `--text` outline, chart highlight); never text on light (1.7:1) |
| `--brand` | `#0B7A45` / `--on-brand` `#FFFFFF` | 5.4:1 |
| `--success` | `#0B7A45` | 5.1:1 |
| `--warning` | `#8A5A00` | 5.5:1 |
| `--danger` | `#B42318` | 6.1:1 |
| `--info` | `#1F5FAD` | 6.0:1 |
| `--danger-fill` | `#B42318` (text `#FFFFFF`) | 6.6:1 |
| `--tint-accent` | `rgba(11,122,69,.10)` | — |
| `--tint-warning` | `rgba(138,90,0,.10)` | — |
| `--tint-danger` | `rgba(180,35,24,.08)` | — |
| `--focus` | `#0B7A45` | 5.1:1 |

Chart palette (categorical, assigned to models in lab order and stable across every chart of a
run root; the baseline model is always `--chart-baseline`, a neutral grey):

| token | dark | light | min contrast vs surface |
|---|---|---|---|
| `--chart-1` | `#2FDC85` | `#0B7A45` | 9.6 / 5.4 |
| `--chart-2` | `#7CC4FF` | `#1F5FAD` | 9.2 / 6.4 |
| `--chart-3` | `#F5C451` | `#8A5A00` | 10.6 / 5.9 |
| `--chart-4` | `#C9A0FF` | `#6B3FB8` | 8.2 / 6.9 |
| `--chart-5` | `#FF9F7A` | `#B4461F` | 8.6 / 5.5 |
| `--chart-6` | `#5FE0D6` | `#0F7A75` | 10.7 / 5.2 |
| `--chart-baseline` | `#8FA39A` | `#5F7A6B` | 6.4 / 4.7 |

More than 6 non-baseline models: colors repeat with a dash pattern (solid, 4-2, 1-2) and the
legend shows the pattern. Color is never the only encoding: every series also has a direct
label or legend with text.

Diverging scale (heatmap, centered at 0 = chance): negative `--danger` → `--surface-2` at 0 →
positive `--accent` (dark) / `--brand` (light), 9 steps, interpolated in OKLab. Cell text uses
`--text` or `--on-accent` by computed luminance so it is ≥ 4.5:1 on every step.

### 5.2 Typography

- UI: Inter (bundled, variable), fallback `system-ui, -apple-system, "Segoe UI", sans-serif`.
- Mono: JetBrains Mono (bundled), fallback `ui-monospace, SFMono-Regular, Menlo, monospace`.
  Used for code, paths, ids, hashes, logs, YAML.
- `font-feature-settings: "tnum" 1, "cv11" 1` on numeric cells, KPIs and axes.

| token | size / line-height | weight | use |
|---|---|---|---|
| `--type-display` | 32/40 | 650 | KPI values |
| `--type-h1` | 24/32 | 650 | page title |
| `--type-h2` | 18/26 | 600 | section/card title |
| `--type-h3` | 15/22 | 600 | sub-section, table group |
| `--type-body` | 14/22 | 400 | default |
| `--type-body-strong` | 14/22 | 550 | emphasis, table primary column |
| `--type-small` | 13/20 | 400 | secondary text, table cells in compact |
| `--type-caption` | 12/16 | 500 | labels, axis ticks, badges (letter-spacing .01em) |
| `--type-mono` | 13/20 | 400 | code, logs |

Minimum text size 12 px. Body text never below 14 px on mobile.

### 5.3 Spacing, layout, radii, elevation, motion

- Spacing scale (4 px base): `--space-0` 0, `-1` 4, `-2` 8, `-3` 12, `-4` 16, `-5` 20, `-6` 24,
  `-8` 32, `-10` 40, `-12` 48, `-16` 64.
- Layout: page gutter 24 (≥ 1024), 20 (640–1023), 16 (< 640). Grid: 12 columns, gap 16.
  Breakpoints: `sm` 640, `md` 1024, `lg` 1280, `xl` 1600. Content max-width 1440.
  Must work at 390 px wide with no horizontal page scroll (tables scroll inside their card
  with sticky first column, or switch to cards).
- Control heights: 32 (compact), 36 (default), 44 (touch targets on < 640 px, and every icon
  button's hit area is ≥ 44×44 on touch, ≥ 24×24 on desktop per WCAG 2.5.8).
- Radii: `--radius-xs` 4 (badges, chips), `--radius-sm` 6 (inputs, buttons), `--radius-md` 10
  (cards, popovers), `--radius-lg` 14 (dialogs, sheets), `--radius-full` 999 (pills, dots).
- Elevation (dark uses lighter surfaces + subtle shadow; light uses shadow):
  `--elev-0` none; `--elev-1` `0 1px 0 rgba(0,0,0,.25)` + `--border` (cards);
  `--elev-2` `0 4px 16px rgba(0,0,0,.35)` (popovers, menus; light: `.10`);
  `--elev-3` `0 12px 40px rgba(0,0,0,.5)` (dialogs, palette; light: `.16`).
  Overlay scrim: dark `rgba(2,8,5,.72)`, light `rgba(6,20,14,.40)`.
- Motion: `--dur-fast` 120 ms, `--dur` 180 ms, `--dur-slow` 280 ms;
  `--ease` `cubic-bezier(.2,.8,.2,1)`. Under `prefers-reduced-motion: reduce` (or setting On):
  no transforms, no pulsing, opacity-only fades ≤ 120 ms, charts render without animation.
- Z-index: sidebar 10, topbar 20, sticky action bar 30, popover 40, drawer 50, dialog 60,
  palette 70, toast 80.

### 5.4 Iconography

Lucide icons (bundled subset), 16 px in text, 20 px in nav, stroke 1.75. Status always pairs an
icon with text. The product mark is a rounded square in `--brand` with a `--accent` diamond.

---

## 6. Component inventory

TypeScript props (abbreviated). All components accept `className` and `data-testid`. All
interactive components forward refs and are keyboard-operable.

### 6.1 Primitives

| component | props | notes |
|---|---|---|
| `Button` | `variant: 'primary'\|'secondary'\|'ghost'\|'danger'; size: 'sm'\|'md'; loading?: boolean; icon?: Icon; iconOnly?: boolean; 'aria-label'?: string; kbd?: string` | `loading` keeps width, sets `aria-busy`; `iconOnly` requires `aria-label` (lint rule) |
| `IconButton` | `icon; label: string; size?` | label → `aria-label` + tooltip |
| `Input`, `NumberInput`, `Textarea` | `label: string; hint?: string; error?: string; prefix?; suffix?; mono?: boolean` | label always rendered (visually hidden allowed only in toolbars); error linked with `aria-describedby` and `aria-invalid` |
| `Select`, `Combobox` | `label; options: {value,label,description?,disabled?}[]; value; onChange; searchable?` | Combobox follows ARIA 1.2 combobox pattern |
| `Checkbox`, `Switch`, `RadioGroup`, `SegmentedControl` | standard + `label` | Segmented = radiogroup semantics |
| `Badge` | `tone: 'neutral'\|'accent'\|'success'\|'warning'\|'danger'\|'info'; icon?` | |
| `StatusPill` | `status: JobStatus \| 'valid'\|'invalid'; label?` | icon + text mapping in 4.3 |
| `Tooltip` | `content; side?` | on hover and focus; never the only place for essential info |
| `Kbd` | `keys: string[]` | renders ⌘ on macOS, Ctrl elsewhere |
| `CodeInline`, `CodeBlock` | `code: string; copy?: boolean; language?: 'bash'\|'yaml'\|'json'` | copy button announces "Copied" via live region |
| `Skeleton` | `shape: 'line'\|'block'\|'circle'; width?; height?; lines?` | `aria-hidden`; container sets `aria-busy` |
| `Callout` | `tone; title?; children; actions?` | `role="alert"` for danger shown after an action, else none |
| `Toast` (via `useToast`) | `{title, description?, action?: {label, onClick}, tone}` | success/info only; `role="status"` |
| `Dialog`, `Sheet`, `Drawer` | `open; onOpenChange; title; description?; size?: 'sm'\|'md'\|'lg'\|'full'` | focus trap, Esc closes, returns focus; Sheet = full-screen on < 640 |
| `Tabs` | `tabs: {id,label,count?,disabled?}[]; value; onChange` | id synced to `?tab=` |
| `EmptyState` | `title; body; action?: ReactNode; command?: string` | |
| `ErrorState` | `error: ApiError; onRetry?` | shows message, hint, disclosure with code + request_id |
| `ProgressBar` | `value?: number (0..1); label: string; indeterminate?` | `role="progressbar"` with `aria-valuenow` |
| `CopyField` | `value; mono?; mask?: 'middle'` | for paths, ids, account ids |

### 6.2 Composite

| component | props | notes |
|---|---|---|
| `AppShell` | `children` | topbar, sidebar, connection banner, toasts, palette host |
| `PageHeader` | `title; description?; actions?; status?: ReactNode; breadcrumbs?` | h1 |
| `KpiCard` | `label; value: string; sub?: string; ci?: [number,number]; href?; tone?` | value in `--type-display` |
| `DataTable<T>` | `columns: Column<T>[]; rows: T[]; rowKey; sort?; onSort?; onRowClick?; selectable?; stickyFirst?; mobile?: 'scroll'\|'cards'; loading?; empty?: ReactNode; pagination?: {offset,limit,total,onChange}` | keyboard: arrow/j/k move row focus, Enter opens; `aria-sort`; virtualized above 500 rows |
| `Column<T>` | `id; header; cell(row); sortValue?(row); align?: 'left'\|'right'; width?; hideBelow?: 'sm'\|'md'` | numeric columns right-aligned |
| `JobChip` | `jobId` | live via SSE store: dot + short progress + link |
| `JobStatusBadge` | `status: JobStatus` | |
| `StageTimeline` | `stages: Stage[]` (API.md shape) | horizontal ≥ 640, vertical list below; `ol` with `aria-current="step"` on active |
| `RunProgressTable` | `progress: JobProgress` | per model/suite rows |
| `LogViewer` | `jobId; follow?: boolean; height?` | virtualized; search, level filter, download; `role="log"` `aria-live="off"` (screen readers get a polite summary every 10 s instead: "412 lines, 2 errors") |
| `YamlEditor` | `value; onChange; problems: Problem[]; readOnly?` | CodeMirror; lint markers from problems; `aria-label="lab.yaml editor"` |
| `LabSummary` | `summary: LabSummary` | cards from 4.2.2 |
| `ModelSpecBadge` | `kind: ModelKind; source?: string; pinned?: boolean` | |
| `SuitePicker` | `suites: SuiteRef[]; value; onChange; multi?` | chips; built-ins with params popover |
| `BackendPicker` | `value: Backend; onChange; availability: Record<Backend,{ok:boolean; reason?:string}>` | segmented; unavailable options disabled with reason tooltip |
| `CostEstimate` | `estimate: Estimate \| undefined; loading` | |
| `TypedConfirm` | `phrase: string; value; onChange; label?` | input labelled "Type `<phrase>` to confirm"; matches exactly; announces "Confirmation matches" |
| `ConfirmDialog` | `title; body; confirmLabel; tone: 'danger'\|'default'; phrase?: string; onConfirm(): Promise` | with `phrase` it renders `TypedConfirm` and passes the typed text to the API |
| `EnvVarChips` | `names: string[]; status: Record<string,boolean>; onChange?` | names only; never an input for values |
| `ProxyNotice` | `variant?: 'inline'\|'banner'` | fixed copy (section 10) |
| `RunRootCard` | `root: RunRootSummary` | |
| `RunPicker` | `value?: RunRef; onChange; filter?` | grouped combobox |
| `RowDetail` | `rootId; suite; rowId; models: string[]` | |
| `ProbBars` | `options: [string,string][]; probs: number[]\|null; gold: number; kind; error?: string` | horizontal bars, gold marked ★, yes/no band shaded 0.2–0.8 |
| `ProvenanceList` | `items: {label, value, mono?, copy?}[]` | `dl` semantics |
| `DoctorList` | `checks: DoctorCheck[]` | |
| `FileDrop` | `accept; maxBytes; onFile` | also a button-triggered file input (keyboard) |
| `CommandPalette` | `open; onOpenChange` | section 8 |
| `ShortcutHelp` | — | `?` overlay listing section 8.2 |

### 6.3 Charts (contracts in section 7)

`CIBar`, `ForestPlot`, `FamilyHeatmap`, `ReliabilityDiagram`, `LatencyChart`,
`TelemetryChart`, `Sparkline`, `LabelBalanceBars`, `JevBenchPanel`. Each accepts
`ariaLabel: string`, `height?`, `showTable?: boolean`, and renders a visually hidden (or
toggled-visible) `<table>` with the same data.

---

## 7. Charts

General rules (all charts):

- SVG, responsive to container width (ResizeObserver), fixed aspect or fixed height as stated.
- Axes: tick labels `--type-caption` in `--text-subtle`; axis lines and gridlines `--border`
  (gridlines only on the value axis, dashed 2-3). Zero/reference lines `--text-muted` 1 px solid.
- Tooltips: on hover and on keyboard focus of a mark (marks are focusable via roving tabindex
  inside the chart: Tab enters the chart, arrows move between marks, Esc leaves). Tooltip shows
  the exact values with units and the n behind them. Touch: tap shows tooltip, tap elsewhere
  hides.
- Every chart has a title (`h3`), a one-line caption explaining what it shows, `role="img"` with
  `aria-label` summarising the main finding generated from data (e.g. "v19 61.4, 95% CI 57.9 to
  64.8; heuristic 12.0 …"), and a "View as table" toggle.
- CI whiskers: horizontal line 1.5 px from lo to hi, end caps 8 px tall, point estimate a 7 px
  circle (filled) on top. Colors per series; when CI is `[null,null]` draw the point only and
  the tooltip says "no CI (bootstrap needs scored rows)".
- Loading: skeleton block with the chart's height. Empty: `EmptyState` sized to the chart.

### 7.1 `CIBar` (inline in tables)

Props: `value: number|null; ci: [number|null, number|null]; domain: [number, number]; color;
width?: 120`. Height 16. Shared `domain` per table: `[min(lo)−5, max(hi)+5]` clipped to
`[−100, 100]`. A 1 px tick at 0 when 0 is inside the domain. Not focusable (the table cell
carries the numbers as text).

### 7.2 `ForestPlot` (paired vs baseline)

- Data: `vs_baseline[model][suite]` → `{diff, ci95, n_paired}` for `intelligence`, `accuracy`,
  `nll` (metric toggle).
- Layout: one row per model (row height 32), y = model names (left, `--type-small`, truncated
  with tooltip), x = Δ metric in units (Intelligence points, accuracy percentage points, NLL).
  Domain symmetric around 0: `±max(|lo|,|hi|)·1.1`, at least ±5 (Intelligence/accuracy) or ±0.05
  (NLL). Ticks 5–7, signed labels (`+10`, `−10`).
- Vertical reference line at 0 labelled "baseline: <name>".
- Color by verdict, not by model: CI entirely on the better side → `--success`; entirely on the
  worse side → `--danger`; crosses 0 → `--text-subtle`. "Better" means > 0 for Intelligence and
  accuracy and < 0 for NLL. Right-hand text column: `+61.4 (57.2 to 65.3) · 540 rows`.
- Tooltip: model, Δ with CI, n paired, verdict sentence ("Better than majority: the 95% CI
  excludes 0." / "No clear difference: the 95% CI includes 0.").
- Sort: by Δ desc (toggle: lab order).

### 7.3 `FamilyHeatmap`

- Rows = families (sorted by name, or by mean across models), columns = models (lab order).
  Cell 44×32 min (cells shrink to 32 wide on mobile; horizontal scroll inside the card with a
  sticky family column).
- Value: family `intelligence` (diverging scale, domain −100..100 centered 0; chance = 0) or
  `accuracy` (sequential `--surface-2` → `--accent`/`--brand`, 0..100). Legend: gradient bar with
  ticks and the words "worse than chance / chance / better".
- Cell text: value with 0 decimals; `—` for missing. Tooltip: family, model, Intelligence,
  accuracy, n rows. n < 20 cells get a dotted outline and the tooltip notes "few rows: wide
  uncertainty" (no per-family CI is computed by the SDK; do not invent one).

### 7.4 `ReliabilityDiagram`

- Data: `GET /api/runs/:rootId/runs/:model/:suite/reliability?bins=10&kind=all|noul|choice|score`
  → bins `{lo, hi, n, mean_conf, accuracy}` for top-option confidence, plus `ece`.
- Square plot, x = mean confidence 0..1, y = accuracy 0..1, ticks at 0, .2, … 1.
  Diagonal y = x dashed `--text-subtle` labelled "perfectly calibrated".
- Raw: points sized by `sqrt(n)` (3–9 px) connected by a 1.5 px line in the model color;
  calibrated (`+cal`) overlaid in the same hue, dashed line and hollow points. Under the plot, a
  count histogram (height 32) of rows per bin.
- For `noul` a shaded vertical band from 0.5 to 0.8 confidence (top-prob in 0.5–0.8 ⇔ P(yes) in
  the 0.2–0.8 abstention band) labelled "abstention band".
- Tooltip: bin range, n, mean confidence, accuracy, gap. Caption shows ECE raw → calibrated.

### 7.5 `LatencyChart`

- Dot-range plot: y = models, x = seconds (log scale when max/min > 20, else linear),
  segment from p50 to p95, p50 filled dot, p95 hollow dot. Baselines with sub-ms latency are
  shown as "< 1 ms" text instead of a mark.
- Histogram for one selected model: 30 log-spaced bins from predictions' `latency_s`, failed
  rows stacked in `--danger` on top so errors are visible.
- Tooltip: p50, p95, n, errors.

### 7.6 `TelemetryChart` and `Sparkline`

- Time series, x = wall time (last 15 min window default; "All" toggle), y fixed domains:
  GPU util % 0–100, memory used GB 0–total (total drawn as a dashed line), temperature °C
  0–100 (85 °C dashed warning line), power W 0–limit (limit dashed), rows/s 0–auto, errors
  cumulative 0–auto (danger color). One line per GPU index (chart palette) for multi-GPU.
- Small multiples: 3×2 grid ≥ 1024, 2 columns ≥ 640, 1 column below. Height 140 each.
- Gaps when a sample is missing (no interpolation across > 2 missing intervals).
- Tooltip crosshair shared across the grid (hovering one chart shows the same timestamp in all).
- `Sparkline` (Overview, job rows): 80×20, no axes, last value as text beside it.

### 7.7 `JevBenchPanel`

Per model card: "JevBench public tasks · local v1.5-rule proxy" as the title (never "JevBench
score"), big number `intelligence_proxy` with label "Intelligence (local proxy)", `n_correct/231
right`, competence by type as three horizontal bars (yes/no, choice, score; domain −100..100, 0
line), "yes/no answers in abstention band: 12 of 77", harness commit (short, copyable), and the
fixed note from the SDK: "local proxy of the JevBench v1.5 rules on the 231 public v1 tasks; not
a board score". No CI is shown because the SDK does not compute one; the card says "no CI: 231
fixed tasks, not resampled".

### 7.8 `LabelBalanceBars`

Per kind: stacked 100% bar of label counts (0..n−1), segments labelled with count; the expected
uniform share is a tick. Used in suite stats.

---

## 8. Command palette and keyboard shortcuts

### 8.1 Command palette (cmd/ctrl+K)

Dialog (`role="dialog"`, input is a combobox controlling a listbox). Fuzzy search over commands
and entities. Sections in order: Recent (last 5 used), Commands, Navigation, Labs, Results, Jobs,
Models. Each item: icon, label, optional detail (muted), shortcut `Kbd`. Enter runs; cmd/ctrl+Enter
opens in a new tab where it is navigation. A typed `>` prefix limits to commands; `#` to jobs;
`@` to labs. Commands that open a dialog never execute side effects directly.

| id | label | does |
|---|---|---|
| `nav.overview` … `nav.settings` | Go to Overview / Labs / Jobs / Results / Models / Data / Compute / Settings | navigate |
| `lab.new` | New lab… | `/labs/new` |
| `lab.run` | Run lab… | pick lab → run dialog |
| `lab.open` | Open lab: <name> | per lab |
| `lab.validate` | Validate current lab | Editor only |
| `lab.save` | Save lab | Editor only |
| `eval.quick` | Quick eval… | dialog 4.3.5 |
| `job.open` | Open job: <title> | per recent job |
| `job.cancel` | Cancel current job… | on a job page, running |
| `job.copyCommand` | Copy job command | on a job page |
| `results.open` | Open results: <lab> | per run root |
| `results.compare` | Compare two runs… | compare page |
| `results.export` | Export report… | run root page |
| `results.calibrate` | Calibrate a run… | dialog |
| `results.rebuild` | Rebuild report | run root page |
| `jevbench.run` | Run JevBench public tasks… | dialog |
| `model.pull` | Pull a model… | dialog |
| `data.uploadCsv` | Upload CSV… | Data tab |
| `data.generate` | Generate training rows… | Data tab |
| `data.split` | Split dev/test… | Data tab |
| `data.leakcheck` | Leakcheck training data… | Data tab |
| `data.inspectSuite` | Inspect suite: smoke / synthetic / heldout | drawer |
| `compute.doctor` | Check this machine (doctor) | Compute > Local |
| `compute.vastOffers` | Find vast.ai offers… | Compute > vast |
| `compute.awsIdentity` | Show AWS identity | Compute > AWS |
| `compute.sshAdd` | Add SSH host… | dialog |
| `compute.sshTest` | Test SSH host: <name> | runs test |
| `theme.toggle` | Toggle theme | |
| `theme.dark` / `theme.light` / `theme.system` | Theme: … | |
| `ui.shortcuts` | Keyboard shortcuts | help overlay |
| `ui.sidebar` | Toggle sidebar | |
| `ui.copyWorkspace` | Copy workspace path | |

Destructive commands (destroy, terminate, delete) are not in the palette; they live only on the
object they act on.

### 8.2 Shortcuts

Mod = ⌘ on macOS, Ctrl elsewhere. Single-key shortcuts are disabled while focus is in an input,
textarea, select or the editor (except Mod-combos and Esc).

| keys | action |
|---|---|
| Mod+K | command palette |
| / | focus the page's search field |
| ? | shortcut help |
| g o / g l / g j / g r / g m / g d / g c / g s | go to Overview / Labs / Jobs / Results / Models / Data / Compute / Settings (second key within 1 s) |
| n | New lab (Labs), Pull a model (Models), Add host (SSH tab) |
| r | Run… (lab page) |
| e | Quick eval… |
| Mod+S | save lab (Editor) |
| Mod+Enter | primary action of the open dialog (if enabled; never bypasses typed confirm) |
| Esc | close dialog/drawer/palette; clear search |
| j / k, ↓ / ↑ | next / previous row in a table |
| Enter | open focused row |
| [ / ] | previous / next row in RowDetail; previous / next tab elsewhere |
| f | toggle Follow in Logs |
| t | toggle theme |
| Mod+\ | toggle sidebar |

---

## 9. Accessibility (WCAG 2.2 AA)

- Contrast: text ≥ 4.5:1 (≥ 3:1 for ≥ 18.66 px bold / 24 px); UI component boundaries and
  graphical objects needed to understand them ≥ 3:1. The tokens in 5.1 satisfy this; new colors
  must be added to the contrast test (`ui/src/theme/contrast.test.ts` asserts every text/bg
  token pair listed in 5.1).
- Focus: every interactive element has a visible focus ring: `outline: 2px solid var(--focus);
  outline-offset: 2px` (inset 2px inside tables and tabs). Never `outline: none` without a
  replacement. Focus is not obscured by sticky bars (scroll-padding set to their heights).
- Keyboard: everything works without a mouse; tab order follows reading order; dialogs trap and
  restore focus; skip link "Skip to content" as the first focusable element.
- Landmarks: `header` (topbar), `nav` (sidebar, `aria-label="Main"`), `main`, `aside` for side
  panels. One `h1` per page; headings do not skip levels.
- Names: icon-only buttons have `aria-label` (e.g. "Cancel job run first-lab", not "Cancel");
  inputs have visible labels; status icons have text; charts have `role="img"` + generated
  `aria-label` + data table alternative.
- Live regions: one polite region for toasts and "Copied"; job status changes announce politely
  ("run first-lab succeeded"); logs are not live (summary every 10 s instead); form errors after
  submit move focus to the first invalid field and announce the count.
- Motion: respects `prefers-reduced-motion` and the setting; no flashing content.
- Targets: ≥ 24×24 px desktop, ≥ 44×44 on touch layouts.
- Zoom/reflow: usable at 200% zoom and at 320 CSS px width without loss of content (390 px is
  the design target; 320 must not break).
- Tables: `th scope`, `aria-sort`, captions (visually hidden where the card title is the caption).
- Automated check: e2e runs `@axe-core/playwright` on every page in both themes and fails on any
  serious or critical violation.

---

## 10. Copy guidelines

Voice: plain, specific, calm, honest. Short sentences. Say what happened, what it means, what to
do. No exclamation marks, no jokes in errors, no "Oops", no blame ("you entered"). Sentence case
for everything (titles, buttons, tabs). Buttons are verbs naming the result: "Start run", "Pull",
"Destroy instance", not "OK"/"Submit"/"Yes".

Required wording (do not paraphrase):

| context | text |
|---|---|
| Metric name | **Intelligence (local proxy)** on first use in a view; "Intelligence" afterwards, with the `ⓘ` tooltip |
| `ProxyNotice` | "Intelligence (local proxy): JevBench v1.5 rules applied to this suite. It ranks these runs against each other; it is not a JevBench board score." |
| JevBench panel title | "JevBench public tasks · local v1.5-rule proxy" |
| Calibrated tag | "+cal: temperature per question kind, fitted on dev rows, scored on test rows" |
| CI tooltip | "95% bootstrap confidence interval over rows (1,000 resamples)" (2,000 for comparisons, "paired") |
| Failed rows | "n rows failed and were scored as uniform answers, so failures cost score instead of disappearing." |
| Overlapping CIs | "No clear difference: the 95% CI includes 0." |
| Remote spend | "This rents a machine that bills until it is destroyed. decider-lab destroys it when the run ends, fails, is cancelled, or reaches max hours." |
| Fake cloud | "Cloud calls return fixtures. Nothing is created or billed." |
| Secrets | "Studio never shows or stores secret values. Set them in the shell you start `decider-lab ui` from." |
| Estimate caveat (aws) | "Approximate on-demand list price (us-east-1, table dated <date>). Your bill may differ." |

Never write: "score" or "rank" for Intelligence without "proxy"; "JevBench score"; "accuracy"
for Intelligence; "significant" (say "the CI excludes 0"); "safe" about spending; "AI".
Numbers carry units and sign where they are differences (`+3.2`, `−0.018`). Use "row" (not
sample/example), "suite", "lab", "run", "model", "baseline", "family", "kind" (yes/no, choice,
score; "noul" only in code and ids) consistently with the SDK docs.

Error messages: first sentence is what failed in human words; second is the fix; the CLI/SDK
message goes in Details. Example: "Could not pull the model. The file's sha256 does not match
the one in the lab; the download was discarded." Details: `sha256 mismatch: expected 9a1f…, got 03be…`.

---

## 11. CLI parity map

| CLI | Studio |
|---|---|
| `init DIR --template` | Labs > New lab |
| `doctor` | Compute > Local; Overview onboarding step 1 |
| `run lab.yaml [--on …] [flags]` | Lab > Run dialog → job `run` |
| `compute offers/ls/down --on vast\|aws` | Compute > vast.ai / AWS |
| `eval --model/--serve …` | Quick eval dialog → job `eval` |
| `compare A B --split` | Results > Compare two runs |
| `calibrate RUN` | Results > Calibration > Calibrate → job `calibrate` |
| `report ROOT` | Results > Rebuild report |
| `suites [--build --out]` | Data > Suites > Inspect / Save as JSONL |
| `data from-csv` | Data > Upload CSV |
| `data generate` | Data > Generate |
| `data check` / `data stats` | Data > Files (validity column) / Inspect |
| `data split` | Data > Split |
| `data leakcheck [--drop-to]` | Data > Leakcheck |
| `pull SOURCE [flags]` | Models > Pull → job `pull` |
| `models` | Models > Cache |
| `jevbench --url --out` | Results > JevBench > Run JevBench → job `jevbench` |
| (new) | `ui` starts Studio |

---

## 12. Test ids and e2e coverage map

Test id convention: `data-testid="<area>-<thing>[-<key>]"`, kebab-case; keys are names as shown
(model names, lab names) or server ids. These ids are part of the contract between C/D and the
e2e specs; changing one is a breaking change.

| area | ids |
|---|---|
| shell | `nav-overview`, `nav-labs`, `nav-jobs`, `nav-results`, `nav-models`, `nav-data`, `nav-compute`, `nav-settings`, `topbar-palette`, `topbar-jobs-badge`, `topbar-theme`, `fake-cloud-pill`, `palette-input`, `palette-item-<commandId>` |
| overview | `kpi-labs`, `kpi-runs`, `kpi-best`, `kpi-jobs`, `kpi-spend`, `recent-results`, `recent-row-<model>-<suite>`, `onboarding`, `onboarding-step-<1..4>`, `quick-eval` |
| labs | `labs-table`, `lab-row-<name>`, `lab-new`, `lab-template-<eval\|finetune>`, `lab-name`, `lab-dir`, `lab-create`, `lab-tab-<summary\|editor\|runs>`, `lab-editor`, `lab-problems`, `lab-save`, `lab-run`, `lab-status` |
| run dialog | `run-backend-<local\|ssh\|aws\|vast>`, `run-field-<name>`, `run-estimate`, `run-blocker`, `run-confirm-input`, `run-confirm-phrase`, `run-command`, `run-start` |
| jobs | `jobs-table`, `job-row-<id>`, `job-status`, `job-stage-<name>`, `job-progress-<model>-<suite>`, `job-cancel`, `job-tab-<progress\|logs\|telemetry\|command>`, `log-viewer`, `log-search`, `log-follow`, `telemetry-chart-<metric>` |
| results | `runroot-card-<lab>`, `suite-chip-<suite>`, `scores-toggle-<raw\|cal\|both>`, `leaderboard`, `leaderboard-row-<model>`, `proxy-notice`, `forest-plot`, `family-heatmap`, `reliability-<model>`, `latency-chart`, `jevbench-panel-<model>`, `rows-table`, `row-detail`, `compare-a`, `compare-b`, `compare-result`, `export-menu`, `provenance` |
| models | `models-table`, `model-row-<ref8>`, `pull-open`, `pull-source`, `pull-inspect`, `pull-require-pinned`, `pull-start`, `model-delete-<ref8>` |
| data | `suite-card-<name>`, `suite-inspect-<name>`, `suite-stats`, `csv-drop`, `csv-preview`, `csv-convert`, `generate-form`, `generate-run`, `split-form`, `leakcheck-form`, `leakcheck-result` |
| compute | `doctor-list`, `doctor-row-<item>`, `vast-credit`, `vast-instance-<id>`, `vast-destroy-<id>`, `vast-offers`, `aws-profile`, `aws-region`, `aws-identity`, `aws-quota-<g\|p\|standard>`, `aws-instance-<id>`, `aws-terminate-<id>`, `bedrock-models`, `ssh-host-<name>`, `ssh-add`, `ssh-test-<name>` |
| shared | `confirm-dialog`, `confirm-input`, `confirm-submit`, `toast`, `error-state`, `empty-state` |

E2e specs (each clicks the real controls, against a real server started on a temp workspace with
`DECIDER_LAB_FAKE_CLOUD=1` and the seeded runs):

1. `shell.spec.ts`: token login via URL, 401 without token, nav to every page, palette opens with
   Mod+K and navigates, `g r` chord, theme toggle persists after reload, 390 px drawer nav,
   axe on every page in both themes.
2. `overview.spec.ts`: KPIs reflect seeded data, recent row opens single run, onboarding on an
   empty workspace.
3. `labs.spec.ts`: create from template, edit YAML with an error (problem shows, Run disabled),
   fix and save, 409 conflict path (file modified on disk by the test), local run starts a job.
4. `jobs.spec.ts`: local smoke run streams logs, progress reaches 90/90, stages complete, result
   link opens results; cancel a long run (fake slow model) → cancelled; log search finds a line;
   a remote vast run in fake mode requires the typed phrase, shows all 7 stages and simulated
   telemetry.
5. `results.spec.ts`: leaderboard shows CI text and proxy notice, raw/cal toggle, forest plot
   has one row per non-baseline model, heatmap cells, reliability diagram, row explorer filter
   "wrong for" and row detail, compare two runs shows Δ with CI, export downloads REPORT.md.
6. `models.spec.ts`: inspect warns on unpinned hf source; require-pinned blocks; pull from a
   local fixture directory and an `https://127.0.0.1` fixture archive with sha256 succeeds and
   appears in cache; delete with typed ref.
7. `data.spec.ts`: inspect smoke stats; upload CSV preview + convert; generate; split; leakcheck
   reports overlap > 0 against smoke for rows generated from it.
8. `compute.spec.ts`: doctor list; vast credit/offers/instances fixtures; destroy requires typed id
   and removes the row; AWS identity/quotas/instances/Bedrock fixtures; terminate typed; SSH add,
   test ok, test `.invalid` host fails.
9. `settings.spec.ts`: env shows names with set/unset and never a value (test sets a known secret
   value in the server env and asserts it appears nowhere in the DOM or network responses).
