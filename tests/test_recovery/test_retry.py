from __future__ import annotations

import pytest

from orchcore.recovery.retry import FailureMode, RetryPolicy


@pytest.mark.parametrize(
    ("attempt", "expected"),
    [
        pytest.param(1, True, id="first-attempt"),
        pytest.param(3, True, id="max-retries"),
        pytest.param(4, False, id="beyond-max-retries"),
    ],
)
def test_should_retry_boundary_cases(attempt: int, expected: bool) -> None:
    policy = RetryPolicy(max_retries=3)

    result = policy.should_retry(attempt)

    assert result is expected


@pytest.mark.parametrize(
    ("policy", "succeeded", "failed", "total", "expected"),
    [
        pytest.param(
            RetryPolicy(failure_mode=FailureMode.FAIL_FAST),
            3,
            0,
            3,
            "done",
            id="fail-fast-done",
        ),
        pytest.param(
            RetryPolicy(failure_mode=FailureMode.FAIL_FAST),
            2,
            1,
            3,
            "failed",
            id="fail-fast-failed",
        ),
        pytest.param(
            RetryPolicy(failure_mode=FailureMode.CONTINUE),
            3,
            0,
            3,
            "done",
            id="continue-done",
        ),
        pytest.param(
            RetryPolicy(failure_mode=FailureMode.CONTINUE),
            2,
            1,
            3,
            "partial",
            id="continue-partial",
        ),
        pytest.param(
            RetryPolicy(failure_mode=FailureMode.CONTINUE),
            0,
            3,
            3,
            "failed",
            id="continue-failed",
        ),
        pytest.param(
            RetryPolicy(failure_mode=FailureMode.REQUIRE_MINIMUM, min_count=2),
            2,
            0,
            2,
            "done",
            id="require-minimum-done",
        ),
        pytest.param(
            RetryPolicy(failure_mode=FailureMode.REQUIRE_MINIMUM, min_count=2),
            2,
            1,
            3,
            "partial",
            id="require-minimum-partial",
        ),
        pytest.param(
            RetryPolicy(failure_mode=FailureMode.REQUIRE_MINIMUM, min_count=2),
            1,
            2,
            3,
            "failed",
            id="require-minimum-failed",
        ),
    ],
)
def test_evaluate_results_cases(
    policy: RetryPolicy,
    succeeded: int,
    failed: int,
    total: int,
    expected: str,
) -> None:
    result = policy.evaluate_results(
        succeeded=succeeded,
        failed=failed,
        total=total,
    )

    assert result == expected
