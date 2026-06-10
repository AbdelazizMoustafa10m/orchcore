#!/usr/bin/env python3
"""Multi-format mock agent CLI for the integration matrix (WP-27).

Invoked by AgentRunner exactly like a real agent CLI::

    python mock_agent.py PROMPT --model M --format claude --scenario ok [-o FILE]

``--format`` selects the canonical session from tests/fixtures/sessions.py;
``--scenario`` selects ok / error / rate-limit behavior. Stream lines are
written via ``sys.stdout.buffer`` so Windows never CRLF-translates the JSONL.
The prompt and ``--model`` arguments the runner appends are accepted and
ignored.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.fixtures.sessions import (
    CANONICAL_SESSIONS,
    DIRECT_FILE_CONTENT,
    RATE_LIMIT_SESSIONS,
    STDERR_ERROR_TEXT,
    STDERR_RATE_LIMIT_TEXT,
    STREAM_ERROR_SESSIONS,
)


def _emit(lines: list[dict[str, object]]) -> None:
    for line in lines:
        sys.stdout.buffer.write(json.dumps(line).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--format", required=True, choices=sorted(CANONICAL_SESSIONS))
    parser.add_argument("--scenario", default="ok", choices=["ok", "error", "rate-limit"])
    parser.add_argument("-o", "--output", default=None)
    # The runner appends the prompt and --model; accept and ignore them.
    args, _extras = parser.parse_known_args()

    if args.scenario == "ok":
        _emit(CANONICAL_SESSIONS[args.format])
        if args.output is not None:
            Path(args.output).write_text(DIRECT_FILE_CONTENT, encoding="utf-8")
        return 0

    if args.scenario == "error":
        stream_lines = STREAM_ERROR_SESSIONS.get(args.format)
        if stream_lines is not None:
            # Terminal error inside the stream, clean exit 0 (WP-15 net).
            _emit(stream_lines)
            return 0
        # No terminal error frame in this wire format: fail like a real CLI.
        _emit(CANONICAL_SESSIONS[args.format][:1])
        print(STDERR_ERROR_TEXT, file=sys.stderr, flush=True)
        return 1

    # scenario == "rate-limit"
    stream_lines = RATE_LIMIT_SESSIONS.get(args.format)
    if stream_lines is not None:
        _emit(stream_lines)
        return 0
    _emit(CANONICAL_SESSIONS[args.format][:1])
    print(STDERR_RATE_LIMIT_TEXT, file=sys.stderr, flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
