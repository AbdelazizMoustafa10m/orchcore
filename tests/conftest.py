from __future__ import annotations

import json

import pytest

from orchcore.registry.agent import AgentConfig, OutputExtraction
from orchcore.stream.events import StreamFormat
from tests.fixtures.sessions import CANONICAL_SESSIONS


@pytest.fixture
def sample_agent_config() -> AgentConfig:
    """A minimal agent config for testing."""
    return AgentConfig(
        name="test-agent",
        binary="echo",
        model="test-model",
        subcommand="-p",
        flags={"plan": ["--verbose"], "fix": []},
        stream_format=StreamFormat.CLAUDE,
        output_extraction=OutputExtraction(
            strategy=OutputExtraction.Strategy.STDOUT_CAPTURE,
        ),
    )


# -- JSONL fixture data for each format --
#
# The canonical sessions live in tests/fixtures/sessions.py so the parser
# fixtures and the mock agent CLI (WP-27 integration matrix) share one source
# of wire data.


def _session_lines(format_name: str) -> list[str]:
    return [json.dumps(obj) for obj in CANONICAL_SESSIONS[format_name]]


@pytest.fixture
def claude_jsonl_lines() -> list[str]:
    """Realistic Claude format JSONL lines."""
    return _session_lines("claude")


@pytest.fixture
def codex_jsonl_lines() -> list[str]:
    """Realistic Codex format JSONL lines."""
    return _session_lines("codex")


@pytest.fixture
def copilot_jsonl_lines() -> list[str]:
    """Realistic Copilot format JSONL lines."""
    return _session_lines("copilot")


@pytest.fixture
def opencode_jsonl_lines() -> list[str]:
    """Realistic OpenCode format JSONL lines."""
    return _session_lines("opencode")


@pytest.fixture
def gemini_jsonl_lines() -> list[str]:
    """Realistic Gemini format JSONL lines."""
    return _session_lines("gemini")
