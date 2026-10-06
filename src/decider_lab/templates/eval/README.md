# first-lab

    decider-lab doctor          # what this machine can run
    decider-lab run lab.yaml    # -> runs/first-lab/REPORT.md

`models` can be Strands Decider checkpoints (`serve`), any System One server (`url`), an
OpenAI-compatible chat model (`chat`), your own Python (`python`), or a baseline. `suites` can be
built-ins or your own JSONL. Every run writes predictions, scores and who answered into
runs/first-lab/<model>/<suite>/, and REPORT.md puts them side by side with paired 95% intervals.

No local GPU? `decider-lab run lab.yaml --on ssh|aws|vast` (docs/compute.md).
