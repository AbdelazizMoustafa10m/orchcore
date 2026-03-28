from __future__ import annotations

import pytest

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
