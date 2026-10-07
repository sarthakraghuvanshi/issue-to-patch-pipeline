"""get_embedder(): OpenAI-or-hashing selection, independent of llm_provider."""

from __future__ import annotations

from pydantic import SecretStr

from issue_to_patch.config.settings import LLMProvider, Settings
from issue_to_patch.llm.client import get_embedder
from issue_to_patch.persistence import HashingEmbedder, OpenAIEmbedder


def _settings(**overrides: object) -> Settings:
    # _env_file=None: these tests assert exact key-presence/absence behavior,
    # so they must never pick up a developer's real local .env file.
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type,call-arg]


def test_fake_provider_always_falls_back_to_hashing_even_with_a_real_key() -> None:
    """The critical safety property: ITP_LLM_PROVIDER=fake (what every test
    in this suite runs under) must never make a real, billed API call just
    because a key happens to be configured - this is this project's one
    established "offline, no real provider" signal."""
    settings = _settings(llm_provider=LLMProvider.FAKE, openai_api_key=SecretStr("sk-real"))
    assert isinstance(get_embedder(settings), HashingEmbedder)


def test_no_key_configured_falls_back_to_hashing() -> None:
    settings = _settings(llm_provider=LLMProvider.ANTHROPIC)
    assert isinstance(get_embedder(settings), HashingEmbedder)


def test_openai_key_selects_a_real_embedder_regardless_of_llm_provider() -> None:
    """Embeddings are OpenAI-only regardless of which LLM provider is chosen
    for reasoning - an Anthropic-reasoning run with an OpenAI key configured
    still gets real embeddings (there is no public Anthropic embeddings API)."""
    settings = _settings(llm_provider=LLMProvider.ANTHROPIC, openai_api_key=SecretStr("sk-real"))
    embedder = get_embedder(settings)
    assert isinstance(embedder, OpenAIEmbedder)
    assert embedder.model == "text-embedding-3-small"


def test_falls_back_to_the_generic_llm_api_key_when_no_openai_specific_key_is_set() -> None:
    settings = _settings(llm_provider=LLMProvider.OPENAI, llm_api_key=SecretStr("sk-generic"))
    embedder = get_embedder(settings)
    assert isinstance(embedder, OpenAIEmbedder)
    assert embedder.api_key == "sk-generic"


def test_a_configured_embedding_model_is_respected() -> None:
    settings = _settings(
        llm_provider=LLMProvider.OPENAI,
        openai_api_key=SecretStr("sk-real"),
        embedding_model="text-embedding-3-large",
    )
    embedder = get_embedder(settings)
    assert isinstance(embedder, OpenAIEmbedder)
    assert embedder.model == "text-embedding-3-large"
