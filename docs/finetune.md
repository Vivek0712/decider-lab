# Fine-tuning a decider

decider-lab runs Strands Decider's own trainer. It adds what is easy to get wrong around it: valid data, no leaks into the evaluation, a known-good configuration, calibration, and a fair comparison afterwards.

## The loop

```yaml
finetune:
  mine:
    from: StrandsAgents/strands-decider-2B-hobson-v19   # or omit `from` and set base_model to train from scratch
    train: data/train.jsonl                             # one file or a list
    steps: 300                                          # omit for one full epoch
    calibrate_on: data/calib.jsonl                      # strands-decider calibrate writes temperatures into the checkpoint
    # lr, head_lr, micro_batch_size, grad_accum, seed, max_length, lora_r, lora_alpha, kl_frozen_weight
    # config: {any: strands-decider TrainConfig field}
models:
  v19: {serve: StrandsAgents/strands-decider-2B-hobson-v19}
  # `mine` is added for you, served from its checkpoint after training
suites: [{synthetic: {per_kind: 100}}, heldout]
baseline: v19
```

`decider-lab run lab.yaml` trains `mine` (reusing a finished checkpoint on re-runs), calibrates it, serves it and every other model, evaluates all of them on every suite, and reports `mine` against `v19` row by row. Training writes `runs/<lab>/_finetune/mine/{train.yaml,train.log,checkpoint/}`.

## Continue or init

| strands-decider has | decider-lab uses | what trains |
|---|---|---|
| `continue_from` (strands-labs/strands-decider#29) | `continue_from` | the checkpoint's LoRA adapter and its trained pointer head: a real fine-tune |
| only `init_from` (upstream main today) | `init_from`, with a warning | a new slot head on a frozen torso and adapter: cheap, much weaker |

`decider-lab doctor` tells you which you have. To get `continue_from` before it is merged, install strands-decider from a branch that has it, for example with `compute.strands_decider: "strands-decider[vision,cuda] @ git+https://github.com/<fork>/strands-decider@<commit>"` in the lab, or `--strands-decider` on the command line.

## Data

- From a CSV: `decider-lab data from-csv`. From code: write rows as JSONL (see [concepts.md](concepts.md)).
- Generated: `decider-lab data generate --families arithmetic,calendar --per-kind 1000 --seed 7 --exclude-suite synthetic:per_kind=100`, with a different seed from the evaluation, excluding its rows.
- Always: `decider-lab data leakcheck data/train.jsonl --against <every suite you will report>`. It matches normalised inputs, so a different question about the same input also counts as a leak. It exits non-zero on overlap; `--drop-to` writes a clean copy.

Training on a family makes that family *seen*. Report it as what training on the family did, and keep a suite of families you did not train on (such as `heldout`) to show what it did elsewhere.

## The recipe

The defaults are the released 2B recipe (Qwen3.5-2B-Base, pointer head, LoRA r16 on attention, MLP and the recurrent layers' projections, frozen-KL 0.3, option shuffling, ordinal smoothing), sized for one GPU, with a smaller learning rate when continuing a trained checkpoint (5e-5 adapter, 5e-4 head). Any TrainConfig field can be set under `config:`; an unknown field is refused before the GPU starts.

A 2B continue-fine-tune fits a 24 GB GPU. Roughly: 150 steps of batch 16 on short rows take a few minutes on an A100.
