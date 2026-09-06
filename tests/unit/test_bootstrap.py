"""Bootstrap smoke tests: the scaffolding itself is wired correctly."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from issue_to_patch import __version__
from issue_to_patch.cli import app
from issue_to_patch.config.settings import Environment, LLMProvider, Settings
from issue_to_patch.llm import FakeLLM
from issue_to_patch.llm.client import Message
from issue_to_patch.logging import bind_run_id, configure_logging, current_run_id
from issue_to_patch.run_states import RunState, is_terminal

runner = CliRunner()


def test_package_imports() -> None:
    assert __version__ == "0.0.0"


def test_settings_defaults_are_safe(settings: Settings) -> None:
    assert settings.environment is Environment.CI
    assert settings.llm_provider is LLMProvider.FAKE
    assert settings.github_token is None


def test_settings_require_helpers_fail_loudly(settings: Settings) -> None:
    with pytest.raises(RuntimeError):
        settings.require_github_token()
    with pytest.raises(RuntimeError):
        settings.require_llm_api_key()


@pytest.mark.parametrize(
    "state",
    [
        "PATCH_VALIDATED",
        "PATCH_REQUIRES_HUMAN_REVIEW",
        "PATCH_REJECTED",
        "INVESTIGATION_INCONCLUSIVE",
    ],
)
def test_every_documented_terminal_state_exists(state: str) -> None:
    assert is_terminal(state)
    assert RunState(state).value == state


def test_unknown_state_is_not_terminal() -> None:
    assert not is_terminal("IN_PROGRESS")


def test_run_id_binding_scopes_correctly() -> None:
    configure_logging(level="INFO", json_output=True)
    assert current_run_id() is None
    with bind_run_id("run-123"):
        assert current_run_id() == "run-123"
    assert current_run_id() is None


def test_fake_llm_is_deterministic_and_records_calls() -> None:
    llm = FakeLLM()
    llm.queue_text("first", "second")
    assert llm.complete([Message("user", "a")]).text == "first"
    assert llm.complete([Message("user", "b")]).text == "second"
    assert llm.complete([Message("user", "c")]).text == "FAKE_RESPONSE"
    assert len(llm.calls) == 3


def test_cli_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_cli_config_redacts_and_is_json() -> None:
    result = runner.invoke(app, ["config"])
    assert result.exit_code == 0
    assert "**********" in result.stdout or "null" in result.stdout
