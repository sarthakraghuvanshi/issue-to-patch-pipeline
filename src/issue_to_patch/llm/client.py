"""The single seam between the pipeline and any language model.

At bootstrap only :class:`FakeLLM` exists. Real providers are added in the
reasoning-engine sprint, behind the same :class:`LLMClient` protocol, and are
selected by ``ITP_LLM_PROVIDER``.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from issue_to_patch.config import Settings, get_settings
from issue_to_patch.config.settings import LLMProvider

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class Message:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass(frozen=True)
class Completion:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = "fake"


@runtime_checkable
class LLMClient(Protocol):
    """Every provider implementation satisfies this."""

    def complete(self, messages: Sequence[Message]) -> Completion: ...

    def structured(self, messages: Sequence[Message], schema: type[T]) -> T: ...


@dataclass
class FakeLLM:
    """Deterministic stub for tests and CI.

    Responses are drained from a queue in order; ``structured`` validates the
    next queued payload against the requested schema so tests fail loudly on a
    contract mismatch.
    """

    _text_replies: deque[str] = field(default_factory=deque)
    _structured_replies: deque[dict[str, object]] = field(default_factory=deque)
    calls: list[list[Message]] = field(default_factory=list)

    def queue_text(self, *replies: str) -> None:
        self._text_replies.extend(replies)

    def queue_structured(self, *payloads: dict[str, object]) -> None:
        self._structured_replies.extend(payloads)

    def complete(self, messages: Sequence[Message]) -> Completion:
        self.calls.append(list(messages))
        text = self._text_replies.popleft() if self._text_replies else "FAKE_RESPONSE"
        return Completion(text=text, output_tokens=len(text.split()))

    def structured(self, messages: Sequence[Message], schema: type[T]) -> T:
        self.calls.append(list(messages))
        if not self._structured_replies:
            raise AssertionError(
                f"FakeLLM.structured called for {schema.__name__} with no queued payload"
            )
        return schema.model_validate(self._structured_replies.popleft())


def get_llm(settings: Settings | None = None) -> LLMClient:
    settings = settings or get_settings()
    if settings.llm_provider is LLMProvider.FAKE:
        return FakeLLM()
    raise NotImplementedError(
        f"provider {settings.llm_provider!r} is added in the reasoning-engine sprint"
    )
