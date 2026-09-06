"""Archive every fetched payload, unmodified except for secret redaction.

The raw archive is the source of truth a run can be replayed and audited from.
Files land in ``artifacts/<run_id>/raw/<name>.json`` as::

    {"_meta": {"source_url": ..., "fetched_at": ..., "content_hash": ...},
     "data": <the exact payload>}
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from issue_to_patch.ingestion.models import stable_hash

# Patterns for things that must never be written to disk or shown to a model.
_SECRET_RE = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{20,}"
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
_REDACTED = "[REDACTED]"


class RawArtifactRecord(BaseModel):
    name: str
    path: str
    source_url: str
    content_hash: str
    fetched_at: datetime


class RawArtifactWriter:
    def __init__(self, run_dir: Path) -> None:
        self.raw_dir = Path(run_dir) / "raw"
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.records: list[RawArtifactRecord] = []

    def write(self, name: str, data: Any, *, source_url: str) -> RawArtifactRecord:
        safe = redact_secrets(_to_jsonable(data))
        body = json.dumps(safe, indent=2, sort_keys=True, ensure_ascii=False)
        content_hash = stable_hash(name, body)
        path = self.raw_dir / f"{name}.json"
        document = {
            "_meta": {
                "source_url": source_url,
                "fetched_at": datetime.now(UTC).isoformat(),
                "content_hash": content_hash,
            },
            "data": safe,
        }
        path.write_text(json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False), "utf-8")
        record = RawArtifactRecord(
            name=name,
            path=str(path),
            source_url=source_url,
            content_hash=content_hash,
            fetched_at=datetime.now(UTC),
        )
        self.records.append(record)
        return record


def redact_secrets(value: Any) -> Any:
    """Recursively replace anything that looks like a token/key with ``[REDACTED]``."""
    if isinstance(value, str):
        return _SECRET_RE.sub(_REDACTED, value)
    if isinstance(value, dict):
        return {
            k: (_REDACTED if _is_secret_key(k) else redact_secrets(v)) for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    return value


def _is_secret_key(key: object) -> bool:
    return isinstance(key, str) and key.lower() in {
        "authorization",
        "access_token",
        "token",
        "client_secret",
        "private_key",
    }


def _to_jsonable(data: Any) -> Any:
    if isinstance(data, BaseModel):
        return json.loads(data.model_dump_json())
    if isinstance(data, (list, tuple)):
        return [_to_jsonable(item) for item in data]
    if isinstance(data, dict):
        return {k: _to_jsonable(v) for k, v in data.items()}
    return data
