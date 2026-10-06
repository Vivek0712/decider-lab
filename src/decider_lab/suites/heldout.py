"""heldout: public task families no Strands Decider trains on, plus the generated families.

Built on your machine from pinned dataset revisions; nothing is redistributed, and each
dataset keeps its own licence (listed in SOURCES). BoolQ, MNLI/SNLI and JevBench are left
out on purpose: Strands Decider trains on the first two, and the third is the benchmark.

    yes/no   StrategyQA, CommonsenseQA 2.0, + generated
    choice   CommonsenseQA, ARC-Challenge, HellaSwag, + generated
    score    STS-B (6 similarity levels), + generated

Needs: pip install "decider-lab[heldout]"  (datasets, huggingface_hub, pyarrow).
"""

from __future__ import annotations

import json
import random
from typing import Any

from ..rows import YES_NO, Row
from . import DEFAULT_FAMILIES, generators, split

SOURCES = {
    "strategyqa": ("ChilleD/StrategyQA", "705562638fe1d8ca6bb98c66fc8f94d45fda8c83", "MIT"),
    "commonsenseqa": ("tau/commonsense_qa", "94630fe30dad47192a8546eb75f094926d47e155", "MIT"),
    "arc_challenge": ("allenai/ai2_arc", "210d026faf9955653af8916fad021475a3f00453", "CC BY-SA 4.0"),
    "stsb": ("sentence-transformers/stsb", "ab7a5ac0e35aa22088bdcf23e7fd99b220e53308", "see dataset card"),
}
FILES = {
    "csqa2": ("tasksource/commonsense_qa_2.0", "23ff83b18ed76882f3af1a403a7c464b72efcd86",
              "teach_your_ai_dev.json", "CC BY 4.0"),
    "hellaswag": ("Rowan/hellaswag", "218ec52e09a7e7462a5400043bb9a69a41d06b76",
                  "data/validation-00000-of-00001.parquet", "MIT"),
}
SIMILARITY = ["completely different meaning", "mostly different, share a topic",
              "not equivalent, share some details", "roughly equivalent, some important details differ",
              "mostly equivalent, minor details differ", "completely equivalent in meaning"]
COUNTS = {"strategyqa": 300, "csqa2": 300, "commonsenseqa": 300, "arc_challenge": 300, "hellaswag": 300, "stsb": 450}


def _load(name: str, split_name: str, config: str | None = None) -> list[dict[str, Any]]:
    try:
        from datasets import load_dataset
    except ImportError as e:
        raise ImportError("the heldout suite needs: pip install 'decider-lab[heldout]'") from e
    repo, revision, _ = SOURCES[name]
    return list(load_dataset(repo, config, split=split_name, revision=revision))


def _file(name: str) -> str:
    from huggingface_hub import hf_hub_download

    repo, revision, filename, _ = FILES[name]
    return hf_hub_download(repo, filename, repo_type="dataset", revision=revision)


def _strategyqa(rows: list[Row], rng: random.Random, n: int) -> list[Row]:
    rng.shuffle(rows)
    return [{"kind": "noul", "state": r["question"], "instructions": "Is the true answer to this question yes?",
             "options": YES_NO, "label": int(bool(r["answer"])), "task": "heldout/strategyqa"}
            for r in rows if r.get("question") and r.get("answer") is not None][:n]


def _csqa2(lines: list[str], rng: random.Random, n: int) -> list[Row]:
    rows = [json.loads(x) for x in lines if x.strip()]
    rng.shuffle(rows)
    return [{"kind": "noul", "state": f"Question or claim: {r['question']}",
             "instructions": "Is the answer yes (for a claim: is the claim true)?",
             "options": YES_NO, "label": int(r["answer"] == "yes"), "task": "heldout/csqa2"}
            for r in rows if r.get("answer") in ("yes", "no")][:n]


def _multiple_choice(rows: list[Row], rng: random.Random, instructions: str, task: str, n: int) -> list[Row]:
    rng.shuffle(rows)
    out = []
    for r in rows:
        labels, texts = r["choices"]["label"], r["choices"]["text"]
        if r["answerKey"] in labels:
            names = list(dict.fromkeys(texts))  # an option text repeated in a question is kept once
            out.append({"kind": "choice", "state": r["question"], "instructions": instructions,
                        "options": [[t, t] for t in names],
                        "label": names.index(texts[labels.index(r["answerKey"])]), "task": task})
        if len(out) == n:
            break
    return out


def _hellaswag(rows: list[Row], rng: random.Random, n: int) -> list[Row]:
    rng.shuffle(rows)
    out = []
    for r in rows:
        endings = [e.strip() for e in r["endings"]]
        if len(set(endings)) != len(endings) or str(r.get("label", "")) == "":
            continue
        out.append({"kind": "choice", "state": f"Activity: {r['activity_label']}\nContext: {r['ctx']}",
                    "instructions": "Which ending is the most plausible continuation of the context?",
                    "options": [[e, e] for e in endings], "label": int(r["label"]), "task": "heldout/hellaswag"})
        if len(out) == n:
            break
    return out


def _stsb(rows: list[Row], rng: random.Random, n: int) -> list[Row]:
    rng.shuffle(rows)
    return [{"kind": "score", "state": f"Sentence A: {r['sentence1']}\nSentence B: {r['sentence2']}",
             "instructions": "How similar in meaning are sentence A and sentence B?",
             "options": [[str(i), d] for i, d in enumerate(SIMILARITY)],
             "label": round(r["score"] * 5), "task": "heldout/stsb"} for r in rows[:n]]


def build(seed: int = 0, per_kind_generated: int = 150, families: list[str] | None = None,
          counts: dict[str, int] | None = None, public: dict[str, Any] | None = None) -> list[Row]:
    """The suite, drawn in a fixed order from one seeded generator. `public` lets tests pass
    stand-in rows instead of downloading."""
    c = {**COUNTS, **(counts or {})}
    rng = random.Random(seed)
    if public is None:
        import pyarrow.parquet as pq

        with open(_file("csqa2"), encoding="utf-8") as fh:
            csqa2_lines = fh.readlines()
        public = {"strategyqa": _load("strategyqa", "train"), "csqa2": csqa2_lines,
                  "commonsenseqa": _load("commonsenseqa", "validation"),
                  "arc_challenge": _load("arc_challenge", "test", "ARC-Challenge"),
                  "hellaswag": pq.read_table(_file("hellaswag")).to_pylist(),
                  "stsb": _load("stsb", "test")}
    rows = _strategyqa(public["strategyqa"], rng, c["strategyqa"])
    rows += _csqa2(public["csqa2"], rng, c["csqa2"])
    rows += _multiple_choice(public["commonsenseqa"], rng, "Which answer is most plausible?",
                             "heldout/commonsenseqa", c["commonsenseqa"])
    rows += _multiple_choice(public["arc_challenge"], rng, "Which answer is correct?", "heldout/arc_challenge",
                             c["arc_challenge"])
    rows += _hellaswag(public["hellaswag"], rng, c["hellaswag"])
    rows += _stsb(public["stsb"], rng, c["stsb"])
    for kind in ("noul", "choice", "score"):
        for fam in families or DEFAULT_FAMILIES:
            rows += generators.draw(fam, kind, per_kind_generated, rng)
    return split(rows, rng)
