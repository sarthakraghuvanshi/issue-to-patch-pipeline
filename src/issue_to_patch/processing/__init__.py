"""Data Processing: parsing, structure analysis, structure-aware chunking, metadata."""

from issue_to_patch.processing.chunker import BoundaryDetector, StructureAwareChunker
from issue_to_patch.processing.index import index_snapshot
from issue_to_patch.processing.metadata import (
    KeywordExtractor,
    MetadataEnricher,
    QuestionGenerator,
    SummaryGenerator,
    split_identifier,
)
from issue_to_patch.processing.models import (
    Chunk,
    ChunkKind,
    FileStructure,
    IndexResult,
    Language,
    SourceSymbol,
    SymbolKind,
)
from issue_to_patch.processing.parser import DocumentParser, detect_language
from issue_to_patch.processing.structure import StructureAnalyzer

__all__ = [
    "BoundaryDetector",
    "Chunk",
    "ChunkKind",
    "DocumentParser",
    "FileStructure",
    "IndexResult",
    "KeywordExtractor",
    "Language",
    "MetadataEnricher",
    "QuestionGenerator",
    "SourceSymbol",
    "StructureAnalyzer",
    "StructureAwareChunker",
    "SummaryGenerator",
    "SymbolKind",
    "detect_language",
    "index_snapshot",
    "split_identifier",
]
