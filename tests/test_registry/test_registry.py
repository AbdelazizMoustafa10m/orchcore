from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from orchcore.config import AgentOverrideConfig
from orchcore.registry import AgentRegistry
from orchcore.registry.agent import AgentConfig, AgentMode

if TYPE_CHECKING:
    from pathlib import Path


def test_empty_registry_raises_key_error() -> None:
    registry = AgentRegistry()

    with pytest.raises(KeyError, match="Agent 'missing' not found in registry"):
        registry.get("missing")


def test_register_and_get_round_trip(sample_agent_config: AgentConfig) -> None:
    registry = AgentRegistry()
    registry.register(sample_agent_config)

    assert registry.get(sample_agent_config.name) == sample_agent_config


def test_list_agents_returns_registered_names(sample_agent_config: AgentConfig) -> None:
    registry = AgentRegistry({"first": sample_agent_config})

    assert registry.list_agents() == ["first"]


def test_available_filters_by_binary_on_path(
    monkeypatch: pytest.MonkeyPatch,
    sample_agent_config: AgentConfig,
) -> None:
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


def test_validate_returns_missing_agent_names(
    monkeypatch: pytest.MonkeyPatch,
    sample_agent_config: AgentConfig,
) -> None:
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


def test_with_overrides_returns_new_registry_with_patched_configs(
    sample_agent_config: AgentConfig,
) -> None:
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


def test_with_overrides_rejects_invalid_field_types(sample_agent_config: AgentConfig) -> None:
    registry = AgentRegistry({sample_agent_config.name: sample_agent_config})

    with pytest.raises(ValidationError):
        registry.with_overrides({sample_agent_config.name: {"stall_timeout": "not-a-number"}})


def test_with_overrides_typed_equals_dict_overrides(sample_agent_config: AgentConfig) -> None:
    """WP-21: AgentOverrideConfig produces the same patched config as the
    equivalent plain dict, field for field."""
    registry = AgentRegistry({sample_agent_config.name: sample_agent_config})
    typed = registry.with_overrides(
        {
            sample_agent_config.name: AgentOverrideConfig(
                model="patched-model",
                max_runtime=120.0,
                kill_on_stall=True,
                env_policy="inherit",
            )
        }
    )
    via_dict = registry.with_overrides(
        {
            sample_agent_config.name: {
                "model": "patched-model",
                "max_runtime": 120.0,
                "kill_on_stall": True,
                "env_policy": "inherit",
            }
        }
    )

    assert typed.get(sample_agent_config.name) == via_dict.get(sample_agent_config.name)


def test_with_overrides_maps_typed_env_to_env_vars(sample_agent_config: AgentConfig) -> None:
    registry = AgentRegistry(
        {
            sample_agent_config.name: sample_agent_config.model_copy(
                update={"env_vars": {"A": "1"}},
            )
        }
    )

    patched = registry.with_overrides(
        {sample_agent_config.name: AgentOverrideConfig(env={"B": "2"})}
    )

    assert patched.get(sample_agent_config.name).env_vars == {"A": "1", "B": "2"}


def test_load_from_toml_reads_nested_models(tmp_path: Path) -> None:
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
    assert agent.flags[AgentMode.PLAN] == ("--verbose",)
    assert agent.output_extraction.strategy.value == "stdout_capture"


_VALID_AGENT_TOML = """
[agents.good]
binary = "echo"
model = "good-model"
subcommand = "-p"
stream_format = "claude"

[agents.good.flags]
plan = ["--verbose"]

[agents.good.output_extraction]
strategy = "stdout_capture"
"""

_INVALID_FORMAT_TOML = """
[agents.broken]
binary = "echo"
model = "broken-model"
subcommand = "-p"
stream_format = "nonsense"

[agents.broken.output_extraction]
strategy = "stdout_capture"
"""

_MALFORMED_OUTPUT_EXTRACTION_TOML = """
[agents.bad_nested]
binary = "echo"
model = "bad-nested-model"
subcommand = "-p"
stream_format = "claude"
flags = { plan = [] }
output_extraction = "stdout_capture"
"""

_MISSING_FIELDS_TOML = """
[agents.incomplete]
binary = "echo"
"""


