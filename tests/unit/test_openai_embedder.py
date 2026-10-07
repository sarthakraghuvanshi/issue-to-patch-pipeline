"""OpenAIEmbedder: batching and dimension - a stub client stands in for
``openai.OpenAI`` (its ``embeddings.create`` is the only method used) so
this suite never touches the network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from openai.types import CreateEmbeddingResponse, Embedding
from openai.types.create_embedding_response import Usage

from issue_to_patch.persistence.vector import OpenAIEmbedder


def _response(vectors: list[list[float]]) -> CreateEmbeddingResponse:
    return CreateEmbeddingResponse(
        data=[Embedding(embedding=v, index=i, object="embedding") for i, v in enumerate(vectors)],
        model="text-embedding-3-small",
        object="list",
        usage=Usage(prompt_tokens=len(vectors), total_tokens=len(vectors)),
    )


@dataclass
class _FakeEmbeddings:
    reply_batches: list[CreateEmbeddingResponse]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs: Any) -> CreateEmbeddingResponse:
        self.calls.append(kwargs)
        return self.reply_batches.pop(0)


@dataclass
class _FakeOpenAIClient:
    embeddings: _FakeEmbeddings


def _client(*reply_batches: CreateEmbeddingResponse) -> _FakeOpenAIClient:
    return _FakeOpenAIClient(embeddings=_FakeEmbeddings(reply_batches=list(reply_batches)))


def test_embed_returns_a_single_vector() -> None:
    client = _client(_response([[0.1, 0.2, 0.3]]))
    embedder = OpenAIEmbedder(api_key="k", client=client)  # type: ignore[arg-type]
    assert embedder.embed("hello") == [0.1, 0.2, 0.3]
    assert client.embeddings.calls[0]["input"] == ["hello"]


def test_embed_batch_sends_one_request_for_many_texts() -> None:
    client = _client(_response([[1.0], [2.0], [3.0]]))
    embedder = OpenAIEmbedder(api_key="k", client=client)  # type: ignore[arg-type]
    result = embedder.embed_batch(["a", "b", "c"])
    assert result == [[1.0], [2.0], [3.0]]
    assert len(client.embeddings.calls) == 1
    assert client.embeddings.calls[0]["input"] == ["a", "b", "c"]


def test_embed_batch_chunks_large_inputs_into_multiple_requests() -> None:
    texts = [f"t{i}" for i in range(250)]
    client = _client(_response([[1.0]] * 100), _response([[1.0]] * 100), _response([[1.0]] * 50))
    embedder = OpenAIEmbedder(api_key="k", client=client)  # type: ignore[arg-type]
    result = embedder.embed_batch(texts)
    assert len(result) == 250
    assert len(client.embeddings.calls) == 3
    assert [len(c["input"]) for c in client.embeddings.calls] == [100, 100, 50]


def test_dim_matches_the_configured_model_default() -> None:
    embedder = OpenAIEmbedder(api_key="k", client=_client())  # type: ignore[arg-type]
    assert embedder.dim == 1536


def test_model_is_configurable() -> None:
    embedder = OpenAIEmbedder(api_key="k", model="text-embedding-3-large", client=_client())  # type: ignore[arg-type]
    assert embedder.model == "text-embedding-3-large"
