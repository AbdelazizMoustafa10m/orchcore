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
| `git_recovery` | `"off" \| "auto_commit" \| "stash"` | `"off"` | Optional dirty-tree recovery before rate-limit retries |
| `git_recovery_cwd` | `Path \| None` | `None` | Directory where git commands run; defaults to the agent cwd when available |
| `git_recovery_no_verify` | `bool` | `False` | Add `--no-verify` to recovery commits only when explicitly requested |

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

## Git Recovery Policy

Git recovery is disabled by default. orchcore never runs `git` or mutates the consumer's repository unless the phase opts in through `RetryPolicy.git_recovery`.

```python
from orchcore.recovery import RetryPolicy

policy = RetryPolicy(git_recovery="auto_commit")
```

Available modes:

| Mode | Behavior |
|------|----------|
| `"off"` | No git subprocesses are started |
| `"auto_commit"` | If the tree is dirty, stage and commit before retrying; hooks run unless `git_recovery_no_verify=True` |
| `"stash"` | Stash before the retry wait and restore the stash before the next attempt |

Git commands run in `git_recovery_cwd` when set, otherwise the explicit agent working directory. If neither exists, recovery is skipped with a warning and an `on_git_recovery("skipped_no_cwd", ...)` callback.

## Stall and Runtime Enforcement

`stall_timeout` and `deep_tool_timeout` detect silence and emit `STALL` events. Enforcement is separate:

| AgentConfig field | Default | Behavior |
|-------------------|---------|----------|
| `kill_on_stall` | `False` | When true, a detected stall terminates the process tree and returns an `AgentResult.error` like `stalled for 300s (kill_on_stall)` |
| `max_runtime` | `None` | When set, caps the stream-consume phase and returns an error like `max_runtime exceeded after 1800s` |

Timeout and stall kills may leave partial output artifacts; `output_empty` reports whether anything useful was written.

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
