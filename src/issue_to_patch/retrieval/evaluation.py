"""Retrieval evaluation: recall@k, MRR, nDCG over labeled issue->file examples.

A labeled example (one JSON object per line in ``evals/labeled_issues.jsonl``)::

    {"issue_id": "...", "repository": "owner/name", "commit_sha": "...",
     "query": "add() returns the wrong result",
     "labels": ["bug"],
     "gold_files": ["src/calc.py"], "gold_symbols": ["add"]}

The report compares BM25 vs dense vs hybrid on the same examples so a mode only
"stays on" if it wins (Milestone 2 gate).
"""

from __future__ import annotations

import math
from pathlib import Path

from pydantic import BaseModel, Field

from issue_to_patch.retrieval.models import RetrievalMode, SearchFilters
from issue_to_patch.retrieval.service import RetrievalService


class LabeledIssue(BaseModel):
    issue_id: str
    repository: str
    commit_sha: str
    query: str
    labels: list[str] = Field(default_factory=list)
    gold_files: list[str] = Field(default_factory=list)
    gold_symbols: list[str] = Field(default_factory=list)


class ModeScores(BaseModel):
    mode: RetrievalMode
    n: int
    file_recall_at: dict[int, float]
    symbol_recall_at: dict[int, float]
    mrr: float
    ndcg_at_10: float


class RetrievalReport(BaseModel):
    ks: list[int]
    per_mode: list[ModeScores]

    def best_mode(self, metric_k: int = 10) -> RetrievalMode:
        return max(self.per_mode, key=lambda m: m.file_recall_at.get(metric_k, 0.0)).mode


def load_labeled_issues(path: str | Path) -> list[LabeledIssue]:
    lines = Path(path).read_text("utf-8").splitlines()
    return [LabeledIssue.model_validate_json(line) for line in lines if line.strip()]


def recall_at_k(retrieved: list[str], gold: list[str], k: int) -> float:
    if not gold:
        return 1.0
    top = set(retrieved[:k])
    return sum(1 for g in gold if g in top) / len(gold)


def reciprocal_rank(retrieved: list[str], gold: list[str]) -> float:
    gold_set = set(gold)
    for i, item in enumerate(retrieved, start=1):
        if item in gold_set:
            return 1.0 / i
    return 0.0


def ndcg_at_k(retrieved: list[str], gold: list[str], k: int) -> float:
    gold_set = set(gold)
    dcg = sum(
        1.0 / math.log2(i + 1) for i, item in enumerate(retrieved[:k], start=1) if item in gold_set
    )
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(gold_set), k) + 1))
    return dcg / ideal if ideal else 0.0


def evaluate(
    service: RetrievalService,
    issues: list[LabeledIssue],
    *,
    ks: list[int] | None = None,
    modes: list[RetrievalMode] | None = None,
    top_k: int = 10,
) -> RetrievalReport:
    ks = ks or [1, 3, 5, 10]
    modes = modes or list(RetrievalMode)
    per_mode: list[ModeScores] = []

    for mode in modes:
        file_recall: dict[int, list[float]] = {k: [] for k in ks}
        symbol_recall: dict[int, list[float]] = {k: [] for k in ks}
        rr: list[float] = []
        ndcg: list[float] = []
        for issue in issues:
            trace = service.search(
                issue.query,
                SearchFilters(repository=issue.repository, commit_sha=issue.commit_sha),
                top_k=top_k,
                mode=mode,
                labels=issue.labels,
            )
            files = trace.result_paths()
            symbols = trace.result_symbols()
            for k in ks:
                file_recall[k].append(recall_at_k(files, issue.gold_files, k))
                symbol_recall[k].append(recall_at_k(symbols, issue.gold_symbols, k))
            rr.append(reciprocal_rank(files, issue.gold_files))
            ndcg.append(ndcg_at_k(files, issue.gold_files, 10))

        per_mode.append(
            ModeScores(
                mode=mode,
                n=len(issues),
                file_recall_at={k: _avg(file_recall[k]) for k in ks},
                symbol_recall_at={k: _avg(symbol_recall[k]) for k in ks},
                mrr=_avg(rr),
                ndcg_at_10=_avg(ndcg),
            )
        )
    return RetrievalReport(ks=ks, per_mode=per_mode)


def render_report(report: RetrievalReport) -> str:
    lines = ["mode    " + "  ".join(f"R@{k}" for k in report.ks) + "   MRR   nDCG@10"]
    for m in report.per_mode:
        cells = "  ".join(f"{m.file_recall_at[k]:.3f}" for k in report.ks)
        lines.append(f"{m.mode.value:<7} {cells}   {m.mrr:.3f}  {m.ndcg_at_10:.3f}")
    lines.append(f"\nbest by file recall@10: {report.best_mode().value}")
    return "\n".join(lines)


def _avg(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0
