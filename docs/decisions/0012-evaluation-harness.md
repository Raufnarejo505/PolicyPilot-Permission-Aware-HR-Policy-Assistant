# 0012 — Offline retrieval-evaluation harness

Date: 2026-10-25
Status: implemented; synthetic smoke set added; real labels and frozen splits remain human-gated

## Scope

`services/evaluation.py` computes aggregate authorized retrieval metrics over an
explicit JSONL item file. It does not discover, download, infer, or write evaluation
data. No frozen final split, split manifest, or final labels are read or generated.

Each item supplies a question, a named permission set, optional relevant chunk IDs,
optional forbidden document IDs, and an optional abstention expectation. The harness
filters by the item's effective permissions, independently checks every returned chunk
with `can_read`, counts unauthorized or forbidden hits, and reports Recall@k,
Precision@k, MRR@10, and abstention accuracy. Question and passage text never appear in
the aggregate report or errors.

## Commands

```sh
uv run --frozen python -m policypilot.manage evaluate-retrieval --items items.jsonl --k 5
```

The item file remains the caller's responsibility. It must not contain final-split
material while tuning. The metrics are a development aid, not a substitute for
human-labelled evidence spans or a frozen final evaluation run.

## Committed smoke set

A small synthetic set is committed at `docs/evaluation/smoke-items.jsonl` with its
aggregate report and interpretation. It exists to make the lexical baseline visible:
Recall@5 is 0.70 on 10 answerable items, abstention accuracy is 1.00 on one item, and
unauthorized/forbidden hits are zero. It is deliberately **not** a benchmark, and its
labels are not a frozen final split.

## Remaining gates

Evaluation precedence, item authoring, annotator agreement, and final-split isolation
still require explicit human decisions. Response caching remains disabled, and no
model judge or paid API is used.
