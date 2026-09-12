"""OpenAILLM: structured-output-as-forced-tool-call, retry-once, cost tracking.

A stub client stands in for ``openai.OpenAI`` (its ``chat.completions.create``
is the only method used) so this suite never touches the network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from openai.types import CompletionUsage
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_message_function_tool_call import (
    ChatCompletionMessageFunctionToolCall,
    Function,
)
from pydantic import BaseModel

from issue_to_patch.llm.client import Message as ITPMessage
from issue_to_patch.llm.client import OpenAILLM


class _Schema(BaseModel):
    answer: str


def _usage(prompt_tokens: int = 10, completion_tokens: int = 5) -> CompletionUsage:
    return CompletionUsage(
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, total_tokens=0
    )


def _text_completion(text: str) -> ChatCompletion:
    message = ChatCompletionMessage(role="assistant", content=text)
    choice = Choice(finish_reason="stop", index=0, message=message)
    return ChatCompletion(
        id="c1",
        choices=[choice],
        created=0,
        model="gpt-5",
        object="chat.completion",
        usage=_usage(),
    )


def _tool_completion(name: str, arguments: dict[str, Any]) -> ChatCompletion:
    import json

    call = ChatCompletionMessageFunctionToolCall(
        id="t1", type="function", function=Function(name=name, arguments=json.dumps(arguments))
    )
    message = ChatCompletionMessage(role="assistant", content=None, tool_calls=[call])
    choice = Choice(finish_reason="tool_calls", index=0, message=message)
    return ChatCompletion(
        id="c1",
        choices=[choice],
        created=0,
        model="gpt-5",
        object="chat.completion",
        usage=_usage(),
    )


@dataclass
class _FakeCompletions:
    replies: list[ChatCompletion]
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create(self, **kwargs: Any) -> ChatCompletion:
        self.calls.append(kwargs)
        return self.replies.pop(0)


@dataclass
class _FakeChat:
    completions: _FakeCompletions


@dataclass
class _FakeOpenAIClient:
    replies: list[ChatCompletion]

    def __post_init__(self) -> None:
        self.chat = _FakeChat(_FakeCompletions(list(self.replies)))


def test_complete_returns_text_and_records_usage() -> None:
    client = _FakeOpenAIClient([_text_completion("hello there")])
    llm = OpenAILLM(api_key="k", client=client)  # type: ignore[arg-type]

    result = llm.complete([ITPMessage(role="user", content="hi")])

    assert result.text == "hello there"
    assert result.input_tokens == 10
    assert result.output_tokens == 5
    assert llm.total_input_tokens == 10
    assert llm.cost_usd > 0


def test_complete_keeps_the_system_message_inline() -> None:
    client = _FakeOpenAIClient([_text_completion("ok")])
    llm = OpenAILLM(api_key="k", client=client)  # type: ignore[arg-type]

    llm.complete(
        [ITPMessage(role="system", content="be terse"), ITPMessage(role="user", content="hi")]
    )

    call = client.chat.completions.calls[0]
    assert call["messages"] == [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "hi"},
    ]


def test_structured_parses_the_forced_tool_call() -> None:
    client = _FakeOpenAIClient([_tool_completion("_Schema", {"answer": "42"})])
    llm = OpenAILLM(api_key="k", client=client)  # type: ignore[arg-type]

    result = llm.structured([ITPMessage(role="user", content="q")], _Schema)

    assert result.answer == "42"
    call = client.chat.completions.calls[0]
    assert call["tool_choice"] == {"type": "function", "function": {"name": "_Schema"}}
    assert call["tools"][0]["function"]["name"] == "_Schema"


def test_structured_retries_once_on_a_bad_reply_then_succeeds() -> None:
    client = _FakeOpenAIClient(
        [
            _tool_completion("_Schema", {"answer": 123}),  # wrong type -> ValidationError
            _tool_completion("_Schema", {"answer": "fixed"}),
        ]
    )
    llm = OpenAILLM(api_key="k", client=client)  # type: ignore[arg-type]

    result = llm.structured([ITPMessage(role="user", content="q")], _Schema)

    assert result.answer == "fixed"
    assert len(client.chat.completions.calls) == 2
    second_call_messages = client.chat.completions.calls[1]["messages"]
    assert second_call_messages[0] == {"role": "user", "content": "q"}
    assert "did not match the schema" in second_call_messages[-1]["content"]


def test_structured_gives_up_after_one_retry() -> None:
    client = _FakeOpenAIClient(
        [
            _tool_completion("_Schema", {"answer": 1}),
            _tool_completion("_Schema", {"answer": 2}),
        ]
    )
    llm = OpenAILLM(api_key="k", client=client)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="failed after one retry"):
        llm.structured([ITPMessage(role="user", content="q")], _Schema)
    assert len(client.chat.completions.calls) == 2


def test_structured_treats_a_missing_tool_call_as_a_retryable_failure() -> None:
    client = _FakeOpenAIClient(
        [_text_completion("oops, no tool call"), _tool_completion("_Schema", {"answer": "ok"})]
    )
    llm = OpenAILLM(api_key="k", client=client)  # type: ignore[arg-type]

    result = llm.structured([ITPMessage(role="user", content="q")], _Schema)
    assert result.answer == "ok"


def test_cost_accumulates_across_calls() -> None:
    client = _FakeOpenAIClient([_text_completion("a"), _text_completion("b")])
    llm = OpenAILLM(api_key="k", client=client)  # type: ignore[arg-type]

    llm.complete([ITPMessage(role="user", content="1")])
    first_cost = llm.cost_usd
    llm.complete([ITPMessage(role="user", content="2")])

    assert llm.cost_usd == pytest.approx(first_cost * 2)
