#!/usr/bin/env python3

from __future__ import annotations

import json

EVENTS = [
    {"type": "system", "subtype": "init", "session_id": "claude-session-1"},
    {"type": "message_start"},
    {"type": "content_block_start", "content_block": {"type": "thinking"}},
    {"type": "content_block_stop"},
    {
        "type": "content_block_start",
        "content_block": {
            "type": "tool_use",
            "name": "Read",
            "id": "tool-1",
            "input": {"file_path": "src/demo.py"},
        },
    },
    {
        "type": "content_block_delta",
        "delta": {
            "type": "input_json_delta",
            "partial_json": '{"file_path":"src/demo.py"}',
        },
    },
    {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Read",
                    "id": "tool-1",
                    "input": {"file_path": "src/demo.py"},
                },
                {
                    "type": "text",
                    "text": "# Claude Output\n\nIntegration succeeded.\n",
                },
            ]
        },
    },
    {"type": "message_stop"},
    {
        "type": "result",
        "total_cost_usd": "0.1234",
        "duration_ms": 42,
        "exit_code": 0,
        "num_turns": 2,
        "session_id": "claude-session-1",
        "usage": {"input_tokens": 12, "output_tokens": 34},
    },
]

for event in EVENTS:
    print(json.dumps(event), flush=True)
