---
id: ADR-002
title: Use async-first architecture with asyncio
status: ACCEPTED
date: 2026-03-25
decision_makers: [Abdelaziz Abdelrasol]
consulted: []
informed: []
confidence: HIGH
tags: [concurrency, asyncio, subprocess, performance]
related_decisions: [ADR-001, ADR-004]
supersedes: []
superseded_by: []
---

# ADR-002: Use async-first architecture with asyncio

## Status

ACCEPTED

## Context and Problem Statement

orchcore's primary job is launching, monitoring, and managing multiple AI coding agent CLIs as subprocesses. This involves concurrent operations: launching parallel agents within a phase, reading stdout/stderr streams from running subprocesses, detecting stalls via timeouts, managing rate-limit recovery waits, and handling signals for graceful shutdown.

Each of the four source systems handles concurrency differently. Planora uses Python asyncio for subprocess management. Articles, Finvault, and Raven use Bash background processes with `wait` and `&`. The Bash approach is fragile — error handling across background processes is manual, stream capture requires FIFO files, and there is no structured way to cancel a subset of running processes.

orchcore needs a concurrency model that supports: launching N subprocesses concurrently with a configurable limit, reading streams from each subprocess without blocking others, implementing timeouts (stall detection) without busy-waiting, graceful cancellation of running tasks on signal receipt, and structured error propagation from concurrent operations.

### Business Context

- All I/O in orchcore is subprocess I/O (launching agents, reading streams) and file I/O (workspace, config) — no network requests or database queries
- Python 3.12+ provides `asyncio.TaskGroup` for structured concurrency, `tomllib` in stdlib, and modern typing features
- Planora already uses asyncio — its patterns directly inform orchcore's design
- The consuming projects that are Bash-based will be migrated to Python (or use orchcore via subprocess), so the concurrency model must be Python-native

## Decision Drivers

| Driver | Priority | Why It Matters |
|---|---|---|
| Efficient parallel subprocess management | Critical | Parallel phases launch 2-5 agents simultaneously; blocking I/O would serialize them |
| Non-blocking stream reading | Critical | Must read stdout/stderr from multiple agents without blocking the event loop |
| Timeout support for stall detection | High | StallDetector needs efficient timer-based watchdog, not busy-waiting |
| Graceful cancellation | High | SIGINT must cancel running tasks and clean up subprocesses |
| Zero additional dependencies | High | stdlib asyncio avoids adding trio, gevent, or threading complexity |
| Structured concurrency | Medium | TaskGroup (Python 3.12+) prevents orphaned tasks and ensures cleanup |
| Compatibility with consuming projects | Medium | Planora already uses asyncio; no impedance mismatch |

## Considered Options

### Option 1: asyncio (stdlib) — async/await throughout (CHOSEN)

**Overview:** Use Python's built-in asyncio library for all concurrent operations. All subprocess launches, stream reads, timeouts, and signal handling use async/await patterns with TaskGroup for structured concurrency.

**Pros:**
- Zero additional dependencies (stdlib since Python 3.4, mature by 3.12)
- `asyncio.create_subprocess_exec()` provides first-class subprocess support with stream readers
- `asyncio.TaskGroup` (Python 3.12+) provides structured concurrency with automatic cleanup
- `asyncio.Semaphore` provides clean concurrency limiting without thread pools
- `asyncio.wait_for()` and `asyncio.timeout()` provide efficient timeout support
- `asyncio.Event` and task cancellation enable clean signal handling
- Single-threaded event loop avoids GIL contention and race conditions
- Planora already uses asyncio — battle-tested patterns can be directly adopted

**Cons:**
- All code in the call chain must be async — sync functions cannot easily call async functions
- Stack traces for async code are harder to read than synchronous code
- Some consuming projects (if calling orchcore from sync code) need `asyncio.run()` at the boundary
- Error handling in TaskGroup requires understanding of ExceptionGroup (Python 3.11+)

**Risk Assessment:**

| Risk Type | Level | Detail |
|---|---|---|
| Technical | Low | asyncio is well-established; subprocess support is mature |
| Schedule | Low | Planora's async patterns provide a ready reference implementation |
| Ecosystem | Low | asyncio is Python's official concurrency framework; not going away |

**Trade-offs:**
- We gain efficient non-blocking concurrency and structured cleanup, accepting that all code must be async and stack traces are slightly harder to read

---

### Option 2: threading with concurrent.futures

**Overview:** Use Python threads with ThreadPoolExecutor for parallel agent execution. Each agent runs in its own thread with blocking I/O.

**Pros:**
- Simpler mental model — synchronous code in each thread
- `subprocess.run()` (blocking) works directly in threads
- No async/await syntax required; easier for developers unfamiliar with asyncio

**Cons:**
- GIL prevents true CPU parallelism (not relevant for I/O-bound work, but creates contention under load)
- Thread synchronization (locks, conditions) is error-prone
- Cancelling a thread is not supported in Python — `thread.cancel()` does not exist
- Thread pool sizing is guesswork; too few threads serialize work, too many waste resources
- No structured concurrency — orphaned threads can leak
- Stream reading requires one thread per stream (stdout + stderr per agent = 2N threads for N agents)

**Why not chosen:**
- The inability to cancel running threads is a dealbreaker for signal handling. When SIGINT arrives, orchcore must cancel running agent tasks — threading provides no mechanism for this. asyncio's `task.cancel()` is purpose-built for exactly this scenario.

---

### Option 3: trio (third-party async library)

**Overview:** Use trio, an alternative async I/O library that provides structured concurrency with its nursery pattern, stricter timeout handling, and better error messages.

