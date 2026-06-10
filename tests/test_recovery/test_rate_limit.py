from __future__ import annotations

import datetime as dt
import os
import subprocess
import sys
from types import SimpleNamespace
from typing import ClassVar, Self, cast

import pytest

from orchcore.recovery import rate_limit as rate_limit_module
from orchcore.recovery.rate_limit import (
    BackoffStrategy,
    RateLimitDetector,
    ResetTimeParser,
)


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        pytest.param(
            "You have hit your usage limit for Claude. Resets at 5pm UTC.",
            True,
            id="claude",
        ),
        pytest.param(
            "Error 429 from Codex API: try again in 300 seconds.",
            True,
            id="codex",
        ),
        pytest.param(
            "Gemini returned RESOURCE_EXHAUSTED because the quota exceeded.",
            True,
            id="gemini",
        ),
        pytest.param(
            "Request failed: rate limit exceeded by upstream service.",
            True,
            id="generic",
        ),
        pytest.param(
            "Execution completed successfully with no retry needed.",
            False,
            id="non-rate-limit",
        ),
    ],
)
def test_is_rate_limited_detects_expected_outputs(
    output: str,
    expected: bool,
) -> None:
    detector = RateLimitDetector()

    result = detector.is_rate_limited(output)

    assert result is expected


def test_extract_message_returns_matching_line() -> None:
    detector = RateLimitDetector()
    output = "\n".join(
        [
            "Started request.",
            "Error 429 from Codex API: try again in 300 seconds.",
            "Retry handler will decide next action.",
        ]
    )

    result = detector.extract_message(output)

    assert result == "Error 429 from Codex API: try again in 300 seconds."


def test_rate_limit_detector_handles_empty_and_multiline_fallback() -> None:
    detector = RateLimitDetector()

    assert detector.is_rate_limited("") is False
    assert detector.extract_message("") is None
    assert detector.extract_message("rate\nlimit") == "rate\nlimit"


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        pytest.param("Please retry in 5 minutes.", 300, id="minutes"),
        pytest.param("Try again in 300 seconds.", 300, id="seconds"),
        pytest.param("Usage resets in 5 days 27 minutes.", 433620, id="days-and-minutes"),
        pytest.param("Please try again later.", None, id="unparseable"),
    ],
)
def test_reset_time_parser_parse_cases(output: str, expected: int | None) -> None:
    parser = ResetTimeParser()

    result = parser.parse(output)

    assert result == expected


def _freeze_rate_limit_now(monkeypatch: pytest.MonkeyPatch, fixed_now: dt.datetime) -> None:
    class FrozenDateTime(dt.datetime):
        frozen_now: ClassVar[dt.datetime] = fixed_now

        @classmethod
        def now(cls, tz: dt.tzinfo | None = None) -> Self:
            if tz is None:
                return cast("Self", cls.frozen_now.replace(tzinfo=None))
            return cast("Self", cls.frozen_now.astimezone(tz))

    monkeypatch.setattr(
        rate_limit_module,
        "datetime",
        SimpleNamespace(datetime=FrozenDateTime, UTC=dt.UTC),
    )


