"""The single seam between the pipeline and any language model.

At bootstrap only :class:`FakeLLM` existed. :class:`AnthropicLLM` (Sprint 5)
is the first real provider, behind the same :class:`LLMClient` protocol, and
is selected by ``ITP_LLM_PROVIDER``. A graph node never imports a provider SDK
directly — only this module does.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol, TypeVar, cast, runtime_checkable

from anthropic import Anthropic
from anthropic.types import MessageParam, ToolChoiceToolParam, ToolParam
from pydantic import BaseModel, ValidationError

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


# $/million tokens, Sonnet-class pricing. A per-model table (not one constant)
# because ``ITP_LLM_MODEL`` picks the actual model; this is a reasonable
# default until Sprint 8 wires real per-model billing.
_COST_PER_MILLION_INPUT = 3.0
_COST_PER_MILLION_OUTPUT = 15.0


@dataclass
class AnthropicLLM:
    """The first real provider. Structured output is one *forced* tool call —
    the schema's own JSON schema becomes the tool's ``input_schema``, so a
    malformed reply is something the API itself already validated against,
    not free text we'd have to parse ourselves. One retry, with the previous
    validation error fed back as feedback, covers the rest.
    """

    api_key: str
    model: str = "claude-sonnet-5"
    max_tokens: int = 4096
    client: Anthropic | None = None  # injectable, so tests never touch the network

    _client: Anthropic = field(init=False, repr=False)
    total_input_tokens: int = field(default=0, init=False)
    total_output_tokens: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        self._client = self.client or Anthropic(api_key=self.api_key)

    @property
    def cost_usd(self) -> float:
        return (
            self.total_input_tokens / 1_000_000 * _COST_PER_MILLION_INPUT
            + self.total_output_tokens / 1_000_000 * _COST_PER_MILLION_OUTPUT
        )

    def complete(self, messages: Sequence[Message]) -> Completion:
        system, rest = _split_system(messages)
        response = self._client.messages.create(
            model=self.model, max_tokens=self.max_tokens, system=system, messages=list(rest)
        )
        self._record_usage(response.usage.input_tokens, response.usage.output_tokens)
        text = "".join(block.text for block in response.content if block.type == "text")
        return Completion(
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=self.model,
        )

    def structured(self, messages: Sequence[Message], schema: type[T]) -> T:
        system, rest = _split_system(messages)
        tool_name = schema.__name__
        tool = cast(
            ToolParam,
            {
                "name": tool_name,
                "description": f"Emit a {tool_name}.",
                "input_schema": schema.model_json_schema(),
            },
        )
        tool_choice = cast(ToolChoiceToolParam, {"type": "tool", "name": tool_name})

        last_error: Exception | None = None
        for _attempt in range(2):  # one retry on a schema-validation failure
            call_messages = list(rest)
            if last_error is not None:
                call_messages.append(
                    cast(
                        MessageParam,
                        {
                            "role": "user",
                            "content": f"Your previous reply did not match the schema: "
                            f"{last_error}. Reply again, correctly this time.",
                        },
                    )
                )
            response = self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=call_messages,
                tools=[tool],
                tool_choice=tool_choice,
            )
            self._record_usage(response.usage.input_tokens, response.usage.output_tokens)
            block = next((b for b in response.content if b.type == "tool_use"), None)
            if block is None:
                last_error = ValueError("model did not return a tool_use block")
                continue
            try:
                return schema.model_validate(block.input)
            except ValidationError as exc:
                last_error = exc
        raise RuntimeError(f"structured({schema.__name__}) failed after one retry: {last_error}")

    def _record_usage(self, input_tokens: int, output_tokens: int) -> None:
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens


def _split_system(messages: Sequence[Message]) -> tuple[str, list[MessageParam]]:
    """Anthropic takes ``system`` as its own field, not a message with that role."""
    system = "\n\n".join(m.content for m in messages if m.role == "system")
    rest = [
        cast(MessageParam, {"role": m.role, "content": m.content})
        for m in messages
        if m.role != "system"
    ]
    return system, rest


def get_llm(settings: Settings | None = None) -> LLMClient:
    settings = settings or get_settings()
    if settings.llm_provider is LLMProvider.FAKE:
        return FakeLLM()
    if settings.llm_provider is LLMProvider.ANTHROPIC:
        return AnthropicLLM(api_key=settings.require_llm_api_key(), model=settings.llm_model)
    raise NotImplementedError(
        f"provider {settings.llm_provider!r} has no client implementation yet"
    )
