from __future__ import annotations

from pydantic import ValidationError

from orchcore.registry.agent import (
    AgentConfig,
    AgentMode,
    OutputExtraction,
    StreamFormat,
    ToolSet,
)
from orchcore.stream.events import StreamFormat as StreamFormatFromEvents


def test_agent_mode_has_all_expected_values() -> None:
    assert [mode.value for mode in AgentMode] == ["plan", "fix", "audit", "review"]


def test_agent_config_validates_and_round_trips(sample_agent_config) -> None:
    validated = AgentConfig.model_validate(sample_agent_config.model_dump())

    assert validated == sample_agent_config
    assert validated.flags[AgentMode.PLAN] == ["--verbose"]


def test_agent_config_rejects_invalid_stream_format(sample_agent_config) -> None:
    payload = sample_agent_config.model_dump()
    payload["stream_format"] = "not-a-format"

    try:
        AgentConfig.model_validate(payload)
    except ValidationError as exc:
        assert "stream_format" in str(exc)
    else:  # pragma: no cover - defensive guard
        raise AssertionError("expected validation error")


def test_output_extraction_strategies_cover_all_variants() -> None:
    assert [strategy.value for strategy in OutputExtraction.Strategy] == [
        "jq_filter",
        "direct_file",
        "stdout_capture",
    ]
    assert (
        OutputExtraction(
            strategy=OutputExtraction.Strategy.STDOUT_CAPTURE,
        ).model_dump(mode="json")["strategy"]
        == "stdout_capture"
    )


def test_toolset_defaults_and_custom_values() -> None:
    default_toolset = ToolSet()
    custom_toolset = ToolSet(
        internal=["Read", "Write"],
        mcp=["exa"],
        permission="full-access",
        max_turns=7,
    )

    assert default_toolset.internal == []
    assert default_toolset.mcp == []
    assert default_toolset.permission == "read-only"
    assert default_toolset.max_turns == 25
    assert custom_toolset.internal == ["Read", "Write"]
    assert custom_toolset.mcp == ["exa"]
    assert custom_toolset.permission == "full-access"
    assert custom_toolset.max_turns == 7


def test_stream_format_is_re_exported_from_registry() -> None:
    assert StreamFormat is StreamFormatFromEvents
