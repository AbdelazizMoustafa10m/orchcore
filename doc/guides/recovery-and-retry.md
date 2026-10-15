# Recovery & Retry

This guide describes how orchcore handles rate limits, stalls, dirty git trees, and partial failures in multi-agent phases.

## Typed Failure Categories

Every failed `AgentResult` carries an `error_category`, an
`AgentErrorCategory` value such as `rate_limit`, `nonzero_exit`,
`stream_error`, `timeout`, or `cancelled`. The category is set where the
failure is first observed (see the
[stream-events reference](../reference/stream-events.md#agenterrorcategory)
for where each category comes from). The engine decides whether to retry from
this category alone: a result with `error_category == AgentErrorCategory.RATE_LIMIT`
is retried under the phase's `RetryPolicy`, using the
`rate_limit_reset_seconds` the runner already parsed. Every other failure is
returned without a retry. The engine does not match error strings, so stdout
text that only looks like a rate limit does not trigger a retry.

## Rate-Limit Detection

`RateLimitDetector` matches agent output against per-CLI regex patterns for rate-limit messages. The runner uses it as a fallback: when a CLI exits non-zero without emitting a typed `rate_limit` stream event, a match in the stderr tail changes the result's category from `nonzero_exit` to `rate_limit`.

Examples of detected messages:

| Agent | Example Message |
|-------|----------------|
| Claude | `"You've hit your usage limit"` |
| Codex | `"try again in 5 minutes"` |
| Antigravity | `"RESOURCE_EXHAUSTED"`, `"Individual quota reached"` |
| Generic | `"rate limit exceeded"`, `"429"`, `"too many requests"` |

```python
from orchcore.recovery import RateLimitDetector

detector = RateLimitDetector()
if detector.is_rate_limited(output_text):
    message = detector.extract_message(output_text)
    print(f"Rate limited: {message}")
```

## Reset Time Parsing

`ResetTimeParser.parse()` returns the number of seconds to wait, read from a rate-limit message, or `None` if the message contains no reset time:

| Format | Example | Result |
|--------|---------|--------|
| Go-style duration | `"resets in 1h30m"` | 5400 |
| Relative duration | `"try again in 5 minutes"` | 300 |
| Seconds | `"retry after 120 seconds"` | 120 |
| Absolute time with time zone | `"resets 7pm Europe/Berlin"` | Seconds until the next 7 pm in Berlin |
| No reset time | `"429 Too Many Requests"` | `None` (the engine uses the backoff schedule) |

For an absolute time, the time zone must be an IANA name such as `Europe/Berlin` or `America/Los_Angeles`. A time without a zone is read as UTC. An unknown name, including abbreviations such as `PST`, logs a warning and is also read as UTC. If the time has already passed today, the parser uses the same time on the next day.

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
| `max_retries` | `int` | `3` | Maximum rate-limit retries per agent |
| `backoff_schedule` | `list[int]` | `[120, 300, 900, 1800]` | Wait times in seconds for each retry; the last entry repeats for later retries |
| `max_wait` | `int` | `21600` | Upper bound for a single wait, in seconds (6 hours) |
| `failure_mode` | `FailureMode` | `FAIL_FAST` | How agent failures decide the phase status; overrides `Phase.failure_mode` |
| `min_count` | `int` | `1` | Minimum successful agents for `REQUIRE_MINIMUM` mode |
| `git_recovery` | `"off" \| "auto_commit" \| "stash"` | `"off"` | Optional dirty-tree recovery before rate-limit retries |
| `git_recovery_cwd` | `Path \| None` | `None` | Directory where git commands run; defaults to the agent cwd when available |
| `git_recovery_no_verify` | `bool` | `False` | Add `--no-verify` to recovery commits only when explicitly requested |

### Backoff Schedule

With the default schedule and `max_retries=3`, the waits are:

| Retry | Wait | Cumulative |
|-------|------|-----------|
| 1st | 2 minutes | 2 min |
| 2nd | 5 minutes | 7 min |
| 3rd | 15 minutes | 22 min |

When the rate-limit message contains a reset time, that time is used instead of the schedule entry. Each wait gets 0-30 seconds of random jitter, so agents that hit the same limit do not all retry at the same moment, and is then capped at `max_wait`.

## Failure Modes

`FailureMode` decides a phase's status from its agents' results. In a parallel phase, `FAIL_FAST` also cancels the agents still running after the first failure. A sequential phase always runs every agent and then evaluates the results.

If a phase sets `retry_policy`, the policy's `failure_mode` and `min_count` apply and `Phase.failure_mode` is ignored. In that case, set the mode on the `RetryPolicy`, as the `REQUIRE_MINIMUM` example does.

### `FAIL_FAST`

Any agent failure fails the phase. This is the default.

```python
phase = Phase(
    name="review",
    agents=["claude", "codex"],
    parallel=True,
    failure_mode=FailureMode.FAIL_FAST,
)
```

### `CONTINUE`

Every agent runs to completion regardless of the others, and the phase status reflects how many succeeded:

- All succeed → `done`
- Some succeed, some fail → `partial`
- All fail → `failed`

```python
phase = Phase(
    name="review",
    agents=["claude", "codex", "antigravity"],
    parallel=True,
    failure_mode=FailureMode.CONTINUE,
)
```

### `REQUIRE_MINIMUM`

At least `min_count` agents must succeed. Use it to run redundant agents when not all of them need to succeed. The phase is `done` if every agent succeeds, `partial` if at least `min_count` succeed, and `failed` otherwise.

```python
from orchcore.recovery import RetryPolicy, FailureMode

phase = Phase(
    name="review",
    agents=["claude", "codex", "antigravity"],
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

Git commands run in `git_recovery_cwd` when it is set, otherwise in the agent's explicit working directory. If neither is set, recovery is skipped with a warning and an `on_git_recovery("skipped_no_cwd", ...)` callback.

## Stall and Runtime Enforcement

`stall_timeout` and `deep_tool_timeout` detect silence and emit `STALL` events, but they do not stop the agent. Two separate settings enforce limits:

| AgentConfig field | Default | Behavior |
|-------------------|---------|----------|
| `kill_on_stall` | `False` | When true, a detected stall terminates the process tree and returns an `AgentResult.error` like `stalled for 300s (kill_on_stall)` |
| `max_runtime` | `None` | When set, caps the stream-consume phase and returns an error like `max_runtime exceeded after 1800s` |

A timeout or stall kill can leave partial output files. `AgentResult.output_empty` reports whether the output file is empty.

## Per-Phase Retry Configuration

Each phase can have its own retry policy:

```python
from orchcore.pipeline import Phase
from orchcore.recovery import RetryPolicy, FailureMode

# Critical phase: more retries with shorter waits
implementation = Phase(
    name="implementation",
    agents=["claude"],
    retry_policy=RetryPolicy(
        max_retries=5,
        backoff_schedule=[60, 120, 300, 600, 1200],
    ),
)

# Review phase: no retries, and one agent's failure does not stop the other
review = Phase(
    name="review",
    agents=["claude", "codex"],
    parallel=True,
    retry_policy=RetryPolicy(max_retries=0, failure_mode=FailureMode.CONTINUE),
)
```

## UICallback Integration

The engine reports recovery steps through the `UICallback` protocol:

| Event | When |
|-------|------|
| `on_rate_limit(agent, message)` | Rate limit detected and a retry follows |
| `on_rate_limit_wait(agent, seconds)` | Waiting for the rate limit to reset |
| `on_retry(agent, attempt, max)` | Retry attempt starting |
| `on_stall_detected(agent, duration)` | Agent idle beyond timeout |
| `on_git_recovery(action, detail)` | Git commit, stash, or stash restore around a retry |

## Related

- [Configuration Reference](../reference/configuration.md): the `max_retries`, `max_wait`, and `stall_timeout` settings
- [ADR-008: Partial failure semantics with retry](../architecture/adrs/008-partial-failure-semantics-with-retry.md)
- [Stream Pipeline](../architecture/stream-pipeline.md): the stall detection stage
