# Recovery & Retry

orchcore handles the messy realities of long-running AI agent processes: rate limits, stalls, git dirty trees, and partial failures. This guide covers the recovery and retry system.

## Rate-Limit Detection

The `RateLimitDetector` uses regex patterns to identify rate-limit messages in agent output across all supported CLIs.

**Detected patterns include:**

| Agent | Example Message |
|-------|----------------|
| Claude | `"You've hit your usage limit"` |
| Codex | `"try again in 5 minutes"` |
| Gemini | `"Resource exhausted"` |
| Generic | `"rate limit exceeded"`, `"429"`, `"too many requests"` |

```python
from orchcore.recovery import RateLimitDetector

detector = RateLimitDetector()
if detector.is_rate_limited(output_text):
    message = detector.extract_message(output_text)
    print(f"Rate limited: {message}")
```

## Reset Time Parsing

The `ResetTimeParser` extracts wait times from rate-limit messages, supporting multiple formats:

| Format | Example | Result |
|--------|---------|--------|
| Absolute time with timezone | `"resets 7pm Europe/Berlin"` | timezone-aware datetime |
| Relative duration | `"try again in 5 minutes"` | 300 seconds |
| Seconds-based | `"retry after 120 seconds"` | 120 seconds |
| Fallback from 429 patterns | `"429 Too Many Requests"` | Default backoff |

The parser is timezone-aware — it correctly handles messages like "resets at 3:00 AM PST" regardless of the local timezone.

## Retry Policy

The `RetryPolicy` model controls retry behavior:

```python
from orchcore.recovery import RetryPolicy, FailureMode

policy = RetryPolicy(
    max_retries=3,
    backoff_schedule=[120, 300, 900, 1800],  # seconds
    max_wait=21600,  # 6 hours
    failure_mode=FailureMode.FAIL_FAST,
    min_count=1,  # for REQUIRE_MINIMUM mode
)
```

### Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_retries` | `int` | `3` | Maximum retry attempts |
| `backoff_schedule` | `list[int]` | `[120, 300, 900, 1800]` | Wait times in seconds for each retry |
| `max_wait` | `int` | `21600` | Maximum total wait time (6 hours) |
| `failure_mode` | `FailureMode` | `FAIL_FAST` | How to handle failures in multi-agent phases |
| `min_count` | `int` | `1` | Minimum successful agents for `REQUIRE_MINIMUM` mode |

### Backoff Schedule

The default backoff schedule uses exponential steps:

| Retry | Wait | Cumulative |
|-------|------|-----------|
| 1st | 2 minutes | 2 min |
| 2nd | 5 minutes | 7 min |
| 3rd | 15 minutes | 22 min |

Random jitter (0-30 seconds) is added to each wait to avoid thundering-herd effects.

## Failure Modes

For multi-agent parallel phases, the `FailureMode` controls how failures are evaluated:

### `FAIL_FAST`

Stop the phase on the first agent failure. The phase is marked as failed.

```python
phase = Phase(
    name="review",
    agents=["claude", "codex"],
    parallel=True,
    failure_mode=FailureMode.FAIL_FAST,
)
```

### `CONTINUE`

Run all agents regardless of individual failures. Report results per agent.

- All succeed → `done`
- Some succeed, some fail → `partial`
- All fail → `failed`

```python
phase = Phase(
    name="review",
    agents=["claude", "codex", "gemini"],
    parallel=True,
    failure_mode=FailureMode.CONTINUE,
)
```

### `REQUIRE_MINIMUM`

Require at least `min_count` agents to succeed. Useful when you want redundancy but don't need every agent to finish.

```python
from orchcore.recovery import RetryPolicy, FailureMode

phase = Phase(
    name="review",
    agents=["claude", "codex", "gemini"],
    parallel=True,
    failure_mode=FailureMode.REQUIRE_MINIMUM,
    retry_policy=RetryPolicy(
        failure_mode=FailureMode.REQUIRE_MINIMUM,
        min_count=2,  # At least 2 of 3 must succeed
    ),
)
```

## Git Dirty-Tree Recovery

Before retrying a failed agent, orchcore checks if the git working tree is dirty (the agent may have written partial changes). The `GitRecovery` module handles this automatically:

| Situation | Action |
|-----------|--------|
| Untracked files only | Auto-commit with descriptive message |
| Modified tracked files | Auto-stash, retry, then restore |
| Clean tree | Proceed with retry |

This ensures each retry attempt starts from a clean working state.

## Per-Phase Retry Configuration

Retry policies can be set per phase:

```python
from orchcore.pipeline import Phase
from orchcore.recovery import RetryPolicy, FailureMode

# Critical phase — retry aggressively
implementation = Phase(
    name="implementation",
    agents=["claude"],
    retry_policy=RetryPolicy(
        max_retries=5,
        backoff_schedule=[60, 120, 300, 600, 1200],
    ),
)

# Review phase — fail fast, no retries
review = Phase(
    name="review",
    agents=["claude", "codex"],
    parallel=True,
    failure_mode=FailureMode.CONTINUE,
    retry_policy=RetryPolicy(max_retries=0),
)
```

## UICallback Integration

The recovery system communicates through the `UICallback` protocol:

| Event | When |
|-------|------|
| `on_rate_limit(agent, message)` | Rate limit detected |
| `on_rate_limit_wait(agent, seconds)` | Waiting for cooldown |
| `on_retry(agent, attempt, max)` | Retry attempt starting |
| `on_stall_detected(agent, duration)` | Agent idle beyond timeout |
| `on_git_recovery(action, detail)` | Git stash/commit before retry |

## Related

- [Configuration Reference](../reference/configuration.md) — `max_retries`, `max_wait`, `stall_timeout` settings
- [ADR-008: Partial failure semantics with retry](../architecture/adrs/008-partial-failure-semantics-with-retry.md)
- [Stream Pipeline](../architecture/stream-pipeline.md) — stall detection stage
