from __future__ import annotations

from pydantic import ValidationError

from orchcore.registry.agent import (
    AgentConfig,
    StreamFormat,
)
from orchcore.stream.events import StreamFormat as StreamFormatFromEvents


def test_agent_config_rejects_invalid_stream_format(sample_agent_config) -> None:
    payload = sample_agent_config.model_dump()
    payload["stream_format"] = "not-a-format"

    try:
        AgentConfig.model_validate(payload)
    except ValidationError as exc:
        assert "stream_format" in str(exc)
    else:  # pragma: no cover - defensive guard
        raise AssertionError("expected validation error")


def test_stream_format_is_re_exported_from_registry() -> None:
    assert StreamFormat is StreamFormatFromEvents
