"""Persistence adapters: relational (SQLAlchemy), vector (pgvector), object storage."""

from issue_to_patch.persistence.audit import AuditTrail, build_audit_trail, render_audit_trail
from issue_to_patch.persistence.models import (
    Artifact,
    Base,
    ChunkRow,
    HumanDecisionRow,
    Run,
    ToolCall,
)
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
    "AuditTrail",
    "Base",
    "ChunkRow",
    "Embedder",
    "HashingEmbedder",
    "HumanDecisionRow",
    "Run",
    "Store",
    "ToolCall",
    "brute_force_search",
    "build_audit_trail",
    "cosine",
    "render_audit_trail",
]