@pytest.mark.parametrize(
    ("now", "output", "expected"),
    [
        pytest.param(
            dt.datetime(2026, 6, 10, 12, 0, tzinfo=dt.UTC),
            "usage resets at 5pm",
            18_000,
            id="utc-5pm",
        ),
        pytest.param(
            dt.datetime(2026, 6, 10, 12, 0, tzinfo=dt.UTC),
            "usage resets 7:30 pm Europe/Berlin",
            19_800,
            id="berlin-evening",
        ),
        pytest.param(
            dt.datetime(2026, 6, 10, 12, 0, tzinfo=dt.UTC),
            "usage resets at 23:45",
            42_300,
            id="twenty-four-hour-clock",
        ),
        pytest.param(
            dt.datetime(2026, 6, 10, 12, 0, tzinfo=dt.UTC),
            "usage resets 12am",
            43_200,
            id="midnight-edge",
        ),
        pytest.param(
            dt.datetime(2026, 6, 10, 11, 0, tzinfo=dt.UTC),
            "usage resets 12pm",
            3_600,
            id="noon-edge",
        ),
        pytest.param(
            dt.datetime(2026, 6, 10, 18, 0, tzinfo=dt.UTC),
            "usage resets at 5pm",
            82_800,
            id="past-time-rolls-to-tomorrow",
        ),
        pytest.param(
            dt.datetime(2026, 3, 29, 0, 30, tzinfo=dt.UTC),
            "usage resets 4:30 Europe/Berlin",
            10_800,
            id="berlin-dst-wall-clock",
        ),
    ],
)
def test_reset_time_parser_absolute_cases(
    monkeypatch: pytest.MonkeyPatch,
    now: dt.datetime,
    output: str,
    expected: int,
) -> None:
    _freeze_rate_limit_now(monkeypatch, now)

    result = ResetTimeParser().parse(output)

    assert result == expected


@pytest.mark.parametrize(
    "output",
    [
        "usage resets at 10:61",
        "usage resets at 24",
        "usage resets at 0pm",
        "usage resets at 13pm",
    ],
)
def test_reset_time_parser_absolute_rejects_invalid_times(
    monkeypatch: pytest.MonkeyPatch,
    output: str,
) -> None:
    _freeze_rate_limit_now(monkeypatch, dt.datetime(2026, 6, 10, 12, 0, tzinfo=dt.UTC))

    assert ResetTimeParser().parse(output) is None


def test_reset_time_parser_absolute_invalid_timezone_falls_back_to_utc(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _freeze_rate_limit_now(monkeypatch, dt.datetime(2026, 6, 10, 12, 0, tzinfo=dt.UTC))

    with caplog.at_level("WARNING", logger="orchcore.recovery.rate_limit"):
        result = ResetTimeParser().parse("usage resets at 5pm Mars/Base")

    assert result == 18_000
    assert "Invalid reset timezone 'Mars/Base'; falling back to UTC." in caplog.text


@pytest.mark.parametrize(
    ("attempt", "reset_seconds", "max_wait", "expected"),
    [
        pytest.param(1, None, 21600, 127.0, id="attempt-1"),
        pytest.param(2, None, 21600, 307.0, id="attempt-2"),
        pytest.param(1, 1800, 21600, 1807.0, id="reset-seconds"),
        pytest.param(4, None, 1000, 1000.0, id="max-wait-cap"),
    ],
)
def test_backoff_strategy_compute_wait_uses_deterministic_jitter(
    monkeypatch: pytest.MonkeyPatch,
    attempt: int,
    reset_seconds: int | None,
    max_wait: int,
    expected: float,
) -> None:
    strategy = BackoffStrategy(max_wait=max_wait)

    def fake_randint(_start: int, _stop: int) -> int:
        return 7

    monkeypatch.setattr(
        "orchcore.recovery.rate_limit.random.randint",
        fake_randint,
    )

    result = strategy.compute_wait(attempt=attempt, reset_seconds=reset_seconds)

    assert result == expected


def test_backoff_strategy_rejects_empty_schedule() -> None:
    with pytest.raises(ValueError, match="schedule must not be empty"):
        BackoffStrategy(schedule=[])


def test_tzdata_dependency_present() -> None:
    import tzdata  # type: ignore[import-untyped]  # noqa: F401 - dependency presence guard.


def test_named_timezone_resolves_without_system_db() -> None:
    env = {**os.environ, "PYTHONTZPATH": ""}

    subprocess.run(  # noqa: S603
        [sys.executable, "-c", "from zoneinfo import ZoneInfo; ZoneInfo('Europe/Berlin')"],
        env=env,
        check=True,
    )
