# Quickstart

## 1. Install and check

```bash
python -m venv .venv && . .venv/bin/activate
pip install "decider-lab @ git+https://github.com/Vivek0712/decider-lab"
decider-lab doctor
```

`doctor` lists what this machine can do (torch and its device, strands-decider and whether it has image input and full fine-tuning, the optional suites, vast.ai credit) and what to install for the rest.

## 2. A first report, no GPU

```bash
decider-lab init first-lab && cd first-lab
```

Comment out the `v19` model in `lab.yaml` if this machine has no GPU and no Apple MPS, then:

```bash
decider-lab run lab.yaml
open runs/first-lab/REPORT.md
```

You get the baselines and the example `my_model.py` on `smoke` and `synthetic`, calibrated and raw, compared row by row with `majority`. Replace `my_model.py`'s `predict_one` with a call to your model and run again.

## 3. A real model

On a machine with a GPU (or Apple silicon):

```bash
pip install "decider-lab[strands]"
decider-lab run lab.yaml       # v19 is downloaded, served, evaluated and stopped
```

Or point at a server you already run: `models: {mine: {url: "http://host:8000"}}`.

No GPU here? Run the same lab somewhere that has one ([compute.md](compute.md)):

```bash
decider-lab run lab.yaml --on ssh --host ubuntu@my-gpu-box            # a machine you have
decider-lab run lab.yaml --on aws --instance-type g6e.xlarge           # pip install "decider-lab[aws]"
decider-lab run lab.yaml --on vast --gpu RTX_4090 --max-price 0.6      # pip install "decider-lab[vast]"
```

## 4. Your own evaluation

```csv
state,question,answer,options,kind
"Order #123 arrived damaged",Should this go to a human?,yes,,
"Refund request, item unopened",Which queue?,returns,returns|billing|shipping,
"Thanks, all good!",How urgent is it?,low,low|medium|high,score
```

```bash
decider-lab data from-csv tickets.csv --out data/tickets.jsonl --task support/triage
decider-lab data split data/tickets.jsonl --out data/tickets.jsonl
```

Add `{file: data/tickets.jsonl}` to `suites`.

## 5. Fine-tune

```bash
decider-lab init ft --template finetune && cd ft && cat README.md
```

The template generates training rows, checks they do not leak into the evaluation, fine-tunes v19 on them, calibrates, and compares the result with v19 on held-out rows. See [finetune.md](finetune.md).
