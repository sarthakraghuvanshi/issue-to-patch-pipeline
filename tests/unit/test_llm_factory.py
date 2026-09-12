"""get_llm(): provider selection and the default-model-swap-on-provider-switch."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from issue_to_patch.config.settings import LLMProvider, Settings
from issue_to_patch.llm.client import AnthropicLLM, FakeLLM, OpenAILLM, get_llm


def _settings(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


def test_fake_provider_needs_no_key() -> None:
    assert isinstance(get_llm(_settings(llm_provider=LLMProvider.FAKE)), FakeLLM)


def test_anthropic_without_a_key_fails_fast() -> None:
    with pytest.raises(RuntimeError, match="ITP_LLM_API_KEY"):
        get_llm(_settings(llm_provider=LLMProvider.ANTHROPIC))


def test_openai_without_a_key_fails_fast() -> None:
    with pytest.raises(RuntimeError, match="ITP_LLM_API_KEY"):
        get_llm(_settings(llm_provider=LLMProvider.OPENAI))


def test_anthropic_with_a_key_and_default_model() -> None:
    llm = get_llm(_settings(llm_provider=LLMProvider.ANTHROPIC, llm_api_key=SecretStr("k")))
    assert isinstance(llm, AnthropicLLM)
    assert llm.model == "claude-sonnet-5"


def test_openai_with_a_key_and_default_model() -> None:
    llm = get_llm(_settings(llm_provider=LLMProvider.OPENAI, llm_api_key=SecretStr("k")))
    assert isinstance(llm, OpenAILLM)
    assert llm.model == "gpt-5"


def test_switching_provider_swaps_a_left_over_default_model() -> None:
    """The common gotcha: flip ITP_LLM_PROVIDER, forget ITP_LLM_MODEL still says
    'claude-sonnet-5' -> OpenAILLM should get its own default, not a broken one."""
    settings = _settings(
        llm_provider=LLMProvider.OPENAI,
        llm_api_key=SecretStr("k"),
        llm_model="claude-sonnet-5",  # the Anthropic default, left over
    )
    llm = get_llm(settings)
    assert isinstance(llm, OpenAILLM)
    assert llm.model == "gpt-5"


def test_an_explicitly_chosen_model_is_never_overridden() -> None:
    settings = _settings(
        llm_provider=LLMProvider.OPENAI, llm_api_key=SecretStr("k"), llm_model="gpt-4o-mini"
    )
    llm = get_llm(settings)
    assert isinstance(llm, OpenAILLM)
    assert llm.model == "gpt-4o-mini"
