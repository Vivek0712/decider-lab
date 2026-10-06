# decider-lab

**Evaluate, calibrate, compare and fine-tune decision models, from one YAML file, on your laptop or on a rented GPU.**

decider-lab is the harness behind a series of Strands Decider experiments, turned into a tool so you do not have to rebuild it. It is built around [Strands Decider](https://github.com/strands-labs/strands-decider)'s decision rows and System One API, and it is open to any model you can put behind an adapter: a Strands Decider checkpoint, any System One server, an OpenAI-compatible chat model, or your own Python function.

```bash
pip install "decider-lab @ git+https://github.com/<you>/decider-lab"   # core: no torch, no GPU
decider-lab init my-lab && cd my-lab
decider-lab run lab.yaml          # -> runs/first-lab/REPORT.md
```

## What you get

| You want to... | decider-lab gives you |
|---|---|
| know if a model is any good | suites with known answers (`smoke`, `synthetic`, `heldout`, your own JSONL), the JevBench v1.5 rules as a local proxy, plus accuracy, NLL, Brier, RPS and ECE, per kind and per task family |
| know if model A beats model B | a paired comparison on the same rows with a bootstrap 95% interval; a delta whose interval crosses zero is noise |
| make probabilities honest | per-kind temperature fitted on `dev` and scored on `test`, for any model's outputs |
| fine-tune your own decider | data from a CSV or from generators, a leak check against your evaluation, `strands-decider train` with the released recipe, calibration, then the same evaluation |
| use a GPU you do not own | `decider-lab gpu run`: rents a vast.ai GPU, installs torch for the host's driver, runs the lab, copies the results back, and destroys the instance even when the run fails |
| run JevBench | the 231 public tasks through JevBench's own harness against any System One URL, scored with the v1.5 rules (a local proxy, never a board score) |
| publish an evaluation | a JSONL of decision rows is a suite; its sha256 fingerprint is recorded in every run, so others can check they scored the same rows |

## Five-minute tour

```bash
decider-lab doctor                                   # what this machine can do
decider-lab eval --model uniform --suite smoke       # the plumbing, with a baseline, in seconds
decider-lab eval --model http://127.0.0.1:8000 --suite synthetic --name my-server
decider-lab compare runs/my-server/synthetic runs/model/synthetic
```

A lab file runs every model on every suite and writes one report:

```yaml
name: first-lab
models:
  v19: {serve: StrandsAgents/strands-decider-2B-hobson-v19}   # started and stopped for you
  mine: {url: "http://10.0.0.5:8000"}                          # any System One server
  heuristic: {python: "my_model:Heuristic"}                     # your code
  majority: {baseline: majority}                                # the floor to beat
suites: [smoke, {synthetic: {per_kind: 100}}, {file: data/my_eval.jsonl}]
calibrate: true
baseline: majority
```

Fine-tune Strands Decider v19 on your own rows and compare it with v19:

```bash
decider-lab init ft --template finetune && cd ft && cat README.md
decider-lab gpu run lab.yaml --gpu A100_SXM4 --max-price 0.8 --max-hours 2
```

## Install

| extra | for |
|---|---|
| (none) | evaluating any model over HTTP or Python, scoring, comparing, calibrating, data tools |
| `[strands]` | serving or fine-tuning Strands Decider on this machine (pins strands-decider from GitHub, which has image input) |
| `[heldout]` | the `heldout` suite (public datasets, built locally at pinned revisions) |
| `[chess]` | the chess family of the generated suites |
| `[gpu]` | `decider-lab gpu` (the vast.ai CLI) |

Python 3.10 or newer. The core depends only on PyYAML.

## Docs

- [docs/quickstart.md](docs/quickstart.md): from nothing to a report, then to a fine-tuned model
- [docs/concepts.md](docs/concepts.md): rows, suites, adapters, runs, and how the scores are defined
- [docs/finetune.md](docs/finetune.md): data, leak checks, the training recipe, and what to expect
- [docs/gpu.md](docs/gpu.md): renting a GPU safely, and the traps the bootstrap handles
- [docs/extending.md](docs/extending.md): your own adapter, your own suite, publishing an evaluation

## Honesty rules built in

- Every run records who answered (`/health`, model name, adapter), the suite fingerprint, the host and the GPU.
- A row the model fails to answer is scored as a uniform answer and counted, never dropped.
- `+cal` runs fit on `dev` and score on `test`; nothing is fitted on the rows it is scored on.
- The Intelligence number is a local proxy of the JevBench v1.5 rules. It ranks runs on the same suite against each other and is never a board score.
- `serve` refuses a port that already answers, and checks after the run that the same server answered throughout.

## Relationship to Strands Decider

decider-lab does not change how Strands Decider trains or answers. It calls `strands-decider serve`, `train` and `calibrate`, and reads and writes its row format. Rows made here are valid Strands Decider training data, and Strands Decider's data is a valid decider-lab suite. Full fine-tuning (`continue_from`) needs a strands-decider that has it (strands-labs/strands-decider#29); with today's upstream main, decider-lab falls back to `init_from` and says so.

Apache-2.0. The generated families' labels are computed by programs in this repository. Public datasets used by `heldout` are downloaded on your machine and keep their own licences; nothing from them is redistributed. JevBench task text stays in your local cache.
