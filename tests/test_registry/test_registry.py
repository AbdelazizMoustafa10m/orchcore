from __future__ import annotations

import pytest
from pydantic import ValidationError

from orchcore.registry import AgentRegistry
from orchcore.registry.agent import AgentMode


def test_empty_registry_raises_key_error() -> None:
    registry = AgentRegistry()

    with pytest.raises(KeyError, match="Agent 'missing' not found in registry"):
        registry.get("missing")


def test_register_and_get_round_trip(sample_agent_config) -> None:
    registry = AgentRegistry()
    registry.register(sample_agent_config)

    assert registry.get(sample_agent_config.name) == sample_agent_config


def test_list_agents_returns_registered_names(sample_agent_config) -> None:
    registry = AgentRegistry({"first": sample_agent_config})

    assert registry.list_agents() == ["first"]


def test_available_filters_by_binary_on_path(monkeypatch, sample_agent_config) -> None:
    registry = AgentRegistry(
        {
            sample_agent_config.name: sample_agent_config,
            "missing": sample_agent_config.model_copy(
                update={"name": "missing", "binary": "missing-binary"},
            ),
        }
    )

    monkeypatch.setattr(
        "orchcore.registry.registry.shutil.which",
        lambda binary: "/bin/echo" if binary == "echo" else None,
    )

    assert registry.available() == [sample_agent_config.name]


def test_validate_returns_missing_agent_names(monkeypatch, sample_agent_config) -> None:
    registry = AgentRegistry(
        {
            sample_agent_config.name: sample_agent_config,
            "missing": sample_agent_config.model_copy(
                update={"name": "missing", "binary": "missing-binary"},
            ),
        }
    )

    monkeypatch.setattr(
        "orchcore.registry.registry.shutil.which",
        lambda binary: "/bin/echo" if binary == "echo" else None,
    )

    assert set(registry.validate(["test-agent", "missing", "unknown"])) == {
        "missing",
        "unknown",
    }


def test_with_overrides_returns_new_registry_with_patched_configs(sample_agent_config) -> None:
    registry = AgentRegistry(
        {
            sample_agent_config.name: sample_agent_config.model_copy(
                update={"env_vars": {"A": "1"}},
            )
        }
    )

    patched = registry.with_overrides(
        {
            sample_agent_config.name: {
                "model": "patched-model",
                "env_vars": {"B": "2"},
            }
        }
    )

    assert patched is not registry
    assert patched.get(sample_agent_config.name).model == "patched-model"
    assert patched.get(sample_agent_config.name).env_vars == {"A": "1", "B": "2"}
    assert registry.get(sample_agent_config.name).model == "test-model"


def test_with_overrides_rejects_invalid_field_types(sample_agent_config) -> None:
    registry = AgentRegistry({sample_agent_config.name: sample_agent_config})

    with pytest.raises(ValidationError):
        registry.with_overrides({sample_agent_config.name: {"stall_timeout": "not-a-number"}})


def test_load_from_toml_reads_nested_models(tmp_path) -> None:
    path = tmp_path / "agents.toml"
    path.write_text(
        """
[agents.demo]
binary = "echo"
model = "demo-model"
subcommand = "-p"
stream_format = "claude"

[agents.demo.flags]
plan = ["--verbose"]
fix = []

[agents.demo.output_extraction]
strategy = "stdout_capture"
""".strip(),
        encoding="utf-8",
    )

    registry = AgentRegistry()
    registry.load_from_toml(path)

    agent = registry.get("demo")
    assert agent.binary == "echo"
    assert agent.model == "demo-model"
    assert agent.flags[AgentMode.PLAN] == ["--verbose"]
    assert agent.output_extraction.strategy.value == "stdout_capture"
