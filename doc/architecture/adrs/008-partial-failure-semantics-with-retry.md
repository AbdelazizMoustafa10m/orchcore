---
id: ADR-008
title: Use partial failure semantics with configurable retry policies
status: ACCEPTED
date: 2026-03-25
decision_makers: [Abdelaziz Abdelrasol]
consulted: []
informed: []
confidence: HIGH
tags: [reliability, retry, partial-failure, recovery, rate-limit, resilience]
related_decisions: [ADR-001, ADR-002, ADR-004]
supersedes: []
superseded_by: []
---

# ADR-008: Use partial failure semantics with configurable retry policies

## Status

ACCEPTED

## Context and Problem Statement

Multi-agent orchestration pipelines frequently encounter partial failures. In a parallel phase with three agents (Claude, Codex, Gemini), one agent might hit a rate limit while the other two succeed. In a sequential phase, an agent might crash due to a transient error that would succeed on retry. Different consuming projects need different failure handling strategies — a critical audit phase must not tolerate any failure, while a multi-perspective review phase can produce useful results even if one of three reviewers fails.

The most common failure mode across all four source systems is rate limiting. AI provider rate limits are aggressive and vary by provider:
- **Claude**: "You've hit your usage limit. Your limit resets at 7pm (Europe/Berlin)." — can be hours away
- **Codex**: "Please try again in 5 days 27 minutes" — can be days away
- **Generic**: "Rate limit exceeded" — may or may not include reset time

In the four source systems:
- **Planora** (Python): Has rate-limit detection for Claude with exponential backoff. No partial failure handling — any failure aborts the entire pipeline. No git recovery.
- **Articles** (Bash): Detects rate limits via grep, sleeps for a hardcoded duration, retries once. No partial failure — pipeline aborts on any error.
- **Finvault** (Bash): Similar to Articles. Detects rate limits, hardcoded sleep. Added git stash recovery after a production incident where a dirty tree caused a retry to fail.
- **Raven** (Bash): Most sophisticated — has rate-limit detection with timezone-aware reset time parsing, exponential backoff schedule, git auto-commit recovery, and loop-based retry with configurable max attempts. But the implementation is monolithic Bash with global variables.

Every source system has independently solved rate-limit recovery, but with different detection patterns, different backoff strategies, and inconsistent partial failure handling. orchcore needs a unified, configurable approach.

### Business Context

- AI provider rate limits are a normal operating condition, not an exceptional one — orchcore must handle them gracefully
- Pipeline runs can take 30-60 minutes; aborting entirely because one agent hits a rate limit wastes significant time and cost
- Git dirty tree issues during retry are a real production problem (Finvault incident)
- Different phases have different failure tolerance: audit phases must be strict, review phases can be lenient
- Consuming projects need to choose their failure semantics without forking orchcore's recovery logic

## Decision Drivers

| Driver | Priority | Why It Matters |
|---|---|---|
| Rate-limit detection across multiple agent CLIs | Critical | Each agent has different rate-limit messages; orchcore must detect all known patterns |
| Configurable retry with exponential backoff | Critical | Rate limits require waiting; the wait time must respect the provider's reset schedule |
| Partial failure semantics per phase | Critical | Different phases need different failure tolerance (strict vs. lenient) |
| Git dirty tree recovery | High | Agents write files; failed retries on dirty trees cause cascading failures |
| Timezone-aware reset time parsing | High | Claude provides absolute reset times in user's timezone; orchcore must parse these correctly |
| Maximum wait time cap | High | Prevents infinite waits when reset times are far in the future |
| Consuming project configurability | High | Each project should set its own retry limits, backoff schedule, and failure mode |

## Considered Options

### Option 1: Configurable RetryPolicy with pluggable detection, backoff, and git recovery (CHOSEN)

**Overview:** Implement three independent, composable modules — RateLimitDetector (regex-based detection), BackoffStrategy (exponential schedule with jitter), and GitRecovery (dirty tree management) — unified by a RetryPolicy model that configures their behavior per phase.

**Components:**

1. **RateLimitDetector**: Regex-based pattern matching against agent stderr/stdout for known rate-limit messages. Patterns organized by agent type. Returns bool + extracted message.

2. **ResetTimeParser**: Parses reset time information from rate-limit messages. Handles absolute times with timezones ("resets 7pm Europe/Berlin") and relative durations ("5 days 27 minutes"). Returns seconds until reset.

3. **BackoffStrategy**: Exponential backoff schedule [120, 300, 900, 1800] seconds with random jitter [0-30s]. Respects parsed reset time if available (uses the longer of backoff or reset time). Configurable max wait (default 6 hours).

4. **GitRecovery**: Detects dirty git trees before retry. Auto-commits with message extracted from agent output (or fallback to diff-based message). Alternatively stashes and restores.

5. **RetryPolicy**: Pydantic model configuring max_retries, backoff_schedule, max_wait, and partial_failure_mode (FAIL_FAST, CONTINUE, REQUIRE_MINIMUM with min_count).

