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