**Pros:**
- Nurseries enforce structured concurrency more strictly than asyncio TaskGroup
- Better error messages and debugging support
- Cancel scopes provide cleaner timeout handling
- trio-process provides subprocess support

**Cons:**
- Additional dependency (trio is not in stdlib)
- Smaller ecosystem — fewer third-party libraries support trio natively
- Consuming projects using asyncio would need compatibility shims (anyio or manual bridging)
- Planora uses asyncio, not trio — migration cost for the primary consumer
- trio's subprocess API is less mature than asyncio's

**Why not chosen:**
- Adding trio as a dependency contradicts the goal of minimal dependencies. asyncio's TaskGroup (added in Python 3.12) provides the structured concurrency that was trio's main advantage, closing the gap. Planora's existing asyncio codebase would require unnecessary migration.

---

### Option 4: multiprocessing

**Overview:** Use Python's multiprocessing module to run each agent in a separate process, sidestepping the GIL entirely.

**Pros:**
- True parallelism (no GIL)
- Process isolation prevents one agent's crash from affecting others

**Cons:**
- Massive overhead for I/O-bound work — launching a Python process per agent (agent CLIs are already separate processes)
- IPC between processes (pipes, queues) is more complex than in-process communication
- No shared state without explicit synchronization primitives
- Would launch a Python process that launches an agent CLI process — unnecessary process nesting
- Debugging across process boundaries is harder

**Why not chosen:**
- orchcore already launches agent CLIs as subprocesses. Adding a Python process layer between orchcore and the agent CLI would be unnecessary nesting. asyncio's subprocess support provides direct subprocess management without the overhead of Python process isolation.

## Decision

**We have decided to use Python's stdlib asyncio as the concurrency foundation for all orchcore operations, requiring Python >= 3.12 for TaskGroup and modern async features.**

### Implementation Details

- All subprocess launches use `asyncio.create_subprocess_exec()` with `PIPE` for stdout and stderr
- Parallel phase execution uses `asyncio.TaskGroup` with `asyncio.Semaphore` for concurrency limiting
- Stall detection uses `asyncio.sleep()` in a concurrent watchdog task
- Signal handling uses `loop.add_signal_handler()` for SIGINT/SIGTERM
- Rate-limit waits use `asyncio.sleep()` for non-blocking backoff
- Stream reading uses `asyncio.StreamReader` line-by-line iteration
- The public API exposes `async def` methods; consuming projects call them from `asyncio.run()` or their own event loop

### When to Revisit This Decision

- If Python introduces a significantly better concurrency primitive (unlikely in the near term)
- If a consuming project requires trio and the bridging cost becomes unsustainable
- If orchcore needs to support CPU-bound work (e.g., local model inference) where the GIL matters
- If Python drops or deprecates asyncio (effectively impossible given its centrality to the ecosystem)

## Consequences

### Positive

- Efficient parallel subprocess management with zero additional dependencies
- Clean task cancellation via `task.cancel()` enables graceful SIGINT/SIGTERM handling
- Structured concurrency via TaskGroup prevents orphaned tasks and ensures cleanup
- Non-blocking stream reading allows monitoring multiple agents without dedicated threads
- asyncio.Semaphore provides simple, correct concurrency limiting
- Planora's existing asyncio patterns can be directly adopted (reducing implementation risk)

### Negative

- All orchcore code must be async — sync helper functions need explicit `async def` wrappers or must avoid I/O
- Consuming projects calling orchcore from synchronous code must use `asyncio.run()` at the boundary
- ExceptionGroup handling (from TaskGroup) requires Python 3.11+ understanding
- Async stack traces are more complex to read than synchronous ones

### Neutral

- Python >= 3.12 requirement excludes older Python versions (3.10, 3.11) but aligns with orchcore's other requirements (type parameter syntax, tomllib)
- asyncio is the standard choice for I/O-bound Python applications — not controversial

## Validation and Monitoring

| Success Metric | Target | How to Measure |
|---|---|---|
| Parallel agent launch overhead | < 10ms scheduling overhead per agent | Benchmark test comparing sequential vs. parallel launch |
| Stream processing does not block event loop | Each stream read completes in < 1ms | Profile with asyncio debug mode enabled |
| Graceful shutdown completes | All subprocesses terminated within 30 seconds of SIGINT (PhaseRunner grace period) | Integration test with simulated signal |
| No orphaned tasks after pipeline completion | Zero pending tasks after run_pipeline returns | Assertion in test teardown |

**Review Schedule:**
- Quarterly: Review async-related bug reports for concurrency issues
- Annually: Reassess asyncio vs. alternatives

## Related Decisions

- **ADR-001:** [Extract reusable orchestration core](./001-extract-reusable-orchestration-core.md) — asyncio is part of the Python package decision
- **ADR-004:** [Composable stream processing pipeline](./004-composable-stream-processing-pipeline.md) — stream pipeline uses async stream readers

## References

- [Python asyncio — Subprocesses](https://docs.python.org/3/library/asyncio-subprocess.html)
- [Python asyncio — TaskGroup](https://docs.python.org/3/library/asyncio-task.html#asyncio.TaskGroup)
- [PEP 654 — Exception Groups and except*](https://peps.python.org/pep-0654/)
- [trio documentation](https://trio.readthedocs.io/) (rejected alternative)

## Document History

| Version | Date | Author | Changes |
|---|---|---|---|
| 1.0 | 2026-03-25 | Abdelaziz Abdelrasol | Initial version (ACCEPTED) |
