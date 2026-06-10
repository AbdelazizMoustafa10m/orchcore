"""Canonical JSONL sessions per stream format, shared between the parser
fixtures (tests/conftest.py) and the mock agent CLI (tests/fixtures/
mock_agent.py) so both sides of the integration matrix exercise the same wire
data (WP-27).

Deliberately dependency-free: imported by a plain subprocess script.
"""

from __future__ import annotations

# -- Scenario "ok": each format's canonical happy-path session. --

CANONICAL_SESSIONS: dict[str, list[dict[str, object]]] = {
    "claude": [
        {"type": "system", "subtype": "init", "session_id": "sess-123"},
        {
            "type": "content_block_start",
            "content_block": {"type": "tool_use", "name": "Read", "id": "tb-1"},
        },
        {"type": "content_block_delta", "delta": {"type": "input_json_delta"}},
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
        },
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
        },
        {
            "type": "result",
            "total_cost_usd": "0.0123",
            "num_turns": 3,
            "session_id": "sess-123",
        },
    ],
    "codex": [
        {"type": "thread.started"},
        {"type": "item.started", "item": {"id": "item-1", "type": "function_call"}},
        {"type": "response.output_item.delta", "delta": {}},
        {"type": "item.completed", "item": {"id": "item-1", "type": "function_call"}},
        {
            "type": "item.completed",
            "item": {
                "id": "msg-1",
                "type": "agent_message",
                "content": [
                    {"type": "output_text", "text": "Codex summary"},
                ],
            },
        },
        {"type": "turn.completed"},
    ],
    "copilot": [
        {
            "id": "cp-1",
            "toolName": "Read",
            "parameters": {"file_path": "src/planora/core/config.py"},
        },
        {
            "id": "cp-1",
            "toolName": "Read",
            "parameters": {"file_path": "src/planora/core/config.py"},
            "done": True,
            "result": "ok",
        },
        {"text": "Copilot response text"},
    ],
    "opencode": [
        {"type": "step_start"},
        {
            "type": "tool_use",
            "id": "oc-1",
            "tool": "bash",
            "input": {"command": "ls -la"},
        },
        {
            "type": "tool_use",
            "id": "oc-1",
            "tool": "bash",
            "input": {"command": "ls -la"},
            "result": "ok",
        },
        {"type": "text", "part": {"text": "OpenCode response text"}},
        {"type": "step_finish"},
    ],
    "gemini": [
        {
            "functionCall": {
                "name": "web_search_exa",
                "args": {"query": "planora tests"},
            }
        },
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
        },
    ],
}

# Joined TEXT content the canonical sessions produce (JQ_FILTER/STDOUT_CAPTURE
# extraction output).
EXPECTED_CANONICAL_TEXT: dict[str, str] = {
    "claude": "# Plan\n\nAdd the requested tests.",
    "codex": "Codex summary",
    "copilot": "Copilot response text",
    "opencode": "OpenCode response text",
    "gemini": "Gemini response text",
}

# -- Scenario "error" (WP-15 regression net): formats whose wire format can
# express a terminal error end with it and exit 0. Copilot and Gemini have no
# terminal error frame; their mock prints STDERR_ERROR_TEXT and exits 1. --

STREAM_ERROR_SESSIONS: dict[str, list[dict[str, object]]] = {
    "claude": [
        {"type": "system", "subtype": "init", "session_id": "sess-err"},
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "partial output"}]},
        },
        {"type": "result", "exit_code": 0, "error": "mock structured failure"},
    ],
    "codex": [
        {"type": "thread.started"},
        {
            "type": "item.completed",
            "item": {"id": "msg-1", "type": "agent_message", "content": "partial output"},
        },
        {"type": "turn.completed", "error": "mock structured failure"},
    ],
    "opencode": [
        {"type": "step_start"},
        {"type": "text", "part": {"text": "partial output"}},
        {"type": "step_finish", "error": "mock structured failure"},
    ],
}

STDERR_ERROR_TEXT = "mock agent failure: simulated crash"

# -- Scenario "rate-limit": formats with a typed rate-limit frame emit it and
# exit 0; Copilot and OpenCode have none, so their mock prints
# STDERR_RATE_LIMIT_TEXT and exits 1 — exercising the fallback stderr
# classifier that upgrades NONZERO_EXIT to RATE_LIMIT. --

RATE_LIMIT_SESSIONS: dict[str, list[dict[str, object]]] = {
    "claude": [
        {"type": "system", "subtype": "init", "session_id": "sess-rl"},
        {"type": "system", "subtype": "rate_limit", "retry_after_ms": 5000},
    ],
    "codex": [
        {"type": "thread.started"},
        {"type": "error", "code": "rate_limit_exceeded", "retry_after_ms": 5000},
    ],
    "gemini": [
        {
            "error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "quota"},
            "retry_after_ms": 60000,
        },
    ],
}

STDERR_RATE_LIMIT_TEXT = "429 rate limit exceeded, try again in 60 seconds"

# Content the mock agent writes when invoked with -o (DIRECT_FILE strategy).
DIRECT_FILE_CONTENT = "Codex direct output\n"

ALL_FORMATS: tuple[str, ...] = ("claude", "codex", "copilot", "opencode", "gemini")
