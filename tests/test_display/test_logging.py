from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from orchcore.display import logging as logging_module
from orchcore.display.formatting import (
    format_cost,
    format_duration,
    format_file_size,
    format_tokens,
)

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.mark.parametrize(
    ("log_func", "message", "expected"),
    [
        (
            logging_module.log_info,
            "info message",
            (
                f"{logging_module.DIM}12:34:56{logging_module.NC} "
                f"{logging_module.CYAN}{logging_module.ICON_INFO}{logging_module.NC} "
                "info message\n"
            ),
        ),
        (
            logging_module.log_success,
            "success message",
            (
                f"{logging_module.DIM}12:34:56{logging_module.NC} "
                f"{logging_module.GREEN}{logging_module.ICON_SUCCESS}{logging_module.NC} "
                "success message\n"
            ),
        ),
        (
            logging_module.log_warn,
            "warn message",
            (
                f"{logging_module.DIM}12:34:56{logging_module.NC} "
                f"{logging_module.YELLOW}{logging_module.ICON_WARN}{logging_module.NC} "
                "warn message\n"
            ),
        ),
        (
            logging_module.log_error,
            "error message",
            (
                f"{logging_module.DIM}12:34:56{logging_module.NC} "
                f"{logging_module.RED}{logging_module.ICON_ERROR}{logging_module.NC} "
                "error message\n"
            ),
        ),
        (
            logging_module.log_dim,
            "dim message",
            f"{logging_module.DIM}12:34:56 dim message{logging_module.NC}\n",
        ),
    ],
)
def test_log_functions_write_expected_output_to_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    log_func: Callable[[str], None],
    message: str,
    expected: str,
) -> None:
    monkeypatch.setattr(logging_module, "_timestamp", lambda: "12:34:56")

    log_func(message)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "\u2014"),
        (timedelta(seconds=-1), "\u2014"),
        (timedelta(seconds=59), "59s"),
        (timedelta(seconds=61), "1m 1s"),
    ],
)
def test_format_duration(value: timedelta | None, expected: str) -> None:
    assert format_duration(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "N/A"),
        (Decimal("1.23456"), "$1.2346"),
    ],
)
def test_format_cost(value: Decimal | None, expected: str) -> None:
    assert format_cost(value) == expected


@pytest.mark.parametrize(
    ("size_bytes", "expected"),
    [
        (999, "999 bytes"),
        (1024, "1.0 KB"),
        (1_048_576, "1.0 MB"),
    ],
)
def test_format_file_size(size_bytes: int, expected: str) -> None:
    assert format_file_size(size_bytes) == expected


@pytest.mark.parametrize(
    ("token_usage", "expected"),
    [
        (None, "\u2014"),
        ({"input_tokens": 1_234, "output_tokens": 5_678}, "1,234/5,678"),
        ({"in": 5, "out": 6}, "5/6"),
    ],
)
def test_format_tokens(
    token_usage: dict[str, int] | None,
    expected: str,
) -> None:
    assert format_tokens(token_usage) == expected


def test_timestamp_returns_hh_mm_ss() -> None:
    timestamp = logging_module._timestamp()

    assert len(timestamp) == 8
    assert timestamp.count(":") == 2


@pytest.mark.parametrize(
    ("elapsed", "tool_count", "cost", "state", "expected_time"),
    [
        (5.2, 2, 1.5, "running", "5s"),
        (125.0, 3, 2.0, "waiting", "2m05s"),
    ],
)
def test_status_line_writes_expected_progress(
    capsys: pytest.CaptureFixture[str],
    elapsed: float,
    tool_count: int,
    cost: float,
    state: str,
    expected_time: str,
) -> None:
    logging_module.status_line(
        elapsed=elapsed,
        tool_count=tool_count,
        cost=cost,
        state=state,
    )

    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == (
        f"\r{logging_module.DIM}{logging_module.ICON_TIMER} {expected_time}{logging_module.NC}"
        f" | {logging_module.CYAN}{tool_count} tools{logging_module.NC}"
        f" | {logging_module.GREEN}{logging_module.ICON_COST}{cost:.2f}{logging_module.NC}"
        f" | {logging_module.MAGENTA}{state}{logging_module.NC}"
    )


def test_clear_status_line_writes_erase_sequence(capsys: pytest.CaptureFixture[str]) -> None:
    logging_module.clear_status_line()

    captured = capsys.readouterr()

    assert captured.out == ""
    assert captured.err == "\r" + (" " * 80) + "\r"


def test_phase_header_renders_section_header(capsys: pytest.CaptureFixture[str]) -> None:
    logging_module.phase_header("planning", index=1, total=3)

    captured = capsys.readouterr()

    assert captured.out == ""
    assert "Phase 2/3: planning" in captured.err
    assert captured.err.count("=" * 60) == 2


def test_summary_box_renders_key_value_rows(capsys: pytest.CaptureFixture[str]) -> None:
    logging_module.summary_box(
        "Run Summary",
        {
            "agents": "2",
            "cost": "$0.25",
        },
    )

    captured = capsys.readouterr()

    assert captured.out == ""
    assert "Run Summary" in captured.err
    assert f"agents: {logging_module.CYAN}2{logging_module.NC}" in captured.err
    assert f"cost: {logging_module.CYAN}$0.25{logging_module.NC}" in captured.err
