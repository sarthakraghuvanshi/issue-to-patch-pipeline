"""Issue-to-Patch Automation Pipeline.

A RAG-based pipeline that ingests a GitHub issue, retrieves relevant repository
context, proposes a source change, and emits a validated ``.patch`` artifact.

See ``RAG_LEARNING_PLAN.md`` (what / why) and ``IMPLEMENTATION_PLAN.md`` (how).
"""

__version__ = "0.0.0"
