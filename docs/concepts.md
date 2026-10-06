# Concepts

## Rows

Everything is a **decision row**, the same record Strands Decider trains on:

```json
{"kind": "choice", "state": "Context: ...", "instructions": "Which ending is most plausible?",
 "options": [["A", "first ending"], ["B", "second ending"]], "label": 1,
 "task": "myeval/endings", "split": "test", "images": ["img/0001.png"]}
```

| kind | options | label |
|---|---|---|
| `noul` (yes/no) | exactly `[["false", ...], ["true", ...]]` | 1 = yes |
| `choice` | 2 or more `[name, description]`, names unique | index of the right option |
| `score` | 2 to 10 levels named `"0"`, `"1"`, ... from low to high | the gold level |

`task` names the family (reports break down by it), `split` is `dev`/`test`/`train`, and `images` (optional) are paths relative to the suite file or `data:` URIs. A row's `id` is a hash of what the model sees, never of the label. `decider-lab data check file.jsonl` validates a file and names every bad row.

## Suites

A suite is a named list of rows. The built-ins:

| suite | rows | notes |
|---|---|---|
| `smoke` | 90 | three generated families x three kinds x 10; proves the plumbing, says nothing about the model |
| `synthetic` | `per_kind` x families x 3 | arithmetic, calendar, seating (and chess with the extra); labels computed by programs, balanced by construction, 40% dev / 60% test |
| `heldout` | about 3,300 | StrategyQA, CommonsenseQA 2.0, CommonsenseQA, ARC-Challenge, HellaSwag, STS-B at pinned revisions, plus the synthetic families; none is a Strands Decider training family |
| a path | yours | any JSONL of rows |

On the command line, parameters go after a colon: `synthetic:per_kind=100,seed=3`. `decider-lab suites --build heldout --out heldout.jsonl` exports a suite to inspect or share.

## Adapters

An adapter answers rows with one probability per option, in the row's option order. Built in:

| spec | answers with |
|---|---|
| `{serve: <checkpoint or hub id>, vision: false}` | `strands-decider serve`, started for the lab and stopped after it |
| `{url: http://...}` or a bare URL | any System One server (`POST /v1/systemone`) |
| `{bedrock: us.amazon.nova-pro-v1:0, region: ...}` | an Amazon Bedrock model through the Converse API (text and images) ([llms.md](llms.md)) |
| `{strands: {provider: ..., model_id: ...}}` | a Strands Agents model: bedrock, openai, anthropic, ollama, litellm |
| `{chat: <model>, base_url: ..., api_key_env: ...}` | an OpenAI-compatible endpoint asked for JSON probabilities (text only) |
| `{python: module:attr}` | a function `row -> probabilities`, or an object or class with `predict_one` / `predict` |
| `uniform`, `majority`, `random` | baselines; `majority` is the constant-answer floor |

## Runs

`runs/<lab>/<model>/<suite>/` holds `predictions.jsonl` (one line per row: probabilities, latency, error), `scores.json` and `run.json` (the answerer's `/health`, the suite sha256, host, GPU, versions, wall time). Runs resume: rows already answered are not asked again. `REPORT.md` and `report.json` sit at the lab root.

## Scores

**Intelligence (local proxy)** applies the published JevBench v1.5 rules to the suite:

- yes/no: a P(yes) strictly between 0.2 and 0.8 is an abstention and counts as wrong; a decisive answer on the right side is right. Credit is chance-corrected: right = +1, wrong = -1.
- choice: right if the top option is gold; credit `(right - 1/n) / (1 - 1/n)`.
- score: graded on the expected level: `100 x (1 - sum nMAE / sum nMAE_chance)`.
- competence per kind is the mean credit in %, and Intelligence is the mean over the kinds present.

It ranks runs on the same rows. It is not, and cannot be compared with, a JevBench board score (the board's tier weights and sealed half are not public).

Beside it: **accuracy** (top option), **NLL** and **Brier** (proper scoring rules: lower is better and honest probabilities minimise them), **RPS** for score questions, top-probability **ECE**, the share of yes/no answers **in the band**, and median and p95 **latency**.

**Comparisons** are paired: both runs are scored on the same row ids, rows are resampled with replacement (bootstrap, 2,000 draws), and the 95% interval of the difference is reported. Read a delta whose interval contains zero as no measured difference.

**Calibration** (`calibrate: true`, or `decider-lab calibrate RUN`) fits one temperature per kind on `dev` rows by minimising NLL and scores the result on `test` rows, written to `<run>+cal`. It makes probabilities honest; under the band rule, honest uncertainty on yes/no can lower Intelligence, and the report shows both so you can see it.

## Comparing like with like

Run every arm of a comparison on the same kind of device. The same v19 on the same 90 rows, served on a CPU (AWS c7i) and on a GPU (A100, bf16), agreed to a median of 0.002 in probability, but 3 yes/no answers moved across the 0.2/0.8 band edge, which moved Intelligence by 6.7 points (95% CI -0.0 to 14.8). `decider-lab compare cpu_run gpu_run --split all` measures this for your model.
