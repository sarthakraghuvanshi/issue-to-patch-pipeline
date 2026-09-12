"""Runtime configuration.

All configuration comes from environment variables (or a local ``.env``) and is
validated once at process start. Missing required values fail fast and loud
rather than surfacing as a confusing error deep in a run.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    CI = "ci"
    STAGING = "staging"
    PROD = "prod"


class LLMProvider(StrEnum):
    """Exactly one provider is active at a time (Principle: learn the abstraction first)."""

    FAKE = "fake"  # deterministic stub used by tests and CI
    ANTHROPIC = "anthropic"
    OPENAI = "openai"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ITP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = Environment.LOCAL

    # --- persistence -------------------------------------------------------
    database_url: str = "sqlite+pysqlite:///./artifacts/dev.db"
    redis_url: str = "redis://localhost:6379/0"

    # --- filesystem ------------------------------------------------------
    artifacts_dir: Path = Path("artifacts")

    # --- external services ---------------------------------------------
    github_token: SecretStr | None = None
    github_api_base: str = "https://api.github.com"

    # --- models --------------------------------------------------------
    llm_provider: LLMProvider = LLMProvider.FAKE
    llm_api_key: SecretStr | None = None
    llm_model: str = "claude-sonnet-5"
    embedding_model: str = "text-embedding-3-small"

    # --- sandbox ------------------------------------------------------
    sandbox_image: str = "issue-to-patch/sandbox:latest"
    sandbox_cpu_limit: float = 2.0
    sandbox_memory_limit: str = "1g"
    sandbox_wall_clock_seconds: int = 300
    sandbox_pids_limit: int = 256

    # --- observability -----------------------------------------------
    langfuse_host: str | None = None
    langfuse_public_key: SecretStr | None = None
    langfuse_secret_key: SecretStr | None = None

    # --- router thresholds (tuned by the feedback loop, never hard-coded in nodes) ---
    retrieval_confidence_floor: float = Field(default=0.35, ge=0.0, le=1.0)
    max_patch_revisions: int = Field(default=1, ge=0)

    # --- API (Sprint 6) ------------------------------------------------
    # None disables auth — fine for local dev, never for staging/prod (checked
    # at startup, not just documented, so a forgotten key fails loud).
    api_key: SecretStr | None = None
    api_rate_limit_per_minute: int = Field(default=60, ge=1)

    log_level: str = "INFO"
    log_json: bool = True

    @field_validator("artifacts_dir")
    @classmethod
    def _resolve_artifacts_dir(cls, value: Path) -> Path:
        return value.expanduser()

    @model_validator(mode="after")
    def _api_key_required_in_deployed_environments(self) -> Settings:
        # local/ci run the API in-process against a test client, never exposed
        # to a network — staging/prod are reachable, so an unauthenticated API
        # there is a real hole, not just a missing convenience.
        if self.environment in (Environment.STAGING, Environment.PROD) and self.api_key is None:
            raise ValueError(
                f"ITP_API_KEY is required when ITP_ENVIRONMENT={self.environment.value!r}"
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PROD

    def require_github_token(self) -> str:
        if self.github_token is None:
            raise RuntimeError("ITP_GITHUB_TOKEN is required for GitHub ingestion")
        return self.github_token.get_secret_value()

    def require_llm_api_key(self) -> str:
        if self.llm_provider is LLMProvider.FAKE:
            raise RuntimeError("the fake LLM provider has no API key")
        if self.llm_api_key is None:
            raise RuntimeError("ITP_LLM_API_KEY is required for a real LLM provider")
        return self.llm_api_key.get_secret_value()


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
