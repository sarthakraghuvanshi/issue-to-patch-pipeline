"""Walk a repository snapshot and write structure-aware chunks.

    for each tracked, supported, not-too-big file:
        parse -> analyse structure -> chunk -> enrich metadata -> persist

Re-indexing the same commit is idempotent: :meth:`Store.replace_chunks` wipes
the previous chunk set for that repo@sha first.
"""

from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath

from issue_to_patch.ingestion.git_ops import SafeGit
from issue_to_patch.ingestion.models import RepositorySnapshot
from issue_to_patch.logging import get_logger
from issue_to_patch.persistence import Store
from issue_to_patch.processing.chunker import DEFAULT_MAX_LINES, StructureAwareChunker
from issue_to_patch.processing.metadata import MetadataEnricher
from issue_to_patch.processing.models import Chunk, IndexResult, Language
from issue_to_patch.processing.parser import DocumentParser
from issue_to_patch.processing.structure import StructureAnalyzer

_log = get_logger("index")

_SKIP_DIRS = {
    ".git",
    ".hg",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "site-packages",
    "vendor",
    ".tox",
    ".eggs",
}
_MAX_FILE_BYTES = 1_000_000
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def index_snapshot(
    snapshot: RepositorySnapshot,
    store: Store,
    *,
    max_lines: int = DEFAULT_MAX_LINES,
    out_dir: Path | None = None,
) -> IndexResult:
    repo_path = snapshot.root_path
    repository = snapshot.repo or repo_path.name
    git = SafeGit(root=repo_path.parent if repo_path.parent.exists() else repo_path)
    git.go_offline()
    tracked = git.run("ls-files", cwd=repo_path).stdout.splitlines()

    parser = DocumentParser()
    analyzer = StructureAnalyzer()
    chunker = StructureAwareChunker(max_lines=max_lines)
    enricher = MetadataEnricher()

    candidates = [p for p in tracked if _should_index(p)]
    test_index = _build_test_index(repo_path, candidates)

    all_chunks: list[Chunk] = []
    indexed = skipped = 0
    for rel_path in candidates:
        file_path = repo_path / rel_path
        try:
            raw = file_path.read_bytes()
        except OSError:
            skipped += 1
            continue
        if len(raw) > _MAX_FILE_BYTES or b"\x00" in raw[:4096]:
            skipped += 1
            continue

        content = raw.decode("utf-8", "replace")
        parsed = parser.parse(rel_path, content)
        structure = analyzer.analyze(rel_path, parsed.text, parsed.language)
        chunks = chunker.chunk(
            structure, parsed.text, repository=repository, commit_sha=snapshot.commit_sha
        )
        all_chunks.extend(enricher.enrich(c, structure, test_index=test_index) for c in chunks)
        indexed += 1

    written = store.replace_chunks(
        repository,
        snapshot.commit_sha,
        [_row(c) for c in all_chunks],
    )
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "chunks.jsonl").write_text(
            "\n".join(c.model_dump_json() for c in all_chunks), "utf-8"
        )

    result = IndexResult(
        repository=repository,
        commit_sha=snapshot.commit_sha,
        files_seen=len(tracked),
        files_indexed=indexed,
        files_skipped=len(tracked) - indexed,
        chunks_written=written,
    )
    _log.info("index.done", **result.model_dump())
    return result


def _should_index(rel_path: str) -> bool:
    parts = PurePosixPath(rel_path).parts
    if any(part in _SKIP_DIRS for part in parts):
        return False
    from issue_to_patch.processing.parser import detect_language

    return detect_language(rel_path) is not Language.UNKNOWN


def _build_test_index(repo_path: Path, candidates: list[str]) -> dict[str, list[str]]:
    """Map a bare symbol name -> test files whose text mentions it."""
    from issue_to_patch.processing.parser import is_probably_test_path

    index: dict[str, list[str]] = {}
    for rel_path in candidates:
        if not is_probably_test_path(rel_path) or not rel_path.endswith(".py"):
            continue
        try:
            text = (repo_path / rel_path).read_text("utf-8", "replace")
        except OSError:
            continue
        for token in set(_IDENTIFIER.findall(text)):
            index.setdefault(token, []).append(rel_path)
    return index


def _row(chunk: Chunk) -> dict[str, object]:
    data: dict[str, object] = json.loads(chunk.model_dump_json())
    data["reference_paths"] = data.pop("references")
    return data
