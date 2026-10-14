"""Golden-fixture tests freezing the StreamParser event contract (WP-25).

Each golden file under ``tests/test_stream/golden/`` is self-contained: it
carries the input JSONL lines and the exact event sequences (full field dumps,
timestamps excluded) the parser must produce for them. The goldens were
recorded from the pre-refactor parser and act as the safety net for the
per-format extraction refactor: they must stay green **and unmodified**
through every WP-25 change.

Regenerating (only for deliberate, reviewed contract changes):
    ORCHCORE_REGEN_GOLDENS=1 pytest tests/test_stream/test_parser_golden.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from orchcore.stream.events import StreamFormat
from orchcore.stream.parser import StreamParser

GOLDEN_DIR = Path(__file__).parent / "golden"

# Inputs per format. Sessions are parsed with a fresh parser each (stateful
# formats: Copilot implicit INIT, Antigravity open tool steps).
GOLDEN_SESSIONS: dict[StreamFormat, list[tuple[str, list[dict[str, object]]]]] = {
    StreamFormat.CLAUDE: [
        (
            "canonical",
            [
                {"type": "system", "subtype": "init", "session_id": "sess-claude-1"},
                {
                    "type": "system",
                    "subtype": "api_retry",
                    "attempt": 1,
                    "max_retries": 3,
                    "delay": 2000,
                },
                {
                    "type": "system",
                    "subtype": "api_retry",
                    "attempt": 2,
                    "max_retries": 3,
                    "delay": 4000,
                    "error_code": "rate_limit_error",
                },
                {"type": "system", "subtype": "rate_limit", "retry_after_ms": 30000},
                {"type": "content_block_start", "content_block": {"type": "thinking"}},
                {"type": "content_block_start", "content_block": {"type": "text"}},
                {
                    "type": "content_block_start",
                    "content_block": {
                        "type": "tool_use",
                        "name": "Read",
                        "id": "tb-1",
                        "input": {"file_path": "src/app.py"},
                    },
                },
                {
                    "type": "content_block_delta",
                    "delta": {"type": "input_json_delta", "partial_json": "{}"},
                },
                {
                    "type": "content_block_start",
                    "content_block": {
                        "type": "tool_use",
                        "name": "Agent",
                        "id": "tb-2",
                        "input": {"description": "review auth"},
                    },
                },
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Read",
                                "id": "tb-1",
                                "input": {"file_path": "src/app.py"},
                            },
                            {"type": "text", "text": "# Result\n\nDone."},
                        ]
                    },
                },
                {
                    "type": "result",
                    "total_cost_usd": "0.0456",
                    "duration_ms": 1234,
                    "exit_code": 0,
                    "num_turns": 4,
                    "session_id": "sess-claude-1",
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                },
            ],
        ),
        (
            "errors-and-unknowns",
            [
                {"type": "result", "error": "agent failed: boom"},
                {"type": "result", "error": {"message": "overloaded"}},
                {"type": "system", "subtype": "unknown_subtype"},
                {"type": "unknown_event"},
                {"type": "content_block_start", "content_block": {"type": "unknown_block"}},
                {"type": "content_block_delta", "delta": {"type": "text_delta"}},
            ],
        ),
    ],
    StreamFormat.CODEX: [
        (
            "canonical",
            [
                {"type": "thread.started", "thread_id": "th-1"},
                {"type": "item.started", "item": {"id": "call-1", "type": "function_call"}},
                {
                    "type": "item.started",
                    "item": {"id": "agent-1", "type": "agent_runner", "description": "sub work"},
                },
                {"type": "response.output_item.delta"},
                {"type": "item.completed", "item": {"id": "call-1", "type": "function_call"}},
                {
                    "type": "item.completed",
                    "item": {
                        "id": "msg-1",
                        "type": "agent_message",
                        "content": [{"type": "output_text", "text": "Codex says hi"}],
                    },
                },
                {
                    "type": "item.completed",
                    "item": {
                        "id": "msg-2",
                        "type": "agent_message",
                        "content": "plain string content",
                    },
                },
                {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}},
            ],
        ),
        (
            "errors-and-unknowns",
            [
                {"type": "turn.completed", "error": "stream blew up", "exit_code": 3},
                {"type": "error", "code": "rate_limit_exceeded", "retry_after_ms": 5000},
                {"type": "error", "code": "internal_error", "message": "boom", "exit_code": 2},
                {"type": "error", "message": {"text": "dict message"}},
                {"type": "unknown.evt"},
            ],
        ),
    ],
    StreamFormat.COPILOT: [
        (
            "canonical",
            [
                {
                    "sessionId": "cop-1",
                    "toolName": "Read",
                    "id": "cp-1",
                    "parameters": {"file_path": "README.md"},
                },
                {
                    "toolName": "Read",
                    "id": "cp-1",
                    "parameters": {"file_path": "README.md"},
                    "result": "ok",
                },
                {"tool": "Bash", "id": "cp-2", "input": {"command": "ls"}},
                {"tool": "Bash", "id": "cp-2", "input": {"command": "ls"}, "done": True},
                {"text": "Copilot text"},
                {"message": "Copilot message"},
                {"content": "Copilot content"},
                {"unknownKey": 1},
            ],
        ),
        (
            "metadata-session-id",
            [
                {"metadata": {"conversationId": "cop-meta-1"}, "text": "hello"},
            ],
        ),
    ],
    StreamFormat.OPENCODE: [
        (
            "canonical",
            [
                {"type": "step_start"},
                {"type": "tool_use", "id": "oc-1", "tool": "bash", "input": {"command": "ls -la"}},
                {
                    "type": "tool_use",
                    "id": "oc-1",
                    "tool": "bash",
                    "input": {"command": "ls -la"},
                    "result": "ok",
                },
                {
                    "type": "tool_use",
                    "id": "oc-2",
                    "name": "read",
                    "input": {"file_path": "x.py"},
                    "result": None,
                },
                {"type": "text", "part": {"text": "OpenCode text"}},
                {"type": "text", "part": {"text": ""}},
                {"type": "step_finish", "exit_code": 0},
            ],
        ),
        (
            "errors-and-unknowns",
            [
                {"type": "step_finish", "error": "opencode failed"},
                {"type": "mystery"},
            ],
        ),
    ],
    StreamFormat.ANTIGRAVITY: [
        (
            # Shapes captured from agy 1.2.17 / 1.3.5 --output-format stream-json.
            "canonical",
            [
                {
                    "event": "init",
                    "conversation_id": "conv-1",
                    "init": {
                        "model": "gemini-3.8-flash-low",
                        "cwd": "/repo",
                        "tools": ["view_file", "run_command"],
                        "permission_mode": "always-proceed",
                    },
                },
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": "conv-1",
                        "step_index": 0,
                        "state": "DONE",
                        "step_type": "user_input",
                    },
                },
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": "conv-1",
                        "step_index": 1,
                        "state": "DONE",
                        "step_type": "agent_response",
                        "duration_seconds": 3.67,
                        "usage": {"input_tokens": 11849, "output_tokens": 48},
                    },
                },
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": "conv-1",
                        "step_index": 2,
                        "state": "ACTIVE",
                        "step_type": "tool",
                        "tool_name": "view_file",
                        "tool_info": {
                            "name": "view_file",
                            "parameters": {"AbsolutePath": "/repo/note.txt"},
                        },
                    },
                },
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": "conv-1",
                        "step_index": 2,
                        "state": "DONE",
                        "step_type": "tool",
                        "tool_name": "view_file",
                        "duration_seconds": 0.120576,
                        "tool_info": {
                            "name": "view_file",
                            "parameters": {"AbsolutePath": "/repo/note.txt"},
                            "output": "2 lines, 15 bytes",
                        },
                    },
                },
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": "conv-1",
                        "step_index": 3,
                        "state": "ACTIVE",
                        "step_type": "tool",
                        "tool_name": "run_command",
                        "tool_info": {
                            "name": "run_command",
                            "parameters": {"CommandLine": "pytest -q"},
                        },
                    },
                },
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": "conv-1",
                        "step_index": 3,
                        "state": "ACTIVE",
                        "step_type": "tool",
                        "tool_name": "run_command",
                        "tool_info": {
                            "name": "run_command",
                            "parameters": {"CommandLine": "pytest -q"},
                        },
                    },
                },
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": "conv-1",
                        "step_index": 3,
                        "state": "DONE",
                        "step_type": "tool",
                        "tool_name": "run_command",
                        "duration_seconds": 1.5,
                        "tool_info": {
                            "name": "run_command",
                            "parameters": {"CommandLine": "pytest -q"},
                            "output": "3 passed",
                        },
                    },
                },
                {
                    "event": "step_update",
                    "step_update": {"step_index": 4, "step_type": "checkpoint"},
                },
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": "conv-1",
                        "step_index": 5,
                        "state": "ACTIVE",
                        "step_type": "agent_response",
                        "text_delta": "hello orchcore",
                    },
                },
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": "conv-1",
                        "step_index": 5,
                        "state": "DONE",
                        "step_type": "agent_response",
                        "text_delta": "\n",
                        "duration_seconds": 2.061067,
                        "usage": {"input_tokens": 12088, "output_tokens": 3},
                    },
                },
                {
                    "event": "result",
                    "result": {
                        "conversation_id": "conv-1",
                        "status": "SUCCESS",
                        "response": "hello orchcore\n",
                        "duration_seconds": 5.770875,
                        "num_turns": 1,
                        "usage": {
                            "input_tokens": 23937,
                            "output_tokens": 51,
                            "thinking_tokens": 0,
                            "cache_read_tokens": 0,
                            "total_tokens": 23988,
                        },
                    },
                },
            ],
        ),
        (
            "headless-permission-denied",
            [
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": "conv-2",
                        "step_index": 2,
                        "state": "ERROR",
                        "step_type": "tool",
                        "tool_name": "run_command",
                        "duration_seconds": 0.036907,
                        "tool_info": {
                            "name": "run_command",
                            "parameters": {"CommandLine": "echo probe-ok"},
                            "error": {
                                "type": "TOOL_ERROR",
                                "message": "permission check failed: user denied permission",
                            },
                        },
                    },
                },
                {
                    "event": "result",
                    "result": {
                        "conversation_id": "conv-2",
                        "status": "SUCCESS",
                        "response": "",
                        "num_turns": 1,
                        "denied_actions": [{"action": "command", "display_name": "RunCommand"}],
                    },
                },
            ],
        ),
        (
            "subagents",
            [
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": "conv-3",
                        "step_index": 1,
                        "state": "ACTIVE",
                        "step_type": "tool",
                        "tool_name": "invoke_subagent",
                        "tool_info": {"name": "invoke_subagent", "parameters": {}},
                        "subagent_info": {
                            "subagents": [{"type_name": "research", "role": "web researcher"}]
                        },
                    },
                },
                {
                    "event": "step_update",
                    "step_update": {
                        "conversation_id": "conv-3",
                        "step_index": 2,
                        "state": "ACTIVE",
                        "step_type": "tool",
                        "tool_name": "browser_subagent",
                        "tool_info": {
                            "name": "browser_subagent",
                            "parameters": {"Url": "https://example.com"},
                        },
                    },
                },
            ],
        ),
        (
            "errors-and-unknowns",
            [
                {
                    "event": "result",
                    "result": {
                        "conversation_id": "",
                        "status": "ERROR",
                        "response": "",
                        "error": 'invalid model selection (--model "nope")',
                        "duration_seconds": 0,
                        "num_turns": 0,
                    },
                },
                {
                    "event": "result",
                    "result": {
                        "status": "ERROR",
                        "error": (
                            "API error (attempt 3): RESOURCE_EXHAUSTED (code 429): Individual "
                            "quota reached. Resets in 2h39m52s."
                        ),
                    },
                },
                {"event": "result", "result": {"status": "CANCELLED"}},
                {
                    "event": "step_update",
                    "step_update": {
                        "step_index": 6,
                        "state": "DONE",
                        "step_type": "error_message",
                        "error": {"message": "Agent execution terminated due to error."},
                    },
                },
                {
                    "event": "step_update",
                    "step_update": {
                        "step_index": 7,
                        "state": "ERROR",
                        "step_type": "agent_response",
                        "error": "RESOURCE_EXHAUSTED: quota reached",
                    },
                },
                {
                    "event": "step_update",
                    "step_update": {"step_index": 8, "state": "ERROR", "step_type": "planner"},
                },
                {
                    "event": "step_update",
                    "step_update": {
                        "step_index": 9,
                        "state": "DONE",
                        "step_type": "tool",
                        "tool_name": "write_to_file",
                        "tool_info": {"parameters": {"TargetFile": "/repo/out.txt"}},
                    },
                },
                {"event": "step_update", "step_update": {"state": "ACTIVE", "step_type": "tool"}},
                {"event": "step_update", "step_update": {"step_type": "future_step"}},
                {"event": "step_update", "step_update": "not-an-object"},
                {"event": "result", "result": None},
                {"event": "future_event"},
                {"blob": 1},
            ],
        ),
    ],
}


def _golden_path(stream_format: StreamFormat) -> Path:
    return GOLDEN_DIR / f"{stream_format.value}.json"


def _parse_sessions(stream_format: StreamFormat) -> list[dict[str, object]]:
    """Parse every session with a fresh parser and dump full event sequences."""
    sessions: list[dict[str, object]] = []
    for name, objects in GOLDEN_SESSIONS[stream_format]:
        parser = StreamParser(stream_format)
        events: list[dict[str, object]] = []
        for obj in objects:
            for event in parser.parse_line(json.dumps(obj)):
                events.append(event.model_dump(mode="json", exclude={"timestamp"}))
        sessions.append({"name": name, "lines": objects, "events": events})
    return sessions


@pytest.mark.parametrize("stream_format", list(StreamFormat))
def test_parser_matches_golden_event_sequences(stream_format: StreamFormat) -> None:
    """The parser must reproduce the recorded event sequences field-for-field."""
    golden_path = _golden_path(stream_format)

    if os.environ.get("ORCHCORE_REGEN_GOLDENS"):
        GOLDEN_DIR.mkdir(exist_ok=True)
        golden_path.write_text(
            json.dumps({"sessions": _parse_sessions(stream_format)}, indent=2) + "\n",
            encoding="utf-8",
        )

    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    actual = _parse_sessions(stream_format)

    assert actual == golden["sessions"], (
        f"{stream_format.value} parser output drifted from the golden contract; "
        "if this change is deliberate, regenerate with ORCHCORE_REGEN_GOLDENS=1 "
        "and review the diff."
    )