def test_load_from_toml_is_atomic_on_error(tmp_path: Path) -> None:
    """F9 regression: a file mixing valid and invalid entries raises without
    registering anything — the registry is never half-mutated."""
    path = tmp_path / "agents.toml"
    path.write_text(_VALID_AGENT_TOML + _INVALID_FORMAT_TOML, encoding="utf-8")
    registry = AgentRegistry()

    with pytest.raises(ValueError, match="broken"):
        registry.load_from_toml(path)

    assert registry.list_agents() == []


def test_load_from_toml_raise_reports_all_invalid_entries(tmp_path: Path) -> None:
    path = tmp_path / "agents.toml"
    path.write_text(
        "[agents]\nnot_a_table = 5\n"
        + _VALID_AGENT_TOML
        + _INVALID_FORMAT_TOML
        + _MISSING_FIELDS_TOML,
        encoding="utf-8",
    )
    registry = AgentRegistry()

    with pytest.raises(ValueError) as exc_info:
        registry.load_from_toml(path)

    message = str(exc_info.value)
    assert "broken" in message
    assert "incomplete" in message
    assert "not_a_table" in message
    assert "expected a TOML table" in message
    assert registry.list_agents() == []


def test_load_from_toml_aggregates_malformed_nested_shapes_atomically(
    tmp_path: Path,
    sample_agent_config: AgentConfig,
) -> None:
    path = tmp_path / "agents.toml"
    path.write_text(
        _VALID_AGENT_TOML + _MALFORMED_OUTPUT_EXTRACTION_TOML + _INVALID_FORMAT_TOML,
        encoding="utf-8",
    )
    registry = AgentRegistry({"existing": sample_agent_config})

    with pytest.raises(ValueError) as exc_info:
        registry.load_from_toml(path)

    message = str(exc_info.value)
    assert "bad_nested" in message
    assert "output_extraction must be a TOML table" in message
    assert "broken" in message
    assert "nonsense" in message
    assert registry.list_agents() == ["existing"]


def test_load_from_toml_skip_mode_registers_valid_and_warns(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "agents.toml"
    path.write_text(
        "[agents]\nnot_a_table = 5\n" + _VALID_AGENT_TOML + _INVALID_FORMAT_TOML,
        encoding="utf-8",
    )
    registry = AgentRegistry()

    with caplog.at_level("WARNING", logger="orchcore.registry.registry"):
        registry.load_from_toml(path, on_error="skip")

    assert registry.list_agents() == ["good"]
    assert "Skipping invalid agent entry 'broken'" in caplog.text
    assert "Skipping invalid agent entry 'not_a_table'" in caplog.text


def test_load_from_toml_skip_mode_handles_malformed_nested_shape(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "agents.toml"
    path.write_text(_VALID_AGENT_TOML + _MALFORMED_OUTPUT_EXTRACTION_TOML, encoding="utf-8")
    registry = AgentRegistry()

    with caplog.at_level("WARNING", logger="orchcore.registry.registry"):
        registry.load_from_toml(path, on_error="skip")

    assert registry.list_agents() == ["good"]
    assert "Skipping invalid agent entry 'bad_nested'" in caplog.text
    assert "output_extraction must be a TOML table" in caplog.text


def test_load_from_toml_valid_file_loads_identically_in_both_modes(tmp_path: Path) -> None:
    path = tmp_path / "agents.toml"
    path.write_text(_VALID_AGENT_TOML, encoding="utf-8")

    raise_registry = AgentRegistry()
    raise_registry.load_from_toml(path)
    skip_registry = AgentRegistry()
    skip_registry.load_from_toml(path, on_error="skip")

    assert raise_registry.get("good") == skip_registry.get("good")


def test_load_from_toml_performs_no_env_interpolation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F7 (decision): ``${VAR}`` is a literal value, never expanded."""
    monkeypatch.setenv("ORCHCORE_TEST_SECRET", "expanded")
    path = tmp_path / "agents.toml"
    path.write_text(
        _VALID_AGENT_TOML + '\n[agents.good.env_vars]\nAPI_KEY = "${ORCHCORE_TEST_SECRET}"\n',
        encoding="utf-8",
    )
    registry = AgentRegistry()

    registry.load_from_toml(path)

    assert registry.get("good").env_vars["API_KEY"] == "${ORCHCORE_TEST_SECRET}"
