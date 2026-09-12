"""The graph's tool layer: every capability a node is allowed to use.

A node's only handle onto the outside world is a :class:`GraphDependencies`
instance. There is no raw subprocess, no direct git command, no unmediated
GitHub or filesystem access reachable from a node — each field is already a
narrow, typed, previously-validated capability (``RetrievalService.search``,
``patching.generate_patch``/``validate_patch``, ``Store``). The LLM never sees
any of this directly either: nodes call these Python objects themselves and
only ever ask the LLM to fill in a structured field (a hypothesis, an edit
plan), never to choose or invoke a tool. That keeps the "repo content is
untrusted" principle intact — nothing the repository contains ever becomes an
instruction.
"""

from __future__ import annotations

from dataclasses import dataclass

from issue_to_patch.config import Settings, get_settings
from issue_to_patch.llm.client import LLMClient, get_llm
from issue_to_patch.persistence import Store
from issue_to_patch.retrieval import RetrievalService


@dataclass
class GraphDependencies:
    llm: LLMClient
    retrieval: RetrievalService
    store: Store
    settings: Settings


def build_dependencies(
    *,
    store: Store,
    llm: LLMClient | None = None,
    retrieval: RetrievalService | None = None,
    settings: Settings | None = None,
) -> GraphDependencies:
    settings = settings or get_settings()
    return GraphDependencies(
        llm=llm or get_llm(settings),
        retrieval=retrieval or RetrievalService(store),
        store=store,
        settings=settings,
    )
