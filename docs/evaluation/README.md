# Retrieval smoke evaluation

This is a **small synthetic smoke set**, not a benchmark. It exists to make the lexical
baseline's behavior visible and to show the evaluation path; it is not a frozen final
split and must not be used to tune retrieval blindly.

## Run it

```sh
make eval-smoke
```

The harness resolves effective permissions for each item's explicit permission set,
retrieves only authorized candidates, and reports aggregate metrics. It never prints
question or passage text in errors.

## Current report

`smoke-report.json` at the time of writing:

```json
{"abstention_accuracy":1.0,"abstention_items":1,"answerable_items":10,"items":11,"mrr_at_10":0.6642857142857143,"precision_at_k":0.13999999999999999,"recall_at_k":0.7,"unauthorized_or_forbidden_hits":0}
```

| Metric | Value |
| --- | --- |
| Items | 11 |
| Answerable items | 10 |
| Recall@5 | 0.70 |
| Precision@5 | 0.14 |
| MRR@10 | 0.664 |
| Abstention accuracy | 1.00 (1 item) |
| Unauthorized or forbidden hits | 0 |

## What the set covers

- Employee-visible questions: annual leave, flexible work, harassment reporting,
  password rules, travel expenses, data retention.
- Manager/HR questions: grievance stages and annual performance reviews.
- HR-only question: whistleblowing reporting.
- Finance/HR question: salary bands.
- One abstention: an uncovered topic (`sabbatical`), with a forbidden document ID.

The labels are section-level chunk IDs chosen from the corpus's headings, not from the
top retrieval results. Three questions miss the labeled best section in the top five
under the current lexical baseline. That is useful evidence for the next retrieval
step, not a reason to relabel the set.

## Interpretation

The zero unauthorized-hit count is the important authorization result for this
synthetic set. Retrieval quality is only moderate: lexical matching often ranks a broad
"Purpose and Scope" chunk above a specific workflow or retention schedule. The next
engineering step is a properly sized, human-reviewed set plus hybrid/embedding
retrieval, not more tuning against this smoke file.

Do not treat this as a production quality claim. The final evaluation split remains
human-gated per the repository instructions.
