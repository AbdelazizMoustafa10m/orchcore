from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from orchcore.pipeline.phase import Phase, PhaseResult, PhaseStatus, PipelineResult
from orchcore.registry.agent import AgentConfig, AgentMode, OutputExtraction, ToolSet
from orchcore.stream.events import AgentResult, StreamEvent, StreamEventType, StreamFormat


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
            Phase,
            {"name": "test", "agents": ["claude"]},
            id="Phase",
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
            ToolSet,
            {"internal": ["Read"]},
            id="ToolSet",
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
