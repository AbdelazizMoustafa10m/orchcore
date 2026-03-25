from __future__ import annotations

import pytest

from orchcore.registry.agent import AgentMode, OutputExtraction, ToolSet
from orchcore.runner.subprocess import AgentRunner, _strip_preamble_text, _translate_toolset
from orchcore.stream.events import StreamFormat


def test_build_command_uses_mode_flags(sample_agent_config, tmp_path) -> None:
    command = AgentRunner._build_command(
        sample_agent_config,
        "write tests",
        tmp_path / "output.md",
        AgentMode.PLAN,
    )

    assert command == [
        "echo",
        "-p",
        "write tests",
        "--model",
        "test-model",
        "--verbose",
    ]


def test_build_command_appends_direct_file_output_flag(
    sample_agent_config,
    tmp_path,
) -> None:
    agent = sample_agent_config.model_copy(
        update={
            "output_extraction": OutputExtraction(
                strategy=OutputExtraction.Strategy.DIRECT_FILE,
            )
        }
    )

    command = AgentRunner._build_command(
        agent,
        "write tests",
        tmp_path / "output.md",
        AgentMode.FIX,
    )

    assert command[-2:] == ["-o", str(tmp_path / "output.md")]


@pytest.mark.parametrize(
    ("stream_format", "toolset", "expected"),
    [
        (
            StreamFormat.CLAUDE,
            ToolSet(internal=["Read"], mcp=["exa"], max_turns=3),
            [
                "--allowedTools",
                "Read,exa",
                "--max-turns",
                "3",
                "--verbose",
                "--output-format",
                "stream-json",
                "--include-partial-messages",
            ],
        ),
        (
            StreamFormat.CODEX,
            ToolSet(permission="full-access"),
            ["-s", "full-access", "--json"],
        ),
        (
            StreamFormat.GEMINI,
            ToolSet(permission="full-access"),
            ["--yolo"],
        ),
    ],
)
def test_translate_toolset_covers_primary_formats(
    sample_agent_config,
    stream_format: StreamFormat,
    toolset: ToolSet,
    expected: list[str],
) -> None:
    agent = sample_agent_config.model_copy(update={"stream_format": stream_format})

    assert _translate_toolset(agent, toolset) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("preamble\n# Title\nbody", "# Title\nbody"),
        ("# Title\nbody", "# Title\nbody"),
        ("no heading here", "no heading here"),
    ],
)
def test_strip_preamble_text(text: str, expected: str) -> None:
    assert _strip_preamble_text(text) == expected
