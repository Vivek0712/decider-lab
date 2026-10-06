# finetune-lab

Make the data (generated here, so the labels are computed by a program; for your own task use
`decider-lab data from-csv`), check it does not leak into the evaluation, and run:

    mkdir -p data
    decider-lab data generate --out data/train.jsonl --per-kind 400 --seed 7 --exclude-suite synthetic:per_kind=100
    decider-lab data generate --out data/calib.jsonl --per-kind 60 --seed 8 --exclude-suite synthetic:per_kind=100
    decider-lab data leakcheck data/train.jsonl --against synthetic:per_kind=100
    decider-lab run lab.yaml          # or: decider-lab gpu run lab.yaml --gpu A100_SXM4 --max-hours 2

REPORT.md then shows `mine` against v19, row by row, on rows neither trained on.
The generated families are then *seen* families for `mine`: the comparison shows what training
on a family does, not general ability. Add `heldout` to `suites` to see what it did elsewhere.
