"""Persistence adapters: relational (SQLAlchemy), vector (pgvector), object storage."""

from issue_to_patch.persistence.models import Artifact, Base, ChunkRow, Run, ToolCall
from issue_to_patch.persistence.store import Store
from issue_to_patch.persistence.vector import (
    DEFAULT_DIM,
    Embedder,
    HashingEmbedder,
    brute_force_search,
    cosine,
)

__all__ = [
    "DEFAULT_DIM",
    "Artifact",
    "Base",
    "ChunkRow",
    "Embedder",
    "HashingEmbedder",
    "Run",
    "Store",
    "ToolCall",
    "brute_force_search",
    "cosine",
]
