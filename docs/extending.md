# Extending decider-lab

## Your own model

The smallest adapter is a function:

```python
# my_model.py
def answer(row):
    """One probability per option, in row["options"] order."""
    if row["kind"] == "noul":
        p_yes = my_model.prob_yes(row["state"], row["instructions"])
        return [1 - p_yes, p_yes]
    return my_model.distribution(row["state"], row["instructions"], [d for _, d in row["options"]])
```

```yaml
models:
  mine: {python: "my_model:answer"}
```

For batching, give an object a `predict(rows) -> list[probabilities]` method; for state (a loaded model), use a class with no constructor arguments, or an object with `fit(rows)` to see the suite first. In a package, subclass `decider_lab.adapters.Adapter` and implement `predict_one` (and `describe`, which goes into `run.json`). Raise `AdapterError` for a row you cannot answer: the run records it, scores it as uniform, and goes on.

Any server that speaks System One needs no code: `{url: http://...}`.

## Your own suite

Write rows as JSONL ([concepts.md](concepts.md)), check them, and give them splits:

```bash
decider-lab data check my_eval.jsonl
decider-lab data split my_eval.jsonl --out my_eval.jsonl --dev-fraction 0.4
```

For a generated family, write a generator `(rng, kind, template) -> row or None` in the style of `src/decider_lab/suites/generators.py` (labels computed by a program, a template per question shape) and register it in `FAMILIES` and `TEMPLATES`. `draw` balances the labels for you.

## Publishing an evaluation

A suite is a file, so publishing one means publishing the file and how it was made:

1. Rows with `task` names that say what they measure, and `dev`/`test` splits.
2. The licence of every source, and nothing you may not redistribute (publish a builder instead, as `heldout` does).
3. Its fingerprint: `run.json` records `suite_sha256` for every run, so a reader can confirm they scored the same rows.
4. Baselines: run `majority` and `uniform` beside your model; a score is only meaningful above the constant-answer floor.
5. A leak statement: which training sets you checked it against with `decider-lab data leakcheck`.

Results then travel as run directories: `predictions.jsonl` lets anyone recompute every number, or compare their model with yours row by row with `decider-lab compare`.
