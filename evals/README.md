# Retrieval evaluation set

`labeled_issues.jsonl` — one JSON object per line:

```json
{"issue_id": "...", "repository": "owner/name", "commit_sha": "<indexed sha>",
 "query": "free-text bug description",
 "labels": ["bug"],
 "gold_files": ["src/module.py"],
 "gold_symbols": ["function_or_class_name"]}
```

Workflow (automated):

1. `issue-to-patch ingest --issue-url <url>` then `issue-to-patch index --snapshot artifacts/<run_id>/snapshot`
2. `issue-to-patch build-eval-set --repo <owner/name> --commit-sha <indexed sha> --count 20`
   — mines merged bug-fix PRs (issue text -> the files that actually fixed it) and writes
   this file. See `retrieval/dataset.py` for how a PR qualifies.
3. `issue-to-patch eval-retrieval --labeled evals/labeled_issues.jsonl`

Rows can also be added by hand: read a closed issue, follow its linking PR, copy the
changed file(s)/symbol(s) into the schema above.

Milestone 2 gate: ≥ 20 labeled issues over a 500+ file repo; hybrid must beat BM25 on
file recall@10 before it stays the default.

**Result (2026-09-12), `langchain-ai/langchain` @ `eef3eaeac8ff2646c43b37ac2a755a5e7978e8b4`,
3,125 files / 15,370 chunks, 20 labeled issues:**

| mode   | R@1   | R@3   | R@5   | R@10  | MRR   | nDCG@10 |
|--------|-------|-------|-------|-------|-------|---------|
| bm25   | 0.142 | 0.458 | 0.546 | 0.633 | 0.512 | 0.480   |
| dense  | 0.225 | 0.392 | 0.417 | 0.442 | 0.460 | 0.411   |
| hybrid | 0.292 | 0.467 | 0.533 | 0.637 | 0.653 | 0.557   |

Hybrid wins file recall@10 (barely) but wins clearly on MRR and nDCG@10 — it puts the
right file *higher up* even when plain keyword search eventually finds it too. Gate
passed: hybrid stays the default mode. Dense (the hashing-embedding placeholder, not a
real embedding model) trails both — expected; it becomes interesting once Sprint 5 swaps
in a real embedder behind the same `Embedder` protocol.

`benchmark_repositories/` holds checked-out or scripted benchmark repos (git-ignored contents).
