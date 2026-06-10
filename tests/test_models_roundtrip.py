from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from orchcore.pipeline.phase import Phase, PhaseResult, PhaseStatus, PipelineResult
from orchcore.recovery.retry import RetryPolicy
from orchcore.registry.agent import AgentConfig, AgentMode, OutputExtraction, ToolSet
from orchcore.stream.events import (
    AgentErrorCategory,
    AgentResult,
    StreamEvent,
    StreamEventType,
    StreamFormat,
)


@pytest.mark.parametrize(
    "model_cls,kwargs",
    [
        pytest.param(
            AgentConfig,
            {
                "name": "test",
                "binary": "echo",
                "model": "test-model",
                "subcommand": "-p",
                "flags": {AgentMode.PLAN: ["--verbose"]},
                "stream_format": StreamFormat.CLAUDE,
                "env_vars": {"ORCHCORE_ENV": "test"},
                "output_extraction": OutputExtraction(
                    strategy=OutputExtraction.Strategy.STDOUT_CAPTURE
                ),
            },
            id="AgentConfig",
        ),
        pytest.param(
            StreamEvent,
            {
                "event_type": StreamEventType.RESULT,
                "cost_usd": Decimal("0.25"),
                "num_turns": 2,
                "session_id": "sess-1",
                "token_usage": {"input": 10, "output": 4},
            },
            id="StreamEvent",
        ),
        pytest.param(
            AgentResult,
            {
                "agent_name": "test-agent",
                "output_path": Path("outputs/test.md"),
                "exit_code": 1,
                "duration": timedelta(seconds=30),
                "error": "429 rate limit exceeded, try again in 17 seconds",
                "error_category": AgentErrorCategory.RATE_LIMIT,
                "rate_limit_reset_seconds": 17,
                "json_parse_error_count": 2,
            },
            id="AgentResult-error-taxonomy",
        ),
        pytest.param(
            PhaseResult,
            {
                "name": "test",
                "status": PhaseStatus.DONE,
                "duration": timedelta(minutes=2),
                "output_files": [Path("outputs/test.md")],
                "agent_results": [
                    AgentResult(
                        agent_name="test-agent",
                        output_path=Path("outputs/test.md"),
                        duration=timedelta(seconds=30),
                        cost_usd=Decimal("1.25"),
                    )
                ],
                "error": "agent failed; sibling cancelled",
                "error_messages": ["agent failed", "sibling cancelled"],
                "cost_usd": Decimal("1.25"),
            },
            id="PhaseResult",
        ),
        pytest.param(
            PipelineResult,
            {
                "phases": [
                    PhaseResult(
                        name="test",
                        status=PhaseStatus.DONE,
                        duration=timedelta(minutes=2),
                        output_files=[Path("outputs/test.md")],
                        agent_results=[AgentResult(agent_name="test-agent")],
                        cost_usd=Decimal("1.25"),
                    )
                ],
                "total_duration": timedelta(minutes=5),
                "total_cost_usd": Decimal("2.50"),
                "success": True,
            },
            id="PipelineResult",
        ),
    ],
)
def test_model_round_trip(model_cls: type[BaseModel], kwargs: dict[str, object]) -> None:
    original = model_cls(**kwargs)

    dumped = original.model_dump(mode="json")
    restored = model_cls.model_validate(dumped)
    dumped_json = original.model_dump_json()
    restored_from_json = model_cls.model_validate_json(dumped_json)

    assert restored == original
    assert restored_from_json == original


@pytest.mark.parametrize(
    ("model", "field_name", "value"),
    [
        pytest.param(
            Phase(name="test", agents=["claude"]),
            "name",
            "updated",
            id="Phase",
        ),
        pytest.param(
            AgentConfig(
                name="test",
                binary="echo",
                model="test-model",
                subcommand="-p",
                flags={AgentMode.PLAN: ["--verbose"]},
                stream_format=StreamFormat.CLAUDE,
                output_extraction=OutputExtraction(
                    strategy=OutputExtraction.Strategy.STDOUT_CAPTURE
                ),
            ),
            "model",
            "updated-model",
            id="AgentConfig",
        ),
        pytest.param(
            StreamEvent(event_type=StreamEventType.TEXT, text_preview="hello"),
            "text_preview",
            "updated",
            id="StreamEvent",
        ),
    ],
)
def test_frozen_models_reject_attribute_assignment(
    model: BaseModel,
    field_name: str,
    value: object,
) -> None:
    with pytest.raises(ValidationError, match="frozen"):
        setattr(model, field_name, value)


# ---- WP-31: deep-freeze boundary — tuple fields reject mutation, dict
# fields remain shallow-mutable (documented behavior, F8 regression) ----


def _frozen_agent_config() -> AgentConfig:
    return AgentConfig(
        name="test",
        binary="echo",
        model="test-model",
        subcommand="-p",
        flags={AgentMode.PLAN: ["--verbose"]},
        stream_format=StreamFormat.CLAUDE,
        env_vars={"ORCHCORE_ENV": "test"},
        output_extraction=OutputExtraction(strategy=OutputExtraction.Strategy.STDOUT_CAPTURE),
    )


def test_sequence_fields_coerce_lists_to_tuples() -> None:
    phase = Phase(name="plan", agents=["claude"], depends_on=["earlier"])
    toolset = ToolSet(internal=["Read"], mcp=["exa"])
    policy = RetryPolicy(backoff_schedule=[1, 2, 3])
    agent = _frozen_agent_config()

    assert phase.agents == ("claude",)
    assert phase.depends_on == ("earlier",)
    assert toolset.internal == ("Read",)
    assert toolset.mcp == ("exa",)
    assert policy.backoff_schedule == (1, 2, 3)
    assert agent.flags[AgentMode.PLAN] == ("--verbose",)


def test_tuple_fields_reject_nested_mutation() -> None:
    phase = Phase(name="plan", agents=["claude"], depends_on=["earlier"])
    toolset = ToolSet(internal=["Read"], mcp=["exa"])
    policy = RetryPolicy()
    agent = _frozen_agent_config()

    for sequence in (
        phase.agents,
        phase.depends_on,
        toolset.internal,
        toolset.mcp,
        policy.backoff_schedule,
        agent.flags[AgentMode.PLAN],
    ):
        with pytest.raises(AttributeError):
            sequence.append("mutated")  # type: ignore[union-attr]


def test_dict_fields_remain_shallow_mutable_documented_boundary() -> None:
    """frozen=True stays shallow for mapping fields — pinned so the boundary
    is documented behavior, not a surprise (see ADR-006 errata)."""
    agent = _frozen_agent_config()
    phase = Phase(name="plan", agents=["claude"])

    agent.env_vars["INJECTED"] = "still-mutable"
    agent.flags[AgentMode.FIX] = ("--new",)
    phase.agent_tools["claude"] = ToolSet()

    assert agent.env_vars["INJECTED"] == "still-mutable"
    assert agent.flags[AgentMode.FIX] == ("--new",)
    assert "claude" in phase.agent_tools


def test_round_trip_preserves_tuple_fields() -> None:
    phase = Phase(name="plan", agents=["a", "b"], depends_on=["earlier"])

    restored = Phase.model_validate(phase.model_dump(mode="json"))
    restored_json = Phase.model_validate_json(phase.model_dump_json())

    assert restored == phase
    assert restored_json == phase
    assert restored.agents == ("a", "b")
