"""AnthropicLLM: structured-output-as-forced-tool-call, retry-once, cost tracking.

A stub client stands in for ``anthropic.Anthropic`` (its ``messages.create`` is
the only method used) so this suite never touches the network.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import pytest
from anthropic.types import Message, TextBlock, ToolUseBlock, Usage
from pydantic import BaseModel

from issue_to_patch.llm.client import AnthropicLLM
from issue_to_patch.llm.client import Message as ITPMessage


class _Schema(BaseModel):
    answer: str


def _usage(input_tokens: int = 10, output_tokens: int = 5) -> Usage:
    return Usage(input_tokens=input_tokens, output_tokens=output_tokens)


def _text_message(text: str) -> Message:
    return Message(
        id="m1",
        content=[TextBlock(text=text, type="text")],
        model="claude-sonnet-5",
        role="assistant",
        type="message",
        usage=_usage(),
    )


def _tool_message(name: str, tool_input: dict[str, Any]) -> Message:
    return Message(
        id="m1",
        content=[ToolUseBlock(id="t1", input=tool_input, name=name, type="tool_use")],
        model="claude-sonnet-5",
        role="assistant",
        type="message",
        usage=_usage(),
    )


@dataclass
class _FakeMessages:
    replies: list[Message]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs: Any) -> Message:
        self.calls.append(kwargs)
        return self.replies.pop(0)


@dataclass
class _FakeAnthropicClient:
    replies: Iterable[Message]

    def __post_init__(self) -> None:
        self.messages = _FakeMessages(list(self.replies))


def test_complete_returns_text_and_records_usage() -> None:
    client = _FakeAnthropicClient([_text_message("hello there")])
    llm = AnthropicLLM(api_key="k", client=client)  # type: ignore[arg-type]

    result = llm.complete([ITPMessage(role="user", content="hi")])

    assert result.text == "hello there"
    assert result.input_tokens == 10
    assert result.output_tokens == 5
    assert llm.total_input_tokens == 10
    assert llm.cost_usd > 0


def test_complete_separates_system_message_from_the_rest() -> None:
    client = _FakeAnthropicClient([_text_message("ok")])
    llm = AnthropicLLM(api_key="k", client=client)  # type: ignore[arg-type]

    llm.complete(
        [ITPMessage(role="system", content="be terse"), ITPMessage(role="user", content="hi")]
    )

    call = client.messages.calls[0]
    assert call["system"] == "be terse"
    assert call["messages"] == [{"role": "user", "content": "hi"}]


def test_structured_parses_the_forced_tool_call() -> None:
    client = _FakeAnthropicClient([_tool_message("_Schema", {"answer": "42"})])
    llm = AnthropicLLM(api_key="k", client=client)  # type: ignore[arg-type]

    result = llm.structured([ITPMessage(role="user", content="q")], _Schema)

    assert result.answer == "42"
    call = client.messages.calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": "_Schema"}
    assert call["tools"][0]["name"] == "_Schema"


def test_structured_retries_once_on_a_bad_reply_then_succeeds() -> None:
    client = _FakeAnthropicClient(
        [
            _tool_message("_Schema", {"answer": 123}),  # wrong type -> ValidationError
            _tool_message("_Schema", {"answer": "fixed"}),
        ]
    )
    llm = AnthropicLLM(api_key="k", client=client)  # type: ignore[arg-type]

    result = llm.structured([ITPMessage(role="user", content="q")], _Schema)

    assert result.answer == "fixed"
    assert len(client.messages.calls) == 2
    # the retry's feedback message is appended, the original is preserved
    second_call_messages = client.messages.calls[1]["messages"]
    assert second_call_messages[0] == {"role": "user", "content": "q"}
    assert "did not match the schema" in second_call_messages[-1]["content"]


def test_structured_gives_up_after_one_retry() -> None:
    client = _FakeAnthropicClient(
        [
            _tool_message("_Schema", {"answer": 1}),
            _tool_message("_Schema", {"answer": 2}),
        ]
    )
    llm = AnthropicLLM(api_key="k", client=client)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="failed after one retry"):
        llm.structured([ITPMessage(role="user", content="q")], _Schema)
    assert len(client.messages.calls) == 2


def test_structured_treats_a_missing_tool_use_block_as_a_retryable_failure() -> None:
    client = _FakeAnthropicClient(
        [_text_message("oops, no tool call"), _tool_message("_Schema", {"answer": "ok"})]
    )
    llm = AnthropicLLM(api_key="k", client=client)  # type: ignore[arg-type]

    result = llm.structured([ITPMessage(role="user", content="q")], _Schema)
    assert result.answer == "ok"


def test_cost_accumulates_across_calls() -> None:
    client = _FakeAnthropicClient([_text_message("a"), _text_message("b")])
    llm = AnthropicLLM(api_key="k", client=client)  # type: ignore[arg-type]

    llm.complete([ITPMessage(role="user", content="1")])
    first_cost = llm.cost_usd
    llm.complete([ITPMessage(role="user", content="2")])

    assert llm.cost_usd == pytest.approx(first_cost * 2)
