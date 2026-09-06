"""RawArtifactWriter: redaction and the on-disk envelope format."""

from __future__ import annotations

import json
from pathlib import Path

from issue_to_patch.ingestion import RawArtifactWriter, redact_secrets


def test_redacts_token_values_and_secret_keys() -> None:
    payload = {
        "body": "here is my token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 do not share",
        "Authorization": "Bearer ghp_secretsecretsecretsecretsecret123456",
        "nested": {"access_token": "xyz", "safe": "keep me"},
        "list": ["AKIAIOSFODNN7EXAMPLE", "ordinary"],
    }
    clean = redact_secrets(payload)
    assert "ghp_" not in json.dumps(clean)
    assert clean["Authorization"] == "[REDACTED]"
    assert clean["nested"]["access_token"] == "[REDACTED]"
    assert clean["nested"]["safe"] == "keep me"
    assert clean["list"][0] == "[REDACTED]"
    assert clean["list"][1] == "ordinary"


def test_write_produces_meta_envelope_and_record(tmp_path: Path) -> None:
    writer = RawArtifactWriter(tmp_path / "run1")
    record = writer.write(
        "issue",
        {"number": 1, "title": "bug"},
        source_url="https://api.github.com/repos/o/r/issues/1",
    )
    on_disk = json.loads(Path(record.path).read_text())
    assert on_disk["data"] == {"number": 1, "title": "bug"}
    assert on_disk["_meta"]["source_url"].endswith("/issues/1")
    assert on_disk["_meta"]["content_hash"] == record.content_hash
    assert writer.records == [record]


def test_same_payload_same_content_hash(tmp_path: Path) -> None:
    w1 = RawArtifactWriter(tmp_path / "a")
    w2 = RawArtifactWriter(tmp_path / "b")
    r1 = w1.write("x", {"k": [1, 2, 3]}, source_url="u")
    r2 = w2.write("x", {"k": [1, 2, 3]}, source_url="u")
    assert r1.content_hash == r2.content_hash
