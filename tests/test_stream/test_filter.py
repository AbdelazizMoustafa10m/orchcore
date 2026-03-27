from __future__ import annotations

import pytest

from orchcore.stream.events import StreamFormat
from orchcore.stream.filter import StreamFilter


@pytest.mark.parametrize(
    ("stream_format", "line", "expected"),
    [
        pytest.param(StreamFormat.CLAUDE, "", False, id="empty-line"),
        pytest.param(StreamFormat.CLAUDE, "   ", False, id="whitespace-line"),
        pytest.param(
            StreamFormat.CLAUDE,
            '{"type":"content_block_delta","delta":{"type":"text_delta"}}',
            True,
            id="claude-keep-text-delta",
        ),
        pytest.param(
            StreamFormat.CLAUDE,
            '{"type": "content_block_stop", "content_block": {}}',
            False,
            id="claude-drop-stop",
        ),
        pytest.param(
            StreamFormat.CLAUDE,
            '{"type":"assistant","message":{"content":[{"type":"text","text":"ok"}]}}',
            True,
            id="claude-keep-assistant",
        ),
        pytest.param(
            StreamFormat.CLAUDE,
            '{"message":"no type field"}',
            True,
            id="claude-keep-no-type",
        ),
        pytest.param(
            StreamFormat.CODEX,
            '{"type":"response.output_text.delta","delta":"partial"}',
            False,
            id="codex-drop-output-text-delta",
        ),
        pytest.param(
            StreamFormat.CODEX,
            '{"type":"response.reasoning_summary.delta","delta":"thinking"}',
            False,
            id="codex-drop-reasoning-delta",
        ),
        pytest.param(
            StreamFormat.CODEX,
            '{"type":"response.output_item.delta"}',
            True,
            id="codex-keep-tool-progress",
        ),
        pytest.param(
            StreamFormat.OPENCODE,
            '{"type":"text","part":{"text":""}}',
            False,
            id="opencode-drop-empty-text",
        ),
        pytest.param(
            StreamFormat.OPENCODE,
            '{"type":"text","part":{"text":"OpenCode response text"}}',
            True,
            id="opencode-keep-text",
        ),
        pytest.param(
            StreamFormat.GEMINI,
            '{"promptFeedback":{"blockReason":"SAFETY"}}',
            False,
            id="gemini-drop-prompt-feedback",
        ),
        pytest.param(
            StreamFormat.GEMINI,
            '{"functionCall":{"name":"web_search_exa","args":{"query":"planora"}}}',
            True,
            id="gemini-keep-function-call",
        ),
        pytest.param(
            StreamFormat.COPILOT,
            '{"text":""}',
            False,
            id="copilot-drop-empty-text",
        ),
        pytest.param(
            StreamFormat.COPILOT,
            '{"toolName":"Read","parameters":{"file_path":"README.md"}}',
            True,
            id="copilot-keep-tool",
        ),
    ],
)
def test_should_keep_filters_only_known_noise(
    stream_format: StreamFormat,
    line: str,
    expected: bool,
) -> None:
    assert StreamFilter(stream_format).should_keep(line) is expected


@pytest.mark.asyncio
async def test_filter_stream_preserves_actionable_lines_in_order() -> None:
    # Arrange
    async def raw_lines():
        for line in (
            '{"type":"content_block_delta"}',
            '{"type":"assistant"}',
            '{"type":"content_block_stop"}',
            '{"type":"result"}',
        ):
            yield line

    # Act
    filtered = [line async for line in StreamFilter(StreamFormat.CLAUDE).filter_stream(raw_lines())]

    # Assert
    expected = ['{"type":"content_block_delta"}', '{"type":"assistant"}', '{"type":"result"}']
    assert filtered == expected
