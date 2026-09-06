# Retrieval evaluation set

`labeled_issues.jsonl` — one JSON object per line:

```json
{"issue_id": "...", "repository": "owner/name", "commit_sha": "<indexed sha>",
 "query": "free-text bug description",
 "labels": ["bug"],
 "gold_files": ["src/module.py"],
 "gold_symbols": ["function_or_class_name"]}
```

Workflow:

1. `issue-to-patch ingest --issue-url <url>` then `issue-to-patch index --snapshot artifacts/<run_id>/snapshot`
2. Add rows here with the real `commit_sha` and the file(s)/symbol(s) that actually fixed the issue
   (read the linked PR).
3. `issue-to-patch eval-retrieval --labeled evals/labeled_issues.jsonl`

Milestone 2 gate: ≥ 20 labeled issues over a 500+ file repo; hybrid must beat BM25 on
file recall@10 before it stays the default.

`benchmark_repositories/` holds checked-out or scripted benchmark repos (git-ignored contents).
