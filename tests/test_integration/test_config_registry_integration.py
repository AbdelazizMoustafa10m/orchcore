"""Integration tests: config settings flowing into the agent registry.

Finding 4.3 HIGH -- Config→Registry Integration Not Tested.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from orchcore.config.settings import OrchcoreSettings
from orchcore.registry import AgentRegistry

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_config_overrides_flow_to_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Values in [agents.*] TOML sections are accessible on settings.agents,
    which consuming projects use to apply per-agent overrides to the registry."""
    monkeypatch.chdir(tmp_path)
    toml_content = '[agents.claude]\nmodel = "claude-custom"\nstall_timeout = 400\n'
    (tmp_path / "orchcore.toml").write_text(toml_content, encoding="utf-8")

    settings = OrchcoreSettings()

    assert "claude" in settings.agents
    assert settings.agents["claude"]["model"] == "claude-custom"
    assert settings.agents["claude"]["stall_timeout"] == 400


def test_settings_agent_overrides_patch_registry_configs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sample_agent_config,
) -> None:
    """Settings agent tables can be applied directly to registry overrides."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "orchcore.toml").write_text(
        "[agents.test-agent]\n"
        'model = "patched-model"\n'
        "stall_timeout = 400\n"
        "\n"
        "[agents.test-agent.env_vars]\n"
        'B = "2"\n',
        encoding="utf-8",
    )

    settings = OrchcoreSettings()
    registry = AgentRegistry(
        {
            sample_agent_config.name: sample_agent_config.model_copy(
                update={"env_vars": {"A": "1"}},
            )
        }
    )

    patched = registry.with_overrides(settings.agents)

    assert patched.get(sample_agent_config.name).model == "patched-model"
    assert patched.get(sample_agent_config.name).stall_timeout == 400
    assert patched.get(sample_agent_config.name).env_vars == {"A": "1", "B": "2"}
    assert registry.get(sample_agent_config.name).model == "test-model"