**Pros:**
- Each module is independent: use rate-limit detection without git recovery, or backoff without retry policy
- RetryPolicy is a Pydantic model — type-safe, serializable, configurable via TOML
- Three partial failure modes cover all observed use cases across four source systems
- Timezone-aware reset parsing handles the hardest edge case (Claude's absolute times)
- Exponential backoff with jitter prevents thundering herd when multiple agents hit limits simultaneously
- Max wait cap prevents infinite waits (6 hours is a reasonable upper bound for any AI provider)
- Git recovery prevents cascading failures during retry (learned from Finvault production incident)

**Cons:**
- Rate-limit regex patterns are fragile — agent CLI output format changes break detection
- Timezone parsing introduces dependency on Python's `zoneinfo` module (stdlib since 3.9, but needs tzdata on some platforms)
- Three partial failure modes add configuration complexity
- Git auto-commit during recovery might create unwanted commits (mitigated by commit message extraction)

**Risk Assessment:**

| Risk Type | Level | Detail |
|---|---|---|
| Technical | Medium | Rate-limit regex patterns may break when agent CLIs update their output format |
| Schedule | Low | Most logic already exists in Raven and Planora; orchcore formalizes it |
| Ecosystem | Low | All dependencies are stdlib (re, zoneinfo, asyncio) |

**Trade-offs:**
- We gain comprehensive, configurable recovery from the most common failure mode in AI agent orchestration, accepting fragility in regex-based detection and the complexity of three failure modes

---

### Option 2: Simple retry with fixed delay (no detection, no git recovery)

**Overview:** On any non-zero exit code, retry the agent up to N times with a fixed delay between attempts. No rate-limit detection, no backoff escalation, no git recovery.

**Pros:**
- Extremely simple to implement
- No regex patterns to maintain
- Works for any failure type, not just rate limits

**Cons:**
- Fixed delay wastes time (too long for transient errors) or is too short (for rate limits requiring hours-long waits)
- No partial failure handling — all-or-nothing per phase
- Retrying on a dirty git tree causes the same failure repeatedly
- No awareness of provider reset times — blind retry may hit the same rate limit
- No distinction between retryable errors (rate limit) and non-retryable errors (invalid prompt, binary not found)

**Why not chosen:**
- Simple fixed-delay retry does not handle the most common failure mode (rate limiting) effectively. Rate limits require waiting for the provider's reset window, which can be minutes to hours. A fixed delay either wastes time (if too long) or fails again (if too short). The four source systems all evolved beyond fixed-delay retry to backoff-based retry because the simpler approach failed in practice.

---

### Option 3: Circuit breaker pattern

**Overview:** Implement a circuit breaker per agent that opens (stops retrying) after N consecutive failures and periodically half-opens to test if the agent is available again.

**Pros:**
- Prevents wasting resources on a consistently failing agent
- Well-established pattern in distributed systems
- Can provide fast-fail when an agent is known to be unavailable

**Cons:**
- Circuit breaker is designed for high-frequency requests (web services), not low-frequency pipeline phases
- A pipeline runs 3-5 agents per phase, not 1000 requests per second — the statistical assumptions of circuit breakers don't apply
- Circuit breaker state (open/closed/half-open) adds complexity without proportionate benefit
- Rate limits have known reset times — circuit breaker ignores this information
- Does not handle partial failure semantics (circuit breaker is per-agent, not per-phase)

**Why not chosen:**
- Circuit breakers solve a different problem: protecting a service from cascading failures under high request load. orchcore's failure mode is rate limiting with known reset times at low request frequency (a few agents per phase). Exponential backoff with reset-time awareness directly addresses this failure mode; circuit breakers add complexity without benefit.

---

### Option 4: No retry — fail fast, let consuming project retry

**Overview:** orchcore reports failures via AgentResult and UICallback but never retries. Consuming projects implement their own retry logic.

**Pros:**
- Simplest orchcore implementation
- No rate-limit detection patterns to maintain
- Each consuming project has full control over retry behavior

**Cons:**
- Duplicates retry logic across all consuming projects — exactly the problem orchcore is designed to solve
- Every consuming project must independently solve timezone-aware reset time parsing
- Every consuming project must independently implement git dirty tree recovery
- Contradicts ADR-001's goal of eliminating duplication

**Why not chosen:**
- Rate-limit recovery is the single most duplicated piece of logic across the four source systems. Extracting it into orchcore is a primary motivation for the project. Pushing retry logic back to consumers defeats this purpose.

## Decision

**We have decided to implement configurable retry policies with three partial failure modes (FAIL_FAST, CONTINUE, REQUIRE_MINIMUM), rate-limit detection via regex patterns, exponential backoff with timezone-aware reset time parsing, and git dirty tree recovery.**

### Implementation Details

- **RateLimitDetector patterns** are defined as `ClassVar` dicts mapping agent type to compiled regex patterns. Patterns are checked in order; first match wins.
- **ResetTimeParser** uses `zoneinfo.ZoneInfo` for timezone conversion and `re` for extracting time strings. Supports both absolute ("7pm Europe/Berlin") and relative ("5 days 27 minutes") formats.
- **BackoffStrategy schedule** [120, 300, 900, 1800] seconds was derived from empirical observation across the four source systems. Jitter range [0-30] seconds prevents synchronized retries when multiple agents hit limits simultaneously.
- **GitRecovery auto-commit** extracts a commit message from the agent's output using a heuristic (last assistant message or diff summary). Falls back to "orchcore: auto-commit before retry" if extraction fails.
- **RetryPolicy** is configured per phase (not globally) to allow different failure semantics:
  - `FAIL_FAST`: First agent failure fails the entire phase (for critical phases like audit)
  - `CONTINUE`: All agents run to completion; failures recorded but phase continues (for review phases)
  - `REQUIRE_MINIMUM(min_count=N)`: At least N agents must succeed (for consensus phases)
- Default RetryPolicy: max_retries=3, backoff_schedule=[120, 300, 900, 1800], max_wait=21600, partial_failure_mode="fail_fast"
- Consuming projects override via TOML or constructor arguments

### When to Revisit This Decision

- If AI providers standardize rate-limit error formats (could simplify detection)
- If AI providers provide programmatic rate-limit APIs (could replace regex parsing with API calls)
- If the backoff schedule proves suboptimal for new providers (adjust defaults, keep configurability)
- If git auto-commit during recovery causes problems in consuming projects (make it opt-in rather than automatic)
- If more than 3 partial failure modes are needed (currently 3 covers all observed use cases)

## Consequences

### Positive

- Rate-limit recovery is automatic — consuming projects don't need to implement it
- Timezone-aware reset time parsing handles the hardest edge case correctly
- Exponential backoff with jitter prevents thundering herd effects
- Three partial failure modes cover strict (audit), lenient (review), and consensus (voting) phase types
- Git dirty tree recovery prevents cascading failures during retry
- RetryPolicy as a Pydantic model enables per-phase configuration via TOML
- Max wait cap prevents infinite waits

### Negative

- Rate-limit regex patterns are fragile and may break when agent CLIs change their output format
- Git auto-commit during recovery may create unwanted commits (mitigated by extractable commit messages and opt-in configuration)
- Three failure modes add configuration complexity that simple projects may not need (mitigated by sensible defaults)
- zoneinfo requires tzdata package on some platforms (Windows, some minimal Linux containers)

### Neutral

- Exponential backoff schedule [120, 300, 900, 1800] is a reasonable default that can be overridden
- Max wait of 6 hours (21600 seconds) prevents absurdly long waits while accommodating overnight rate-limit resets

## Validation and Monitoring

| Success Metric | Target | How to Measure |
|---|---|---|
| Rate-limit detection accuracy | > 95% for known patterns | Test with real rate-limit output samples from each agent |
| Reset time parsing accuracy | Correct to within 60 seconds | Unit tests with known timezone conversions |
| Backoff schedule adherence | Wait times match schedule + jitter within 1 second | Unit test with mocked sleep |
| Partial failure: FAIL_FAST | Phase fails on first agent failure | Integration test with 3 agents, 1 failing |
| Partial failure: CONTINUE | Phase succeeds with 2/3 agents passing | Integration test with 3 agents, 1 failing |
| Partial failure: REQUIRE_MINIMUM | Phase succeeds with N+ passing, fails with < N | Integration test with configurable min_count |
| Git recovery | Dirty tree cleaned before retry | Integration test with git fixture |

**Review Schedule:**
- On each agent CLI update: verify rate-limit detection patterns still work
- Quarterly: Review retry success/failure rates in consuming projects
- Annually: Reassess backoff schedule and failure modes

## Related Decisions

- **ADR-001:** [Extract reusable orchestration core](./001-extract-reusable-orchestration-core.md) — recovery is a primary motivation for extraction
- **ADR-002:** [Async-first architecture](./002-async-first-architecture-with-asyncio.md) — backoff waits use asyncio.sleep
- **ADR-004:** [Composable stream pipeline](./004-composable-stream-processing-pipeline.md) — RATE_LIMIT StreamEventType triggers recovery

## References

- Raven/Ralph source code (most sophisticated retry implementation among the four systems)
- Finvault production incident report (git dirty tree causing cascading retry failures)
- [Exponential backoff and jitter (AWS)](https://aws.amazon.com/blogs/architecture/exponential-backoff-and-jitter/)
- [Python zoneinfo](https://docs.python.org/3/library/zoneinfo.html)
- [Retry pattern (Microsoft)](https://learn.microsoft.com/en-us/azure/architecture/patterns/retry)

## Document History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-03-25 | Abdelaziz Abdelrasol | Initial version (ACCEPTED) |
