"""LLM boundary.

Nodes and agents call this package, never a provider SDK directly. That keeps
provider choice a one-line config change and lets tests swap in a deterministic
fake (Principle 1: deterministic core first, LLM later).
"""

from issue_to_patch.llm.client import FakeLLM, LLMClient, get_llm

__all__ = ["FakeLLM", "LLMClient", "get_llm"]
