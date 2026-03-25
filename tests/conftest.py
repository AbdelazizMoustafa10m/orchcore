from __future__ import annotations

import json

import pytest

from orchcore.registry.agent import AgentConfig, AgentMode, OutputExtraction
from orchcore.stream.events import StreamEvent, StreamEventType, StreamFormat


@pytest.fixture
def workspace_tmp(tmp_path):
    """Temporary workspace directory."""
    ws = tmp_path / ".orchcore-workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def sample_stream_events():
    """Sample stream events for testing."""
    return [
        StreamEvent(event_type=StreamEventType.INIT, session_id="test-session"),
        StreamEvent(
            event_type=StreamEventType.TOOL_START,
            tool_name="Read",
            tool_id="tool-1",
            tool_status="running",
        ),
        StreamEvent(
            event_type=StreamEventType.TOOL_DONE,
            tool_name="Read",
            tool_id="tool-1",
            tool_status="done",
            tool_detail="src/main.py",
        ),
        StreamEvent(event_type=StreamEventType.TEXT, text_preview="Hello world"),
        StreamEvent(
            event_type=StreamEventType.RESULT,
            cost_usd=None,
            num_turns=5,
        ),
    ]


@pytest.fixture
def sample_agent_config() -> AgentConfig:
    """A minimal agent config for testing."""
    return AgentConfig(
        name="test-agent",
        binary="echo",
        model="test-model",
        subcommand="-p",
        flags={AgentMode.PLAN: ["--verbose"], AgentMode.FIX: []},
        stream_format=StreamFormat.CLAUDE,
        output_extraction=OutputExtraction(
            strategy=OutputExtraction.Strategy.STDOUT_CAPTURE,
        ),
    )


# -- JSONL fixture data for each format --


@pytest.fixture
def claude_jsonl_lines() -> list[str]:
    """Realistic Claude format JSONL lines."""
    return [
        json.dumps({"type": "system", "subtype": "init", "session_id": "sess-123"}),
        json.dumps(
            {
                "type": "content_block_start",
                "content_block": {"type": "tool_use", "name": "Read", "id": "tb-1"},
            }
        ),
        json.dumps({"type": "content_block_delta", "delta": {"type": "input_json_delta"}}),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Read",
                            "id": "tb-1",
                            "input": {"file_path": "foo.py"},
                        }
                    ]
                },
            }
        ),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "# Plan\n\nAdd the requested tests.",
                        }
                    ]
                },
            }
        ),
        json.dumps(
            {
                "type": "result",
                "total_cost_usd": "0.0123",
                "num_turns": 3,
                "session_id": "sess-123",
            }
        ),
    ]


@pytest.fixture
def codex_jsonl_lines() -> list[str]:
    """Realistic Codex format JSONL lines."""
    return [
        json.dumps({"type": "thread.started"}),
        json.dumps({"type": "item.started", "item": {"id": "item-1", "type": "function_call"}}),
        json.dumps({"type": "response.output_item.delta", "delta": {}}),
        json.dumps({"type": "item.completed", "item": {"id": "item-1", "type": "function_call"}}),
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "msg-1",
                    "type": "agent_message",
                    "content": [
                        {"type": "output_text", "text": "Codex summary"},
                    ],
                },
            }
        ),
        json.dumps({"type": "turn.completed"}),
    ]


@pytest.fixture
def copilot_jsonl_lines() -> list[str]:
    """Realistic Copilot format JSONL lines."""
    return [
        json.dumps(
            {
                "id": "cp-1",
                "toolName": "Read",
                "parameters": {"file_path": "src/planora/core/config.py"},
            }
        ),
        json.dumps(
            {
                "id": "cp-1",
                "toolName": "Read",
                "parameters": {"file_path": "src/planora/core/config.py"},
                "done": True,
                "result": "ok",
            }
        ),
        json.dumps({"text": "Copilot response text"}),
    ]


@pytest.fixture
def opencode_jsonl_lines() -> list[str]:
    """Realistic OpenCode format JSONL lines."""
    return [
        json.dumps({"type": "step_start"}),
        json.dumps(
            {
                "type": "tool_use",
                "id": "oc-1",
                "tool": "bash",
                "input": {"command": "ls -la"},
            }
        ),
        json.dumps(
            {
                "type": "tool_use",
                "id": "oc-1",
                "tool": "bash",
                "input": {"command": "ls -la"},
                "result": "ok",
            }
        ),
        json.dumps({"type": "text", "part": {"text": "OpenCode response text"}}),
        json.dumps({"type": "step_finish"}),
    ]


@pytest.fixture
def gemini_jsonl_lines() -> list[str]:
    """Realistic Gemini format JSONL lines."""
    return [
        json.dumps(
            {
                "functionCall": {
                    "name": "web_search_exa",
                    "args": {"query": "planora tests"},
                }
            }
        ),
        json.dumps(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "Gemini response text"},
                            ]
                        }
                    }
                ],
                "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 34},
            }
        ),
    ]
